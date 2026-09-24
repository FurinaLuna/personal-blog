/**
 * SearchView 测试。
 *
 * 搜索页与首页的「关键词筛选」是两件事（这里走 FTS5、按相关度排），它的状态
 * 完全放在 URL 的 `q` + `page` 里。守的契约：
 *
 * 1. **URL 是唯一状态源**：`?q=` 决定请求什么，提交与翻页都写回 URL，
 *    刷新 / 分享 / 前进后退看到的与屏幕上一致；
 * 2. **空关键词不是空结果**：没输入时给「输入关键词开始搜索」这个起点，
 *    既不发请求，也不显示「没有找到相关文章」（那会让人以为站里没这篇）；
 * 3. **加载中不能显示空态**：请求还没回来时不能断言"没找到"；
 * 4. **失败可恢复**：接口挂了要给后端文案 + 重试入口，且不显示「找到 N 条」的
 *    统计（0 条的统计会和错误文案打架）；
 * 5. **非法分页值兜底**：手改地址栏不该把 NaN 发给后端；
 * 6. **竞态**：换关键词后，先发的请求后返回时不能覆盖新结果；
 * 7. 搜索结果页不进搜索引擎索引（不输出 JSON-LD）。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, articleApi } from '@/api'
import ArticleCard from '@/components/ArticleCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import type { ArticleSummary, Page } from '@/types'

import SearchView from './SearchView.vue'

const PAGE_SIZE = 10

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

function makePage(items: ArticleSummary[], total = items.length, page = 1): Page<ArticleSummary> {
  return {
    items,
    total,
    page,
    page_size: PAGE_SIZE,
    pages: Math.max(1, Math.ceil(total / PAGE_SIZE)),
  }
}

/** 手动控制 resolve 时机的 promise：用来观察「请求还没回来」与乱序返回。 */
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
const scrollToMock = vi.fn()

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/search', component: Blank, meta: { title: '搜索' } },
      { path: '/article/:slug', component: Blank },
    ],
  })
}

async function mountSearch(initial = '/search') {
  const router = makeRouter()
  await router.push(initial)
  await router.isReady()
  const wrapper = mount(SearchView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

async function submitKeyword(wrapper: VueWrapper, keyword: string) {
  await wrapper.get('input[aria-label="搜索文章"]').setValue(keyword)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()]))
  vi.stubGlobal('scrollTo', scrollToMock)
  scrollToMock.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/* ------------------------------------------------------------ 用例 */

describe('SearchView · 空关键词与起点', () => {
  it('没带 q 时给明确起点，一个搜索请求都不发', async () => {
    const search = vi.spyOn(articleApi, 'search')

    const { wrapper } = await mountSearch()

    expect(search).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('输入关键词开始搜索')
    // 「没有找到相关文章」是"搜过了但没命中"，在还没搜的时候出现会误导访客
    expect(wrapper.text()).not.toContain('没有找到相关文章')
    expect(wrapper.getComponent(EmptyState).props('title')).toBe('输入关键词开始搜索')
  })

  it('q 只有空白字符时等同于没输入（先 trim 再判断）', async () => {
    const search = vi.spyOn(articleApi, 'search')

    const { wrapper } = await mountSearch('/search?q=%20%20')

    expect(search).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('输入关键词开始搜索')
  })
})

describe('SearchView · 请求参数与 URL 同步', () => {
  it('进页面就用 ?q= 请求，关键词 trim 后传给接口，每页 10 条', async () => {
    const search = vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()]))

    await mountSearch('/search?q=%20%20vue%20%20')

    // 关键词里的空白不参与匹配：不清掉会让 FTS 查一个不存在的词
    expect(search).toHaveBeenCalledWith('vue', { page: 1, page_size: PAGE_SIZE })
  })

  it('提交写回 ?q= 并丢掉旧页码（换关键词是新一次搜索，不是接着翻）', async () => {
    const search = vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()], 50))

    const { wrapper, router } = await mountSearch('/search?q=old&page=3')
    await submitKeyword(wrapper, '  vue 响应式  ')

    expect(router.currentRoute.value.path).toBe('/search')
    expect(router.currentRoute.value.query.q).toBe('vue 响应式')
    expect(router.currentRoute.value.query.page).toBeUndefined()
    expect(search).toHaveBeenLastCalledWith('vue 响应式', { page: 1, page_size: PAGE_SIZE })
  })

  it('空关键词不跳转也不请求（避免落到 /search?q= 这种空搜索页）', async () => {
    const search = vi.spyOn(articleApi, 'search')

    const { wrapper, router } = await mountSearch()
    await submitKeyword(wrapper, '   ')

    expect(router.currentRoute.value.fullPath).toBe('/search')
    expect(search).not.toHaveBeenCalled()
  })

  it('翻页写回 ?page=，并滚回顶部（否则从第 1 条往下看的人会停在中途）', async () => {
    const search = vi
      .spyOn(articleApi, 'search')
      .mockImplementation(async (_q, query = {}) =>
        makePage([makeArticle({ title: '命中一' })], 30, query.page ?? 1),
      )

    const { wrapper, router } = await mountSearch('/search?q=vue')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query).toEqual({ q: 'vue', page: '2' })
    expect(search).toHaveBeenLastCalledWith('vue', { page: 2, page_size: PAGE_SIZE })
    expect(scrollToMock).toHaveBeenCalledWith({ top: 0 })
  })

  it('地址栏变化（前进后退 / 分享链接）触发重新请求', async () => {
    const search = vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()]))

    const { router } = await mountSearch('/search?q=vue')
    expect(search).toHaveBeenCalledTimes(1)

    await router.push('/search?q=ts&page=2')
    await flushPromises()

    expect(search).toHaveBeenCalledTimes(2)
    expect(search).toHaveBeenLastCalledWith('ts', { page: 2, page_size: PAGE_SIZE })
  })

  it('边界：非法 page 兜底成第 1 页，而不是把 NaN 发给后端', async () => {
    const search = vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()]))

    await mountSearch('/search?q=vue&page=abc')
    expect(search).toHaveBeenLastCalledWith('vue', { page: 1, page_size: PAGE_SIZE })

    await mountSearch('/search?q=vue&page=-3')
    expect(search).toHaveBeenLastCalledWith('vue', { page: 1, page_size: PAGE_SIZE })

    await mountSearch('/search?q=vue&page=2.7')
    // 小数页码会被取整，不会出现"第 2.7 页"这种请求
    expect(search).toHaveBeenLastCalledWith('vue', { page: 2, page_size: PAGE_SIZE })
  })

  it('越界页码被接口收敛后，分页器按接口返回的页码显示（不再出现 981–42）', async () => {
    // 接口把越界页码收敛到最后一页（返回 page: 3），而地址栏里还是 99
    vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()], 42, 3))

    const { wrapper } = await mountSearch('/search?q=vue&page=99')
    const pagination = wrapper.getComponent(Pagination)

    // 以前这里读的是 URL 的 page，与接口返回的不一致时会高亮一个不存在的页，
    // 区间文案还成了「981–42 / 共 42 条」（起始比结束还大）。
    // 现在与首页同一口径：以接口返回的 page 为准。
    expect(pagination.props('page')).toBe(3)
    expect(pagination.text()).not.toContain('981–42')
  })

  it('输入框回填 URL 里的关键词（刷新后搜索框不能空着）', async () => {
    const { wrapper } = await mountSearch('/search?q=vue 响应式')

    const input = wrapper.get('input[aria-label="搜索文章"]').element as HTMLInputElement
    expect(input.value).toBe('vue 响应式')
  })

  it('URL 里的关键词按纯文本渲染：`?q=<img onerror>` 不会变成节点', async () => {
    const payload = '<img src=x onerror=alert(1)>'
    vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([]))

    const { wrapper } = await mountSearch(`/search?q=${encodeURIComponent(payload)}`)

    // 关键词是外部输入，而它会被写进「找到 N 条与「X」相关的结果」这句文案里：
    // 拼接而不是插值的话，这里就是一个反射型 XSS 的落点
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain(payload)
  })
})

describe('SearchView · 结果渲染', () => {
  it('渲染命中的文章，并把关键词交给卡片做标红（解释「为什么这条会出现」）', async () => {
    vi.spyOn(articleApi, 'search').mockResolvedValue(
      makePage([
        makeArticle({ id: 1, title: 'Vue 响应式原理', slug: 'vue-reactivity' }),
        makeArticle({ id: 2, title: 'Vue 编译器', slug: 'vue-compiler' }),
      ]),
    )

    const { wrapper } = await mountSearch('/search?q=vue')
    const cards = wrapper.findAllComponents(ArticleCard)

    expect(cards).toHaveLength(2)
    expect(cards.map((card) => card.props('article').title)).toEqual([
      'Vue 响应式原理',
      'Vue 编译器',
    ])
    expect(cards.every((card) => card.props('highlight') === 'vue')).toBe(true)
    expect(wrapper.find('a[href="/article/vue-reactivity"]').exists()).toBe(true)
  })

  it('显示命中总数（用户要能判断"就这么点"还是"还没翻完"）', async () => {
    vi.spyOn(articleApi, 'search').mockResolvedValue(
      makePage([makeArticle({ title: '命中一' })], 42),
    )

    const { wrapper } = await mountSearch('/search?q=vue')

    expect(wrapper.text()).toContain('找到')
    expect(wrapper.text()).toContain('42')
  })

  it('没有命中：给「没有找到相关文章」，并留着回首页的出口', async () => {
    vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([]))

    const { wrapper } = await mountSearch('/search?q=不存在的词')

    expect(wrapper.text()).toContain('没有找到相关文章')
    expect(wrapper.find('a[href="/"]').exists()).toBe(true)
    // 结果为空时不渲染分页器（「第 1 页 / 共 0 条」毫无意义）
    expect(wrapper.findComponent(Pagination).exists()).toBe(false)
  })

  it('结果不超过一页时不渲染分页器', async () => {
    vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()], PAGE_SIZE))

    const { wrapper } = await mountSearch('/search?q=vue')

    expect(wrapper.findComponent(Pagination).exists()).toBe(false)
  })

  it('结果超过一页时渲染分页器，并把 URL 里的页码交给它', async () => {
    vi.spyOn(articleApi, 'search').mockResolvedValue(makePage([makeArticle()], 30, 2))

    const { wrapper } = await mountSearch('/search?q=vue&page=2')
    const pagination = wrapper.getComponent(Pagination)

    expect(pagination.props('total')).toBe(30)
    expect(pagination.props('pageSize')).toBe(PAGE_SIZE)
    expect(pagination.props('page')).toBe(2)
  })
})

describe('SearchView · 加载与失败', () => {
  it('搜索进行中显示进度与骨架屏，不显示「没有找到相关文章」', async () => {
    const pending = deferred<Page<ArticleSummary>>()
    vi.spyOn(articleApi, 'search').mockReturnValue(pending.promise)

    const { wrapper } = await mountSearch('/search?q=vue')

    expect(wrapper.text()).toContain('正在搜索「vue」')
    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    // 请求还没回来时不能断言"没找到"——那是在用"还没搜完"冒充"搜完了没有"
    expect(wrapper.text()).not.toContain('没有找到相关文章')

    pending.resolve(makePage([makeArticle({ title: '迟到的命中' })]))
    await flushPromises()

    expect(wrapper.text()).toContain('迟到的命中')
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('搜索失败：显示后端文案与重试入口，不误报成"没有结果"', async () => {
    vi.spyOn(articleApi, 'search').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountSearch('/search?q=vue')

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('没有找到相关文章')
    // 失败时也不该显示统计：一句「找到 0 条」会和错误文案互相打架
    expect(wrapper.text()).not.toContain('找到')
    expect(wrapper.findAll('button').some((item) => item.text().trim() === '重试')).toBe(true)
  })

  it('点「重试」能真的恢复：重新请求成功后就地渲染结果', async () => {
    const search = vi
      .spyOn(articleApi, 'search')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce(makePage([makeArticle({ title: '重试之后的命中' })]))

    const { wrapper } = await mountSearch('/search?q=vue')
    const retry = wrapper.findAll('button').find((item) => item.text().trim() === '重试')
    await retry?.trigger('click')
    await flushPromises()

    expect(search).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('重试之后的命中')
    expect(wrapper.text()).not.toContain('服务器开小差了')
  })

  it('网络异常（status 0）也有可读文案，而不是裸露的异常信息', async () => {
    vi.spyOn(articleApi, 'search').mockRejectedValue(
      new ApiError('网络异常，请检查网络连接', 0, 'network_error'),
    )

    const { wrapper } = await mountSearch('/search?q=vue')

    expect(wrapper.text()).toContain('网络异常')
  })

  it('竞态：换关键词后，先发的请求后返回时不覆盖新结果', async () => {
    const first = deferred<Page<ArticleSummary>>()
    vi.spyOn(articleApi, 'search').mockImplementation((q) =>
      q === 'a' ? first.promise : Promise.resolve(makePage([makeArticle({ title: 'B 的命中' })])),
    )

    const { wrapper, router } = await mountSearch('/search?q=a')
    await router.push('/search?q=b')
    await flushPromises()
    expect(wrapper.text()).toContain('B 的命中')

    // A 的响应这时才回来：代次已经变了，必须丢弃，否则地址栏是 b、结果是 a
    first.resolve(makePage([makeArticle({ title: 'A 的命中' })]))
    await flushPromises()

    expect(wrapper.text()).toContain('B 的命中')
    expect(wrapper.text()).not.toContain('A 的命中')
  })
})

describe('SearchView · head', () => {
  it('标题跟随关键词（多开几个标签页时分得清谁是谁）', async () => {
    await mountSearch('/search?q=vue')
    expect(document.title).toBe('搜索：vue · 个人博客')

    await mountSearch()
    expect(document.title).toBe('搜索 · 个人博客')
  })

  it('搜索结果页不输出 JSON-LD（同一份内容会随关键词产生无数 URL，不该进索引）', async () => {
    await mountSearch('/search?q=vue')

    expect(document.getElementById('blog-json-ld')).toBeNull()
  })
})
