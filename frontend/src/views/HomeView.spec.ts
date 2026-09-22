/**
 * HomeView 测试。
 *
 * 首页把「筛选 / 排序 / 分页」全部放在 URL query 里，组件不额外维护一份状态——
 * 这是它最值得测的地方：一旦某条路径只改了内存状态而没写回 URL，
 * 刷新、分享链接、浏览器前进后退就会和眼睛看到的对不上。
 * 这里守的契约：
 *
 * 1. **URL 是唯一状态源**：进页面时从 query 读，改条件时写回 query；
 * 2. **失败与空态可区分**：接口挂了要有重试入口，没数据要说清是「一篇都没有」
 *    还是「筛选没命中」（两者的出口完全不同）；
 * 3. **头条只在「无筛选的第一页」出现**：翻页/筛选时用户找的是结果，不是推荐；
 * 4. **侧边栏是配角**：分类/标签加载失败只让侧边栏留空，不能拖垮主列表；
 * 5. **搜索框跳搜索页**：首页的 `?keyword=` 是筛选（按时间），/search 是按相关度，
 *    给同一个输入框两套排序规则只会让人困惑。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, articleApi, categoryApi, tagApi } from '@/api'
import ArticleCard from '@/components/ArticleCard.vue'
import Pagination from '@/components/Pagination.vue'
import { useToast } from '@/composables/useToast'
import type { ArticleSummary, Category, Page, Tag } from '@/types'

import HomeView from './HomeView.vue'

const PAGE_SIZE = 8

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

const CATEGORIES: Category[] = [
  {
    id: 1,
    name: '技术',
    slug: 'tech',
    description: null,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    article_count: 3,
  },
  {
    id: 2,
    name: '空分类',
    slug: 'empty',
    description: null,
    sort_order: 1,
    created_at: '2026-01-01T00:00:00Z',
    article_count: 0,
  },
]

const TAGS: Tag[] = [{ id: 9, name: 'vue', slug: 'vue', article_count: 4 }]

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
      { path: '/tags', component: Blank },
      { path: '/archive', component: Blank },
      { path: '/about', component: Blank },
      { path: '/guestbook', component: Blank },
    ],
  })
}

async function mountHome(initial = '/') {
  const router = makeRouter()
  await router.push(initial)
  await router.isReady()
  const wrapper = mount(HomeView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

beforeEach(() => {
  setActivePinia(createPinia())
  useToast().items.value = []
  vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()]))
  vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
  vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)
  vi.stubGlobal('scrollTo', scrollToMock)
  scrollToMock.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/* ------------------------------------------------------------ 用例 */

describe('HomeView · 加载 / 失败 / 空态', () => {
  it('首屏加载中显示骨架屏，而不是「还没有发布任何文章」', async () => {
    let resolveList!: (value: Page<ArticleSummary>) => void
    vi.spyOn(articleApi, 'list').mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve
      }),
    )

    const { wrapper } = await mountHome()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    // 空态文案在加载期间出现会让访客以为站点是空的
    expect(wrapper.text()).not.toContain('还没有发布任何文章')

    resolveList(makePage([makeArticle({ title: '加载出来的文章' })]))
    await flushPromises()
    expect(wrapper.text()).toContain('加载出来的文章')
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('加载失败显示可读文案与重试入口；点重试能真的恢复', async () => {
    const list = vi
      .spyOn(articleApi, 'list')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce(makePage([makeArticle({ title: '重试之后的文章' })]))

    const { wrapper } = await mountHome()
    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')

    await button(wrapper, '重试').trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('重试之后的文章')
    expect(wrapper.text()).not.toContain('服务器开小差了')
  })

  it('一篇都没有 vs 筛选没命中：文案与出口都不一样', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([]))

    const empty = await mountHome()
    expect(empty.wrapper.text()).toContain('还没有发布任何文章')
    expect(empty.wrapper.text()).not.toContain('没有匹配的文章')

    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([]))
    const filtered = await mountHome('/?tag=vue')
    expect(filtered.wrapper.text()).toContain('没有匹配的文章')
    // 筛选空结果必须给出「清除筛选」这个出口，否则用户卡在一个空页面上
    expect(filtered.wrapper.text()).toContain('清除筛选')
  })

  it('空列表时不渲染分页器（一个「第 1 页 / 共 0 条」的分页器毫无意义）', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([]))
    const { wrapper } = await mountHome()
    expect(wrapper.findComponent(Pagination).exists()).toBe(false)
  })

  it('无筛选且加载完成时显示文章总数', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()], 12))
    const { wrapper } = await mountHome()
    expect(wrapper.text()).toContain('共 12 篇')
  })
})

describe('HomeView · URL 是唯一状态源', () => {
  it('进页面时从 query 读筛选条件，并原样传给列表接口', async () => {
    const list = vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()]))

    const { wrapper } = await mountHome('/?tag=vue&sort=hottest&page=2')

    expect(list).toHaveBeenCalledWith({
      page: 2,
      page_size: PAGE_SIZE,
      sort: 'hottest',
      keyword: undefined,
      tag: 'vue',
      category: undefined,
    })
    // 标题与筛选标签跟着 URL 走，刷新/分享链接后看到的是同一个视图
    expect(wrapper.get('h1').text()).toBe('标签：vue')
  })

  it('分类筛选同样生效，且分类与标签的标题文案不同', async () => {
    const list = vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()]))

    const { wrapper } = await mountHome('/?category=tech')

    expect(list).toHaveBeenCalledWith(expect.objectContaining({ category: 'tech', tag: undefined }))
    expect(wrapper.get('h1').text()).toBe('分类：tech')
  })

  it('query 变化（浏览器前进后退/分享链接）触发重新请求', async () => {
    const list = vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()]))

    const { router } = await mountHome()
    expect(list).toHaveBeenCalledTimes(1)

    await router.push('/?page=3&sort=title')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3, sort: 'title' }))
  })

  it('分页非法值兜底成第 1 页（手改地址栏不该把接口打崩）', async () => {
    const list = vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()]))

    await mountHome('/?page=abc')
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 }))

    await mountHome('/?page=-3')
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 }))
  })

  it('点页码写回 URL 并滚回顶部', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()], 30))
    const { wrapper, router } = await mountHome()

    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.page).toBe('2')
    expect(scrollToMock).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
  })

  it('回到第 1 页时把 page 参数删掉（URL 里不留 ?page=1 这种噪声）', async () => {
    // 分页器读的是接口返回的 page，不是 URL——两者必须一致，否则按钮的禁用态是错的
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()], 30, 2))
    const { wrapper, router } = await mountHome('/?page=2')

    await wrapper.get('button[aria-label="上一页"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.page).toBeUndefined()
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('切排序写回 URL；默认的 latest 同样不留在 query 里', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()]))
    const { wrapper, router } = await mountHome()

    await wrapper.get('select').setValue('hottest')
    await flushPromises()
    expect(router.currentRoute.value.query.sort).toBe('hottest')

    await wrapper.get('select').setValue('latest')
    await flushPromises()
    expect(router.currentRoute.value.query.sort).toBeUndefined()
  })

  it('「清除筛选」把 tag / category / keyword / page 一起清掉', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()], 30))
    const { wrapper, router } = await mountHome('/?tag=vue&keyword=旧词&page=2')

    await button(wrapper, '清除筛选').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(wrapper.get('h1').text()).toBe('最新文章')
    // 没有筛选条件时这个按钮就不该再出现
    expect(wrapper.findAll('button').some((item) => item.text().trim() === '清除筛选')).toBe(false)
  })
})

describe('HomeView · 头条与列表', () => {
  it('无筛选的第一页：第一篇单独作为头条（featured），其余进「更多文章」', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(
      makePage([
        makeArticle({ id: 1, title: '置顶的头条', is_top: true }),
        makeArticle({ id: 2, title: '第二篇' }),
        makeArticle({ id: 3, title: '第三篇' }),
      ]),
    )

    const { wrapper } = await mountHome()
    const cards = wrapper.findAllComponents(ArticleCard)

    expect(cards).toHaveLength(3)
    expect(cards[0]?.props('featured')).toBe(true)
    expect(cards[0]?.props('article').title).toBe('置顶的头条')
    // 头条只出现一次：它同时也必须从「更多文章」里去掉，否则同一篇会连着出现两遍
    expect(cards.slice(1).map((card) => card.props('article').title)).toEqual(['第二篇', '第三篇'])
    expect(cards.slice(1).every((card) => card.props('featured') === false)).toBe(true)
    expect(wrapper.text()).toContain('更多文章')
  })

  it('只有一篇文章时头条就是全部，不渲染空的「更多文章」区块', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle({ title: '唯一一篇' })]))
    const { wrapper } = await mountHome()

    expect(wrapper.findAllComponents(ArticleCard)).toHaveLength(1)
    expect(wrapper.text()).not.toContain('更多文章')
  })

  it('筛选或翻页后不再给头条待遇：结果列表里每一篇都是普通卡片', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(
      makePage(
        [makeArticle({ id: 1, title: '筛选结果一' }), makeArticle({ id: 2, title: '筛选结果二' })],
        20,
        2,
      ),
    )

    const { wrapper } = await mountHome('/?tag=vue&page=2')
    const cards = wrapper.findAllComponents(ArticleCard)

    expect(cards).toHaveLength(2)
    expect(cards.every((card) => card.props('featured') === false)).toBe(true)
    expect(wrapper.text()).not.toContain('更多文章')
  })

  it('分页器按接口返回的 total / page_size 渲染', async () => {
    vi.spyOn(articleApi, 'list').mockResolvedValue(makePage([makeArticle()], 30, 1))
    const { wrapper } = await mountHome()

    const pagination = wrapper.getComponent(Pagination)
    expect(pagination.props('total')).toBe(30)
    expect(pagination.props('pageSize')).toBe(PAGE_SIZE)
    expect(pagination.text()).toContain('共 30 条')
  })
})

describe('HomeView · 搜索入口', () => {
  it('搜索框提交跳到 /search（相关度排序），而不是在本页做筛选', async () => {
    const { wrapper, router } = await mountHome()
    const list = vi.mocked(articleApi.list)

    await wrapper.get('input[aria-label="搜索文章"]').setValue('  vue 响应式  ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    // 不做逐字符比 URL（空格的百分号编码有两种合法写法），只断言「落到了哪个页面、带了什么参数」
    expect(router.currentRoute.value.path).toBe('/search')
    expect(router.currentRoute.value.query.q).toBe('vue 响应式')
    // 关键：没有走「在首页列表里按关键词筛」那条路——那会变成 /?keyword=...，
    // 结果是按时间排的，与 /search 的相关度排序对不上，用户会怀疑搜索坏了
    expect(list.mock.calls.some(([query]) => query?.keyword !== undefined)).toBe(false)
  })

  it('空关键词不跳转（避免跳到 /search?q= 这种空搜索页）', async () => {
    const { wrapper, router } = await mountHome()

    await wrapper.get('input[aria-label="搜索文章"]').setValue('   ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/')
  })
})

describe('HomeView · 侧边栏', () => {
  it('只展示有文章的分类（article_count 为 0 的分类点了也是空页）', async () => {
    const { wrapper } = await mountHome()

    expect(categoryApi.list).toHaveBeenCalledWith(true)
    const sidebar = wrapper.get('aside')
    expect(sidebar.text()).toContain('技术')
    expect(sidebar.text()).not.toContain('空分类')
    expect(sidebar.get('a[href="/?category=tech"]').text()).toContain('3')
    expect(sidebar.get('a[href="/?tag=vue"]').text()).toBe('vue')
  })

  it('当前筛选在侧边栏里有选中态（否则用户不知道自己正在筛什么）', async () => {
    const { wrapper } = await mountHome('/?tag=vue&category=tech')
    const sidebar = wrapper.get('aside')

    expect(sidebar.get('a[href="/?tag=vue"]').classes()).toContain('border-brand-300')
    expect(sidebar.get('a[href="/?category=tech"]').classes()).toContain('nav-link--active')
  })

  it('侧边栏接口挂掉只让侧边栏留空，主列表照常渲染、不弹错误', async () => {
    vi.spyOn(categoryApi, 'list').mockRejectedValue(new ApiError('boom', 500, 'internal_error'))
    vi.spyOn(tagApi, 'list').mockRejectedValue(new ApiError('boom', 500, 'internal_error'))

    const { wrapper } = await mountHome()

    expect(wrapper.text()).toContain('一篇文章')
    expect(wrapper.get('aside').text()).not.toContain('技术')
    expect(wrapper.get('aside').text()).toContain('快捷入口')
    expect(useToast().items.value).toHaveLength(0)
  })

  it('分类为空（全部 article_count 为 0）时整块不渲染', async () => {
    vi.spyOn(categoryApi, 'list').mockResolvedValue([{ ...CATEGORIES[1]! }])
    vi.spyOn(tagApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountHome()
    const sidebar = wrapper.get('aside')

    expect(sidebar.text()).not.toContain('空分类')
    expect(sidebar.text()).not.toContain('标签')
  })
})

describe('HomeView · head', () => {
  it('浏览器标题跟随筛选状态（多开几个标签页时分得清谁是谁）', async () => {
    await mountHome('/?tag=vue')
    expect(document.title).toBe('标签：vue · 个人博客')

    await mountHome()
    expect(document.title).toBe('首页 · 个人博客')
  })
})
