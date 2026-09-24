/**
 * 后台留言板管理页回归测试。
 *
 * 守住四类契约：
 * 1. **队列口径**：默认只看待审（唯一需要"处理"的队列），筛选切换会带上
 *    `approved` 参数并回到第 1 页；
 * 2. **回复只有一条**：保存回复走 `PUT /guestbook/{id}/reply`；**清空后保存等于删除回复**
 *    （必须给出"回复已清除"的明确反馈，否则站长会以为点错了）；
 * 3. **失败不装成功**：接口失败时提示、按钮解禁、列表不刷新；
 * 4. **删掉当前页最后一条要退页**：否则会停在一个空页上，而空态里没有分页器。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 的 guestbookApi.*）。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, guestbookApi } from '@/api'
import { useToast } from '@/composables/useToast'
import type { GuestbookMessageAdmin, Page } from '@/types'

import GuestbookView from './GuestbookView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeMessage(overrides: Partial<GuestbookMessageAdmin> = {}): GuestbookMessageAdmin {
  return {
    id: 1,
    author_name: '访客甲',
    author_email: 'guest@example.com',
    author_site: 'https://example.com',
    content: '主题很好看，请问字体是什么？',
    is_approved: false,
    reply_content: null,
    replied_at: null,
    created_at: '2026-09-24T10:00:00Z',
    ip_address: '203.0.113.7',
    ...overrides,
  }
}

function makePage(
  items: GuestbookMessageAdmin[],
  overrides: Partial<Page<GuestbookMessageAdmin>> = {},
): Page<GuestbookMessageAdmin> {
  return { items, total: items.length, page: 1, page_size: 20, pages: 1, ...overrides }
}

/* ------------------------------------------------------------ 脚手架 */

const Blank = { template: '<div />' }

async function mountAdmin(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/admin/guestbook', component: Blank },
    ],
  })
  await router.push('/admin/guestbook')
  await router.isReady()
  const wrapper = mount(GuestbookView, { global: { plugins: [router, createPinia()] } })
  await flushPromises()
  return { wrapper, router }
}

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

function filterTab(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到筛选按钮「${label}」`)
  return found
}

function lastToastMessage(): string {
  const items = useToast().items.value
  return items.length ? items[items.length - 1].message : ''
}

function dialogButton(label: string): HTMLButtonElement | undefined {
  return [...document.body.querySelectorAll('button')].find(
    (item) => item.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined
}

beforeEach(() => {
  setActivePinia(createPinia())
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.restoreAllMocks()
})

/* ------------------------------------------------------------ 用例 */

describe('GuestbookView（后台） · 队列与筛选', () => {
  it('默认只看待审，并显示总数', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage()]))

    const { wrapper } = await mountAdmin()

    // 待审是唯一需要"处理"的队列，默认就该停在这里
    expect(list).toHaveBeenCalledWith(1, 20, false)
    expect(wrapper.text()).toContain('共 1 条')
    expect(wrapper.text()).toContain('访客甲')
  })

  it('切到「已通过」带上 approved=true 并回到第 1 页', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage({ is_approved: true })], { total: 25, pages: 2 }))

    const { wrapper } = await mountAdmin()
    // 先翻到第 2 页，再切筛选：切筛选必须把页码收回第 1 页
    await wrapper.findAll('button').find((item) => item.text().trim() === '2')?.trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(2, 20, false)

    await filterTab(wrapper, '已通过').trigger('click')
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith(1, 20, true)
  })

  it('没有待审留言时给出空态文案', async () => {
    vi.spyOn(guestbookApi, 'listManaged').mockResolvedValue(makePage([]))

    const { wrapper } = await mountAdmin()

    expect(wrapper.text()).toContain('没有待审核的留言')
  })

  it('加载失败给出错误文案与重试入口', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockRejectedValueOnce(new ApiError('服务器开小差了', 500, 'internal_error'))
      .mockResolvedValueOnce(makePage([makeMessage()]))

    const { wrapper } = await mountAdmin()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    await wrapper
      .findAll('button')
      .find((item) => item.text().trim() === '重试')
      ?.trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('访客甲')
  })
})

describe('GuestbookView（后台） · 联系方式', () => {
  it('后台显示邮箱与 IP（公开接口刻意不含这些字段）', async () => {
    vi.spyOn(guestbookApi, 'listManaged').mockResolvedValue(
      makePage([makeMessage({ author_email: 'someone@example.com', ip_address: '198.51.100.9' })]),
    )

    const { wrapper } = await mountAdmin()

    expect(wrapper.text()).toContain('someone@example.com')
    expect(wrapper.text()).toContain('198.51.100.9')
  })
})

describe('GuestbookView（后台） · 审核', () => {
  it('通过：调用 moderate(id, true) 并刷新列表', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage({ id: 5 })]))
    const moderate = vi
      .spyOn(guestbookApi, 'moderate')
      .mockResolvedValue(makeMessage({ id: 5, is_approved: true }))

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    expect(moderate).toHaveBeenCalledWith(5, true)
    expect(lastToastMessage()).toBe('已通过')
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('撤下：调用 moderate(id, false)', async () => {
    vi.spyOn(guestbookApi, 'listManaged').mockResolvedValue(
      makePage([makeMessage({ id: 6, is_approved: true })]),
    )
    const moderate = vi
      .spyOn(guestbookApi, 'moderate')
      .mockResolvedValue(makeMessage({ id: 6, is_approved: false }))

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '撤下').trigger('click')
    await flushPromises()

    expect(moderate).toHaveBeenCalledWith(6, false)
    expect(lastToastMessage()).toBe('已撤下')
  })

  it('审核失败：提示错误且不刷新列表', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage({ id: 5 })]))
    vi.spyOn(guestbookApi, 'moderate').mockRejectedValue(
      new ApiError('没有权限', 403, 'forbidden'),
    )

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toContain('没有权限')
    expect(list).toHaveBeenCalledTimes(1)
  })
})

describe('GuestbookView（后台） · 回复', () => {
  it('保存回复：调用 reply(id, 内容) 并收起编辑器', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage({ id: 8 })]))
    const reply = vi
      .spyOn(guestbookApi, 'reply')
      .mockResolvedValue(makeMessage({ id: 8, reply_content: '谢谢！' }))

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '回复').trigger('click')
    await flushPromises()

    const textarea = wrapper.get('[aria-label="回复 访客甲 的留言"]')
    await textarea.setValue('  谢谢，用的是思源宋体。  ')
    await rowButton(rowByText(wrapper, '访客甲'), '保存回复').trigger('click')
    await flushPromises()

    expect(reply).toHaveBeenCalledWith(8, '谢谢，用的是思源宋体。')
    expect(lastToastMessage()).toBe('回复已保存')
    expect(wrapper.find('[aria-label="回复 访客甲 的留言"]').exists()).toBe(false)
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('清空后保存＝删除回复，文案必须说清是「清除」而不是「保存」', async () => {
    vi.spyOn(guestbookApi, 'listManaged').mockResolvedValue(
      makePage([
        makeMessage({
          id: 9,
          reply_content: '旧的回复',
          replied_at: '2026-09-24T12:00:00Z',
        }),
      ]),
    )
    const reply = vi
      .spyOn(guestbookApi, 'reply')
      .mockResolvedValue(makeMessage({ id: 9, reply_content: null, replied_at: null }))

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '编辑回复').trigger('click')
    await flushPromises()

    await wrapper.get('[aria-label="回复 访客甲 的留言"]').setValue('   ')
    await rowButton(rowByText(wrapper, '访客甲'), '保存回复').trigger('click')
    await flushPromises()

    expect(reply).toHaveBeenCalledWith(9, '')
    expect(lastToastMessage()).toBe('回复已清除')
  })

  it('已回复的留言默认只读展示，点「编辑回复」才展开编辑器', async () => {
    vi.spyOn(guestbookApi, 'listManaged').mockResolvedValue(
      makePage([
        makeMessage({
          id: 10,
          reply_content: '已经回过你了',
          replied_at: '2026-09-24T12:00:00Z',
        }),
      ]),
    )

    const { wrapper } = await mountAdmin()

    expect(wrapper.text()).toContain('已经回过你了')
    expect(wrapper.text()).toContain('已回复')
    expect(wrapper.find('[aria-label="回复 访客甲 的留言"]').exists()).toBe(false)
  })
})

describe('GuestbookView（后台） · 删除', () => {
  it('点删除只打开确认框，一个请求都不发', async () => {
    vi.spyOn(guestbookApi, 'listManaged').mockResolvedValue(makePage([makeMessage({ id: 3 })]))
    const remove = vi.spyOn(guestbookApi, 'remove')

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
  })

  it('确认删除后调用 remove(id) 并刷新列表', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage({ id: 3 })]))
    const remove = vi.spyOn(guestbookApi, 'remove').mockResolvedValue({ detail: '留言已删除' })

    const { wrapper } = await mountAdmin()
    await rowButton(rowByText(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(remove).toHaveBeenCalledWith(3)
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('删掉当前页最后一条时页码退回上一页（否则停在空页且没有入口回去）', async () => {
    const list = vi
      .spyOn(guestbookApi, 'listManaged')
      .mockResolvedValue(makePage([makeMessage({ id: 3 })], { total: 25, pages: 2 }))
    vi.spyOn(guestbookApi, 'remove').mockResolvedValue({ detail: '留言已删除' })

    const { wrapper } = await mountAdmin()
    await wrapper.findAll('button').find((item) => item.text().trim() === '2')?.trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(2, 20, false)

    await rowButton(rowByText(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith(1, 20, false)
  })
})
