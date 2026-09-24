/**
 * 后台友链管理页回归测试。
 *
 * 守住四类契约：
 * 1. **列表口径**：读的是 `/links/manage`（含未启用），并如实给出"前台会显示几条"；
 * 2. **写操作正确**：新建/编辑的 payload 形状（trim、空串转 null、排序为数字）、
 *    启用-隐藏切换只提交 `is_active`（部分更新，别把整条记录回写）；
 * 3. **失败不装成功**：接口失败时提示、按钮解禁、**列表不刷新**（刷新会让界面
 *    与真实状态不符）、编辑态保留草稿；
 * 4. **校验就地进行**：空名称/空地址一个请求都不发（协议白名单以后端为准，
 *    前端只拦"明显没填"）。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 的 linkApi.*）。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, linkApi } from '@/api'
import { useToast } from '@/composables/useToast'
import type { FriendLink } from '@/types'

import LinksView from './LinksView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeLink(overrides: Partial<FriendLink> = {}): FriendLink {
  return {
    id: 1,
    name: 'FastAPI',
    url: 'https://fastapi.tiangolo.com',
    description: '高性能 Python Web 框架',
    avatar_url: null,
    sort_order: 0,
    is_active: true,
    created_at: '2026-09-22T00:00:00Z',
    ...overrides,
  }
}

const ACTIVE = makeLink({ id: 1, name: 'FastAPI', sort_order: 0 })
const HIDDEN = makeLink({ id: 2, name: 'Vue', is_active: false, sort_order: 5, description: null })

/* ------------------------------------------------------------ 脚手架 */

const Blank = { template: '<div />' }

async function mountAdmin(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/admin/links', component: Blank },
    ],
  })
  await router.push('/admin/links')
  await router.isReady()
  const wrapper = mount(LinksView, { global: { plugins: [router, createPinia()] } })
  await flushPromises()
  return { wrapper, router }
}

/**
 * 列表行的定位：按文案找 li。
 *
 * ⚠️ 进入编辑态后，行内渲染的是输入框，`textContent` 里不再有站点名 ——
 * 所以**必须先取到行、再点编辑**，不能靠文案重新查找（这也是下面几条用例的写法）。
 */
function rowByText(wrapper: VueWrapper, text: string): DOMWrapper<Element> {
  const found = wrapper.findAll('li').find((item) => item.text().includes(text))
  if (!found) throw new Error(`找不到包含「${text}」的行`)
  return found
}

function rowButton(row: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = row.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`行内找不到按钮「${label}」`)
  return found
}

function field(wrapper: VueWrapper, label: string) {
  return wrapper.get(`input[aria-label="${label}"]`)
}

function lastToastMessage(): string {
  const items = useToast().items.value
  return items.length ? items[items.length - 1].message : ''
}

function dialog(): HTMLElement | null {
  return document.body.querySelector('[role="dialog"]')
}

function dialogButton(label: string): HTMLButtonElement | undefined {
  return [...document.body.querySelectorAll('button')].find(
    (item) => item.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined
}

async function confirmDelete(): Promise<void> {
  dialogButton('删除友链')?.click()
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.restoreAllMocks()
})

/* ------------------------------------------------------------ 用例 */

describe('LinksView（后台） · 列表', () => {
  it('读的是含未启用的管理列表，并把"前台会显示几条"算出来', async () => {
    const list = vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE, HIDDEN])

    const { wrapper } = await mountAdmin()

    expect(list).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('共 2 条')
    expect(wrapper.text()).toContain('1 条在前台显示')
    // 未启用的那行要有明确标记，否则站长以为它已经在前台了
    expect(rowByText(wrapper, 'Vue').text()).toContain('已隐藏')
  })

  it('加载中显示骨架屏，失败给错误与重试且不伪装成空列表', async () => {
    let resolve!: (value: FriendLink[]) => void
    vi.spyOn(linkApi, 'listManaged').mockReturnValue(
      new Promise<FriendLink[]>((res) => {
        resolve = res
      }),
    )

    const { wrapper } = await mountAdmin()
    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    expect(wrapper.text()).not.toContain('还没有友链')

    resolve([ACTIVE])
    await flushPromises()
    expect(wrapper.text()).toContain('FastAPI')
  })

  it('列表失败时可以重试恢复', async () => {
    const list = vi
      .spyOn(linkApi, 'listManaged')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce([ACTIVE])

    const { wrapper } = await mountAdmin()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)

    const retry = wrapper.findAll('button').find((item) => item.text().trim() === '重试')
    await retry?.trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('FastAPI')
  })

  it('空列表时显示空态并指向前台页面', async () => {
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([])

    const { wrapper } = await mountAdmin()

    expect(wrapper.text()).toContain('还没有友链')
    expect(wrapper.text()).toContain('/links')
  })
})

describe('LinksView（后台） · 新建', () => {
  it('提交 trim 后的字段，空串转 null，成功后清空表单并刷新列表', async () => {
    const created = vi.spyOn(linkApi, 'create').mockResolvedValue(makeLink({ id: 9, name: 'Vite' }))
    const list = vi
      .spyOn(linkApi, 'listManaged')
      .mockResolvedValueOnce([ACTIVE])
      .mockResolvedValueOnce([ACTIVE, makeLink({ id: 9, name: 'Vite' })])

    const { wrapper } = await mountAdmin()
    await field(wrapper, '站点名称').setValue('  Vite  ')
    await field(wrapper, '站点地址').setValue('  vitejs.dev  ')
    await field(wrapper, '介绍').setValue('   前端工具链   ')
    await field(wrapper, '排序').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(created).toHaveBeenCalledWith({
      name: 'Vite',
      url: 'vitejs.dev',
      description: '前端工具链',
      avatar_url: null,
      sort_order: 2,
    })
    expect(lastToastMessage()).toBe('友链已添加')
    expect(list).toHaveBeenCalledTimes(2)
    // 表单清空，方便连着录下一条
    expect((field(wrapper, '站点名称').element as HTMLInputElement).value).toBe('')
  })

  it('空名称或空地址就地拦下，一个请求都不发', async () => {
    const created = vi.spyOn(linkApi, 'create')
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([])

    const { wrapper } = await mountAdmin()

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(created).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写站点名称')

    await field(wrapper, '站点名称').setValue('Vite')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(created).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写站点地址')
  })

  it('创建失败：提示后端文案、表单内容不丢、按钮解禁可重试', async () => {
    vi.spyOn(linkApi, 'create').mockRejectedValue(
      new ApiError('友链地址只支持 http 或 https 协议', 422, 'validation_error'),
    )
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    await field(wrapper, '站点名称').setValue('Vite')
    await field(wrapper, '站点地址').setValue('javascript:alert(1)')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(lastToastMessage()).toBe('友链地址只支持 http 或 https 协议')
    // 用户的下一个动作是改地址，内容不能丢
    expect((field(wrapper, '站点地址').element as HTMLInputElement).value).toBe('javascript:alert(1)')
    const submit = wrapper.get('button[type="submit"]')
    expect(submit.attributes('disabled')).toBeUndefined()
  })
})

describe('LinksView（后台） · 编辑与启停', () => {
  it('点「编辑」只把那一行换成输入框并回填（空描述回填空串）', async () => {
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE, HIDDEN])

    const { wrapper } = await mountAdmin()
    const row = rowByText(wrapper, 'Vue')
    await rowButton(row, '编辑').trigger('click')
    await flushPromises()

    expect((row.get('input[aria-label="编辑站点名称"]').element as HTMLInputElement).value).toBe('Vue')
    expect(
      (row.get('input[aria-label="编辑站点地址"]').element as HTMLInputElement).value,
    ).toBe('https://fastapi.tiangolo.com')
    // null 描述回填成空串，否则输入框里会出现 "null" 字样
    expect((row.get('input[aria-label="编辑介绍"]').element as HTMLInputElement).value).toBe('')
  })

  it('保存编辑：提交 trim 后的字段并退出编辑态', async () => {
    const update = vi.spyOn(linkApi, 'update').mockResolvedValue(makeLink({ name: 'Vue 3' }))
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    const row = rowByText(wrapper, 'FastAPI')
    await rowButton(row, '编辑').trigger('click')
    await flushPromises()
    await row.get('input[aria-label="编辑站点名称"]').setValue('  Vue 3  ')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledWith(1, {
      name: 'Vue 3',
      url: 'https://fastapi.tiangolo.com',
      description: '高性能 Python Web 框架',
      avatar_url: null,
      sort_order: 0,
    })
    expect(rowByText(wrapper, 'FastAPI').find('input').exists()).toBe(false)
  })

  it('编辑时清空名称/地址会被就地拦下（与新建同一道校验）', async () => {
    const update = vi.spyOn(linkApi, 'update')
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    const row = rowByText(wrapper, 'FastAPI')
    await rowButton(row, '编辑').trigger('click')
    await flushPromises()

    await row.get('input[aria-label="编辑站点名称"]').setValue('   ')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()
    expect(update).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写站点名称')

    await row.get('input[aria-label="编辑站点名称"]').setValue('FastAPI')
    await row.get('input[aria-label="编辑站点地址"]').setValue('   ')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()
    expect(update).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写站点地址')
  })

  it('保存失败：提示错误、留在编辑态、草稿不丢', async () => {
    vi.spyOn(linkApi, 'update').mockRejectedValue(
      new ApiError('友链地址只支持 http 或 https 协议', 422, 'validation_error'),
    )
    const list = vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    const row = rowByText(wrapper, 'FastAPI')
    await rowButton(row, '编辑').trigger('click')
    await flushPromises()
    await row.get('input[aria-label="编辑站点地址"]').setValue('ftp://x.example')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('友链地址只支持 http 或 https 协议')
    expect(list).toHaveBeenCalledTimes(1) // 失败不刷新
    expect((row.get('input[aria-label="编辑站点地址"]').element as HTMLInputElement).value).toBe(
      'ftp://x.example',
    )
  })

  it('点「隐藏」只提交 is_active（部分更新，别把整条记录回写）', async () => {
    const update = vi.spyOn(linkApi, 'update').mockResolvedValue(makeLink({ is_active: false }))
    const list = vi
      .spyOn(linkApi, 'listManaged')
      .mockResolvedValueOnce([ACTIVE])
      .mockResolvedValueOnce([makeLink({ is_active: false })])

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, 'FastAPI'), '隐藏').trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledWith(1, { is_active: false })
    expect(lastToastMessage()).toBe('已从前台隐藏')
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('启停失败：提示错误且不刷新列表', async () => {
    vi.spyOn(linkApi, 'update').mockRejectedValue(new ApiError('操作失败', 500, 'internal_error'))
    const list = vi.spyOn(linkApi, 'listManaged').mockResolvedValue([HIDDEN])

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, 'Vue'), '显示').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('操作失败')
    expect(list).toHaveBeenCalledTimes(1)
  })
})

describe('LinksView（后台） · 删除', () => {
  it('点「删除」只打开确认框，一个请求都不发', async () => {
    const remove = vi.spyOn(linkApi, 'remove')
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, 'FastAPI'), '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).not.toBeNull()
    expect(dialog()?.textContent).toContain('FastAPI')
  })

  it('取消删除：不发请求、确认框关掉、行还在', async () => {
    const remove = vi.spyOn(linkApi, 'remove')
    vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, 'FastAPI'), '删除').trigger('click')
    await flushPromises()
    dialogButton('取消')?.click()
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(rowByText(wrapper, 'FastAPI').exists()).toBe(true)
  })

  it('确认删除：调用接口、提示、刷新列表', async () => {
    const remove = vi.spyOn(linkApi, 'remove').mockResolvedValue({ detail: '已删除' })
    const list = vi
      .spyOn(linkApi, 'listManaged')
      .mockResolvedValueOnce([ACTIVE])
      .mockResolvedValueOnce([])

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, 'FastAPI'), '删除').trigger('click')
    await flushPromises()
    await confirmDelete()

    expect(remove).toHaveBeenCalledWith(1)
    expect(lastToastMessage()).toBe('《FastAPI》已删除')
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('删除失败：确认框留着可重试，列表不刷新', async () => {
    vi.spyOn(linkApi, 'remove').mockRejectedValue(new ApiError('删除失败', 500, 'internal_error'))
    const list = vi.spyOn(linkApi, 'listManaged').mockResolvedValue([ACTIVE])

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, 'FastAPI'), '删除').trigger('click')
    await flushPromises()
    await confirmDelete()

    expect(lastToastMessage()).toBe('删除失败')
    expect(dialog()).not.toBeNull()
    expect(list).toHaveBeenCalledTimes(1)
  })
})
