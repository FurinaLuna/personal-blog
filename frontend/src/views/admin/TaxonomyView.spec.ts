/**
 * TaxonomyView 测试（分类与标签管理）。
 *
 * 这一页把「分类」和「标签」两个高度相似的区块并排放在同一屏，两者的按钮文案
 * 还有重名（都有「删除」「添加」），所以测试里必须按区块取元素，否则很容易
 * 断言到隔壁那一栏。守的契约：
 *
 * 1. **请求形态**：分类要带 `with_counts`（要显示「N 篇」），标签走默认参数；
 * 2. **写操作的 payload**：名称 trim、空描述转 null（后端靠 null 清空描述）、
 *    新建分类时 sort_order 固定 0；
 * 3. **本地校验的口径**：新建分类名称为空要拦下并提示；标签为空是静默忽略；
 * 4. **重命名是原地编辑**：点「编辑」只把那一行换成输入框，取消不发请求；
 * 5. **删除的范围**：删分类会改变文章的分类归属，所以要**同时**刷新分类与标签；
 *    删标签只影响标签本身；
 * 6. **失败与权限**：403 要给出人话（只有站长可以…），失败保留确认框、不刷新列表；
 *    清理空标签的提示文案取接口返回值而不是写死的串；
 * 7. 加载失败与「还没有分类」必须分开（失败给错误文案 + 重试入口，加载中给骨架屏）；
 *    重命名与新建走同一道非空校验。这三条原先都是缺陷，现由用例守住修复后的行为。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法），
 * 与既有视图 spec 的写法一致。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, categoryApi, tagApi } from '@/api'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { Category, Tag, User } from '@/types'

import TaxonomyView from './TaxonomyView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeCategory(overrides: Partial<Category> = {}): Category {
  return {
    id: 1,
    name: '技术',
    slug: 'tech',
    description: null,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    article_count: 3,
    ...overrides,
  }
}

function makeTag(overrides: Partial<Tag> = {}): Tag {
  return { id: 9, name: 'vue', slug: 'vue', article_count: 4, ...overrides }
}

const CATEGORIES: Category[] = [
  makeCategory({ id: 1, name: '技术', slug: 'tech', description: '技术相关', article_count: 3 }),
  makeCategory({ id: 2, name: '生活', slug: 'life', description: null, article_count: 0 }),
]

const TAGS: Tag[] = [makeTag({ id: 9, name: 'vue', article_count: 4 })]

const AUTHOR: User = {
  id: 2,
  username: 'author',
  nickname: null,
  avatar_url: null,
  email: 'author@example.com',
  bio: null,
  role: 'author',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

const ADMIN: User = { ...AUTHOR, id: 1, username: 'admin', nickname: '站长', role: 'admin' }

/** 手动控制 resolve 时机的 promise：用来观察「请求还没回来」这段中间态。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }
let pinia: Pinia

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/admin/taxonomy', component: Blank }],
  })
}

async function mountTaxonomy(options: { asAdmin?: boolean } = {}) {
  const router = makeRouter()
  await router.push('/admin/taxonomy')
  await router.isReady()
  const auth = useAuthStore()
  auth.user = options.asAdmin ? ADMIN : AUTHOR
  const wrapper = mount(TaxonomyView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, auth }
}

/**
 * 按区块取元素：分类与标签两个 section 里的按钮文案大量重名
 * （「删除」「添加」各有一份），不从区块出发就会断言到隔壁那一栏。
 */
function section(wrapper: VueWrapper, index: number): DOMWrapper<Element> {
  const found = wrapper.findAll('section')[index]
  if (!found) throw new Error(`找不到第 ${index} 个区块`)
  return found
}

function categorySection(wrapper: VueWrapper): DOMWrapper<Element> {
  return section(wrapper, 0)
}

function tagSection(wrapper: VueWrapper): DOMWrapper<Element> {
  return section(wrapper, 1)
}

function categoryRows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return categorySection(wrapper).findAll('li')
}

function tagRows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return tagSection(wrapper).findAll('li')
}

function rowButton(row: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = row.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`行内找不到文案为「${label}」的按钮`)
  return found
}

function inputValue(wrapper: VueWrapper, ariaLabel: string): string {
  return (wrapper.get(`input[aria-label="${ariaLabel}"]`).element as HTMLInputElement).value
}

/* 确认框 Teleport 到 body，只能从 document 里取（与 ConfirmDialog.spec.ts 一致） */
function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]')
}

function dialogButton(label: string): HTMLButtonElement | undefined {
  const panel = dialog()?.querySelector('.card')
  return [...(panel?.querySelectorAll('button') ?? [])].find(
    (item) => item.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined
}

function lastToastMessage(): string | undefined {
  return useToast().items.value.at(-1)?.message
}

function lastToastKind(): string | undefined {
  return useToast().items.value.at(-1)?.kind
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().items.value = []
  vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
  vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* ------------------------------------------------------------ 用例 */

describe('TaxonomyView · 加载与列表', () => {
  it('挂载时并发拉分类与标签，分类要带 with_counts（否则「N 篇」全是 0）', async () => {
    const categories = vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
    const tags = vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)

    await mountTaxonomy()

    expect(categories).toHaveBeenCalledWith(true)
    expect(tags).toHaveBeenCalledWith()
  })

  it('渲染分类的名称 / slug / 描述 / 篇数与标签的名称，空描述不占位', async () => {
    const { wrapper } = await mountTaxonomy()

    const rows = categoryRows(wrapper)
    expect(rows).toHaveLength(2)
    expect(rows[0]?.text()).toContain('技术')
    expect(rows[0]?.text()).toContain('/tech')
    expect(rows[0]?.text()).toContain('技术相关')
    expect(rows[0]?.text()).toContain('3 篇')
    // 空描述不该留一行空白（排版会莫名多出一段间距）
    expect(rows[1]?.text()).not.toContain('undefined')
    expect(rows[1]?.text()).toContain('0 篇')

    expect(tagRows(wrapper)).toHaveLength(1)
    expect(tagSection(wrapper).text()).toContain('vue')
    expect(tagSection(wrapper).text()).toContain('4 篇')
  })

  it('区块标题上带各自的总数（一眼看出有多少个分类 / 标签）', async () => {
    const { wrapper } = await mountTaxonomy()

    expect(categorySection(wrapper).text()).toContain('全部分类 2')
    expect(tagSection(wrapper).text()).toContain('全部标签 1')
  })

  it('两个区块各自的空态文案分得开（空分类 ≠ 空标签）', async () => {
    vi.spyOn(categoryApi, 'list').mockResolvedValue([])
    vi.spyOn(tagApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountTaxonomy()

    expect(categorySection(wrapper).text()).toContain('还没有分类。')
    expect(tagSection(wrapper).text()).toContain('还没有标签。')
  })

  it('分类接口失败时给出错误与重试入口，而不是伪装成「还没有分类」', async () => {
    const list = vi
      .spyOn(categoryApi, 'list')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce(CATEGORIES)

    const { wrapper } = await mountTaxonomy()

    // 失败 ≠ 空：以前两者共用同一段文案，站长会以为分类被删光了
    expect(categorySection(wrapper).text()).toContain('服务器开小差了')
    expect(categorySection(wrapper).text()).not.toContain('还没有分类。')

    // 重试入口必须真的能恢复（而不是只画一个按钮）
    await rowButton(categorySection(wrapper), '重试').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(2)
    expect(categorySection(wrapper).text()).toContain('技术')
  })

  it('加载中显示骨架屏，而不是先闪一下「还没有分类」', async () => {
    const pending = deferred<Category[]>()
    vi.spyOn(categoryApi, 'list').mockReturnValue(pending.promise)

    const { wrapper } = await mountTaxonomy()

    // 请求还没回来时不能断言"没有分类"——那是在用"还没加载"冒充"加载完了"
    expect(categorySection(wrapper).text()).not.toContain('还没有分类。')
    expect(categorySection(wrapper).findAll('.animate-pulse').length).toBeGreaterThan(0)

    pending.resolve(CATEGORIES)
    await flushPromises()
    expect(categorySection(wrapper).text()).toContain('技术')
  })
})

describe('TaxonomyView · 新建分类', () => {
  it('名称与描述都 trim，空描述转 null，成功后清空表单并刷新列表', async () => {
    const created = vi
      .spyOn(categoryApi, 'create')
      .mockResolvedValue(makeCategory({ id: 3, name: '读书', slug: 'reading' }))
    const list = vi
      .spyOn(categoryApi, 'list')
      .mockResolvedValueOnce(CATEGORIES)
      .mockResolvedValueOnce([...CATEGORIES, makeCategory({ id: 3, name: '读书' })])

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="分类名称"]').setValue('  读书  ')
    await wrapper.get('input[aria-label="分类描述（选填）"]').setValue('  ')
    await categorySection(wrapper).get('button').trigger('click')
    await flushPromises()

    expect(created).toHaveBeenCalledWith({ name: '读书', description: null, sort_order: 0 })
    expect(lastToastMessage()).toBe('分类已创建')
    // 表单必须清空，否则用户会以为没提交成功、再点一次（第二个就是重名）
    expect(inputValue(wrapper, '分类名称')).toBe('')
    expect(list).toHaveBeenCalledTimes(2)
    expect(categorySection(wrapper).text()).toContain('读书')
  })

  it('填写描述时原样（trim 后）提交', async () => {
    const created = vi.spyOn(categoryApi, 'create').mockResolvedValue(makeCategory({ id: 3 }))

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="分类名称"]').setValue('读书')
    await wrapper.get('input[aria-label="分类描述（选填）"]').setValue('  一年读五十本  ')
    await categorySection(wrapper).get('button').trigger('click')
    await flushPromises()

    expect(created).toHaveBeenCalledWith({
      name: '读书',
      description: '一年读五十本',
      sort_order: 0,
    })
  })

  it('名称为空或只有空格：一个请求都不发，就地提示', async () => {
    const created = vi.spyOn(categoryApi, 'create')

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="分类名称"]').setValue('   ')
    await categorySection(wrapper).get('button').trigger('click')
    await flushPromises()

    // 交给后端只会白跑一趟并拿到 422，前端拦下来更快也更清楚
    expect(created).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写分类名称')
  })

  it('创建失败：提示后端文案、表单内容不丢、按钮解禁可重试', async () => {
    vi.spyOn(categoryApi, 'create').mockRejectedValue(
      new ApiError('分类名已存在', 422, 'validation_error'),
    )

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="分类名称"]').setValue('技术')
    await categorySection(wrapper).get('button').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('分类名已存在')
    expect(lastToastKind()).toBe('error')
    // 辛苦敲的名字不能被清掉
    expect(inputValue(wrapper, '分类名称')).toBe('技术')
    expect(categorySection(wrapper).get('button').attributes('disabled')).toBeUndefined()
  })

  it('创建进行中两个「添加」按钮都禁用（共用同一个 action 计数）', async () => {
    const pending = deferred<Category>()
    vi.spyOn(categoryApi, 'create').mockReturnValue(pending.promise)

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="分类名称"]').setValue('读书')
    await categorySection(wrapper).get('button').trigger('click')
    await flushPromises()

    expect(categorySection(wrapper).get('button').attributes('disabled')).toBeDefined()
    expect(tagSection(wrapper).get('button').attributes('disabled')).toBeDefined()

    pending.resolve(makeCategory({ id: 3 }))
    await flushPromises()
    expect(categorySection(wrapper).get('button').attributes('disabled')).toBeUndefined()
  })
})

describe('TaxonomyView · 重命名分类', () => {
  it('点「编辑」只把那一行换成输入框并回填名称与描述', async () => {
    const update = vi.spyOn(categoryApi, 'update')

    const { wrapper } = await mountTaxonomy()
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '编辑').trigger('click')

    const row = categoryRows(wrapper)[0] as DOMWrapper<Element>
    // 只有这一行进入编辑态，另一行仍是只读的
    expect(categoryRows(wrapper)[1]?.find('input').exists()).toBe(false)
    expect((row.get('input[aria-label="分类名称"]').element as HTMLInputElement).value).toBe('技术')
    expect((row.get('input[aria-label="分类描述"]').element as HTMLInputElement).value).toBe(
      '技术相关',
    )
    expect(update).not.toHaveBeenCalled()
  })

  it('保存走 update(id, payload)，成功后退出编辑态并刷新列表', async () => {
    const updated = vi
      .spyOn(categoryApi, 'update')
      .mockResolvedValue(makeCategory({ id: 1, name: '技术笔记' }))
    const list = vi
      .spyOn(categoryApi, 'list')
      .mockResolvedValueOnce(CATEGORIES)
      .mockResolvedValueOnce([makeCategory({ id: 1, name: '技术笔记' })])

    const { wrapper } = await mountTaxonomy()
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '编辑').trigger('click')
    const row = categoryRows(wrapper)[0] as DOMWrapper<Element>
    await row.get('input[aria-label="分类名称"]').setValue('  技术笔记  ')
    await row.get('input[aria-label="分类描述"]').setValue('')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()

    expect(updated).toHaveBeenCalledWith(1, { name: '技术笔记', description: null })
    expect(lastToastMessage()).toBe('分类已更新')
    expect(list).toHaveBeenCalledTimes(2)
    // 退出编辑态：行里不再有输入框
    expect(categoryRows(wrapper)[0]?.find('input').exists()).toBe(false)
  })

  it('取消编辑：不发请求，回到只读态且原内容不变', async () => {
    const update = vi.spyOn(categoryApi, 'update')

    const { wrapper } = await mountTaxonomy()
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '编辑').trigger('click')
    const row = categoryRows(wrapper)[0] as DOMWrapper<Element>
    await row.get('input[aria-label="分类名称"]').setValue('改了一半')
    await rowButton(row, '取消').trigger('click')

    expect(update).not.toHaveBeenCalled()
    expect(categoryRows(wrapper)[0]?.find('input').exists()).toBe(false)
    expect(categorySection(wrapper).text()).toContain('技术')
    expect(categorySection(wrapper).text()).not.toContain('改了一半')
  })

  it('保存失败：留在编辑态、内容不丢、提示后端文案', async () => {
    vi.spyOn(categoryApi, 'update').mockRejectedValue(
      new ApiError('分类名已存在', 422, 'validation_error'),
    )

    const { wrapper } = await mountTaxonomy()
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '编辑').trigger('click')
    const row = categoryRows(wrapper)[0] as DOMWrapper<Element>
    await row.get('input[aria-label="分类名称"]').setValue('生活')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('分类名已存在')
    // 失败还退出编辑态的话，用户改的内容就白敲了
    expect(categoryRows(wrapper)[0]?.find('input').exists()).toBe(true)
    expect(
      (categoryRows(wrapper)[0]?.get('input[aria-label="分类名称"]').element as HTMLInputElement)
        .value,
    ).toBe('生活')
  })

  it('重命名时清空名称会被就地拦下，不发请求（与新建同一道校验）', async () => {
    const updated = vi.spyOn(categoryApi, 'update').mockResolvedValue(makeCategory({ id: 1 }))

    const { wrapper } = await mountTaxonomy()
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '编辑').trigger('click')
    const row = categoryRows(wrapper)[0] as DOMWrapper<Element>
    await row.get('input[aria-label="分类名称"]').setValue('   ')
    await rowButton(row, '保存').trigger('click')
    await flushPromises()

    // 以前会把空名字原样发给后端靠 422 兜底，用户看到的是服务端报错而不是"不能为空"
    expect(updated).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写分类名称')
    // 仍留在编辑态，用户可以直接补上名字
    expect(row.find('input[aria-label="分类名称"]').exists()).toBe(true)
  })
})

describe('TaxonomyView · 标签', () => {
  it('点「添加」提交 trim 后的名称，成功后清空输入框并刷新列表', async () => {
    const created = vi.spyOn(tagApi, 'create').mockResolvedValue(makeTag({ id: 10, name: 'vite' }))
    const list = vi
      .spyOn(tagApi, 'list')
      .mockResolvedValueOnce(TAGS)
      .mockResolvedValueOnce([...TAGS, makeTag({ id: 10, name: 'vite' })])

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="标签名称"]').setValue('  vite  ')
    await tagSection(wrapper).get('button').trigger('click')
    await flushPromises()

    expect(created).toHaveBeenCalledWith({ name: 'vite' })
    expect(lastToastMessage()).toBe('标签已创建')
    expect(inputValue(wrapper, '标签名称')).toBe('')
    expect(list).toHaveBeenCalledTimes(2)
    expect(tagSection(wrapper).text()).toContain('vite')
  })

  it('在输入框里按回车等同于点「添加」', async () => {
    const created = vi.spyOn(tagApi, 'create').mockResolvedValue(makeTag({ id: 10 }))

    const { wrapper } = await mountTaxonomy()
    const input = wrapper.get('input[aria-label="标签名称"]')
    await input.setValue('vite')
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(created).toHaveBeenCalledWith({ name: 'vite' })
  })

  it('空标签静默忽略：不发请求也不提示（回车空敲一下不该弹错误）', async () => {
    const created = vi.spyOn(tagApi, 'create')

    const { wrapper } = await mountTaxonomy()
    const input = wrapper.get('input[aria-label="标签名称"]')
    await input.setValue('   ')
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(created).not.toHaveBeenCalled()
    expect(useToast().items.value).toHaveLength(0)
  })

  it('创建标签失败：提示后端文案，输入内容不被清掉', async () => {
    vi.spyOn(tagApi, 'create').mockRejectedValue(new ApiError('标签名已存在', 422, 'validation_error'))

    const { wrapper } = await mountTaxonomy()
    await wrapper.get('input[aria-label="标签名称"]').setValue('vue')
    await tagSection(wrapper).get('button').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('标签名已存在')
    expect(inputValue(wrapper, '标签名称')).toBe('vue')
  })
})

describe('TaxonomyView · 清理空标签（仅站长）', () => {
  it('作者视角看不到「清理空标签」，也不会发这个请求', async () => {
    const cleanup = vi.spyOn(tagApi, 'cleanup')

    const { wrapper } = await mountTaxonomy()

    expect(wrapper.findAll('button').some((item) => item.text().trim() === '清理空标签')).toBe(false)
    expect(cleanup).not.toHaveBeenCalled()
  })

  it('站长清理成功：提示用接口返回的 detail，并刷新标签列表', async () => {
    const cleanup = vi
      .spyOn(tagApi, 'cleanup')
      .mockResolvedValue({ detail: '清理了 3 个空标签' })
    const list = vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await tagSection(wrapper).get('button.btn--ghost').trigger('click')
    await flushPromises()

    expect(cleanup).toHaveBeenCalledTimes(1)
    // 文案取接口返回值而不是写死的串：后端才知道清掉了几个
    expect(lastToastMessage()).toBe('清理了 3 个空标签')
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('403 时给出人话：只有站长可以清理标签', async () => {
    vi.spyOn(tagApi, 'cleanup').mockRejectedValue(new ApiError('Forbidden', 403, 'forbidden'))

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await tagSection(wrapper).get('button.btn--ghost').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('只有站长可以清理标签')
    expect(lastToastKind()).toBe('error')
  })

  it('其它失败走兜底提示，不误报成权限问题', async () => {
    vi.spyOn(tagApi, 'cleanup').mockRejectedValue(new ApiError('', 500, 'internal_error'))

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await tagSection(wrapper).get('button.btn--ghost').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('清理失败')
    expect(lastToastKind()).toBe('error')
  })
})

describe('TaxonomyView · 删除', () => {
  it('删除分类的入口只对站长显示（作者点了会拿到 403）', async () => {
    const asAuthor = await mountTaxonomy()
    expect(
      categoryRows(asAuthor.wrapper)[0]?.findAll('button').some((b) => b.text().trim() === '删除'),
    ).toBe(false)
    asAuthor.wrapper.unmount()

    const asAdmin = await mountTaxonomy({ asAdmin: true })
    expect(
      categoryRows(asAdmin.wrapper)[0]?.findAll('button').some((b) => b.text().trim() === '删除'),
    ).toBe(true)
  })

  it('标签的删除入口不限制角色（作者可以删标签）', async () => {
    const { wrapper } = await mountTaxonomy()

    expect(
      tagRows(wrapper)[0]?.findAll('button').some((b) => b.text().trim() === '删除'),
    ).toBe(true)
  })

  it('点删除分类只开确认框，文案里带上名称与文章数', async () => {
    const remove = vi.spyOn(categoryApi, 'remove')

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    // 「其下 3 篇文章会变成未分类，文章本身不会被删除」是这个操作的全部代价
    expect(dialog()?.textContent).toContain('技术')
    expect(dialog()?.textContent).toContain('3 篇文章')
    expect(dialog()?.textContent).toContain('文章本身不会被删除')
  })

  it('取消删除分类：不发请求，确认框关闭', async () => {
    const remove = vi.spyOn(categoryApi, 'remove')

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()
    dialogButton('取消')?.click()
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
  })

  it('确认删除分类：成功后同时刷新分类与标签（文章数变了，标签计数也会变）', async () => {
    const categories = vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
    const tags = vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)
    const remove = vi
      .spyOn(categoryApi, 'remove')
      .mockResolvedValue({ detail: '分类已删除，其下文章变为未分类' })

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()
    dialogButton('删除分类')?.click()
    await flushPromises()

    expect(remove).toHaveBeenCalledWith(1)
    expect(lastToastMessage()).toBe('分类已删除，其下文章变为未分类')
    expect(categories).toHaveBeenCalledTimes(2)
    expect(tags).toHaveBeenCalledTimes(2)
    expect(dialog()).toBeNull()
  })

  it('删除分类失败：403 给出人话，确认框保留、列表不刷新', async () => {
    const categories = vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
    vi.spyOn(categoryApi, 'remove').mockRejectedValue(new ApiError('Forbidden', 403, 'forbidden'))

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()
    dialogButton('删除分类')?.click()
    await flushPromises()

    expect(lastToastMessage()).toBe('只有站长可以删除分类')
    expect(lastToastKind()).toBe('error')
    // 失败刷新列表会让行消失，用户以为删成功了
    expect(categories).toHaveBeenCalledTimes(1)
    expect(categorySection(wrapper).text()).toContain('技术')
    expect(dialog()).not.toBeNull()
  })

  it('确认删除标签：成功后只刷新标签列表，分类不受影响', async () => {
    const categories = vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
    const tags = vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)
    const remove = vi.spyOn(tagApi, 'remove').mockResolvedValue({ detail: '标签已删除' })

    const { wrapper } = await mountTaxonomy()
    await rowButton(tagRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()
    expect(dialog()?.textContent).toContain('只会解除它与文章的关联')

    dialogButton('删除标签')?.click()
    await flushPromises()

    expect(remove).toHaveBeenCalledWith(9)
    expect(lastToastMessage()).toBe('标签已删除')
    expect(tags).toHaveBeenCalledTimes(2)
    expect(categories).toHaveBeenCalledTimes(1)
    expect(dialog()).toBeNull()
  })

  it('删除标签失败：报错、确认框留着可重试、列表不刷新', async () => {
    const tags = vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)
    vi.spyOn(tagApi, 'remove').mockRejectedValue(new ApiError('标签被引用', 409, 'conflict'))

    const { wrapper } = await mountTaxonomy()
    await rowButton(tagRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()
    dialogButton('删除标签')?.click()
    await flushPromises()

    expect(lastToastMessage()).toBe('标签被引用')
    expect(tags).toHaveBeenCalledTimes(1)
    expect(tagSection(wrapper).text()).toContain('vue')
    expect(dialog()).not.toBeNull()
  })

  it('删除分类进行中：确认框按钮禁用并显示「处理中…」', async () => {
    const pending = deferred<{ detail: string }>()
    vi.spyOn(categoryApi, 'remove').mockReturnValue(pending.promise)

    const { wrapper } = await mountTaxonomy({ asAdmin: true })
    await rowButton(categoryRows(wrapper)[0] as DOMWrapper<Element>, '删除').trigger('click')
    await flushPromises()
    dialogButton('删除分类')?.click()
    await flushPromises()

    expect(dialogButton('处理中…')?.disabled).toBe(true)
    expect(dialogButton('取消')?.disabled).toBe(true)

    pending.resolve({ detail: '分类已删除' })
    await flushPromises()
    expect(dialog()).toBeNull()
  })
})
