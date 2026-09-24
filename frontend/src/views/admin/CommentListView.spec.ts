/**
 * CommentListView 测试（后台评论审核队列）。
 *
 * 这一页是「审核状态机」的入口：一行评论要么待审（按钮是「通过」）、要么已通过
 * （按钮是「撤下」），点下去必须先落库、再刷新列表，**绝不允许界面抢跑**。
 * 守的契约：
 *
 * 1. **请求形态**：默认「全部」不带 approved；「待审核」= false、「已通过」= true；
 *    固定 pageSize=20，切筛选要把页码拉回第 1 页；
 * 2. **审核状态流转**：通过 → `moderate(id, true)` + 「已通过」+ 刷新（行上变「撤下」）；
 *    撤下 → `moderate(id, false)` + 「已撤下」+ 刷新（行上变「通过」）；
 * 3. **失败与空队列分得开**：接口挂了要显示后端文案，不能报成「没有评论」；
 *    首屏加载中给骨架屏，刷新时保留旧列表（不回退到首次加载态）；
 * 4. **写操作失败必须让界面停在原样**：审核失败不刷新列表、行上仍是原状态、
 *    按钮解禁可重试；删除失败保留确认框且行不消失；
 * 5. **删除先确认再发请求**：弹框时一个请求都不发，取消什么都不做；
 * 6. **行级忙碌**：审核进行中只禁用那一行的按钮，别的行照常可点
 *    （useAction 刻意没有单飞锁，快速连审两条是合法的并发）。
 *
 * 回归守卫：模板里的「查看文章 #N」「回复 #N」曾经写成单花括号 `#{item.article_id}`，
 * Vue 不插值，行上渲染的是字面量 —— 两处都由用例守住修好后的行为。
 *
 * 另外钉住两条**现状问题**（本轮只加测试、不改产品代码）：
 * - `?approved=true` 进页面会落到「全部」而不是「已通过」（模板只认 `approved=false`）；
 * - 删掉当前页最后一条评论后不会回退页码：列表显示「没有评论」、分页器整个消失，
 *   但 total 明明还有 20 条 —— 用户没有任何入口回到第 1 页。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, commentApi } from '@/api'
import { useToast } from '@/composables/useToast'
import type { Comment, Page } from '@/types'

import CommentListView from './CommentListView.vue'

const PAGE_SIZE = 20

/* ------------------------------------------------------------ 测试数据 */

/** 相对时间按「现在」算，所以取一个稳定的小时数而不是写死日期。 */
const THREE_HOURS_AGO = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString()

function makeComment(overrides: Partial<Comment> = {}): Comment {
  return {
    id: 1,
    article_id: 7,
    parent_id: null,
    author_name: '访客甲',
    author_site: null,
    content: '第一条评论',
    is_admin_reply: false,
    is_approved: false,
    created_at: THREE_HOURS_AGO,
    replies: [],
    ...overrides,
  }
}

function makePage(items: Comment[], total = items.length, page = 1): Page<Comment> {
  return {
    items,
    total,
    page,
    page_size: PAGE_SIZE,
    pages: Math.max(1, Math.ceil(total / PAGE_SIZE)),
  }
}

const PENDING = makeComment({ id: 1, author_name: '访客甲', content: '待审的评论' })
const APPROVED = makeComment({ id: 2, author_name: '访客乙', content: '已通过的评论', is_approved: true })
const ADMIN_REPLY = makeComment({
  id: 3,
  author_name: '博主',
  content: '站长的回复',
  is_admin_reply: true,
  is_approved: true,
  parent_id: 5,
})

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
    routes: [
      { path: '/admin/comments', component: Blank },
      { path: '/article/:slug', component: Blank },
    ],
  })
}

async function mountComments(initial = '/admin/comments') {
  const router = makeRouter()
  await router.push(initial)
  await router.isReady()
  const wrapper = mount(CommentListView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
}

/**
 * 列表接口按**请求里的 page** 回包：真实后端就是这么做的，
 * 这样分页器读到的页码与发出的请求才一致，页码相关的断言才有意义。
 */
function stubList(items: Comment[], total = items.length) {
  return vi
    .spyOn(commentApi, 'listModeration')
    .mockImplementation(async (params = {}) => makePage(items, total, params.page ?? 1))
}

function rows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('ul li')
}

function rowByAuthor(wrapper: VueWrapper, author: string): DOMWrapper<Element> {
  const found = rows(wrapper).find((row) => row.text().includes(author))
  if (!found) throw new Error(`找不到作者为「${author}」的评论行`)
  return found
}

function rowButton(row: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = row.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`行内找不到文案为「${label}」的按钮`)
  return found
}

/** 行里的状态徽标（「已通过」/「待审核」）。 */
function badgeText(row: DOMWrapper<Element>): string | undefined {
  return row
    .findAll('span')
    .map((item) => item.text().trim())
    .find((text) => text === '已通过' || text === '待审核')
}

/** 按文案取按钮：同一屏有筛选、行级动作、分页三组按钮。 */
function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

/** 当前选中的筛选项：模板用 font-medium 标记激活态（唯一可观测的信号）。 */
function activeFilter(wrapper: VueWrapper): string | undefined {
  return wrapper
    .findAll('button')
    .find((item) => item.classes().includes('font-medium'))
    ?.text()
    .trim()
}

async function switchFilter(wrapper: VueWrapper, label: string): Promise<void> {
  await button(wrapper, label).trigger('click')
  await flushPromises()
}

/** 翻页按钮是图标按钮（没有文字），只能按 aria-label 取。 */
function nextPageButton(wrapper: VueWrapper) {
  return wrapper.get('button[aria-label="下一页"]')
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

async function confirmDialog(label: string): Promise<void> {
  dialogButton(label)?.click()
  await flushPromises()
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
  stubList([PENDING])
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* ------------------------------------------------------------ 用例 */

describe('CommentListView · 加载 / 失败 / 空队列', () => {
  it('默认停在「全部」：请求不带 approved，并按行渲染作者、状态、正文与文章链接', async () => {
    const list = stubList([PENDING, ADMIN_REPLY], 2)

    const { wrapper } = await mountComments()

    expect(list).toHaveBeenCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: undefined })
    expect(activeFilter(wrapper)).toBe('全部')
    expect(wrapper.text()).toContain('共 2 条')
    expect(rows(wrapper)).toHaveLength(2)

    const pending = rowByAuthor(wrapper, '访客甲')
    expect(pending.text()).toContain('待审的评论')
    expect(badgeText(pending)).toBe('待审核')
    expect(pending.get('a').attributes('href')).toBe('/article/7')
    // 这条曾经记录的是缺陷：模板写成了单花括号 `查看文章 #{item.article_id}`，
    // Vue 不插值，行上显示的是字面量。现在守的是修复后的契约。
    expect(pending.text()).toContain('查看文章 #7')
    // 相对时间挂在 title 上是绝对时间，正文里是「3 小时前」
    expect(pending.text()).toContain('3 小时前')

    const reply = rowByAuthor(wrapper, '博主')
    // 站长回复要能一眼看出来
    expect(reply.findAll('span').some((item) => item.text().trim() === '站长')).toBe(true)
    // 同上：`回复 #{item.parent_id}` 曾渲染成字面量，现在应当显示真实楼层号
    expect(reply.text()).toContain('回复 #5')
  })

  it('?approved=false 进页面直接落到「待审核」队列，请求带 approved=false', async () => {
    const list = stubList([PENDING], 1)

    const { wrapper } = await mountComments('/admin/comments?approved=false')

    expect(activeFilter(wrapper)).toBe('待审核')
    expect(list).toHaveBeenCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: false })
  })

  it('待审队列空了：文案是「没有待审核的评论」，而不是「没有评论」', async () => {
    stubList([], 0)

    const { wrapper } = await mountComments('/admin/comments?approved=false')

    expect(wrapper.text()).toContain('没有待审核的评论')
    expect(wrapper.text()).toContain('都处理完了，很清爽。')
    expect(wrapper.text()).not.toContain('没有评论')
    // total 为 0 时连分页器都不渲染
    expect(wrapper.find('nav').exists()).toBe(false)
  })

  it('「全部」为空时文案是「没有评论」（两种空态不能混用）', async () => {
    stubList([], 0)

    const { wrapper } = await mountComments()

    expect(wrapper.text()).toContain('没有评论')
    expect(wrapper.text()).not.toContain('没有待审核的评论')
  })

  it('首屏加载中显示 5 条骨架屏，而不是先闪一下「没有评论」', async () => {
    const pending = deferred<Page<Comment>>()
    vi.spyOn(commentApi, 'listModeration').mockReturnValue(pending.promise)

    const { wrapper } = await mountComments()

    expect(wrapper.findAll('.skeleton')).toHaveLength(5)
    expect(wrapper.text()).not.toContain('没有评论')

    pending.resolve(makePage([PENDING]))
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(rows(wrapper)).toHaveLength(1)
  })

  it('加载失败：显示后端文案，绝不能伪装成「没有评论」的空态', async () => {
    vi.spyOn(commentApi, 'listModeration').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountComments()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    // 失败 ≠ 空：以前两者共用同一个空态分支，站长会以为评论被清空了
    expect(wrapper.text()).not.toContain('没有评论')
    expect(rows(wrapper)).toHaveLength(0)
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('加载失败后切筛选能重新拉取并恢复（这一页没有独立的重试按钮）', async () => {
    const list = vi
      .spyOn(commentApi, 'listModeration')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockImplementation(async (params = {}) => makePage([APPROVED], 1, params.page ?? 1))

    const { wrapper } = await mountComments()
    expect(wrapper.text()).toContain('服务器开小差了')

    await switchFilter(wrapper, '已通过')

    expect(list).toHaveBeenCalledTimes(2)
    expect(list).toHaveBeenLastCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: true })
    expect(wrapper.text()).not.toContain('服务器开小差了')
    expect(rows(wrapper)).toHaveLength(1)
  })
})

describe('CommentListView · 筛选与分页', () => {
  it('切「待审核」/「已通过」/「全部」分别带 false / true / 不带参数', async () => {
    const list = stubList([PENDING], 1)

    const { wrapper } = await mountComments()
    await switchFilter(wrapper, '待审核')
    await switchFilter(wrapper, '已通过')
    await switchFilter(wrapper, '全部')

    expect(list).toHaveBeenNthCalledWith(2, { page: 1, pageSize: PAGE_SIZE, approved: false })
    expect(list).toHaveBeenNthCalledWith(3, { page: 1, pageSize: PAGE_SIZE, approved: true })
    expect(list).toHaveBeenNthCalledWith(4, { page: 1, pageSize: PAGE_SIZE, approved: undefined })
  })

  it('翻到第 2 页后再切筛选：页码回到第 1 页（否则会停在一个不存在的结果页上）', async () => {
    const list = stubList([PENDING], 25)

    const { wrapper } = await mountComments()
    await nextPageButton(wrapper).trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith({ page: 2, pageSize: PAGE_SIZE, approved: undefined })

    await switchFilter(wrapper, '待审核')
    expect(list).toHaveBeenLastCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: false })
  })

  it('分页器按接口返回的 total / page_size 渲染，翻页把页码带进请求', async () => {
    const list = vi
      .spyOn(commentApi, 'listModeration')
      .mockImplementation(async (params = {}) => {
        const page = params.page ?? 1
        return makePage([makeComment({ id: page, author_name: `第 ${page} 页` })], 25, page)
      })

    const { wrapper } = await mountComments()
    expect(wrapper.text()).toContain('1–20 / 共 25 条')

    await nextPageButton(wrapper).trigger('click')
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith({ page: 2, pageSize: PAGE_SIZE, approved: undefined })
    expect(wrapper.text()).toContain('21–25 / 共 25 条')
    expect(rowByAuthor(wrapper, '第 2 页')).toBeTruthy()
  })

  it('边界：?approved=true 不会被当成「已通过」筛选（模板只认 approved=false）', async () => {
    const list = stubList([PENDING], 1)

    // 现状：仪表盘只生成 approved=false 的链接，所以这里没接上也算「没坏」，
    // 但语义上 ?approved=true 与 ?approved=false 不对称，深链分享会落到「全部」。
    const { wrapper } = await mountComments('/admin/comments?approved=true')

    expect(activeFilter(wrapper)).toBe('全部')
    expect(list).toHaveBeenCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: undefined })
  })
})

describe('CommentListView · 审核状态流转', () => {
  it('待审 → 通过：moderate(id, true) + 「已通过」+ 刷新列表后行上变「撤下」', async () => {
    const moderate = vi
      .spyOn(commentApi, 'moderate')
      .mockResolvedValue(makeComment({ ...PENDING, is_approved: true }))
    const list = vi
      .spyOn(commentApi, 'listModeration')
      .mockResolvedValueOnce(makePage([PENDING], 1))
      .mockResolvedValueOnce(makePage([{ ...PENDING, is_approved: true }], 1))

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    expect(moderate).toHaveBeenCalledWith(1, true)
    expect(lastToastMessage()).toBe('已通过')
    expect(lastToastKind()).toBe('success')
    expect(list).toHaveBeenCalledTimes(2)

    const row = rowByAuthor(wrapper, '访客甲')
    expect(badgeText(row)).toBe('已通过')
    expect(rowButton(row, '撤下').exists()).toBe(true)
    expect(row.findAll('button').some((item) => item.text().trim() === '通过')).toBe(false)
  })

  it('已通过 → 撤下：moderate(id, false) + 「已撤下」+ 刷新后行上变回「通过」', async () => {
    const moderate = vi
      .spyOn(commentApi, 'moderate')
      .mockResolvedValue(makeComment({ ...APPROVED, is_approved: false }))
    const list = vi
      .spyOn(commentApi, 'listModeration')
      .mockResolvedValueOnce(makePage([APPROVED], 1))
      .mockResolvedValueOnce(makePage([{ ...APPROVED, is_approved: false }], 1))

    const { wrapper } = await mountComments('/admin/comments?approved=true')
    await rowButton(rowByAuthor(wrapper, '访客乙'), '撤下').trigger('click')
    await flushPromises()

    expect(moderate).toHaveBeenCalledWith(2, false)
    expect(lastToastMessage()).toBe('已撤下')
    expect(list).toHaveBeenCalledTimes(2)

    const row = rowByAuthor(wrapper, '访客乙')
    expect(badgeText(row)).toBe('待审核')
    expect(rowButton(row, '通过').exists()).toBe(true)
  })

  it('审核失败：提示后端文案、不刷新列表、行上仍是原状态与按钮（不允许界面抢跑）', async () => {
    // 「另一台设备上已经删掉这条评论」是完全可能的：后端返回 404
    vi.spyOn(commentApi, 'moderate').mockRejectedValue(
      new ApiError('评论不存在或已被删除', 404, 'not_found'),
    )
    const list = stubList([PENDING], 1)

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('评论不存在或已被删除')
    expect(lastToastKind()).toBe('error')
    // 失败还刷新列表的话，重排后的行会让人以为「通过」成功了
    expect(list).toHaveBeenCalledTimes(1)

    const row = rowByAuthor(wrapper, '访客甲')
    expect(badgeText(row)).toBe('待审核')
    const retry = rowButton(row, '通过')
    expect(retry.exists()).toBe(true)
    // 忙碌标记必须清掉，否则这一行再也点不动
    expect(retry.attributes('disabled')).toBeUndefined()
  })

  it('后端没给可读消息时用兜底文案「操作失败」', async () => {
    vi.spyOn(commentApi, 'moderate').mockRejectedValue(new ApiError('', 500, 'internal_error'))

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('操作失败')
    expect(lastToastKind()).toBe('error')
  })

  it('审核进行中只禁用那一行的按钮，别的行照常可点（行级忙碌，不是全局锁）', async () => {
    const pending = deferred<Comment>()
    vi.spyOn(commentApi, 'moderate').mockReturnValue(pending.promise)
    stubList([PENDING, makeComment({ id: 9, author_name: '访客丙', content: '另一条' })], 2)

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    expect(rowButton(rowByAuthor(wrapper, '访客甲'), '通过').attributes('disabled')).toBeDefined()
    expect(rowButton(rowByAuthor(wrapper, '访客丙'), '通过').attributes('disabled')).toBeUndefined()

    pending.resolve(makeComment({ ...PENDING, is_approved: true }))
    await flushPromises()
    // 刷新回来后这一行重新可点（stub 回的仍是待审那条，所以按钮还是「通过」）
    expect(rowButton(rowByAuthor(wrapper, '访客甲'), '通过').attributes('disabled')).toBeUndefined()
  })

  it('审核成功后的刷新期间保留旧列表，不闪骨架屏（ready 之后不回退到首次加载态）', async () => {
    vi.spyOn(commentApi, 'moderate').mockResolvedValue(makeComment({ ...PENDING, is_approved: true }))
    const refresh = deferred<Page<Comment>>()
    vi.spyOn(commentApi, 'listModeration')
      .mockResolvedValueOnce(makePage([PENDING], 1))
      .mockReturnValueOnce(refresh.promise)

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '通过').trigger('click')
    await flushPromises()

    // 刷新中：旧内容还在，骨架屏不该出现（否则整页闪一下）
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(rowByAuthor(wrapper, '访客甲')).toBeTruthy()

    refresh.resolve(makePage([{ ...PENDING, is_approved: true }], 1))
    await flushPromises()
    expect(badgeText(rowByAuthor(wrapper, '访客甲'))).toBe('已通过')
  })
})

describe('CommentListView · 删除', () => {
  it('点「删除」只打开确认框，一个请求都不发', async () => {
    const remove = vi.spyOn(commentApi, 'remove')

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    // 文案要说清连带代价：回复会跟着一起没
    expect(dialog()?.textContent).toContain('删除后无法恢复')
    expect(dialog()?.textContent).toContain('回复也会一起被删除')
  })

  it('取消删除：不发请求、行还在、确认框关掉', async () => {
    const remove = vi.spyOn(commentApi, 'remove')

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('取消')

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(rows(wrapper)).toHaveLength(1)
  })

  it('确认删除：remove(id) + 「评论已删除」+ 刷新列表（被删的行消失）', async () => {
    const remove = vi.spyOn(commentApi, 'remove').mockResolvedValue(undefined)
    const list = vi
      .spyOn(commentApi, 'listModeration')
      .mockResolvedValueOnce(makePage([PENDING, APPROVED], 2))
      .mockResolvedValueOnce(makePage([APPROVED], 1))

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除')

    expect(remove).toHaveBeenCalledWith(1)
    expect(lastToastMessage()).toBe('评论已删除')
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('访客甲')
    expect(dialog()).toBeNull()
  })

  it('删除失败：报错、行不消失、确认框留着可重试（不允许「界面已删、服务端还在」）', async () => {
    vi.spyOn(commentApi, 'remove').mockRejectedValue(new ApiError('评论不存在或已被删除', 404, 'not_found'))
    const list = stubList([PENDING], 1)

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除')

    expect(lastToastMessage()).toBe('评论不存在或已被删除')
    expect(lastToastKind()).toBe('error')
    // 失败刷新列表会让行消失，用户以为删成功了
    expect(list).toHaveBeenCalledTimes(1)
    expect(rowByAuthor(wrapper, '访客甲')).toBeTruthy()
    expect(dialog()).not.toBeNull()
  })

  it('删除进行中：确认框两个按钮都禁用并显示「处理中…」（防重复提交）', async () => {
    const pending = deferred<void>()
    vi.spyOn(commentApi, 'remove').mockReturnValue(pending.promise)

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(dialogButton('处理中…')?.disabled).toBe(true)
    expect(dialogButton('取消')?.disabled).toBe(true)

    pending.resolve()
    await flushPromises()
    expect(dialog()).toBeNull()
  })

  it('删掉当前页最后一条后回退一页，不会停在空页上', async () => {
    // 第 2 页是最后一页且只有 1 条（total=21）。删掉它之后如果页码仍是 2，
    // 后端这一页已经空了：列表走空态分支，连 Pagination 都不渲染 ——
    // 用户没有任何入口回到第 1 页，只能靠切筛选。
    // 现在删除后会把页码收敛回上一页。
    let deleted = false
    vi.spyOn(commentApi, 'remove').mockImplementation(async () => {
      deleted = true
    })
    const list = vi.spyOn(commentApi, 'listModeration').mockImplementation(async (params = {}) => {
      const page = params.page ?? 1
      const total = deleted ? 20 : 21
      return page === 2 && deleted ? makePage([], total, 2) : makePage([PENDING], total, page)
    })

    const { wrapper } = await mountComments()
    await nextPageButton(wrapper).trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith({ page: 2, pageSize: PAGE_SIZE, approved: undefined })

    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除')

    expect(lastToastMessage()).toBe('评论已删除')
    // 关键：请求落回第 1 页，而不是继续问一个已经空掉的第 2 页
    expect(list).toHaveBeenLastCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: undefined })
  })

  it('第 1 页删光最后一条时不需要回退（已经在第 1 页）', async () => {
    vi.spyOn(commentApi, 'remove').mockResolvedValue(undefined)
    // 第一次请求给出 1 条（否则根本没有行可删），删除后这一页才变空
    const list = vi
      .spyOn(commentApi, 'listModeration')
      .mockResolvedValueOnce(makePage([PENDING], 1, 1))
      .mockResolvedValue(makePage([], 0, 1))

    const { wrapper } = await mountComments()
    await rowButton(rowByAuthor(wrapper, '访客甲'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除')

    expect(list).toHaveBeenLastCalledWith({ page: 1, pageSize: PAGE_SIZE, approved: undefined })
    expect(wrapper.text()).toContain('没有评论')
  })

})
