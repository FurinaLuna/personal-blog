/**
 * MediaView 测试（后台媒体库）。
 *
 * 这个页面的特点：列表、上传、复制、运维回填、删除五条路都挂在同一屏上，
 * 而且上传是**批量且允许部分失败**的。守的契约：
 *
 * 1. **请求参数**：`attachmentApi.list(page, pageSize, kind)` 是位置参数（不同于其它列表
 *    接口的对象参数），切筛选必须把页码归零，否则会停在一个不存在的页上；
 * 2. **上传的部分失败语义**：逐文件提示失败原因，只有成功数 > 0 才汇总「成功上传 N 个」；
 *    全部失败时既不能弹成功提示、也不该白刷一次列表；
 * 3. **上传进行中**：按钮禁用 + 文案换成「上传中…」，进度条按 onProgress 更新
 *    （大图在慢上行要等几十秒，没有进度等于让用户干等）；
 * 4. **删除要么确认后成功、要么原样不动**：确认前不发请求，取消什么都不做，
 *    失败保留确认框且不刷新列表（不许出现「界面已删、服务端还在」）；
 * 5. **复制 Markdown 必须降级**：非 HTTPS 下 clipboard 不可用是常态，不能把异常抛出去；
 * 6. **回填变体是站长专属运维动作**：updated 为 0 时说「没有需要回填的图片」且不刷列表，
 *    失败走兜底文案，进行中按钮禁用；
 * 7. **失败态与空态分得开**：接口挂了显示错误文案，不能报成「媒体库是空的」。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法、以及 navigator.clipboard），
 * 与 ArticleEditView.spec.ts / ArticleListView.spec.ts 的写法一致。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, attachmentApi, http } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { Attachment, Page, User } from '@/types'

import MediaView from './MediaView.vue'

const PAGE_SIZE = 24

/* ------------------------------------------------------------ 测试数据 */

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
    thumbnail_url: '/media/cover-thumb.png',
    variants: [],
    created_at: '2026-01-01T00:00:00Z',
    markdown: '![cover](/media/cover.png)',
    ...overrides,
  }
}

function makePage(items: Attachment[], total = items.length, page = 1): Page<Attachment> {
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

const writeText = vi.fn()

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

function makeFile(name: string, type = 'image/png'): File {
  return new File(['x'], name, { type })
}

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }
let pinia: Pinia
/** 原始适配器：删除用例会临时替换它，让真实 axios 产生后端那种响应形态。 */
const originalAdapter = http.defaults.adapter

/**
 * 用真实 axios 适配器模拟「200 + JSON 消息体」（`DELETE /attachments/{id}` 的真机形态），
 * 并记录实际发出的请求。
 *
 * 为什么不 spy `attachmentApi.remove`：它的类型签名是 `Promise<void>`，而真机上
 * axios 拿到的是 JSON 对象 —— 打桩时按哪一边写都在定义一个与另一半不一致的契约
 * （这正是 ArticleListView.spec.ts 里「undefined 哨兵」那条隐患用例的由来）。
 * 走真实 axios 就没有这层歧义。传 gate 可以让请求停在半路，观察中间态。
 */
function stubDeleteAdapter(gate?: Promise<unknown>): string[] {
  const calls: string[] = []
  http.defaults.adapter = (async (config: InternalAxiosRequestConfig) => {
    calls.push(`${(config.method ?? 'get').toUpperCase()} ${config.url ?? ''}`)
    if (gate) await gate
    return {
      data: { detail: '附件已删除' },
      status: 200,
      statusText: 'OK',
      headers: new AxiosHeaders(),
      config,
    }
  }) as unknown as AxiosAdapter
  return calls
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/media', component: Blank },
      { path: '/admin/articles/new', component: Blank },
    ],
  })
}

async function mountMedia(initial = '/admin/media', options: { asAdmin?: boolean } = {}) {
  const router = makeRouter()
  await router.push(initial)
  await router.isReady()
  const auth = useAuthStore()
  auth.user = options.asAdmin ? ADMIN : AUTHOR
  const wrapper = mount(MediaView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, auth }
}

/** 列表接口按请求里的 page 回包，这样分页器读到的页码与发出的请求才一致。 */
function stubList(items: Attachment[], total = items.length) {
  return vi
    .spyOn(attachmentApi, 'list')
    .mockImplementation(async (page = 1) => makePage(items, total, page))
}

function cardByName(wrapper: VueWrapper, name: string): DOMWrapper<Element> {
  const found = wrapper.findAll('li').find((item) => item.text().includes(name))
  if (!found) throw new Error(`找不到文件名为「${name}」的卡片`)
  return found
}

function cardButton(card: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = card.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`卡片内找不到文案为「${label}」的按钮`)
  return found
}

/** 按文案取按钮：模板里同一屏有十几个 button，按 class 取会随样式改动而失效。 */
function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

function kindButton(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到筛选按钮「${label}」`)
  return found
}

/** 选择本地文件触发 change（jsdom 不允许直接给 file input 赋值）。 */
async function pickFiles(wrapper: VueWrapper, files: File[]): Promise<void> {
  const picker = wrapper.get('input[aria-label="选择要上传的文件"]')
  Object.defineProperty(picker.element, 'files', { value: files, configurable: true })
  await picker.trigger('change')
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

function toastMessages(): string[] {
  return useToast().items.value.map((item) => item.message)
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
  stubList([makeAttachment()])
  writeText.mockReset()
  writeText.mockResolvedValue(undefined)
  // jsdom 没实现 clipboard，测试里自己装一个可控的实现
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
  Reflect.deleteProperty(navigator, 'clipboard')
  http.defaults.adapter = originalAdapter
})

/* ------------------------------------------------------------ 用例 */

describe('MediaView · 加载 / 失败 / 空态', () => {
  it('首屏加载中显示骨架屏，而不是「媒体库是空的」', async () => {
    const pending = deferred<Page<Attachment>>()
    vi.spyOn(attachmentApi, 'list').mockReturnValue(pending.promise)

    const { wrapper } = await mountMedia()

    expect(wrapper.findAll('.skeleton')).toHaveLength(8)
    // 加载期间报「空的」会让作者以为文件都丢了
    expect(wrapper.text()).not.toContain('媒体库是空的')

    pending.resolve(makePage([makeAttachment({ original_name: '加载出来的.png' })]))
    await flushPromises()

    expect(wrapper.text()).toContain('加载出来的.png')
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('加载失败显示后端文案，且不会错报成「媒体库是空的」', async () => {
    vi.spyOn(attachmentApi, 'list').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountMedia()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(wrapper.findAll('li')).toHaveLength(0)
  })

  it('加载失败后切换筛选能重新拉取并恢复', async () => {
    const list = vi
      .spyOn(attachmentApi, 'list')
      .mockRejectedValueOnce(new ApiError('服务器开小差了', 500, 'internal_error'))
      .mockResolvedValueOnce(makePage([makeAttachment({ original_name: '恢复.png' })]))

    const { wrapper } = await mountMedia()
    expect(wrapper.text()).toContain('服务器开小差了')

    await kindButton(wrapper, '图片').trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('恢复.png')
    expect(wrapper.text()).not.toContain('服务器开小差了')
  })

  it('媒体库为空：显示空态与「上传文件」出口，且不渲染分页器', async () => {
    stubList([], 0)

    const { wrapper } = await mountMedia()

    expect(wrapper.text()).toContain('媒体库是空的')
    expect(wrapper.findComponent(Pagination).exists()).toBe(false)
    expect(wrapper.text()).toContain('共 0 个')
  })

  it('空态里的「上传文件」真的会去点隐藏的文件选择框（否则用户没有下一步）', async () => {
    stubList([], 0)
    const clickPicker = vi.spyOn(HTMLInputElement.prototype, 'click')

    const { wrapper } = await mountMedia()
    await wrapper.getComponent(EmptyState).get('button').trigger('click')

    expect(clickPicker).toHaveBeenCalledTimes(1)
  })
})

describe('MediaView · 列表与筛选', () => {
  it('首屏用 list(page, pageSize, kind) 拉第一页全部类型', async () => {
    const list = stubList([makeAttachment()])

    await mountMedia()

    // 位置参数：与其它列表接口的对象参数不同，改错顺序会静默拉错数据
    expect(list).toHaveBeenCalledWith(1, PAGE_SIZE, undefined)
  })

  it('切到「图片」/「附件」时带上 kind 并把页码归零', async () => {
    const list = stubList([makeAttachment()], 60)

    const { wrapper } = await mountMedia()
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(2, PAGE_SIZE, undefined)

    // 在第 2 页切筛选：必须先回到第 1 页，否则「图片」可能只有 1 页而停在空页上
    await kindButton(wrapper, '图片').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(1, PAGE_SIZE, 'image')

    await kindButton(wrapper, '附件').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(1, PAGE_SIZE, 'file')

    await kindButton(wrapper, '全部').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(1, PAGE_SIZE, undefined)
  })

  it('当前筛选按钮有选中态（用户得知道自己在看哪一类）', async () => {
    const { wrapper } = await mountMedia()

    expect(kindButton(wrapper, '全部').classes()).toContain('font-medium')
    expect(kindButton(wrapper, '图片').classes()).not.toContain('font-medium')

    await kindButton(wrapper, '图片').trigger('click')
    await flushPromises()

    expect(kindButton(wrapper, '图片').classes()).toContain('font-medium')
    expect(kindButton(wrapper, '全部').classes()).not.toContain('font-medium')
  })

  it('总数与分页器跟随接口返回值', async () => {
    stubList([makeAttachment()], 50)

    const { wrapper } = await mountMedia()

    expect(wrapper.text()).toContain('共 50 个')
    const pagination = wrapper.getComponent(Pagination)
    expect(pagination.props('total')).toBe(50)
    expect(pagination.props('pageSize')).toBe(PAGE_SIZE)
  })

  it('图片卡片优先用缩略图，附件卡片不渲染 img（否则就是一堆碎图）', async () => {
    stubList([
      makeAttachment({ id: 1, original_name: '图.png' }),
      makeAttachment({
        id: 2,
        original_name: '文档.pdf',
        kind: 'file',
        mime_type: 'application/pdf',
        thumbnail_url: null,
        variants: [],
      }),
    ])

    const { wrapper } = await mountMedia()

    expect(cardByName(wrapper, '图.png').get('img').attributes('src')).toBe('/media/cover-thumb.png')
    expect(cardByName(wrapper, '图.png').get('img').attributes('alt')).toBe('图.png')
    expect(cardByName(wrapper, '文档.pdf').find('img').exists()).toBe(false)
  })

  it('没有缩略图时退回原图；变体宽度与尺寸文案都亮出来', async () => {
    stubList([
      makeAttachment({
        id: 5,
        original_name: '大图.png',
        thumbnail_url: null,
        size: 2 * 1024 * 1024,
        variants: [
          { width: 480, url: '/media/a-480.png' },
          { width: 800, url: '/media/a-800.png' },
        ],
      }),
      makeAttachment({ id: 6, original_name: '小图.png', variants: [] }),
    ])

    const { wrapper } = await mountMedia()
    const big = cardByName(wrapper, '大图.png')

    expect(big.get('img').attributes('src')).toBe('/media/cover.png')
    expect(big.text()).toContain('2.00 MB')
    expect(big.text()).toContain('1600×900')
    expect(big.text()).toContain('变体 480/800')
    // 没有变体要说清「无变体」，而不是留白让人以为界面坏了
    expect(cardByName(wrapper, '小图.png').text()).toContain('无变体')
  })

  it('「查看」用新窗口打开原文件地址（不覆盖后台页面）', async () => {
    stubList([makeAttachment({ url: '/media/full.png' })])

    const { wrapper } = await mountMedia()
    const link = cardByName(wrapper, 'cover.png').get('a')

    expect(link.attributes('href')).toBe('/media/full.png')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
  })
})

describe('MediaView · 上传', () => {
  it('选择文件后逐个上传；成功后汇总「成功上传 N 个文件」并刷新列表', async () => {
    const list = stubList([makeAttachment()])
    const upload = vi
      .spyOn(attachmentApi, 'upload')
      .mockImplementation(async (file) => makeAttachment({ original_name: file.name }))

    const { wrapper } = await mountMedia()
    const callsBefore = list.mock.calls.length

    await pickFiles(wrapper, [makeFile('a.png'), makeFile('b.png')])
    await flushPromises()

    expect(upload).toHaveBeenCalledTimes(2)
    expect(upload).toHaveBeenCalledWith(expect.any(File), expect.any(Function))
    // 批量场景刻意不逐个弹「上传成功」，由外层汇总一次
    expect(toastMessages()).not.toContain('上传成功')
    expect(lastToastMessage()).toBe('成功上传 2 个文件')
    expect(list.mock.calls.length).toBe(callsBefore + 1)
  })

  it('批量里部分失败：失败的那个有自己的原因，成功的仍然汇总并刷新', async () => {
    const list = stubList([makeAttachment()])
    const upload = vi.spyOn(attachmentApi, 'upload').mockImplementation(async (file) => {
      if (file.name === 'bad.txt') throw new ApiError('文件类型不支持', 415, 'bad_type')
      return makeAttachment({ original_name: file.name })
    })

    const { wrapper } = await mountMedia()
    const callsBefore = list.mock.calls.length

    await pickFiles(wrapper, [makeFile('ok.png'), makeFile('bad.txt')])
    await flushPromises()

    expect(upload).toHaveBeenCalledTimes(2)
    // 逐文件汇报，作者才知道是哪一个坏了
    expect(toastMessages()).toContain('文件类型不支持')
    expect(lastToastMessage()).toBe('成功上传 1 个文件')
    expect(list.mock.calls.length).toBe(callsBefore + 1)
  })

  it('全部上传失败：不弹成功汇总，也不白刷一次列表', async () => {
    const list = stubList([makeAttachment()])
    vi.spyOn(attachmentApi, 'upload').mockRejectedValue(
      new ApiError('服务端拒绝了这个文件', 413, 'too_large'),
    )

    const { wrapper } = await mountMedia()
    const callsBefore = list.mock.calls.length

    await pickFiles(wrapper, [makeFile('a.png')])
    await flushPromises()

    expect(toastMessages()).toEqual(['服务端拒绝了这个文件'])
    expect(list.mock.calls.length).toBe(callsBefore)
  })

  it('上传进行中：按钮禁用并显示「上传中…」，进度条按 onProgress 更新', async () => {
    const gate = deferred<void>()
    vi.spyOn(attachmentApi, 'upload').mockImplementation(async (_file, onProgress) => {
      onProgress?.(42)
      await gate.promise
      return makeAttachment()
    })

    const { wrapper } = await mountMedia()
    await pickFiles(wrapper, [makeFile('big.png')])
    await flushPromises()

    expect(button(wrapper, '上传中…').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('42')
    expect(wrapper.get('[role="progressbar"]').attributes('aria-label')).toBe('上传进度')

    gate.resolve()
    await flushPromises()

    // 传完必须收掉进度条并解禁，否则用户以为还在传
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(button(wrapper, '上传文件').attributes('disabled')).toBeUndefined()
    expect(lastToastMessage()).toBe('成功上传 1 个文件')
  })

  it('没有选中任何文件时不发请求（取消文件选择对话框的路径）', async () => {
    const upload = vi.spyOn(attachmentApi, 'upload')

    const { wrapper } = await mountMedia()
    await pickFiles(wrapper, [])
    await flushPromises()

    expect(upload).not.toHaveBeenCalled()
    expect(useToast().items.value).toHaveLength(0)
  })

  it('文件选择框支持多选，并限制在可上传的类型上', async () => {
    const { wrapper } = await mountMedia()
    const picker = wrapper.get('input[aria-label="选择要上传的文件"]')

    expect(picker.attributes('multiple')).toBeDefined()
    const accept = picker.attributes('accept') ?? ''
    expect(accept).toContain('image/*')
    // 后端会拒的类型不该出现在选择器里，省掉一次注定失败的往返
    expect(accept).not.toContain('exe')
  })
})

describe('MediaView · 复制 Markdown', () => {
  it('点「复制 MD」把 markdown 片段写进剪贴板并提示', async () => {
    stubList([makeAttachment({ markdown: '![cover](/media/cover.png)' })])

    const { wrapper } = await mountMedia()
    await cardButton(cardByName(wrapper, 'cover.png'), '复制 MD').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('![cover](/media/cover.png)')
    expect(lastToastMessage()).toBe('Markdown 片段已复制')
  })

  it('剪贴板不可用（非 HTTPS / 权限被拒）时降级提示，异常不外泄', async () => {
    stubList([makeAttachment()])
    writeText.mockRejectedValue(new Error('NotAllowedError'))

    const { wrapper } = await mountMedia()
    await cardButton(cardByName(wrapper, 'cover.png'), '复制 MD').trigger('click')
    await flushPromises()

    // 抛出去会变成一个没人处理的 rejection，用户那边什么提示都没有
    expect(lastToastMessage()).toBe('浏览器不允许自动复制，请手动复制链接')
    expect(lastToastKind()).toBe('error')
  })
})

describe('MediaView · 回填变体（仅站长）', () => {
  it('作者视角看不到「回填变体」，也不会发这个请求', async () => {
    const backfill = vi.spyOn(attachmentApi, 'backfillVariants')

    const { wrapper } = await mountMedia()

    expect(wrapper.findAll('button').some((item) => item.text().trim() === '回填变体')).toBe(false)
    expect(backfill).not.toHaveBeenCalled()
  })

  it('站长点「回填变体」：有生成时提示计数并刷新列表', async () => {
    const list = stubList([makeAttachment()])
    const backfill = vi
      .spyOn(attachmentApi, 'backfillVariants')
      .mockResolvedValue({ processed: 100, updated: 12, skipped: 88 })

    const { wrapper } = await mountMedia('/admin/media', { asAdmin: true })
    const callsBefore = list.mock.calls.length

    await button(wrapper, '回填变体').trigger('click')
    await flushPromises()

    expect(backfill).toHaveBeenCalledTimes(1)
    expect(lastToastMessage()).toBe('回填完成：生成 12 张，跳过 88 张，还剩待处理可再次运行')
    // 变体是新生成的，卡片上的「变体 480/800」得跟着更新
    expect(list.mock.calls.length).toBe(callsBefore + 1)
  })

  it('没有需要回填的图片时只说一句，不再刷列表', async () => {
    const list = stubList([makeAttachment()])
    vi.spyOn(attachmentApi, 'backfillVariants').mockResolvedValue({
      processed: 0,
      updated: 0,
      skipped: 0,
    })

    const { wrapper } = await mountMedia('/admin/media', { asAdmin: true })
    const callsBefore = list.mock.calls.length

    await button(wrapper, '回填变体').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('没有需要回填的图片')
    expect(list.mock.calls.length).toBe(callsBefore)
  })

  it('回填失败：提示后端文案，按钮解禁可重试', async () => {
    vi.spyOn(attachmentApi, 'backfillVariants').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountMedia('/admin/media', { asAdmin: true })
    await button(wrapper, '回填变体').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('服务器开小差了，请稍后重试')
    expect(lastToastKind()).toBe('error')
    expect(button(wrapper, '回填变体').attributes('disabled')).toBeUndefined()
  })

  it('回填进行中按钮禁用并显示「回填中…」（重复点只会白跑一遍运维任务）', async () => {
    const pending = deferred<{ processed: number; updated: number; skipped: number }>()
    vi.spyOn(attachmentApi, 'backfillVariants').mockReturnValue(pending.promise)

    const { wrapper } = await mountMedia('/admin/media', { asAdmin: true })
    await button(wrapper, '回填变体').trigger('click')
    await flushPromises()

    expect(button(wrapper, '回填中…').attributes('disabled')).toBeDefined()

    pending.resolve({ processed: 1, updated: 1, skipped: 0 })
    await flushPromises()
    expect(button(wrapper, '回填变体').attributes('disabled')).toBeUndefined()
  })
})

describe('MediaView · 删除', () => {
  it('点「删除」只打开确认框，一个请求都不发', async () => {
    const remove = vi.spyOn(attachmentApi, 'remove')

    const { wrapper } = await mountMedia()
    await cardButton(cardByName(wrapper, 'cover.png'), '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    // 文案要说清代价：磁盘文件一起没，引用它的文章会变裂图
    expect(dialog()?.textContent).toContain('文章里的图片会变成裂图')
  })

  it('取消删除：不发请求、卡片还在、确认框关掉', async () => {
    const remove = vi.spyOn(attachmentApi, 'remove')

    const { wrapper } = await mountMedia()
    await cardButton(cardByName(wrapper, 'cover.png'), '删除').trigger('click')
    await flushPromises()

    dialogButton('取消')?.click()
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(wrapper.text()).toContain('cover.png')
  })

  it('确认删除：用 id 调 remove，成功后关框、提示并刷新列表（保留当前筛选）', async () => {
    const list = vi
      .spyOn(attachmentApi, 'list')
      // 首屏 → 切到「图片」→ 删除后刷新，三次都要能对上，卡片才会一直在
      .mockResolvedValueOnce(
        makePage([makeAttachment({ id: 1, original_name: '要删的.png' })], 30, 1),
      )
      .mockResolvedValueOnce(
        makePage([makeAttachment({ id: 1, original_name: '要删的.png' })], 30, 1),
      )
      .mockResolvedValueOnce(makePage([], 29, 1))
    const calls = stubDeleteAdapter()

    const { wrapper } = await mountMedia('/admin/media')
    await kindButton(wrapper, '图片').trigger('click')
    await flushPromises()

    await cardButton(cardByName(wrapper, '要删的.png'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(calls).toEqual(['DELETE /attachments/1'])
    expect(lastToastMessage()).toBe('已删除')
    expect(list).toHaveBeenLastCalledWith(1, PAGE_SIZE, 'image')
    expect(dialog()).toBeNull()
    expect(wrapper.text()).not.toContain('要删的.png')
  })

  it('删除失败：报错、不刷新列表、确认框留着可重试（不许「界面已删、服务端还在」）', async () => {
    const list = stubList([makeAttachment({ id: 1, original_name: '删不掉的.png' })], 1)
    vi.spyOn(attachmentApi, 'remove').mockRejectedValue(
      new ApiError('该文件正在被文章引用', 409, 'conflict'),
    )

    const { wrapper } = await mountMedia()
    await cardButton(cardByName(wrapper, '删不掉的.png'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(lastToastMessage()).toBe('该文件正在被文章引用')
    expect(lastToastKind()).toBe('error')
    // 失败绝不能刷新列表：一旦刷新，卡片会消失，用户以为删成功了
    expect(list).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('删不掉的.png')
    expect(dialog()).not.toBeNull()
  })

  it('删除进行中：确认框两个按钮都禁用并显示「处理中…」（防重复提交）', async () => {
    const gate = deferred<void>()
    stubDeleteAdapter(gate.promise)

    const { wrapper } = await mountMedia()
    await cardButton(cardByName(wrapper, 'cover.png'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除')?.click()
    await flushPromises()

    expect(dialogButton('处理中…')?.disabled).toBe(true)
    expect(dialogButton('取消')?.disabled).toBe(true)

    gate.resolve()
    await flushPromises()
    expect(dialog()).toBeNull()
  })
})
