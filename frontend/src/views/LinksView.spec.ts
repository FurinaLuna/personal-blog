/**
 * LinksView（前台友链）回归测试。
 *
 * 守住四类契约：
 * 1. **只信后端**：页面不做二次过滤（「只显示已启用」是后端规则，前端再来一遍
 *    迟早会对不上）；请求就是 `linkApi.list()` 一次。
 * 2. **三态分得清**：加载中不显示空态、失败不伪装成"还没有友链"、空态给出口。
 * 3. **外链安全**：每条都是 `target="_blank"` + `rel="noopener noreferrer"`
 *    （没有 noopener 时，目标页能通过 window.opener 反向操作本页）。
 * 4. **降级好看**：没有头像时用站点名首字占位，而不是一张破图。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 的 linkApi.list）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, linkApi } from '@/api'
import type { FriendLink } from '@/types'

import LinksView from './LinksView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeLink(overrides: Partial<FriendLink> = {}): FriendLink {
  return {
    id: 1,
    name: 'FastAPI',
    url: 'https://fastapi.tiangolo.com',
    description: '高性能 Python Web 框架',
    avatar_url: null,
    sort_order: 0,
    is_active: true,
    created_at: '2026-09-22T00:00:00Z',
    ...overrides,
  }
}

/* ------------------------------------------------------------ 脚手架 */

const Blank = { template: '<div />' }

async function mountLinks(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/links', component: Blank },
      { path: '/guestbook', component: Blank },
    ],
  })
  await router.push('/links')
  await router.isReady()
  const wrapper = mount(LinksView, { global: { plugins: [router, createPinia()] } })
  await flushPromises()
  return { wrapper, router }
}

function cards(wrapper: VueWrapper) {
  return wrapper.findAll('ul > li > a')
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

/* ------------------------------------------------------------ 用例 */

describe('LinksView · 请求', () => {
  it('只请求一次公开列表，不在前端做二次过滤', async () => {
    const list = vi.spyOn(linkApi, 'list').mockResolvedValue([makeLink()])

    await mountLinks()

    // 「只显示已启用」是后端规则（GET /links 只返回 is_active）；前端再过滤一遍
    // 等于把同一条规则实现两次，迟早在某一侧改漏
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith()
  })
})

describe('LinksView · 三态', () => {
  it('加载中显示骨架屏，不显示空态', async () => {
    let resolve!: (value: FriendLink[]) => void
    vi.spyOn(linkApi, 'list').mockReturnValue(
      new Promise<FriendLink[]>((res) => {
        resolve = res
      }),
    )

    const { wrapper } = await mountLinks()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    expect(wrapper.text()).not.toContain('还没有友链')

    resolve([makeLink()])
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.text()).toContain('FastAPI')
  })

  it('失败时给出错误文案与重试入口，绝不伪装成「还没有友链」', async () => {
    const list = vi
      .spyOn(linkApi, 'list')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce([makeLink({ name: 'Vue' })])

    const { wrapper } = await mountLinks()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('服务器开小差了')
    expect(wrapper.text()).not.toContain('还没有友链')

    const retry = wrapper.findAll('button').find((item) => item.text().trim() === '重试')
    await retry?.trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Vue')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('空列表时显示空态而不是一片空白', async () => {
    vi.spyOn(linkApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountLinks()

    expect(wrapper.text()).toContain('还没有友链')
    expect(cards(wrapper)).toHaveLength(0)
  })
})

describe('LinksView · 卡片渲染', () => {
  it('渲染名称、介绍与地址，并外链新窗口 + noopener', async () => {
    vi.spyOn(linkApi, 'list').mockResolvedValue([
      makeLink({ id: 1, name: 'FastAPI', url: 'https://fastapi.tiangolo.com' }),
      makeLink({ id: 2, name: 'Vue', url: 'https://vuejs.org', description: null }),
    ])

    const { wrapper } = await mountLinks()
    const rendered = cards(wrapper)

    expect(rendered).toHaveLength(2)
    expect(rendered[0]?.text()).toContain('FastAPI')
    expect(rendered[0]?.text()).toContain('高性能 Python Web 框架')
    expect(rendered[0]?.attributes('href')).toBe('https://fastapi.tiangolo.com')
    // 没有 noopener 时，目标页能通过 window.opener 反向操作本页
    expect(rendered[0]?.attributes('target')).toBe('_blank')
    expect(rendered[0]?.attributes('rel')).toContain('noopener')
    // 没填介绍的第二条不该留一个空占位
    expect(rendered[1]?.text()).not.toContain('高性能')
  })

  it('有头像时渲染图片，没有头像时用站点名首字占位', async () => {
    vi.spyOn(linkApi, 'list').mockResolvedValue([
      makeLink({
        id: 1,
        name: 'FastAPI',
        avatar_url: 'https://fastapi.tiangolo.com/img/icon.png',
      }),
      makeLink({ id: 2, name: 'vue', avatar_url: null }),
    ])

    const { wrapper } = await mountLinks()

    const images = wrapper.findAll('img')
    expect(images).toHaveLength(1)
    expect(images[0]?.attributes('src')).toBe('https://fastapi.tiangolo.com/img/icon.png')
    // 无头像那一条走首字占位（大写化，避免小写站点名看起来像笔误）
    expect(cards(wrapper)[1]?.text()).toContain('V')
    expect(cards(wrapper)[1]?.find('img').exists()).toBe(false)
  })

  it('页面标题是「友情链接」', async () => {
    vi.spyOn(linkApi, 'list').mockResolvedValue([])

    await mountLinks()

    expect(document.title).toBe('友情链接 · 个人博客')
  })
})
