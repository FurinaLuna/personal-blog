/**
 * SiteHeader 测试：导航渲染，以及「登录」入口的后台开关。
 *
 * 这个组件此前没有测试，而它刚刚多了一个**策略分支**（`show_login_entry`），
 * 于是先把这几条契约钉住：
 *
 * 1. **两个渲染位**：桌面顶栏与移动端抽屉各有一份入口。开关关掉时必须**两处
 *    都不渲染** —— 两处各写一遍判断正是最容易漂成不一致的地方（所以判断放在
 *    store 的 `showLoginEntry` 里，这里断言的是结果）；
 * 2. **已登录不受开关影响**：`后台` / `进入后台` 入口始终在。关入口是为了访客
 *    看不见，站长自己反而更需要它 —— 若开关连它也藏起来，站长就被自己锁在门外；
 * 3. **默认显示**：开关开着时，已登录显示「后台」，匿名显示「登录」，与改造前一致；
 * 4. **降级方向**：档案接口失败（store 回落默认值）时仍然显示登录入口。
 *    降级路径必须和站点默认行为一致，否则一次接口抖动就让站长在前台找不到入口。
 *
 * 说明：不 mock 业务模块，只替换网络出口（`siteApi.profile`），走真实 store。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { siteApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import type { SiteProfile, User } from '@/types'

import SiteHeader from './SiteHeader.vue'

const Blank = { template: '<div />' }
let pinia: Pinia

function makeProfile(overrides: Partial<SiteProfile> = {}): SiteProfile {
  return {
    owner_name: '写代码的猫',
    headline: null,
    avatar_url: null,
    bio_md: null,
    about_md: null,
    email: null,
    location: null,
    icp: null,
    social_links: null,
    skills: null,
    comment_need_approval: true,
    allow_guest_comment: true,
    show_login_entry: true,
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const ADMIN: User = {
  id: 1,
  username: 'admin',
  nickname: '站长',
  avatar_url: null,
  email: 'admin@example.com',
  bio: null,
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

function makeRouter(): Router {
  // 路由表要覆盖 SiteHeader 的**全部**导航项，否则 vue-router 会对每个找不到的
  // path 刷一条 "[Vue Router warn] No match found"。警告本身无害，但刷屏会淹掉
  // 以后真正有意义的警告。
  const NAV_PATHS = [
    '/',
    '/categories',
    '/tags',
    '/archive',
    '/series',
    '/search',
    '/links',
    '/about',
    '/login',
    '/admin',
  ]
  return createRouter({
    history: createMemoryHistory(),
    routes: NAV_PATHS.map((path) => ({ path, component: Blank })),
  })
}

/** 走一遍真实加载链路：顶栏自己不请求，档案由 DefaultLayout 灌进 store。 */
async function mountHeader(options: { profile?: SiteProfile | Error; loggedIn?: boolean } = {}) {
  const router = makeRouter()
  await router.push('/')
  await router.isReady()

  const site = useSiteStore()
  const auth = useAuthStore()
  if (options.profile instanceof Error) {
    vi.spyOn(siteApi, 'profile').mockRejectedValue(options.profile)
  } else {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(options.profile ?? makeProfile())
  }
  if (options.loggedIn) auth.user = ADMIN

  await site.load()
  const wrapper = mount(SiteHeader, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, site, auth }
}

/** 展开移动端抽屉（顶栏里那个汉堡按钮）。 */
async function openDrawer(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('button[aria-label="切换导航菜单"]').trigger('click')
}

const loginLinks = (wrapper: VueWrapper) => wrapper.findAll('a[href="/login"]')

/**
 * 移动端抽屉里的后台入口。
 *
 * 必须限定在 `nav[aria-label="移动端导航"]` 内：桌面那份在 DOM 里也在，
 * `wrapper.get('a[href="/admin"]')` 取到的是桌面的那个（文案不同），
 * 拿它断言移动端会得到一条看着像缺陷、其实是选择器写错的失败。
 */
const drawerAdminLink = (wrapper: VueWrapper) =>
  wrapper.get('nav[aria-label="移动端导航"] a[href="/admin"]')

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

describe('SiteHeader · 登录入口开关', () => {
  it('开关默认开着：匿名访客在桌面与移动端都看得到「登录」', async () => {
    const { wrapper } = await mountHeader()

    expect(loginLinks(wrapper)).toHaveLength(1) // 桌面
    await openDrawer(wrapper)
    expect(loginLinks(wrapper)).toHaveLength(2) // + 移动端抽屉
    expect(loginLinks(wrapper)[0].text()).toBe('登录')
  })

  it('后台关掉开关后，桌面与移动端**两处**都不再渲染登录入口', async () => {
    const { wrapper } = await mountHeader({ profile: makeProfile({ show_login_entry: false }) })

    expect(loginLinks(wrapper)).toHaveLength(0)
    await openDrawer(wrapper)
    // 抽屉里的导航项照旧（8 个主菜单项），只是没有登录入口
    expect(loginLinks(wrapper)).toHaveLength(0)
    expect(wrapper.findAll('nav[aria-label="移动端导航"] .nav-link').length).toBeGreaterThan(0)
  })

  it('已登录时「后台」入口不受开关影响（否则站长被自己的开关锁在门外）', async () => {
    const { wrapper } = await mountHeader({
      profile: makeProfile({ show_login_entry: false }),
      loggedIn: true,
    })

    expect(wrapper.get('a[href="/admin"]').text()).toBe('后台')
    expect(loginLinks(wrapper)).toHaveLength(0)

    await openDrawer(wrapper)
    expect(drawerAdminLink(wrapper).text()).toBe('进入后台')
    expect(loginLinks(wrapper)).toHaveLength(0)
  })

  it('开关开着且已登录：显示「后台」而不是「登录」（与改造前一致）', async () => {
    const { wrapper } = await mountHeader({ loggedIn: true })

    expect(wrapper.get('a[href="/admin"]').text()).toBe('后台')
    expect(loginLinks(wrapper)).toHaveLength(0)

    await openDrawer(wrapper)
    expect(drawerAdminLink(wrapper).text()).toBe('进入后台')
    expect(loginLinks(wrapper)).toHaveLength(0)
  })

  it('站点档案加载失败时仍显示登录入口（降级方向与默认行为一致）', async () => {
    const { wrapper, site } = await mountHeader({ profile: new Error('网络不可用') })

    // 先确认降级真的发生了：否则这条用例会在"档案其实加载成功"的假状态下变绿
    expect(site.loaded).toBe(false)
    expect(loginLinks(wrapper)).toHaveLength(1)
  })
})
