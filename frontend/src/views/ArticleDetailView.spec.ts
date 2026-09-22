/**
 * ArticleDetailView 测试。
 *
 * 详情页是「外面的人唯一会看到的页面」，它同时挂着正文渲染、SEO head、
 * 点赞、删除、上下篇、系列导航、相关阅读与评论区。这里守的是下面这些契约：
 *
 * 1. **失败路径可恢复**：404 与 5xx 要给出不同的文案，并且必须有重试入口，
 *    不能把读者留在一条空白或骨架屏上；
 * 2. **权限**：编辑/删除入口只对站长与作者本人出现（未登录、路人一概不给）；
 * 3. **点赞以后端返回值为准**：本地 +1 在并发下会与真实值越差越远；
 * 4. **竞态**：正文由 useAsyncData 的请求代次保护，相关阅读由组件自己的
 *    `relatedGeneration` 保护——快速连点两篇文章时，迟到的响应绝不能
 *    把当前文章的正文 / 推荐覆盖掉（这类 bug 不报错，只是「内容不对」）；
 * 5. **锦上添花的功能不许反客为主**：相关阅读接口挂掉只隐藏区块，不能弹错误；
 * 6. **切换文章要重置页面级状态**：上一篇的「已点赞」不能串到下一篇。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口
 * （spy 掉 `@/api` 上的方法与 `window.confirm` / `window.scrollTo`）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, articleApi, commentApi, http } from '@/api'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { ArticleDetail, ArticleSummary, User } from '@/types'

import ArticleDetailView from './ArticleDetailView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeArticle(overrides: Partial<ArticleDetail> = {}): ArticleDetail {
  return {
    id: 7,
    title: '一篇文章',
    slug: 'hello',
    summary: '摘要',
    cover_image: null,
    cover_variants: [],
    status: 'published',
    is_top: false,
    allow_comment: true,
    view_count: 12,
    like_count: 0,
    reading_time: 3,
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
    content_md: '正文内容',
    prev: null,
    next: null,
    series_prev: null,
    series_next: null,
    ...overrides,
  }
}

function makeSummary(overrides: Partial<ArticleSummary> = {}): ArticleSummary {
  return {
    id: 99,
    title: '相关文章',
    slug: 'related',
    summary: null,
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

const ADMIN: User = { ...AUTHOR, id: 1, username: 'admin', role: 'admin' }

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
/** 原始适配器：个别用例会临时替换它来模拟 204 空响应。 */
const originalAdapter = http.defaults.adapter

/**
 * 用真实 axios 适配器模拟「204 + 空响应体」，并记录实际发出的请求。
 * 只在需要观察真实响应形态（而不是返回值）时使用。
 */
function stubNoContentAdapter(): string[] {
  const calls: string[] = []
  http.defaults.adapter = (async (config: InternalAxiosRequestConfig) => {
    calls.push(`${(config.method ?? 'get').toUpperCase()} ${config.url ?? ''}`)
    return { data: '', status: 204, statusText: 'No Content', headers: new AxiosHeaders(), config }
  }) as unknown as AxiosAdapter
  return calls
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/article/:slug', component: Blank, meta: { title: '文章' } },
      { path: '/series/:slug', component: Blank },
      { path: '/tags', component: Blank },
      { path: '/admin/articles/:id/edit', component: Blank },
    ],
  })
}

async function mountView(slug = 'hello') {
  const router = makeRouter()
  await router.push(`/article/${slug}`)
  await router.isReady()
  const wrapper = mount(ArticleDetailView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim().startsWith(label))
  if (!found) throw new Error(`找不到文案以「${label}」开头的按钮`)
  return found
}

function lastToastMessage(): string | undefined {
  return useToast().items.value.at(-1)?.message
}

beforeEach(() => {
  setActivePinia(createPinia())
  useToast().items.value = []
  vi.spyOn(articleApi, 'detail').mockResolvedValue(makeArticle())
  vi.spyOn(articleApi, 'related').mockResolvedValue([])
  vi.spyOn(commentApi, 'listForArticle').mockResolvedValue([])
  // jsdom 没有实现这两个：不桩掉的话每条用例都会往控制台喷 "Not implemented"
  vi.stubGlobal('scrollTo', scrollToMock)
  scrollToMock.mockClear()
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  vi.unstubAllGlobals()
  http.defaults.adapter = originalAdapter
})

/* ------------------------------------------------------------ 用例 */

describe('ArticleDetailView · 加载与错误态', () => {
  it('首次加载渲染骨架屏，而不是空白或「没找到」', async () => {
    const pending = deferred<ArticleDetail>()
    vi.spyOn(articleApi, 'detail').mockReturnValue(pending.promise)

    const { wrapper } = await mountView()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    expect(wrapper.find('article').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('出错了')

    pending.resolve(makeArticle())
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.find('article').exists()).toBe(true)
  })

  it('404 显示 404 文案，并同时给出「返回首页」与「重试」两个出口', async () => {
    vi.spyOn(articleApi, 'detail').mockRejectedValue(
      new ApiError('文章不存在或已删除', 404, 'not_found'),
    )

    const { wrapper } = await mountView('not-exist')

    expect(wrapper.text()).toContain('404')
    expect(wrapper.text()).toContain('文章不存在或已删除')
    // 详情页最常见的入口是分享链接：作者删了文、链接过期，读者必须能走掉
    expect(wrapper.find('a[href="/"]').exists()).toBe(true)
    expect(button(wrapper, '重试').exists()).toBe(true)
  })

  it('非 404 的错误显示「出错了」，不与「文章不存在」混淆', async () => {
    vi.spyOn(articleApi, 'detail').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('出错了')
    expect(wrapper.text()).not.toContain('404')
    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
  })

  it('网络异常（status 0）也有可读文案，而不是裸露的异常信息', async () => {
    vi.spyOn(articleApi, 'detail').mockRejectedValue(
      new ApiError('网络连接失败，请检查网络后重试', 0, 'network_error'),
    )

    const { wrapper } = await mountView()
    expect(wrapper.text()).toContain('网络连接失败')
  })

  it('点「重试」能真的恢复：重新请求成功后就地渲染正文', async () => {
    const detail = vi
      .spyOn(articleApi, 'detail')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce(makeArticle({ title: '重试之后的文章' }))

    const { wrapper } = await mountView()
    expect(wrapper.text()).toContain('出错了')

    await button(wrapper, '重试').trigger('click')
    await flushPromises()

    expect(detail).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('重试之后的文章')
    expect(wrapper.text()).not.toContain('出错了')
  })

  it('正文 Markdown 被渲染成 HTML，并一次渲染同时喂给桌面目录', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        content_md: '# 一级标题\n\n## 章节甲\n\n正文一\n\n## 章节乙\n\n正文二',
      }),
    )

    const { wrapper } = await mountView()

    // 正文里的 # / ## 变成真实标题节点（渲染管线接对了才会有）。
    // 只断言「在不在」，不断言总数：评论区/相关阅读也各自带一个 <h2>
    const headings = wrapper.findAll('article h2').map((item) => item.text())
    expect(headings).toContain('章节甲')
    expect(headings).toContain('章节乙')
    const toc = wrapper.get('nav[aria-label="文章目录"]')
    expect(toc.text()).toContain('章节甲')
    expect(toc.text()).toContain('章节乙')
  })

  it('正文里的脚本被消毒，不会真的进 DOM', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({ content_md: '正常段落\n\n<script>window.__pwned = 1</script>' }),
    )

    const { wrapper } = await mountView()

    expect(wrapper.find('article script').exists()).toBe(false)
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined()
  })
})

describe('ArticleDetailView · 元信息与权限', () => {
  it('已发布且未排期时不显示状态徽标', async () => {
    const { wrapper } = await mountView()
    expect(wrapper.text()).not.toContain('草稿（仅你可见）')
    expect(wrapper.text()).not.toContain('定时发布')
  })

  it('草稿显示「草稿（仅你可见）」，定时发布显示「定时发布（仅你可见）」', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(makeArticle({ status: 'draft' }))
    const draft = await mountView()
    expect(draft.wrapper.text()).toContain('草稿（仅你可见）')

    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({ status: 'published', is_scheduled: true }),
    )
    const scheduled = await mountView()
    // 作者预览时状态是 published 但访客看不到：不给标记的话作者会以为已经发出去了
    expect(scheduled.wrapper.text()).toContain('定时发布（仅你可见）')
  })

  it('修订信息只在改过一天以上时出现（顺手改个错别字不该挂「有更新」）', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        published_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T02:00:00Z',
      }),
    )
    const fresh = await mountView()
    expect(fresh.wrapper.text()).not.toContain('修订于')

    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        published_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-03-01T00:00:00Z',
      }),
    )
    const updated = await mountView()
    expect(updated.wrapper.text()).toContain('修订于')
  })

  it('未登录访客看不到编辑 / 删除入口', async () => {
    const { wrapper } = await mountView()
    expect(wrapper.find('a[href="/admin/articles/7/edit"]').exists()).toBe(false)
    expect(wrapper.findAll('button').some((item) => item.text().trim() === '删除')).toBe(false)
  })

  it('其他登录用户（既非站长也非作者本人）同样看不到编辑 / 删除', async () => {
    useAuthStore().user = { ...AUTHOR, id: 999 }
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({ author: { id: 2, username: 'author', nickname: null, avatar_url: null } }),
    )

    const { wrapper } = await mountView()
    expect(wrapper.findAll('button').some((item) => item.text().trim() === '删除')).toBe(false)
  })

  it('作者本人看到编辑 / 删除入口，编辑链接指向这篇的编辑页', async () => {
    useAuthStore().user = AUTHOR
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({ author: { id: 2, username: 'author', nickname: null, avatar_url: null } }),
    )

    const { wrapper } = await mountView()
    expect(wrapper.get('a[href="/admin/articles/7/edit"]').text()).toBe('编辑')
    expect(wrapper.findAll('button').some((item) => item.text().trim() === '删除')).toBe(true)
  })

  it('站长对任何一篇文章都有编辑 / 删除入口', async () => {
    useAuthStore().user = ADMIN
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({ author: { id: 2, username: 'author', nickname: null, avatar_url: null } }),
    )

    const { wrapper } = await mountView()
    expect(wrapper.find('a[href="/admin/articles/7/edit"]').exists()).toBe(true)
  })

  it('没有标签时不渲染标签区（空容器会在标题下留一条无意义的空白）', async () => {
    const empty = await mountView()
    expect(empty.wrapper.findAll('a.chip')).toHaveLength(0)

    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        tags: [
          { id: 1, name: 'vue', slug: 'vue' },
          { id: 2, name: 'ts', slug: 'ts' },
        ],
      }),
    )
    const tagged = await mountView()
    expect(tagged.wrapper.findAll('a.chip').map((item) => item.text())).toEqual(['# vue', '# ts'])
    expect(tagged.wrapper.get('a.chip').attributes('href')).toBe('/?tag=vue')
  })

  it('系列导航：有上下篇时给链接，没有时给「系列第一篇 / 最后一篇」提示', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        series: { id: 5, name: 'Vue 源码', slug: 'vue-internals' },
        series_prev: null,
        series_next: { id: 8, title: '下一篇', slug: 'next-one' },
      }),
    )

    const { wrapper } = await mountView()
    expect(wrapper.text()).toContain('系列 · Vue 源码')
    expect(wrapper.text()).toContain('系列第一篇')
    expect(wrapper.get('a[href="/article/next-one"]').text()).toContain('下一篇')
    expect(wrapper.get('a[href="/series/vue-internals"]').text()).toContain('查看全部')

    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        series: { id: 5, name: 'Vue 源码', slug: 'vue-internals' },
        series_prev: { id: 6, title: '上一篇', slug: 'prev-one' },
        series_next: null,
      }),
    )
    const last = await mountView()
    expect(last.wrapper.text()).toContain('系列最后一篇')
  })

  it('不属于任何系列时不渲染系列导航条', async () => {
    const { wrapper } = await mountView()
    expect(wrapper.text()).not.toContain('系列 ·')
  })

  it('head 跟随文章：标题与 canonical 都用 slug（同一篇的两个 URL 不该各算一份权重）', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({ title: '被索引的标题', slug: 'canonical-slug' }),
    )

    await mountView('canonical-slug')

    expect(document.title).toBe('被索引的标题 · 个人博客')
    const canonical = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]')
    expect(canonical?.href.endsWith('/article/canonical-slug')).toBe(true)
  })
})

describe('ArticleDetailView · 点赞', () => {
  it('草稿不渲染点赞按钮（后端会拒，前端就不该给这个入口）', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(makeArticle({ status: 'draft' }))
    const { wrapper } = await mountView()
    expect(wrapper.findAll('button').some((item) => item.text().trim().startsWith('点赞'))).toBe(
      false,
    )
  })

  it('点赞成功后用后端返回的计数更新，而不是本地 +1', async () => {
    const like = vi.spyOn(articleApi, 'like').mockResolvedValue({ like_count: 99 })
    const { wrapper } = await mountView()

    expect(button(wrapper, '点赞').text()).toContain('0')
    await button(wrapper, '点赞').trigger('click')
    await flushPromises()

    expect(like).toHaveBeenCalledWith(7)
    // 本地 +1 在并发下会和真实值越差越远，所以只认后端返回值
    expect(button(wrapper, '已点赞').text()).toContain('99')
    expect(lastToastMessage()).toBe('感谢支持！')
  })

  it('点赞失败时计数不动、按钮解禁并给出提示', async () => {
    vi.spyOn(articleApi, 'like').mockRejectedValue(new ApiError('不能给自己的文章点赞', 400, 'bad_request'))
    const { wrapper } = await mountView()

    await button(wrapper, '点赞').trigger('click')
    await flushPromises()

    expect(button(wrapper, '点赞').text()).toContain('0')
    expect(button(wrapper, '点赞').attributes('disabled')).toBeUndefined()
    expect(lastToastMessage()).toBe('不能给自己的文章点赞')
  })

  it('点赞进行中按钮禁用（同一篇文章不会连点出两次计数）', async () => {
    const pending = deferred<{ like_count: number }>()
    vi.spyOn(articleApi, 'like').mockReturnValue(pending.promise)
    const { wrapper } = await mountView()

    await button(wrapper, '点赞').trigger('click')
    await flushPromises()
    expect(button(wrapper, '点赞').attributes('disabled')).toBeDefined()

    pending.resolve({ like_count: 1 })
    await flushPromises()
    expect(button(wrapper, '已点赞').attributes('disabled')).toBeUndefined()
  })

  it('切换到另一篇文章时「已点赞」被重置（不能把上一篇的状态串过来）', async () => {
    vi.spyOn(articleApi, 'detail').mockImplementation(async (slug) =>
      String(slug) === 'first'
        ? makeArticle({ id: 1, slug: 'first', title: '第一篇' })
        : makeArticle({ id: 2, slug: 'second', title: '第二篇' }),
    )
    vi.spyOn(articleApi, 'like').mockResolvedValue({ like_count: 5 })

    const { wrapper, router } = await mountView('first')
    await button(wrapper, '点赞').trigger('click')
    await flushPromises()
    expect(button(wrapper, '已点赞').exists()).toBe(true)

    await router.push('/article/second')
    await flushPromises()

    expect(wrapper.text()).toContain('第二篇')
    expect(button(wrapper, '点赞').exists()).toBe(true)
    expect(button(wrapper, '点赞').text()).toContain('0')
  })
})

describe('ArticleDetailView · 上下篇与相关阅读', () => {
  it('有上一篇/下一篇时渲染导航，都没有时整块不渲染', async () => {
    const none = await mountView()
    expect(none.wrapper.text()).not.toContain('上一篇')

    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeArticle({
        prev: { id: 6, title: '更早的一篇', slug: 'prev-one' },
        next: { id: 8, title: '更晚的一篇', slug: 'next-one' },
      }),
    )
    const both = await mountView()
    expect(both.wrapper.get('a[href="/article/prev-one"]').text()).toContain('更早的一篇')
    expect(both.wrapper.get('a[href="/article/next-one"]').text()).toContain('更晚的一篇')
  })

  it('相关阅读渲染后端返回的列表', async () => {
    vi.spyOn(articleApi, 'related').mockResolvedValue([
      makeSummary({ id: 11, title: '相关一', slug: 'r1' }),
      makeSummary({ id: 12, title: '相关二', slug: 'r2' }),
    ])

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('相关阅读')
    expect(wrapper.get('a[href="/article/r1"]').text()).toContain('相关一')
    expect(wrapper.get('a[href="/article/r2"]').text()).toContain('相关二')
  })

  it('相关阅读接口挂掉只隐藏区块：正文照常、评论区照常、不弹错误', async () => {
    vi.spyOn(articleApi, 'related').mockRejectedValue(new ApiError('boom', 500, 'internal_error'))

    const { wrapper } = await mountView()

    expect(wrapper.text()).not.toContain('相关阅读')
    expect(wrapper.find('article').exists()).toBe(true)
    expect(wrapper.text()).toContain('正文内容')
    expect(wrapper.find('#comments').exists()).toBe(true)
    expect(useToast().items.value).toHaveLength(0)
  })

  it('相关阅读竞态：先发的请求后返回时不会覆盖当前文章的推荐（relatedGeneration）', async () => {
    const firstRelated = deferred<ArticleSummary[]>()
    vi.spyOn(articleApi, 'detail').mockImplementation(async (slug) =>
      String(slug) === 'a'
        ? makeArticle({ id: 1, slug: 'a', title: 'A 篇' })
        : makeArticle({ id: 2, slug: 'b', title: 'B 篇' }),
    )
    vi.spyOn(articleApi, 'related').mockImplementation((id) =>
      id === 1
        ? firstRelated.promise
        : Promise.resolve([makeSummary({ id: 21, title: 'B 的相关文章' })]),
    )

    const { wrapper, router } = await mountView('a')
    await router.push('/article/b')
    await flushPromises()
    expect(wrapper.text()).toContain('B 的相关文章')

    // A 的相关文章请求这时才回来：代次已经变了，必须丢弃
    firstRelated.resolve([makeSummary({ id: 11, title: 'A 的相关文章' })])
    await flushPromises()

    expect(wrapper.text()).toContain('B 的相关文章')
    expect(wrapper.text()).not.toContain('A 的相关文章')
  })

  it('详情竞态：迟到的正文响应不会覆盖新文章（useAsyncData 的请求代次）', async () => {
    const first = deferred<ArticleDetail>()
    vi.spyOn(articleApi, 'detail').mockImplementation((slug) =>
      String(slug) === 'a'
        ? first.promise
        : Promise.resolve(makeArticle({ id: 2, slug: 'b', title: 'B 篇的标题' })),
    )

    const { wrapper, router } = await mountView('a')
    await router.push('/article/b')
    await flushPromises()
    expect(wrapper.text()).toContain('B 篇的标题')

    // 「先点 A，等不及又点了 B，A 的响应最后才到」——不丢弃的话，
    // 地址栏是 B，正文却是 A，而且没有任何报错
    first.resolve(makeArticle({ id: 1, slug: 'a', title: 'A 篇的标题' }))
    await flushPromises()

    expect(wrapper.text()).toContain('B 篇的标题')
    expect(wrapper.text()).not.toContain('A 篇的标题')
  })

  it('切换文章会把页面滚回顶部（否则读者从上下篇点进来会停在页面中部）', async () => {
    const { router } = await mountView('a')
    scrollToMock.mockClear()

    await router.push('/article/b')
    await flushPromises()

    expect(scrollToMock).toHaveBeenCalledWith({ top: 0 })
  })
})

describe('ArticleDetailView · 评论区与删除', () => {
  it('允许评论时挂载评论区，并把详情接口带回来的评论数传给首屏', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(makeArticle({ comment_count: 12 }))
    const list = vi.spyOn(commentApi, 'listForArticle').mockResolvedValue([])

    const { wrapper } = await mountView()

    expect(list).toHaveBeenCalledWith(7)
    // 列表还没回来时先显示详情接口的计数，避免「12 条评论」下面写着「还没有评论 · 0」
    expect(wrapper.get('#comments').text()).toContain('12')
  })

  it('关闭评论的文章只给一句提示，既不挂载评论区也不请求评论列表', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(makeArticle({ allow_comment: false }))
    const list = vi.spyOn(commentApi, 'listForArticle')

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('本文已关闭评论')
    expect(wrapper.find('#comments').exists()).toBe(false)
    expect(list).not.toHaveBeenCalled()
  })

  it('删除：确认框取消时一个请求都不发', async () => {
    useAuthStore().user = ADMIN
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const remove = vi.spyOn(articleApi, 'remove')

    const { wrapper } = await mountView()
    await button(wrapper, '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
  })

  it('删除：确认后调接口并跳回首页', async () => {
    useAuthStore().user = ADMIN
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    // 删除接口真实的成功形态是 204 空响应体（axios 会给出空字符串而不是 undefined）。
    // 这里走真实适配器而不是 spy 掉 articleApi.remove，才能钉住「空响应也算成功」——
    // 视图里的 `if (done === undefined) return` 依赖的正是这个事实
    const calls = stubNoContentAdapter()

    const { wrapper, router } = await mountView()
    await button(wrapper, '删除').trigger('click')
    await flushPromises()

    expect(calls).toContain('DELETE /articles/7')
    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(lastToastMessage()).toBe('文章已删除')
  })

  it('删除失败时留在原地并提示，不把读者踢回首页', async () => {
    useAuthStore().user = ADMIN
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(articleApi, 'remove').mockRejectedValue(new ApiError('没有权限删除', 403, 'forbidden'))

    const { wrapper, router } = await mountView()
    await button(wrapper, '删除').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/article/hello')
    expect(lastToastMessage()).toBe('没有权限删除')
    expect(wrapper.text()).toContain('一篇文章')
  })
})
