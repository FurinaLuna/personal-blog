/**
 * SeriesDetailView 测试。
 *
 * 系列详情是「照着读」的页面：文章按 `series_order` 排好，读者从第 1 篇顺下去。
 * 守的契约：
 *
 * 1. **阅读顺序**：编号从 1 开始、与接口返回的顺序一致（前端不重排，排了就会
 *    和详情页的「系列上一/下一章」对不上）；
 * 2. **请求形态**：`detail(slug, 1, 50)`，参数是 URL 里的 slug；
 * 3. **失败与空态分得开**：接口挂了给后端文案（不显示「这个系列还没有文章」），
 *    加载中也不许先闪空态；头部在数据到位前不渲染（否则会先亮一个没有名字的标题）；
 * 4. **每行的降级**：摘要缺失不占位，时间取 `published_at ?? created_at`
 *    （草稿 / 定时发布的文章没有 published_at）；
 * 5. 标题跟随系列名（多开几个标签页时分得清谁是谁）。
 *
 * 本文件里还有两条**现状缺陷**的记录：路由参数变化不重新拉取、以及硬编码
 * page_size=50 导致第 51 篇起没有任何入口 —— 都用例钉住现状，修好后应改为
 * 断言正确行为。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, seriesApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import type { ArticleSummary, Page, Series, SeriesWithArticles } from '@/types'

import SeriesDetailView from './SeriesDetailView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeSeries(overrides: Partial<Series> = {}): Series {
  return {
    id: 1,
    name: 'Vue 源码',
    slug: 'vue-internals',
    description: null,
    article_count: 2,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

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
    // 不带时区：带 Z 的话西半球时区会把日期整体挪一天，用例就成了「跑在哪个时区」
    published_at: '2026-03-05T12:00:00',
    created_at: '2026-03-05T12:00:00',
    updated_at: '2026-03-05T12:00:00',
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

function makeArticles(items: ArticleSummary[], total = items.length): Page<ArticleSummary> {
  return { items, total, page: 1, page_size: 50, pages: Math.max(0, Math.ceil(total / 50)) }
}

function makeDetail(
  series: Series | null,
  items: ArticleSummary[] = [],
  total = items.length,
): SeriesWithArticles {
  return { series: series as Series, articles: makeArticles(items, total) }
}

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

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/series', component: Blank, meta: { title: '系列' } },
      { path: '/series/:slug', component: Blank, meta: { title: '系列详情' } },
      { path: '/article/:slug', component: Blank },
    ],
  })
}

async function mountDetail(slug = 'vue-internals') {
  const router = makeRouter()
  await router.push(`/series/${slug}`)
  await router.isReady()
  const wrapper = mount(SeriesDetailView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

/**
 * 行内的三段结构：`[序号, 内容包裹层, 标题, 摘要?]`。
 * 按位置取而不是按 class 取 —— 断言的意图是"这一行有没有摘要"，
 * class 随样式改动会失效。
 */
function rowIndex(row: DOMWrapper<Element>): string {
  return row.findAll('span')[0]?.text() ?? ''
}

function rowTitle(row: DOMWrapper<Element>): string {
  return row.findAll('span')[2]?.text() ?? ''
}

/** 摘要段：只有后端给了 summary 时才存在。 */
function rowSummary(row: DOMWrapper<Element>): string {
  return row.findAll('span')[3]?.text() ?? ''
}

function rows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('ol li')
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(seriesApi, 'detail').mockResolvedValue(
    makeDetail(makeSeries(), [
      makeArticle({ id: 1, title: '第一篇', slug: 'part-1', series_order: 1 }),
      makeArticle({ id: 2, title: '第二篇', slug: 'part-2', series_order: 2 }),
    ]),
  )
})

/* ------------------------------------------------------------ 用例 */

describe('SeriesDetailView · 加载 / 失败 / 空态', () => {
  it('用 URL 里的 slug 请求，固定第 1 页 50 条', async () => {
    const detail = vi.spyOn(seriesApi, 'detail')

    await mountDetail('vue-internals')

    expect(detail).toHaveBeenCalledWith('vue-internals', 1, 50)
  })

  it('加载中显示骨架屏，不显示「这个系列还没有文章」，也不渲染没名字的头部', async () => {
    const pending = deferred<SeriesWithArticles>()
    vi.spyOn(seriesApi, 'detail').mockReturnValue(pending.promise)

    const { wrapper } = await mountDetail()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    expect(wrapper.text()).not.toContain('这个系列还没有文章')
    // 头部依赖接口数据：数据没到时先渲染会亮出一个没有名字、篇数为空的标题
    expect(wrapper.find('h1').exists()).toBe(false)

    pending.resolve(makeDetail(makeSeries(), [makeArticle({ title: '第一篇', slug: 'part-1' })]))
    await flushPromises()

    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.get('h1').text()).toBe('Vue 源码')
  })

  it('加载失败显示后端文案，且不会错报成「这个系列还没有文章」', async () => {
    vi.spyOn(seriesApi, 'detail').mockRejectedValue(
      new ApiError('请求的内容不存在', 404, 'not_found'),
    )

    const { wrapper } = await mountDetail('not-exist')

    expect(wrapper.text()).toContain('请求的内容不存在')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('这个系列还没有文章')
  })

  it('网络异常（status 0）也有可读文案，而不是裸露的异常信息', async () => {
    vi.spyOn(seriesApi, 'detail').mockRejectedValue(
      new ApiError('网络异常，请检查网络连接', 0, 'network_error'),
    )

    const { wrapper } = await mountDetail()

    expect(wrapper.text()).toContain('网络异常')
  })

  it('系列还没有文章：给空态，头部仍显示系列名与 0 篇', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries({ name: '刚建的系列', article_count: 0 })),
    )

    const { wrapper } = await mountDetail('fresh')

    expect(wrapper.getComponent(EmptyState).props('title')).toBe('这个系列还没有文章')
    // 头部的「0 篇」与空态是一致的，不该藏起来让人以为系列不存在
    expect(wrapper.get('h1').text()).toBe('刚建的系列')
    expect(wrapper.text()).toContain('0 篇')
    expect(rows(wrapper)).toHaveLength(0)
  })
})

describe('SeriesDetailView · 头部与阅读顺序', () => {
  it('头部渲染系列名、篇数、描述与回「系列」的面包屑', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries({ description: '逐行读响应式', article_count: 12 }), [
        makeArticle({ id: 1, title: '第一篇', slug: 'part-1' }),
      ]),
    )

    const { wrapper } = await mountDetail()

    expect(wrapper.get('h1').text()).toBe('Vue 源码')
    expect(wrapper.text()).toContain('逐行读响应式')
    // 篇数取接口给的 article_count，不是"这一页有几条"
    expect(wrapper.text()).toContain('12 篇')
    expect(wrapper.get('a[href="/series"]').text()).toBe('系列')
  })

  it('没有描述时不渲染描述段（不留空白，也不出 undefined）', async () => {
    const { wrapper } = await mountDetail()

    expect(wrapper.find('header p').exists()).toBe(false)
    expect(wrapper.get('header').text()).not.toContain('undefined')
  })

  it('按接口返回顺序编号，从 1 开始连续', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries(), [
        makeArticle({ id: 3, title: '丙', slug: 'c', series_order: 1 }),
        makeArticle({ id: 1, title: '甲', slug: 'a', series_order: 2 }),
        makeArticle({ id: 2, title: '乙', slug: 'b', series_order: 3 }),
      ]),
    )

    const { wrapper } = await mountDetail()

    // 顺序完全照接口来：前端重排会和详情页的「系列上一章 / 下一章」对不上
    expect(rows(wrapper).map(rowTitle)).toEqual(['丙', '甲', '乙'])
    expect(rows(wrapper).map(rowIndex)).toEqual(['1', '2', '3'])
    expect(rows(wrapper).map((row) => row.get('a').attributes('href'))).toEqual([
      '/article/c',
      '/article/a',
      '/article/b',
    ])
  })

  it('每行给出标题、摘要与发布时间', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries(), [
        makeArticle({ title: '第一篇', slug: 'part-1', summary: '第一篇的摘要' }),
      ]),
    )

    const { wrapper } = await mountDetail()
    const row = rows(wrapper)[0] as DOMWrapper<Element>

    expect(rowTitle(row)).toBe('第一篇')
    expect(rowSummary(row)).toBe('第一篇的摘要')
    expect(row.get('time').text()).toContain('2026')
    expect(row.get('time').attributes('datetime')).toBe('2026-03-05T12:00:00')
  })

  it('摘要缺失时只留标题（空的一行会把列表撑出多余的间距）', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries(), [makeArticle({ title: '没有摘要的一篇', slug: 'bare', summary: null })]),
    )

    const { wrapper } = await mountDetail()
    const row = rows(wrapper)[0] as DOMWrapper<Element>

    expect(rowTitle(row)).toBe('没有摘要的一篇')
    // 没有摘要就没有那一段：既不留空行，也不会渲染成 "null"
    expect(row.findAll('span')).toHaveLength(3)
    expect(rowSummary(row)).toBe('')
    expect(row.text()).not.toContain('null')
  })

  it('时间降级：没有 published_at 的（草稿 / 定时发布）用 created_at', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries(), [
        makeArticle({
          title: '还没发的',
          slug: 'draft-one',
          published_at: null,
          created_at: '2026-01-02T12:00:00',
        }),
      ]),
    )

    const { wrapper } = await mountDetail()
    const row = rows(wrapper)[0] as DOMWrapper<Element>

    expect(row.get('time').attributes('datetime')).toBe('2026-01-02T12:00:00')
    expect(row.get('time').text()).toContain('2026')
  })

  it('标题跟随系列名（多开几个标签页时分得清谁是谁）', async () => {
    await mountDetail()
    expect(document.title).toBe('Vue 源码 · 个人博客')

    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries({ name: '另一个系列' }), [makeArticle({ title: '甲', slug: 'a' })]),
    )
    await mountDetail('another')
    expect(document.title).toBe('另一个系列 · 个人博客')
  })
})

describe('SeriesDetailView · 现状缺陷', () => {
  it('在系列之间跳转会重新请求并换成新系列（组件实例被复用）', async () => {
    const detail = vi.spyOn(seriesApi, 'detail').mockImplementation(async (slug) =>
      slug === 'first'
        ? makeDetail(makeSeries({ name: '第一个系列' }), [makeArticle({ title: '甲', slug: 'a' })])
        : makeDetail(makeSeries({ name: '第二个系列' }), [makeArticle({ title: '乙', slug: 'b' })]),
    )

    const { wrapper, router } = await mountDetail('first')
    expect(wrapper.text()).toContain('第一个系列')

    await router.push('/series/second')
    await flushPromises()

    // `/series/:slug` 是同一条路由记录，组件实例会被复用 —— 只绑 onMounted 的话
    // 页面会停在旧系列的标题与列表上且不报错（地址栏变了、内容没变）。
    // 现在 watch 了 slug，与 ArticleDetailView 同一口径。
    expect(detail).toHaveBeenCalledTimes(2)
    expect(detail).toHaveBeenLastCalledWith('second', 1, 50)
    expect(wrapper.text()).toContain('第二个系列')
    expect(wrapper.text()).not.toContain('第一个系列')
  })

  it('系列超过一页时给出分页器，第 51 篇起有入口（页码写回 URL）', async () => {
    const items = Array.from({ length: 50 }, (_, index) =>
      makeArticle({ id: index + 1, title: `第 ${index + 1} 篇`, slug: `part-${index + 1}` }),
    )
    const detail = vi
      .spyOn(seriesApi, 'detail')
      .mockResolvedValue(makeDetail(makeSeries({ article_count: 60 }), items, 60))

    const { wrapper, router } = await mountDetail()

    // 以前这里没有分页器：头部写着 60 篇、下面只有 50 条，第 51 篇起对读者不存在
    expect(wrapper.text()).toContain('60 篇')
    expect(rows(wrapper)).toHaveLength(50)

    const pagination = wrapper.findComponent(Pagination)
    expect(pagination.exists()).toBe(true)
    expect(pagination.props('total')).toBe(60)

    // 翻页要真的带上页码请求，并把页码写回 URL（刷新/分享都对得上）
    await pagination.vm.$emit('change', 2)
    await flushPromises()
    expect(detail).toHaveBeenLastCalledWith('vue-internals', 2, 50)
    expect(router.currentRoute.value.query.page).toBe('2')
  })

  it('不足一页时不渲染分页器（别制造不存在的导航）', async () => {
    vi.spyOn(seriesApi, 'detail').mockResolvedValue(
      makeDetail(makeSeries({ article_count: 3 }), [makeArticle({ title: '唯一一篇' })], 3),
    )

    const { wrapper } = await mountDetail()

    expect(wrapper.findComponent(Pagination).exists()).toBe(false)
  })
})
