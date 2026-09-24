/**
 * GuestbookView（前台留言板）回归测试。
 *
 * 守住四类契约：
 * 1. **提交口径**：空白内容/匿名缺昵称一个请求都不发；payload 里空串转 `null`
 *    （后端把 `None` 当作"没填"，空串会被当成一个真实的空值）；
 * 2. **审核态要说得清楚**：未过审时留言**不在列表里**，界面必须明确提示
 *    "已提交，等待审核"，否则用户会以为提交失败；站长自己发的直接过审并刷新列表。
 * 3. **三态分得清**：加载中不显示空态、失败不伪装成"还没有留言"、空态给出口；
 * 4. **外链安全**：`author_site` 完全由访客控制，必须过 `safeExternalUrl`
 *    （`javascript:` 会被 Vue 原样写进 href）。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 的 guestbookApi.*）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, guestbookApi } from '@/api'
import { useToast } from '@/composables/useToast'
import type { GuestbookMessage, Page } from '@/types'

import GuestbookView from './GuestbookView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeMessage(overrides: Partial<GuestbookMessage> = {}): GuestbookMessage {
  return {
    id: 1,
    author_name: '访客甲',
    author_site: null,
    content: '博客做得不错，收藏了。',
    is_approved: true,
    reply_content: null,
    replied_at: null,
    created_at: '2026-09-24T10:00:00Z',
    ...overrides,
  }
}

function makePage(items: GuestbookMessage[], overrides: Partial<Page<GuestbookMessage>> = {}) {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 10,
    pages: 1,
    ...overrides,
  } satisfies Page<GuestbookMessage>
}

/* ------------------------------------------------------------ 脚手架 */

const Blank = { template: '<div />' }

async function mountGuestbook(
  query = '',
): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/guestbook', component: Blank },
    ],
  })
  await router.push(`/guestbook${query}`)
  await router.isReady()
  const wrapper = mount(GuestbookView, { global: { plugins: [router, createPinia()] } })
  await flushPromises()
  return { wrapper, router }
}

/** 表单字段：按 aria-label 定位（placeholder 不是可访问名称）。 */
function field(wrapper: VueWrapper, label: string) {
  return wrapper.get(`[aria-label="${label}"]`)
}

function submitButton(wrapper: VueWrapper) {
  const found = wrapper.findAll('button').find((item) => item.text().includes('发表留言'))
  if (!found) throw new Error('找不到「发表留言」按钮')
  return found
}

function lastToastMessage(): string {
  const items = useToast().items.value
  return items.length ? items[items.length - 1].message : ''
}

beforeEach(() => {
  setActivePinia(createPinia())
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.restoreAllMocks()
})

/* ------------------------------------------------------------ 用例 */

describe('GuestbookView · 列表', () => {
  it('按 URL 上的页码请求公开列表（默认第 1 页）', async () => {
    const list = vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([makeMessage()]))

    await mountGuestbook()

    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith(1, 10)
  })

  it('渲染昵称、正文与站长回复', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(
      makePage([
        makeMessage({
          id: 7,
          author_name: '路人乙',
          content: '想问一下主题配色是怎么定的？',
          reply_content: '用的是暖色打底，正文用高对比灰。',
          replied_at: '2026-09-24T12:00:00Z',
        }),
      ]),
    )

    const { wrapper } = await mountGuestbook()

    expect(wrapper.text()).toContain('路人乙')
    expect(wrapper.text()).toContain('想问一下主题配色是怎么定的？')
    expect(wrapper.text()).toContain('站长回复')
    expect(wrapper.text()).toContain('用的是暖色打底')
  })

  it('访客网站链接带 noopener，伪协议链接不渲染', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(
      makePage([
        makeMessage({ id: 1, author_name: '正常', author_site: 'https://example.com' }),
        makeMessage({ id: 2, author_name: '可疑', author_site: 'javascript:alert(1)' }),
      ]),
    )

    const { wrapper } = await mountGuestbook()
    const rows = wrapper.findAll('li')
    const safeLink = rows[0]?.findAll('a').find((item) => item.text() === '网站')

    // safeExternalUrl 内部会做一次 new URL() 规范化，所以 href 是补过斜杠的形式
    expect(safeLink?.attributes('href')).toBe('https://example.com/')
    expect(safeLink?.attributes('rel')).toContain('noopener')
    // 存量数据里可能有修复之前存进去的脏值，所以这里始终拦截
    expect(rows[1]?.findAll('a').some((item) => item.text() === '网站')).toBe(false)
  })

  it('加载中显示骨架屏，不显示空态', async () => {
    let resolve!: (value: Page<GuestbookMessage>) => void
    vi.spyOn(guestbookApi, 'list').mockReturnValue(
      new Promise<Page<GuestbookMessage>>((res) => {
        resolve = res
      }),
    )

    const { wrapper } = await mountGuestbook()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    expect(wrapper.text()).not.toContain('还没有留言')

    resolve(makePage([makeMessage()]))
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.text()).toContain('访客甲')
  })

  it('失败时给出错误文案与重试入口，绝不伪装成「还没有留言」', async () => {
    const list = vi
      .spyOn(guestbookApi, 'list')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce(makePage([makeMessage({ author_name: '恢复后的访客' })]))

    const { wrapper } = await mountGuestbook()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('服务器开小差了')
    expect(wrapper.text()).not.toContain('还没有留言')

    await wrapper
      .findAll('button')
      .find((item) => item.text().trim() === '重试')
      ?.trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('恢复后的访客')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('空列表时显示空态', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))

    const { wrapper } = await mountGuestbook()

    expect(wrapper.text()).toContain('还没有留言')
  })

  it('页码来自 URL：?page=2 直接请求第 2 页', async () => {
    const list = vi.spyOn(guestbookApi, 'list').mockResolvedValue(
      makePage([makeMessage({ id: 20 })], { page: 2, total: 25, pages: 3 }),
    )

    await mountGuestbook('?page=2')

    expect(list).toHaveBeenCalledWith(2, 10)
  })

  it('翻页写回 URL 并重拉同一页', async () => {
    const list = vi.spyOn(guestbookApi, 'list').mockResolvedValue(
      makePage([makeMessage()], { page: 1, total: 25, pages: 3 }),
    )

    const { wrapper, router } = await mountGuestbook()
    const next = wrapper.findAll('button').find((item) => item.text().trim() === '2')
    expect(next).toBeTruthy()

    await next?.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.page).toBe('2')
    expect(list).toHaveBeenLastCalledWith(2, 10)
  })
})

describe('GuestbookView · 发表留言', () => {
  it('内容为空时不发请求', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))
    const create = vi.spyOn(guestbookApi, 'create')

    const { wrapper } = await mountGuestbook()
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('留言内容不能为空')
  })

  it('匿名访客没填昵称时不发请求', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))
    const create = vi.spyOn(guestbookApi, 'create')

    const { wrapper } = await mountGuestbook()
    await field(wrapper, '留言内容').setValue('你好')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写昵称')
  })

  it('提交成功（待审核）：payload 里空串转 null，并明确提示还在等审核', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))
    const create = vi
      .spyOn(guestbookApi, 'create')
      .mockResolvedValue(makeMessage({ is_approved: false }))

    const { wrapper } = await mountGuestbook()
    await field(wrapper, '昵称（必填）').setValue('  新访客  ')
    await field(wrapper, '留言内容').setValue('  第一次来，留个脚印。  ')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    // 空串不是"没填"：后端把 None 当没填，空串会被当成一个真实的空值存下来
    expect(create).toHaveBeenCalledWith({
      author_name: '新访客',
      author_email: null,
      author_site: null,
      content: '第一次来，留个脚印。',
    })
    expect(lastToastMessage()).toBe('已提交，等待站长审核')
    // 未过审的留言不在列表里，界面必须说清它去哪了
    expect(wrapper.text()).toContain('站长审核通过后会显示在下方')
    // 内容清空，但昵称保留（同一个人通常还会再留一条）
    expect((field(wrapper, '留言内容').element as HTMLTextAreaElement).value).toBe('')
    // 输入框保留用户原样输入（只有提交的 payload 才 trim），
    // 否则光标/内容会在提交瞬间被改写，像被程序动过一样
    expect((field(wrapper, '昵称（必填）').element as HTMLInputElement).value).toContain(
      '新访客',
    )
  })

  it('提交成功（已过审，如站长本人）：提示已发布并刷新列表', async () => {
    const list = vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))
    vi.spyOn(guestbookApi, 'create').mockResolvedValue(makeMessage({ is_approved: true }))

    const { wrapper } = await mountGuestbook()
    await field(wrapper, '昵称（必填）').setValue('站长')
    await field(wrapper, '留言内容').setValue('欢迎留言')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('留言已发布')
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('站长审核通过后会显示在下方')
  })

  it('提交失败：提示错误、内容保留、不刷新列表', async () => {
    const list = vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))
    vi.spyOn(guestbookApi, 'create').mockRejectedValue(
      new ApiError('提交太频繁了，请稍后再试', 429, 'too_many_requests'),
    )

    const { wrapper } = await mountGuestbook()
    await field(wrapper, '昵称（必填）').setValue('访客')
    await field(wrapper, '留言内容').setValue('这条要保留')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toContain('提交太频繁了')
    // 失败还清空输入框等于让用户重打一遍，是最容易被忽略的体验缺陷
    expect((field(wrapper, '留言内容').element as HTMLTextAreaElement).value).toBe('这条要保留')
    expect(list).toHaveBeenCalledTimes(1)
  })
})

describe('GuestbookView · 元信息', () => {
  it('页面标题是「留言板」', async () => {
    vi.spyOn(guestbookApi, 'list').mockResolvedValue(makePage([]))

    await mountGuestbook()

    expect(document.title).toBe('留言板 · 个人博客')
  })
})
