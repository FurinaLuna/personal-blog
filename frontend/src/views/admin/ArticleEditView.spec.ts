/**
 * ArticleEditView 测试。
 *
 * 这是全站最长、也最容易出事的视图：它同时承担「新建 / 编辑」两种模式、
 * 一个会写本地快照的表单、以及「保存后把 URL 换成编辑态」这种自触发导航。
 * 这里守的不是排版，而是下面这些真会丢数据的契约：
 *
 * 1. **模式判别**：有没有 `:id` 决定拉不拉服务端、显不显示历史入口；
 * 2. **保存 payload 的清洗**：空串要转成 null（后端靠 null 走自动生成 slug /
 *    清掉排期分支），首尾空白要 trim，否则标题里一个空格就能让 slug 生成出空值；
 * 3. **保存成功后的回填**：slug / status / published_at 必须以后端返回值为准，
 *    不回填作者就不知道到底发出去没有，很可能再点一次；
 * 4. **失败路径**：422 字段级错误要贴到字段旁、同时保留顶部提示条；
 *    失败后按钮必须解禁，不能把表单永久锁死；
 * 5. **本地草稿**：只在「比服务端新」时提示恢复、保存成功后清掉快照、
 *    表单加载完成前绝不写盘（否则加载期间的空表单会覆盖已有草稿）；
 * 6. **路由变化**：同一个组件实例被复用而 id 变了必须重新拉取（否则界面停在上一篇、
 *    保存却 PATCH 到新 id）；但「新建保存后自己 replace 出来的那次」不能重新拉取。
 *
 * 说明：这里不 mock 业务模块，只替换网络出口（spy 掉 `@/api` 上的方法），
 * 组件、composable、store 全部走真实实现——与 http.spec.ts / CommentSection.spec.ts 一致。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import {
  ApiError,
  articleApi,
  attachmentApi,
  categoryApi,
  revisionApi,
  seriesApi,
  tagApi,
} from '@/api'
import RevisionHistory from '@/components/RevisionHistory.vue'
import { useToast } from '@/composables/useToast'
import type { ArticleDetail, Attachment, Category, Series, Tag } from '@/types'

import ArticleEditView from './ArticleEditView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeDetail(overrides: Partial<ArticleDetail> = {}): ArticleDetail {
  return {
    id: 7,
    title: '服务端标题',
    slug: 'server-slug',
    summary: '服务端摘要',
    cover_image: null,
    cover_variants: [],
    status: 'draft',
    is_top: false,
    allow_comment: true,
    view_count: 0,
    like_count: 0,
    reading_time: 1,
    published_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    author: null,
    category: null,
    series: null,
    series_order: 0,
    tags: [],
    comment_count: 0,
    is_scheduled: false,
    content_md: '服务端正文',
    prev: null,
    next: null,
    series_prev: null,
    series_next: null,
    ...overrides,
  }
}

function makeAttachment(overrides: Partial<Attachment> = {}): Attachment {
  return {
    id: 1,
    original_name: 'cover.png',
    mime_type: 'image/png',
    size: 1024,
    kind: 'image',
    width: 1600,
    height: 900,
    url: '/media/cover.png',
    thumbnail_url: null,
    variants: [],
    created_at: '2026-01-01T00:00:00Z',
    markdown: '![cover](/media/cover.png)',
    ...overrides,
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
]

const SERIES_LIST: Series[] = [
  {
    id: 5,
    name: 'Vue 源码',
    slug: 'vue-internals',
    description: null,
    article_count: 2,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

const TAGS: Tag[] = [{ id: 9, name: 'vue', slug: 'vue', article_count: 4 }]

/** 草稿快照的 payload 必须与表单同构（真实读写走 JSON 序列化）。 */
function makeDraftPayload(overrides: Record<string, unknown> = {}) {
  return {
    title: '',
    slug: '',
    summary: '',
    content_md: '',
    cover_image: '',
    status: 'draft',
    published_at: '',
    is_top: false,
    allow_comment: true,
    category_id: null,
    series_id: null,
    series_order: 0,
    tags: [],
    ...overrides,
  }
}

function seedDraft(key: string, payload: Record<string, unknown>, savedAt = Date.now()): void {
  window.localStorage.setItem(key, JSON.stringify({ savedAt, key, payload }))
}

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

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/admin/articles', component: Blank },
      { path: '/admin/articles/new', component: Blank },
      { path: '/admin/articles/:id/edit', component: Blank },
    ],
  })
}

async function mountEditor(path: string) {
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  // 直接挂视图而不是挂 RouterView：路由表里的占位组件与视图是同一个实例语义，
  // 直接挂能省掉一层，同时 useRoute() 仍然是真实且可导航的
  const wrapper = mount(ArticleEditView, { global: { plugins: [router, createPinia()] } })
  await flushPromises()
  return { wrapper, router }
}

/**
 * 默认把三个「下拉框数据源」与详情接口都接上，避免测试踩到真实网络。
 * 每个用例只覆盖自己关心的那个出口。
 */
function stubBaseline(options: { detail?: ArticleDetail | null } = {}): void {
  vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
  vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES_LIST)
  vi.spyOn(tagApi, 'list').mockResolvedValue(TAGS)
  vi.spyOn(articleApi, 'detail').mockResolvedValue(options.detail ?? makeDetail())
  vi.spyOn(revisionApi, 'list').mockResolvedValue([])
  vi.spyOn(attachmentApi, 'upload').mockResolvedValue(makeAttachment())
}

/** 按文案取按钮：模板里同一屏有十几个 button，按 class 取会随样式改动而失效。 */
function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

function inputValue(wrapper: VueWrapper, ariaLabel: string): string {
  return (wrapper.get(`input[aria-label="${ariaLabel}"]`).element as HTMLInputElement).value
}

function textValue(wrapper: VueWrapper, ariaLabel: string): string {
  return (wrapper.get(`textarea[aria-label="${ariaLabel}"]`).element as HTMLTextAreaElement).value
}

function selectValue(wrapper: VueWrapper, ariaLabel: string): string {
  return (wrapper.get(`select[aria-label="${ariaLabel}"]`).element as HTMLSelectElement).value
}

/**
 * 状态下拉框没有 aria-label（只有视觉标题），只能按选项文案定位。
 * 「已归档」只有这一个 select 有，因此是稳定的锚点。
 */
function statusSelect(wrapper: VueWrapper) {
  const found = wrapper.findAll('select').find((item) => item.text().includes('已归档'))
  if (!found) throw new Error('找不到状态下拉框')
  return found
}

/** 触发 composable 的 beforeunload 落盘：3 秒防抖在测试里等不起，但这条路径是同步的。 */
function flushDraft(): void {
  window.dispatchEvent(new Event('beforeunload'))
}

function lastToastMessage(): string | undefined {
  return useToast().items.value.at(-1)?.message
}

function lastToastKind(): string | undefined {
  return useToast().items.value.at(-1)?.kind
}

beforeEach(() => {
  window.localStorage.clear()
  useToast().items.value = []
  stubBaseline()
})

/* ------------------------------------------------------------ 用例 */

describe('ArticleEditView · 新建 / 编辑模式', () => {
  it('新建页不请求详情接口，也不显示「历史」入口', async () => {
    const detail = vi.spyOn(articleApi, 'detail')
    const { wrapper } = await mountEditor('/admin/articles/new')

    expect(detail).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('新建文章')
    // 还没有 id，也就没有任何历史可言；这个入口出现本身就是错的
    expect(wrapper.findAll('button').some((item) => item.text().trim() === '历史')).toBe(false)
  })

  it('编辑页拉取详情并逐字段回填表单', async () => {
    const detail = vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeDetail({
        id: 7,
        title: '服务端的标题',
        slug: 'server-slug',
        summary: '服务端的摘要',
        content_md: '服务端的正文',
        status: 'published',
        cover_image: '/media/old.png',
        published_at: '2026-09-20T01:00:00Z',
        is_top: true,
        allow_comment: false,
        category: { id: 1, name: '技术', slug: 'tech' },
        series: { id: 5, name: 'Vue 源码', slug: 'vue-internals' },
        series_order: 3,
        tags: [
          { id: 9, name: 'vue', slug: 'vue' },
          { id: 10, name: 'ts', slug: 'ts' },
        ],
      }),
    )

    const { wrapper } = await mountEditor('/admin/articles/7/edit')

    expect(detail).toHaveBeenCalledWith(7)
    expect(wrapper.text()).toContain('编辑已有文章')
    expect(inputValue(wrapper, '文章标题')).toBe('服务端的标题')
    expect(inputValue(wrapper, 'URL 别名')).toBe('server-slug')
    expect(textValue(wrapper, '文章摘要')).toBe('服务端的摘要')
    expect((wrapper.get('textarea[aria-label="正文（Markdown）"]').element as HTMLTextAreaElement).value).toBe(
      '服务端的正文',
    )
    expect(statusSelect(wrapper).element.value).toBe('published')
    // 分类/系列下拉是异步加载的，回填必须在下拉渲染出来之后仍然选中正确的项
    expect(selectValue(wrapper, '文章分类')).toBe('1')
    expect(selectValue(wrapper, '所属系列')).toBe('5')
    expect(inputValue(wrapper, '发布时间（留空表示立即发布）')).not.toBe('')
    expect(
      wrapper.findAll('button[aria-label^="移除标签"]').map((item) => item.attributes('aria-label')),
    ).toEqual(['移除标签 vue', '移除标签 ts'])
    // 头部会亮出这篇文章的对外地址，作者一眼能确认自己在改哪一篇
    expect(wrapper.text()).toContain('/article/server-slug')
  })

  it('详情加载失败时给出可读提示条，但表单不卡死（仍能编辑并落本地草稿）', async () => {
    vi.spyOn(articleApi, 'detail').mockRejectedValue(new ApiError('文章不存在或已删除', 404, 'not_found'))
    const { wrapper } = await mountEditor('/admin/articles/404/edit')

    expect(wrapper.text()).toContain('文章不存在或已删除')

    // formReady 已经是 true 的证据：加载失败后用户敲的字必须被兜进本地快照。
    // 若卡在「未就绪」，用户补写的标题会在关页面时静默丢失
    await wrapper.get('input[aria-label="文章标题"]').setValue('补写的标题')
    flushDraft()
    const raw = window.localStorage.getItem('article:404')
    expect(raw).not.toBeNull()
    expect(JSON.parse(raw as string).payload.title).toBe('补写的标题')
  })

  it('表单加载完成前不写本地快照（否则空表单会覆盖掉已有草稿）', async () => {
    const pending = deferred<ArticleDetail>()
    vi.spyOn(articleApi, 'detail').mockReturnValue(pending.promise)

    const { wrapper } = await mountEditor('/admin/articles/7/edit')
    await wrapper.get('input[aria-label="文章标题"]').setValue('加载期间敲的字')
    flushDraft()
    expect(window.localStorage.getItem('article:7')).toBeNull()

    // 加载完成后同一次输入就能被兜住（enabled 从 false 翻到 true 时会补一次落盘机会）
    pending.resolve(makeDetail())
    await flushPromises()
    await wrapper.get('input[aria-label="文章标题"]').setValue('加载完成后敲的字')
    flushDraft()
    const raw = window.localStorage.getItem('article:7')
    expect(raw).not.toBeNull()
    expect(JSON.parse(raw as string).payload.title).toBe('加载完成后敲的字')
  })

  it('分类与系列列表加载失败不阻塞编辑：给出系列提示，正文照常能保存', async () => {
    vi.spyOn(seriesApi, 'list').mockRejectedValue(new ApiError('服务开小差', 500, 'internal_error'))
    vi.spyOn(categoryApi, 'list').mockRejectedValue(new ApiError('服务开小差', 500, 'internal_error'))
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 8 }))

    const { wrapper } = await mountEditor('/admin/articles/new')

    expect(wrapper.text()).toContain('系列列表加载失败')
    await wrapper.get('input[aria-label="文章标题"]').setValue('标题')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('标签联想列表加载失败时静默降级，不打断编辑（仍可手动输入）', async () => {
    vi.spyOn(tagApi, 'list').mockRejectedValue(new ApiError('boom', 500, 'internal_error'))
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 8 }))

    const { wrapper } = await mountEditor('/admin/articles/new')

    expect(wrapper.findAll('datalist option')).toHaveLength(0)
    await wrapper.get('input[aria-label="添加标签"]').setValue('手输的标签')
    await wrapper.get('input[aria-label="添加标签"]').trigger('keydown', { key: 'Enter' })
    await wrapper.get('input[aria-label="文章标题"]').setValue('标题')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ tags: ['手输的标签'] }))
  })
})

describe('ArticleEditView · 保存', () => {
  it('新建页「保存草稿」走 create，成功后把地址换成编辑态且不重新拉取详情', async () => {
    const create = vi
      .spyOn(articleApi, 'create')
      .mockResolvedValue(makeDetail({ id: 42, slug: 'brand-new' }))
    const detail = vi.spyOn(articleApi, 'detail')
    const update = vi.spyOn(articleApi, 'update')

    const { wrapper, router } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('  新文章  ')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith(expect.objectContaining({ title: '新文章', status: 'draft' }))
    expect(update).not.toHaveBeenCalled()
    expect(router.currentRoute.value.fullPath).toBe('/admin/articles/42/edit')
    // 「我自己刚 replace 出来的 id」不能触发重新拉取：表单里已经是刚保存的内容，
    // 重拉既白跑一趟，还可能因为草稿时间戳比对弹出无意义的「恢复草稿」
    expect(detail).not.toHaveBeenCalled()
    // slug 回填：不回填的话下次保存又会用旧的（可能是空的）
    expect(wrapper.text()).toContain('/article/brand-new')
    expect(lastToastMessage()).toBe('草稿已保存')
  })

  it('新建页「发布」后状态与按钮文案一起切到已发布（作者能确认到底发出去没有）', async () => {
    vi.spyOn(articleApi, 'create').mockResolvedValue(
      makeDetail({
        id: 42,
        slug: 'published-one',
        status: 'published',
        published_at: '2026-09-20T01:00:00Z',
      }),
    )

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('要发布的文章')
    expect(button(wrapper, '发布').exists()).toBe(true)

    await button(wrapper, '发布').trigger('click')
    await flushPromises()

    expect(statusSelect(wrapper).element.value).toBe('published')
    // 按钮文案由 form.status 驱动：不回填就会一直显示「发布」，
    // 作者以为没发出去，很可能再点一次或跑去草稿箱里找
    expect(button(wrapper, '更新').exists()).toBe(true)
    // published_at 同理且必须以后端返回值为准：留空提交时后端会补上「现在」
    expect(inputValue(wrapper, '发布时间（留空表示立即发布）')).not.toBe('')
    expect(lastToastMessage()).toBe('文章已发布')
  })

  it('编辑页保存走 update（带 id），绝不走 create', async () => {
    const update = vi
      .spyOn(articleApi, 'update')
      .mockResolvedValue(makeDetail({ id: 7, slug: 'server-slug' }))
    const create = vi.spyOn(articleApi, 'create')

    const { wrapper } = await mountEditor('/admin/articles/7/edit')
    await wrapper.get('input[aria-label="文章标题"]').setValue('改过的标题')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
    expect(update).toHaveBeenCalledWith(7, expect.objectContaining({ title: '改过的标题' }))
  })

  it('payload 清洗：标题去首尾空白，选填项的空串一律转成 null', async () => {
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 42 }))

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('  带空格的标题  ')
    await wrapper.get('input[aria-label="URL 别名"]').setValue('   ')
    await wrapper.get('textarea[aria-label="文章摘要"]').setValue('   ')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith({
      title: '带空格的标题',
      // 传 null 而不是空串：后端靠 null 走「按标题自动生成 slug」分支
      slug: null,
      summary: null,
      content_md: '',
      cover_image: null,
      status: 'draft',
      // 空串必须显式传 null，作者才能取消一个已设定的排期时间
      published_at: null,
      is_top: false,
      allow_comment: true,
      category_id: null,
      series_id: null,
      series_order: 0,
      tags: [],
    })
  })

  it('标题为空时一个请求都不发，错误贴在标题下方', async () => {
    const create = vi.spyOn(articleApi, 'create')

    const { wrapper } = await mountEditor('/admin/articles/new')
    await button(wrapper, '发布').trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('标题不能为空')
    expect(lastToastMessage()).toBe('请先填写标题')
  })

  it('改动标题后旧的字段级错误立刻消失（否则红字会一直挂着）', async () => {
    vi.spyOn(articleApi, 'create').mockRejectedValue(
      new ApiError('校验失败', 422, 'validation_error', { title: '标题重复' }),
    )

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('重复的标题')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('标题重复')

    await wrapper.get('input[aria-label="文章标题"]').setValue('另一个标题')
    expect(wrapper.text()).not.toContain('标题重复')
  })

  it('422 字段级错误：字段旁与顶部提示条同时出现，且按钮解禁、不跳转', async () => {
    vi.spyOn(articleApi, 'create').mockRejectedValue(
      new ApiError('提交的内容有误', 422, 'validation_error', {
        title: '标题重复',
        content_md: '正文太短',
      }),
    )

    const { wrapper, router } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('重复的标题')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    // 字段级错误贴到输入框旁，整体原因留在顶部——两个位置都有用，缺一不可
    expect(wrapper.text()).toContain('标题重复')
    expect(wrapper.text()).toContain('正文太短')
    expect(wrapper.text()).toContain('提交的内容有误')
    // 保存失败后必须解禁，否则用户改完也没法再提交
    expect(button(wrapper, '保存草稿').attributes('disabled')).toBeUndefined()
    expect(button(wrapper, '发布').attributes('disabled')).toBeUndefined()
    expect(router.currentRoute.value.fullPath).toBe('/admin/articles/new')
  })

  it('500 时顶部提示条显示后端文案，用户已输入的内容不丢', async () => {
    vi.spyOn(articleApi, 'create').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('辛苦写的标题')
    await wrapper.get('textarea[aria-label="正文（Markdown）"]').setValue('辛苦写的正文')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    expect(inputValue(wrapper, '文章标题')).toBe('辛苦写的标题')
    expect(
      (wrapper.get('textarea[aria-label="正文（Markdown）"]').element as HTMLTextAreaElement).value,
    ).toBe('辛苦写的正文')
  })

  it('保存进行中两个提交按钮都禁用并显示「保存中…」（防重复提交）', async () => {
    const pending = deferred<ArticleDetail>()
    vi.spyOn(articleApi, 'create').mockReturnValue(pending.promise)

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('标题')
    await button(wrapper, '发布').trigger('click')
    await flushPromises()

    expect(button(wrapper, '保存中…').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '保存草稿').attributes('disabled')).toBeDefined()

    pending.resolve(makeDetail({ id: 42 }))
    await flushPromises()
    expect(button(wrapper, '更新').attributes('disabled')).toBeUndefined()
    expect(button(wrapper, '保存草稿').attributes('disabled')).toBeUndefined()
  })

  it('边界：超长标题不被截断地提交，摘要与标题仍受 maxlength 约束', async () => {
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 42 }))
    const longTitle = '标'.repeat(200)

    const { wrapper } = await mountEditor('/admin/articles/new')
    // 长度上限交给原生 maxlength，前端不再自己截断（截断会造成「我明明写了」的困惑）
    expect(wrapper.get('input[aria-label="文章标题"]').attributes('maxlength')).toBe('200')
    expect(wrapper.get('textarea[aria-label="文章摘要"]').attributes('maxlength')).toBe('500')

    await wrapper.get('input[aria-label="文章标题"]').setValue(longTitle)
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ title: longTitle }))
  })
})

describe('ArticleEditView · 本地草稿', () => {
  it('新建页发现本地草稿时给出恢复横幅，点「恢复草稿」把内容填回表单', async () => {
    seedDraft(
      'article:new',
      makeDraftPayload({ title: '草稿里的标题', content_md: '草稿里的正文', tags: ['vue'] }),
    )

    const { wrapper } = await mountEditor('/admin/articles/new')
    expect(wrapper.text()).toContain('发现本地未保存的草稿')

    await button(wrapper, '恢复草稿').trigger('click')
    await flushPromises()

    expect(inputValue(wrapper, '文章标题')).toBe('草稿里的标题')
    expect(
      (wrapper.get('textarea[aria-label="正文（Markdown）"]').element as HTMLTextAreaElement).value,
    ).toBe('草稿里的正文')
    expect(wrapper.findAll('button[aria-label^="移除标签"]')).toHaveLength(1)
    // 横幅必须收掉，否则用户会以为还没恢复
    expect(wrapper.text()).not.toContain('发现本地未保存的草稿')
    expect(lastToastMessage()).toBe('已恢复本地草稿')
  })

  it('点「放弃」清掉本地草稿，表单保持空白（下次进来不再被问一次）', async () => {
    seedDraft('article:new', makeDraftPayload({ title: '不要了的草稿' }))

    const { wrapper } = await mountEditor('/admin/articles/new')
    await button(wrapper, '放弃').trigger('click')
    await flushPromises()

    expect(window.localStorage.getItem('article:new')).toBeNull()
    expect(wrapper.text()).not.toContain('发现本地未保存的草稿')
    expect(inputValue(wrapper, '文章标题')).toBe('')
    expect(lastToastMessage()).toBe('已放弃本地草稿')
  })

  it('编辑页：本地草稿比服务端新时才提示恢复', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeDetail({ updated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString() }),
    )
    seedDraft('article:7', makeDraftPayload({ title: '比服务端新的草稿' }))

    const { wrapper } = await mountEditor('/admin/articles/7/edit')
    expect(wrapper.text()).toContain('发现本地未保存的草稿')
  })

  it('编辑页：本地草稿比服务端旧时静默清掉，不打扰刚保存过的用户', async () => {
    vi.spyOn(articleApi, 'detail').mockResolvedValue(
      makeDetail({ updated_at: new Date(Date.now() + 60 * 60 * 1000).toISOString() }),
    )
    seedDraft('article:7', makeDraftPayload({ title: '过期的草稿' }))

    const { wrapper } = await mountEditor('/admin/articles/7/edit')

    expect(wrapper.text()).not.toContain('发现本地未保存的草稿')
    expect(window.localStorage.getItem('article:7')).toBeNull()
    expect(inputValue(wrapper, '文章标题')).toBe('服务端标题')
  })

  it('保存成功后清掉本地快照（留着只会在下次打开时误报「有未保存内容」）', async () => {
    vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 42, slug: 'saved' }))

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('会被保存的标题')
    flushDraft()
    expect(window.localStorage.getItem('article:new')).not.toBeNull()

    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(window.localStorage.getItem('article:new')).toBeNull()
  })

  it('保存失败时保留本地快照（内容必须还在，用户能刷新找回）', async () => {
    vi.spyOn(articleApi, 'create').mockRejectedValue(new ApiError('服务器开小差', 500, 'internal_error'))

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('没能保存的标题')
    flushDraft()
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(window.localStorage.getItem('article:new')).not.toBeNull()
  })
})

describe('ArticleEditView · 发布设置', () => {
  it('发布时间在「本地时间控件」与「ISO 接口值」之间双向转换', async () => {
    const create = vi
      .spyOn(articleApi, 'create')
      .mockImplementation(async (payload) =>
        makeDetail({ id: 42, slug: 's', published_at: payload.published_at ?? null }),
      )

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('定时发布')
    await wrapper.get('input[aria-label="发布时间（留空表示立即发布）"]').setValue('2026-09-20T09:00')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    // 控件给的是本地时间，接口要的是 ISO：这里按本地时区解释后转 UTC
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ published_at: new Date('2026-09-20T09:00').toISOString() }),
    )
    // 再转回来时仍然是作者填的那个本地时刻（跨时区不漂移）
    expect(inputValue(wrapper, '发布时间（留空表示立即发布）')).toBe('2026-09-20T09:00')
  })

  it('填未来时间且状态为已发布时给出「到点前访客看不到」的说明', async () => {
    const { wrapper } = await mountEditor('/admin/articles/new')
    expect(wrapper.text()).toContain('留空表示立即发布；填未来时间即为定时发布。')

    await statusSelect(wrapper).setValue('published')
    await wrapper.get('input[aria-label="发布时间（留空表示立即发布）"]').setValue('2099-01-01T09:00')
    expect(wrapper.text()).toContain('到点前访客看不到这篇文章')

    // 清空排期后又回到「立即发布」的说明
    await wrapper.get('input[aria-label="发布时间（留空表示立即发布）"]').setValue('')
    expect(wrapper.text()).toContain('留空表示立即发布；填未来时间即为定时发布。')
  })

  it('置顶与允许评论两个开关写进 payload', async () => {
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 42 }))

    const { wrapper } = await mountEditor('/admin/articles/new')
    await wrapper.get('input[aria-label="文章标题"]').setValue('标题')
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    await checkboxes[0]?.setValue(true) // 置顶
    await checkboxes[1]?.setValue(false) // 允许评论
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith(expect.objectContaining({ is_top: true, allow_comment: false }))
  })

  it('未挂系列时「系列内顺序」禁用，选了系列才解禁', async () => {
    const { wrapper } = await mountEditor('/admin/articles/new')
    const order = wrapper.get('input[type="number"]')
    expect(order.attributes('disabled')).toBeDefined()

    await wrapper.get('select[aria-label="所属系列"]').setValue('5')
    expect(order.attributes('disabled')).toBeUndefined()
  })

  it('封面上传成功后回填地址、出现预览，并可从 payload 清掉', async () => {
    const upload = vi
      .spyOn(attachmentApi, 'upload')
      .mockResolvedValue(makeAttachment({ url: '/media/new-cover.png' }))
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 42 }))

    const { wrapper } = await mountEditor('/admin/articles/new')
    expect(wrapper.find('img[alt="封面预览"]').exists()).toBe(false)

    const picker = wrapper.get('input[aria-label="上传封面图"]')
    Object.defineProperty(picker.element, 'files', {
      value: [new File(['x'], 'cover.png', { type: 'image/png' })],
      configurable: true,
    })
    await picker.trigger('change')
    await flushPromises()

    expect(upload).toHaveBeenCalledTimes(1)
    expect(wrapper.get('img[alt="封面预览"]').attributes('src')).toBe('/media/new-cover.png')
    expect(button(wrapper, '更换封面').exists()).toBe(true)

    await wrapper.get('input[aria-label="文章标题"]').setValue('带封面的文章')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ cover_image: '/media/new-cover.png' }),
    )
  })

  it('点「移除」清掉封面预览，并让 payload 里的封面变回 null', async () => {
    vi.spyOn(attachmentApi, 'upload').mockResolvedValue(makeAttachment({ url: '/media/tmp.png' }))
    const create = vi.spyOn(articleApi, 'create').mockResolvedValue(makeDetail({ id: 42 }))

    const { wrapper } = await mountEditor('/admin/articles/new')
    const picker = wrapper.get('input[aria-label="上传封面图"]')
    Object.defineProperty(picker.element, 'files', {
      value: [new File(['x'], 'c.png', { type: 'image/png' })],
      configurable: true,
    })
    await picker.trigger('change')
    await flushPromises()
    expect(wrapper.find('img[alt="封面预览"]').exists()).toBe(true)

    await button(wrapper, '移除').trigger('click')
    // 预览必须跟着消失：留着会让作者以为封面还在
    expect(wrapper.find('img[alt="封面预览"]').exists()).toBe(false)
    expect(button(wrapper, '上传封面').exists()).toBe(true)

    await wrapper.get('input[aria-label="文章标题"]').setValue('标题')
    await button(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ cover_image: null }))
  })

  it('上传失败时不回填封面，而是给出错误提示', async () => {
    vi.spyOn(attachmentApi, 'upload').mockRejectedValue(new ApiError('文件类型不支持', 415, 'bad_type'))

    const { wrapper } = await mountEditor('/admin/articles/new')
    const picker = wrapper.get('input[aria-label="上传封面图"]')
    Object.defineProperty(picker.element, 'files', {
      value: [new File(['x'], 'bad.txt', { type: 'text/plain' })],
      configurable: true,
    })
    await picker.trigger('change')
    await flushPromises()

    expect(wrapper.find('img[alt="封面预览"]').exists()).toBe(false)
    expect(lastToastMessage()).toBe('文件类型不支持')
    expect(lastToastKind()).toBe('error')
  })

  it('超过 5MB 的封面在发请求之前就被拦下', async () => {
    const upload = vi.spyOn(attachmentApi, 'upload')
    const { wrapper } = await mountEditor('/admin/articles/new')

    const huge = new File(['x'], 'huge.png', { type: 'image/png' })
    Object.defineProperty(huge, 'size', { value: 6 * 1024 * 1024 })
    const picker = wrapper.get('input[aria-label="上传封面图"]')
    Object.defineProperty(picker.element, 'files', { value: [huge], configurable: true })
    await picker.trigger('change')
    await flushPromises()

    // 客户端预检的意义就是省掉一次注定失败的上传（5MB 在慢网络上要等很久）
    expect(upload).not.toHaveBeenCalled()
    expect(lastToastMessage()).toContain('图片过大')
  })
})

describe('ArticleEditView · 标签输入', () => {
  it('回车添加标签、重复的静默忽略、点 × 可移除', async () => {
    const { wrapper } = await mountEditor('/admin/articles/new')
    const input = wrapper.get('input[aria-label="添加标签"]')

    await input.setValue('vue')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.findAll('button[aria-label^="移除标签"]')).toHaveLength(1)
    // 添加后输入框要清空，否则用户会以为没生效、再敲一次回车
    expect(inputValue(wrapper, '添加标签')).toBe('')
    expect(wrapper.text()).toContain('1/10')

    await input.setValue('vue')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.findAll('button[aria-label^="移除标签"]')).toHaveLength(1)

    await wrapper.get('button[aria-label="移除标签 vue"]').trigger('click')
    expect(wrapper.findAll('button[aria-label^="移除标签"]')).toHaveLength(0)
  })

  it('标签数量封顶 10 个，第 11 个被拦下并提示（与后端上限一致）', async () => {
    const { wrapper } = await mountEditor('/admin/articles/new')
    const input = wrapper.get('input[aria-label="添加标签"]')

    for (let index = 0; index < 10; index += 1) {
      await input.setValue(`tag-${index}`)
      await input.trigger('keydown', { key: 'Enter' })
    }
    expect(wrapper.text()).toContain('10/10')

    await input.setValue('tag-10')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.findAll('button[aria-label^="移除标签"]')).toHaveLength(10)
    expect(lastToastMessage()).toBe('最多 10 个标签')
  })
})

describe('ArticleEditView · 版本历史', () => {
  it('新建页没有历史入口，也不请求版本列表', async () => {
    const list = vi.spyOn(revisionApi, 'list')
    const { wrapper } = await mountEditor('/admin/articles/new')

    expect(wrapper.findComponent(RevisionHistory).exists()).toBe(false)
    expect(list).not.toHaveBeenCalled()
  })

  it('编辑页点「历史」才挂载面板并拉取版本（不点就一次请求都不发）', async () => {
    const list = vi.spyOn(revisionApi, 'list').mockResolvedValue([])
    const { wrapper } = await mountEditor('/admin/articles/7/edit')

    expect(list).not.toHaveBeenCalled()

    await button(wrapper, '历史').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalledWith(7)
    expect(wrapper.findComponent(RevisionHistory).exists()).toBe(true)

    await button(wrapper, '隐藏历史').trigger('click')
    expect(wrapper.findComponent(RevisionHistory).exists()).toBe(false)
  })

  it('从版本历史恢复后回填表单，并清掉已不一致的本地草稿', async () => {
    const { wrapper } = await mountEditor('/admin/articles/7/edit')
    await button(wrapper, '历史').trigger('click')
    await flushPromises()

    // 先制造一份本地草稿，证明恢复之后它会被清掉
    await wrapper.get('input[aria-label="文章标题"]').setValue('本地改了一半')
    flushDraft()
    expect(window.localStorage.getItem('article:7')).not.toBeNull()

    wrapper.findComponent(RevisionHistory).vm.$emit(
      'restored',
      makeDetail({ title: '恢复出来的标题', content_md: '恢复出来的正文', slug: 'restored' }),
    )
    await flushPromises()

    expect(inputValue(wrapper, '文章标题')).toBe('恢复出来的标题')
    expect(
      (wrapper.get('textarea[aria-label="正文（Markdown）"]').element as HTMLTextAreaElement).value,
    ).toBe('恢复出来的正文')
    // 恢复后的内容与本地快照已经不一致，留着会在下次进来时误报「有未保存草稿」
    expect(window.localStorage.getItem('article:7')).toBeNull()
  })
})

describe('ArticleEditView · 路由变化', () => {
  it('路由里的 id 变了就重新拉取并覆盖表单（组件实例被复用，不能停在上一篇）', async () => {
    const detail = vi
      .spyOn(articleApi, 'detail')
      .mockImplementation(async (id) => makeDetail({ id: Number(id), title: `第 ${id} 篇` }))

    const { wrapper, router } = await mountEditor('/admin/articles/1/edit')
    expect(inputValue(wrapper, '文章标题')).toBe('第 1 篇')

    await router.push('/admin/articles/2/edit')
    await flushPromises()

    expect(detail).toHaveBeenCalledWith(2)
    // 只绑 onMounted 的话这里会静静显示「第 1 篇」，而保存会 PATCH 到 id=2 上
    expect(inputValue(wrapper, '文章标题')).toBe('第 2 篇')
  })

  it('切到新建页时草稿分桶跟着切换（不会把编辑中的内容写进新建桶）', async () => {
    const { wrapper, router } = await mountEditor('/admin/articles/7/edit')
    await wrapper.get('input[aria-label="文章标题"]').setValue('第七篇的改动')
    flushDraft()
    expect(window.localStorage.getItem('article:7')).not.toBeNull()

    await router.push('/admin/articles/new')
    await flushPromises()
    await wrapper.get('input[aria-label="文章标题"]').setValue('新文章的内容')
    flushDraft()

    // 两个桶各存各的，互不覆盖
    expect(JSON.parse(window.localStorage.getItem('article:7') as string).payload.title).toBe(
      '第七篇的改动',
    )
    expect(JSON.parse(window.localStorage.getItem('article:new') as string).payload.title).toBe(
      '新文章的内容',
    )
  })

  it('迟到的详情响应不会覆盖当前表单（乱序返回 = 静默数据丢失）', async () => {
    // 两篇各自一个可控 promise：让**先发**的那篇后返回
    const first = deferred<ArticleDetail>()
    const second = deferred<ArticleDetail>()
    vi.spyOn(articleApi, 'detail').mockImplementation((id) =>
      Number(id) === 1 ? first.promise : second.promise,
    )

    const { wrapper, router } = await mountEditor('/admin/articles/1/edit')

    // 还没等第一篇回来就切到第二篇（地址栏改 id / 前进后退都是这条路径）
    await router.push('/admin/articles/2/edit')

    // 第二篇先到：表单这会儿是正确的
    second.resolve(makeDetail({ id: 2, title: '第 2 篇' }))
    await flushPromises()
    expect(inputValue(wrapper, '文章标题')).toBe('第 2 篇')

    // 第一篇**后**到：没有代次守卫时，这里会把表单改回「第 1 篇」，
    // 而保存按钮 PATCH 的是 id=2 —— 用户以为在改第二篇，实际把第一篇的内容写了进去
    first.resolve(makeDetail({ id: 1, title: '第 1 篇' }))
    await flushPromises()
    expect(inputValue(wrapper, '文章标题')).toBe('第 2 篇')
  })

  it('迟到的失败响应不会把当前文章标成「加载失败」', async () => {
    const first = deferred<ArticleDetail>()
    const second = deferred<ArticleDetail>()
    vi.spyOn(articleApi, 'detail').mockImplementation((id) =>
      Number(id) === 1 ? first.promise : second.promise,
    )

    const { wrapper, router } = await mountEditor('/admin/articles/1/edit')
    await router.push('/admin/articles/2/edit')

    second.resolve(makeDetail({ id: 2, title: '第 2 篇' }))
    await flushPromises()

    // 第一篇的请求最后**失败了**：它属于已经被换掉的那次加载，
    // 不该把已经正常渲染的第二篇打成"加载失败"
    first.reject(new Error('第一篇炸了'))
    await flushPromises()

    expect(inputValue(wrapper, '文章标题')).toBe('第 2 篇')
    expect(wrapper.text()).not.toContain('第一篇炸了')
  })

  it('从加载失败的 A 切到正常的 B 时，旧的错误提示条会消失', async () => {
    const detail = vi.spyOn(articleApi, 'detail').mockImplementation(async (id) => {
      if (Number(id) === 1) throw new Error('A 篇加载失败')
      return makeDetail({ id: 2, title: '第 2 篇' })
    })

    const { wrapper, router } = await mountEditor('/admin/articles/1/edit')
    // 错误文案取自异常本身（toErrorMessage 优先用后端给的可读消息）
    expect(wrapper.text()).toContain('A 篇加载失败')

    await router.push('/admin/articles/2/edit')
    await flushPromises()

    expect(detail).toHaveBeenCalledWith(2)
    // 重新加载时必须先清掉上一次的错误，否则红色提示条会一直挂在一篇正常文章上
    expect(wrapper.text()).not.toContain('A 篇加载失败')
    expect(inputValue(wrapper, '文章标题')).toBe('第 2 篇')
  })
})
