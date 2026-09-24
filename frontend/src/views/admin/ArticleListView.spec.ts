/**
 * ArticleListView 测试（后台文章管理）。
 *
 * 后台列表和前台首页长得像，但契约不一样：这里的每一行都挂着「发布 / 转草稿 /
 * 删除」三个真会改数据的动作，所以守的是下面这些点：
 *
 * 1. **失败与空态分得开**：接口挂了要显示后端文案，不能报成「没有匹配的文章」；
 * 2. **写操作失败必须让界面停在原样**：切换状态失败不重新拉列表、删除失败保留确认框
 *    且不刷新 —— 绝不允许出现「界面已经删掉、服务端还在」这种不一致；
 * 3. **删除先确认再发请求**：确认框弹出时一个请求都不该发，取消要什么都不做；
 * 4. **请求参数正确**：列表固定带 sort=updated，行级 update / remove 带对 id；
 * 5. **URL query 只被读一次**：`?status=` 用来初始化筛选，之后翻页 / 切筛选 / 搜索
 *    都只改内存状态、**不回写地址栏**（详见下面「URL 与列表状态」一节，那是现状缺陷，
 *    这里把它钉成断言，避免以后误以为已经同步）；
 * 6. **权限**：作者视角没有「作者」列，站长视角才有，且昵称 → 用户名 → 「—」降级。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法），
 * 与 ArticleEditView.spec.ts / HomeView.spec.ts 的写法保持一致。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, articleApi, http } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { ArticleDetail, ArticleSummary, Page, User } from '@/types'

import ArticleListView from './ArticleListView.vue'

const PAGE_SIZE = 15

/* ------------------------------------------------------------ 测试数据 */

function makeArticle(overrides: Partial<ArticleSummary> = {}): ArticleSummary {
  return {
    id: 1,
    title: '一篇文章',
    slug: 'hello',
    summary: '摘要',
    cover_image: null,
    cover_variants: [],
    status: 'published',
    is_top: false,
    allow_comment: true,
    view_count: 1,
    like_count: 0,
    reading_time: 1,
    published_at: '2026-01-01T00:00:00Z',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    author: null,
    category: null,
    series: null,
    series_order: 0,
    tags: [],
    comment_count: 0,
    is_scheduled: false,
    ...overrides,
  }
}

/** 写接口（update）返回的是详情：摘要 + 正文与相邻文章。 */
function makeDetail(overrides: Partial<ArticleSummary> = {}): ArticleDetail {
  return {
    ...makeArticle(overrides),
    content_md: '正文',
    prev: null,
    next: null,
    series_prev: null,
    series_next: null,
  }
}

function makePage(items: ArticleSummary[], total = items.length, page = 1): Page<ArticleSummary> {
  return {
    items,
    total,
    page,
    page_size: PAGE_SIZE,
    pages: Math.max(1, Math.ceil(total / PAGE_SIZE)),
  }
}

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

/** 手动控制 resolve / reject 时机的 promise：用来观察「请求还没回来」这段中间态。 */
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
/** 原始适配器：删除用例会临时替换它，让真实 axios 产生「204 + 空响应体」。 */
const originalAdapter = http.defaults.adapter

/**
 * 用真实 axios 适配器模拟后端的 204 空响应（`DELETE /articles/{id}` 就是 204），
 * 并记录实际发出的请求。
 *
 * 为什么删除用例不走 `vi.spyOn(articleApi, 'remove')`：那样会把「接口到底 resolve
 * 成什么」这一层糊掉 —— 而这一层曾经真的出过事：useConfirmDelete 用 `=== undefined`
 * 当失败哨兵，恰好撞上 `api.delete` 默认泛型 `void` 的成功返回值（详见「删除」一节的
 * 回归用例）。传 gate 可以让请求停在半路，用来观察「请求还没回来」这段中间态。
 */
function stubNoContentAdapter(gate?: Promise<unknown>): string[] {
  const calls: string[] = []
  http.defaults.adapter = (async (config: InternalAxiosRequestConfig) => {
    calls.push(`${(config.method ?? 'get').toUpperCase()} ${config.url ?? ''}`)
    if (gate) await gate
    return { data: '', status: 204, statusText: 'No Content', headers: new AxiosHeaders(), config }
  }) as unknown as AxiosAdapter
  return calls
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/articles', component: Blank },
      { path: '/admin/articles/new', component: Blank },
      { path: '/admin/articles/:id/edit', component: Blank },
      { path: '/article/:slug', component: Blank },
    ],
  })
}

async function mountList(initial = '/admin/articles', options: { asAdmin?: boolean } = {}) {
  const router = makeRouter()
  await router.push(initial)
  await router.isReady()
  // 组件里读的 auth store 必须是同一个 pinia 实例，否则设了角色也读不到
  const auth = useAuthStore()
  auth.user = options.asAdmin ? ADMIN : AUTHOR
  const wrapper = mount(ArticleListView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, auth }
}

/**
 * 列表接口按**请求里的 page** 回包：真实后端就是这么做的，
 * 这样分页器读到的页码与发出的请求才一致，页码相关的断言才有意义。
 */
function stubList(items: ArticleSummary[], total = items.length) {
  return vi
    .spyOn(articleApi, 'listManaged')
    .mockImplementation(async (query = {}) => makePage(items, total, query.page ?? 1))
}

/** 桌面表格的行；移动端卡片是同一份数据的另一份渲染，断言一律只看表格。 */
function rows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('tbody tr')
}

function rowTitles(wrapper: VueWrapper): string[] {
  return rows(wrapper).map((row) => row.get('a').text())
}

function rowByTitle(wrapper: VueWrapper, title: string): DOMWrapper<Element> {
  const found = rows(wrapper).find((row) => row.get('a').text() === title)
  if (!found) throw new Error(`找不到标题为「${title}」的行`)
  return found
}

function rowButton(row: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = row.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`行内找不到文案为「${label}」的按钮`)
  return found
}

function columnHeaders(wrapper: VueWrapper): string[] {
  return wrapper.findAll('thead th').map((item) => item.text())
}

/** 按文案取按钮：模板里同一屏有十几个 button，按 class 取会随样式改动而失效。 */
function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

function statusSelect(wrapper: VueWrapper) {
  return wrapper.get('select[aria-label="按状态筛选"]')
}

function statusValue(wrapper: VueWrapper): string {
  return (statusSelect(wrapper).element as HTMLSelectElement).value
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
  stubList([makeArticle()])
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
  http.defaults.adapter = originalAdapter
})

/* ------------------------------------------------------------ 用例 */

describe('ArticleListView · 加载 / 失败 / 空态', () => {
  it('首屏加载中显示骨架屏，而不是「没有匹配的文章」', async () => {
    const pending = deferred<Page<ArticleSummary>>()
    vi.spyOn(articleApi, 'listManaged').mockReturnValue(pending.promise)

    const { wrapper } = await mountList()

    expect(wrapper.findAll('.skeleton')).toHaveLength(6)
    // 空态文案在加载期间出现，作者会以为自己的文章全没了
    expect(wrapper.text()).not.toContain('没有匹配的文章')

    pending.resolve(makePage([makeArticle({ title: '加载出来的文章' })]))
    await flushPromises()

    expect(rowTitles(wrapper)).toEqual(['加载出来的文章'])
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('加载失败显示后端文案，且不会错报成「没有匹配的文章」', async () => {
    vi.spyOn(articleApi, 'listManaged').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountList()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    // 两种状态的出口完全不同：这里不能出现「换个筛选条件，或者现在就写一篇」
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(rows(wrapper)).toHaveLength(0)
  })

  it('非 ApiError 的异常也有兜底文案（不会渲染成一条空白）', async () => {
    vi.spyOn(articleApi, 'listManaged').mockRejectedValue(new TypeError('boom'))

    const { wrapper } = await mountList()

    expect(wrapper.text()).toContain('加载失败，请稍后重试')
  })

  it('加载失败后改状态筛选能重新拉取并恢复（当前没有独立的「重试」按钮）', async () => {
    const list = vi
      .spyOn(articleApi, 'listManaged')
      .mockRejectedValueOnce(new ApiError('服务器开小差了', 500, 'internal_error'))
      .mockResolvedValueOnce(makePage([makeArticle({ title: '恢复出来的文章' })]))

    const { wrapper } = await mountList()
    expect(wrapper.text()).toContain('服务器开小差了')

    await statusSelect(wrapper).setValue('published')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(rowTitles(wrapper)).toEqual(['恢复出来的文章'])
    expect(wrapper.text()).not.toContain('服务器开小差了')
  })

  it('手动刷新列表时保留旧表格，不闪骨架屏（ready 之后不回退到首次加载态）', async () => {
    const pending = deferred<Page<ArticleSummary>>()
    vi.spyOn(articleApi, 'listManaged')
      .mockResolvedValueOnce(makePage([makeArticle({ title: '旧数据' })]))
      .mockReturnValueOnce(pending.promise)

    const { wrapper } = await mountList()
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    // 刷新期间表格不能消失：否则每次搜索都像整页重载
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(rowTitles(wrapper)).toEqual(['旧数据'])

    pending.resolve(makePage([makeArticle({ title: '新数据' })]))
    await flushPromises()
    expect(rowTitles(wrapper)).toEqual(['新数据'])
  })

  it('一篇都没有：显示空态与「写文章」出口，且不渲染分页器', async () => {
    stubList([], 0)

    const { wrapper } = await mountList()

    expect(wrapper.text()).toContain('没有匹配的文章')
    // 「第 1 页 / 共 0 条」的分页器毫无意义
    expect(wrapper.findComponent(Pagination).exists()).toBe(false)
  })

  it('空态里的「写文章」按钮跳到新建页', async () => {
    stubList([], 0)

    const { wrapper, router } = await mountList()
    await button(wrapper, '写文章').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/admin/articles/new')
  })
})

describe('ArticleListView · 列表渲染与权限', () => {
  it('固定用 sort=updated 请求后台列表，空筛选不塞进参数里', async () => {
    const list = stubList([makeArticle()])

    await mountList()

    expect(list).toHaveBeenCalledWith({
      page: 1,
      page_size: PAGE_SIZE,
      status: undefined,
      keyword: undefined,
      sort: 'updated',
    })
  })

  it('状态列按 status / is_scheduled 出文案（排期文章不能写成「已发布」）', async () => {
    stubList([
      makeArticle({ id: 1, title: '甲', status: 'published' }),
      makeArticle({ id: 2, title: '乙', status: 'draft' }),
      makeArticle({ id: 3, title: '丙', status: 'published', is_scheduled: true }),
    ])

    const { wrapper } = await mountList()

    // 排期文章 status 也是 published，只看 status 会让作者以为访客已经能看到
    expect(rows(wrapper).map((row) => row.findAll('td')[1]?.text())).toEqual([
      '已发布',
      '草稿',
      '定时发布',
    ])
  })

  it('标题下方亮出置顶 / 分类 / 最多 3 个标签（多余的会挤爆这一格）', async () => {
    stubList([
      makeArticle({
        is_top: true,
        category: { id: 1, name: '技术', slug: 'tech' },
        tags: [
          { id: 1, name: 'vue', slug: 'vue' },
          { id: 2, name: 'ts', slug: 'ts' },
          { id: 3, name: 'vite', slug: 'vite' },
          { id: 4, name: 'pinia', slug: 'pinia' },
        ],
      }),
    ])

    const { wrapper } = await mountList()
    const row = rowByTitle(wrapper, '一篇文章')

    expect(row.text()).toContain('置顶')
    expect(row.text()).toContain('技术')
    expect(row.text()).toContain('#vue')
    expect(row.text()).not.toContain('#pinia')
  })

  it('作者视角没有「作者」列；站长视角有，并按昵称 → 用户名 → 「—」降级', async () => {
    stubList([
      makeArticle({
        id: 1,
        title: '甲',
        author: { id: 2, username: 'author', nickname: '小作者', avatar_url: null },
      }),
      makeArticle({
        id: 2,
        title: '乙',
        author: { id: 3, username: 'nameless', nickname: null, avatar_url: null },
      }),
      makeArticle({ id: 3, title: '丙', author: null }),
    ])

    const asAuthor = await mountList()
    // 作者只看得到自己的文章，作者列是纯噪声
    expect(columnHeaders(asAuthor.wrapper)).not.toContain('作者')
    asAuthor.wrapper.unmount()

    const asAdmin = await mountList('/admin/articles', { asAdmin: true })
    expect(columnHeaders(asAdmin.wrapper)).toContain('作者')
    expect(rows(asAdmin.wrapper).map((row) => row.findAll('td')[4]?.text())).toEqual([
      '小作者',
      'nameless',
      '—',
    ])
  })

  it('分页器按接口返回的 total / page_size 渲染，翻页把页码带进请求', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper } = await mountList()
    const pagination = wrapper.getComponent(Pagination)

    expect(pagination.props('total')).toBe(40)
    expect(pagination.props('pageSize')).toBe(PAGE_SIZE)

    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
    expect(wrapper.getComponent(Pagination).props('page')).toBe(2)
  })

  it('在第 2 页搜索时页码回到第 1 页（否则会停在一个不存在的结果页上）', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper } = await mountList()
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

    await wrapper.get('input[aria-label="搜索文章"]').setValue('vue')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: 'vue' }))
  })
})

describe('ArticleListView · URL 与列表状态', () => {
  it('进页面时读一次 ?status=，下拉框与请求参数都跟着它走', async () => {
    const list = stubList([makeArticle({ status: 'draft' })])

    const { wrapper } = await mountList('/admin/articles?status=draft')

    expect(list).toHaveBeenCalledWith(expect.objectContaining({ status: 'draft' }))
    expect(statusValue(wrapper)).toBe('draft')
  })

  it('URL 上没有 status 时停在「全部状态」，请求里不带状态', async () => {
    const list = stubList([makeArticle()])

    const { wrapper } = await mountList()

    expect(statusValue(wrapper)).toBe('')
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ status: undefined }))
  })

  it('用同一个 URL 重新挂载（等价于刷新）时筛选仍然生效，且回到第 1 页', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper } = await mountList('/admin/articles?status=draft')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

    wrapper.unmount()
    await mountList('/admin/articles?status=draft')

    // status 是进入时从 query 读的，所以刷新后还在；page 只活在内存里，刷新即丢
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, status: 'draft' }))
  })

  it('切状态筛选：请求用新状态 + 回到第 1 页 + 地址栏同步（刷新/分享都对得上）', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper, router } = await mountList('/admin/articles?status=draft')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()

    await statusSelect(wrapper).setValue('published')
    await flushPromises()

    // @change 里读到的是更新后的值（v-model 与 @change 的执行顺序不能反）
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, status: 'published' }))
    // URL 是筛选状态的唯一来源：此刻按 F5 或把链接发出去，看到的与屏幕上一致
    expect(router.currentRoute.value.query).toEqual({ status: 'published' })
  })

  it('翻页与搜索都会写回地址栏（分享出去的链接就是当前视图）', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper, router } = await mountList('/admin/articles')

    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.page).toBe('2')

    await wrapper.get('input[aria-label="搜索文章"]').setValue('vue 响应式')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    // 换关键词要回到第 1 页，URL 里也就不该再留 page
    expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: 'vue 响应式', page: 1 }),
    )
    expect(router.currentRoute.value.fullPath).toBe('/admin/articles?q=vue+%E5%93%8D%E5%BA%94%E5%BC%8F')
  })

  it('第 1 页不进 URL（避免 ?page=1 这种噪音），其余查询条件照旧', async () => {
    const { wrapper, router } = await mountList('/admin/articles?page=1&status=draft')

    expect(statusValue(wrapper)).toBe('draft')
    // 进入时就不该把 page=1 留在地址栏里
    expect(router.currentRoute.value.query.page).toBeUndefined()
  })

  it('直接改地址栏（或前进/后退）会把视图拉回来', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper, router } = await mountList('/admin/articles')

    await router.push('/admin/articles?status=archived&page=2')
    await flushPromises()

    expect(statusValue(wrapper)).toBe('archived')
    expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'archived', page: 2 }),
    )
  })

  it('边界：非法 status 被拦下，既不进请求也不显示（与首页对 page=abc 的兜底一致）', async () => {
    const list = stubList([makeArticle()])

    const { wrapper } = await mountList('/admin/articles?status=hacked')

    // 白名单校验：非法值不会原样发给后端（以前会，只能靠 422 兜底）
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ status: undefined }))
    expect(statusValue(wrapper)).toBe('')
  })

  it('边界：非法 page 回落到第 1 页，而不是把 NaN 发给后端', async () => {
    const list = stubList([makeArticle()], 40)

    const { wrapper } = await mountList('/admin/articles?page=abc')

    expect(list).toHaveBeenCalledWith(expect.objectContaining({ page: 1 }))
    expect(statusValue(wrapper)).toBe('')
  })

  it('边界：只输入空格的搜索词会被当成有效筛选（关键词不做 trim）', async () => {
    const list = stubList([makeArticle()])

    const { wrapper } = await mountList()
    await wrapper.get('input[aria-label="搜索文章"]').setValue('   ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: '   ' }))
  })
})

describe('ArticleListView · 发布 / 转草稿', () => {
  it('已发布的行显示「转草稿」，点击后用 id 调 update({status: draft}) 并重新拉取列表', async () => {
    const list = stubList([makeArticle({ id: 7, title: '已经发出去的文章' })], 1)
    const update = vi
      .spyOn(articleApi, 'update')
      .mockResolvedValue(makeDetail({ id: 7, status: 'draft' }))

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '已经发出去的文章'), '转草稿').trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledWith(7, { status: 'draft' })
    expect(lastToastMessage()).toBe('已转为草稿')
    // 行上的状态来自服务端，成功之后必须重新拉一次才对得上
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('草稿行显示「发布」，点击后 update({status: published})', async () => {
    stubList([makeArticle({ id: 9, title: '还没发的草稿', status: 'draft' })], 1)
    const update = vi
      .spyOn(articleApi, 'update')
      .mockResolvedValue(makeDetail({ id: 9, status: 'published' }))

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '还没发的草稿'), '发布').trigger('click')
    await flushPromises()

    // 按钮文案与目标状态必须互为反向：草稿要点的是「发布」，不是「转草稿」
    expect(update).toHaveBeenCalledWith(9, { status: 'published' })
    expect(lastToastMessage()).toBe('已发布')
  })

  it('切换状态失败：提示后端文案、不重新拉取列表、行上仍是原状态', async () => {
    const list = stubList([makeArticle({ id: 7, title: '改不动的文章' })], 1)
    vi.spyOn(articleApi, 'update').mockRejectedValue(
      new ApiError('没有权限执行该操作', 403, 'forbidden'),
    )

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '改不动的文章'), '转草稿').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('没有权限执行该操作')
    expect(lastToastKind()).toBe('error')
    // 失败不刷新：拿服务端旧数据盖回来只会让「我刚点的那一下去哪了」更难解释
    expect(list).toHaveBeenCalledTimes(1)
    // 没有任何乐观更新，所以界面天然是自洽的：仍然显示已发布 + 「转草稿」
    const row = rowByTitle(wrapper, '改不动的文章')
    expect(row.text()).toContain('已发布')
    expect(rowButton(row, '转草稿').exists()).toBe(true)
  })

  it('切换状态成功后保留当前页（不会把翻到第 3 页的人弹回第 1 页）', async () => {
    const list = stubList([makeArticle({ id: 7, title: '文章甲' })], 60)
    vi.spyOn(articleApi, 'update').mockResolvedValue(makeDetail({ id: 7, status: 'draft' }))

    const { wrapper } = await mountList()
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3 }))

    // stub 每页都返回同一行，这里只关心请求里的页码
    await rowButton(rowByTitle(wrapper, '文章甲'), '转草稿').trigger('click')
    await flushPromises()

    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3 }))
  })
})

describe('ArticleListView · 删除', () => {
  it('点「删除」只打开确认框，一个请求都不发', async () => {
    const remove = vi.spyOn(articleApi, 'remove')

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '一篇文章'), '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    // 文案里必须带上标题：一屏十几行，问「确定要删除吗」等于没问
    expect(dialog()?.textContent).toContain('《一篇文章》')
  })

  it('取消删除：不发请求、列表不变、确认框关掉', async () => {
    const remove = vi.spyOn(articleApi, 'remove')

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '一篇文章'), '删除').trigger('click')
    await flushPromises()

    dialogButton('取消')?.click()
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(rowTitles(wrapper)).toEqual(['一篇文章'])
  })

  it('确认删除：DELETE 打到正确地址，成功后关框、提示、重新拉取列表（被删的行消失）', async () => {
    const calls = stubNoContentAdapter()
    const list = vi
      .spyOn(articleApi, 'listManaged')
      .mockResolvedValueOnce(
        makePage([makeArticle({ id: 1, title: '要删的' }), makeArticle({ id: 2, title: '留下的' })], 2),
      )
      .mockResolvedValueOnce(makePage([makeArticle({ id: 2, title: '留下的' })], 1))

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '要删的'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(calls).toEqual(['DELETE /articles/1'])
    expect(lastToastMessage()).toBe('《要删的》已删除')
    expect(list).toHaveBeenCalledTimes(2)
    expect(rowTitles(wrapper)).toEqual(['留下的'])
    // 成功之后确认框必须收掉，否则用户会以为没删掉、再点一次（第二次就是 404）
    expect(dialog()).toBeNull()
  })

  it('删除失败：报错、列表不刷新、确认框留着可重试（不允许「界面已删、服务端还在」）', async () => {
    const list = stubList([makeArticle({ id: 1, title: '删不掉的文章' })], 1)
    vi.spyOn(articleApi, 'remove').mockRejectedValue(
      new ApiError('该文章正在被其他记录引用', 409, 'conflict'),
    )

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '删不掉的文章'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(lastToastMessage()).toBe('该文章正在被其他记录引用')
    expect(lastToastKind()).toBe('error')
    // 这一条是「写失败后 UI 一致性」的核心：既不能刷新（行会消失），也不能本地摘掉那一行
    expect(list).toHaveBeenCalledTimes(1)
    expect(rowTitles(wrapper)).toEqual(['删不掉的文章'])
    expect(dialog()).not.toBeNull()
  })

  it('删除请求进行中：确认框两个按钮都禁用并显示「处理中…」（防重复提交）', async () => {
    const gate = deferred<void>()
    stubNoContentAdapter(gate.promise)

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '一篇文章'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(dialogButton('处理中…')?.disabled).toBe(true)
    expect(dialogButton('取消')?.disabled).toBe(true)

    gate.resolve()
    await flushPromises()
    expect(dialog()).toBeNull()
  })

  it('删除接口 resolve 成 undefined 时也算成功（成败不能拿返回值判定）', async () => {
    const list = stubList([makeArticle({ id: 1, title: '要删的' })], 1)
    // 类型上 articleApi.remove 的契约是 Promise<void>，也就是「resolve 成 undefined」。
    // 这条用例原本记录的是**缺陷**：useConfirmDelete 拿 `done === undefined` 判失败，
    // 于是成功也被当成失败 —— 绿色 toast 弹了「已删除」，而确认框还开着、列表没刷新。
    // 现在它守的是修复后的契约：成败只由"有没有抛异常"决定。
    vi.spyOn(articleApi, 'remove').mockResolvedValue(undefined)

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '要删的'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(lastToastMessage()).toBe('《要删的》已删除')
    // 成功 ⇒ 关框 + 刷新列表（提示与界面必须一致）
    expect(dialog()).toBeNull()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('删除接口抛异常时保留确认框（失败可重试，不能假装成功）', async () => {
    const list = stubList([makeArticle({ id: 1, title: '删不掉的' })], 1)
    vi.spyOn(articleApi, 'remove').mockRejectedValue(new Error('boom'))

    const { wrapper } = await mountList()
    await rowButton(rowByTitle(wrapper, '删不掉的'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(dialog()).not.toBeNull()
    // 失败不该刷新列表：列表本来就没变，刷新只是白跑一趟
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('删除成功后保留当前页（与切换状态的口径一致）', async () => {
    const list = stubList([makeArticle({ id: 1, title: '文章甲' })], 60)
    stubNoContentAdapter()

    const { wrapper } = await mountList()
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3 }))

    await rowButton(rowByTitle(wrapper, '文章甲'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    // 以前删除走 reload()（默认重置页码）：第 3 页删一条会被弹回第 1 页，
    // 想接着删下一条还得重新翻回去。现在与 togglePublish 一样保留当前页。
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3 }))
  })
})
