/**
 * DefaultLayout 启动行为。
 *
 * ## 这个文件为什么必须存在
 *
 * 前台布局挂在**公开页面**上（首页 / 归档 / 文章详情……），挂载时会恢复登录态，
 * 好让顶栏正确显示"后台 / 登录"。而 `access_token` 只存内存，刷新页面后必然为空 ——
 * 如果这里调的是无条件的 `restore()`，**每个匿名访客每次进站都会先打一次注定 401 的
 * `POST /auth/refresh`**。
 *
 * 这正是一次真实回归：消除那次 401 的改动只在**路由守卫**里加了提示 Cookie 的门控，
 * 而本布局仍在无条件调 `restore()` —— 门控被绕过、目的完全没达成。单测当时是绿的，
 * 因为它们只在路由层断言。**所以这个文件测的是调用方，而不是守卫。**
 *
 * 与仓库其它 spec 一致：不 mock 业务模块，只替换网络出口。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { authApi, siteApi, tokenStore } from '@/api'
import type { User } from '@/types'

import DefaultLayout from './DefaultLayout.vue'

const ADMIN_USER: User = {
  id: 1,
  username: 'admin',
  nickname: null,
  avatar_url: null,
  email: 'admin@example.com',
  bio: null,
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/admin', name: 'admin', component: { template: '<div>admin</div>' } },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
    ],
  })
}

function setHintCookie(path = '/') {
  document.cookie = `blog_session=1; path=${path}`
}

function clearHintCookie() {
  document.cookie = 'blog_session=; path=/; max-age=0'
}

/** 挂载前台布局。子组件打桩：本文件只关心布局**自己**在挂载时发起了什么请求。 */
async function mountLayout(): Promise<VueWrapper> {
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  const wrapper = mount(DefaultLayout, {
    global: {
      plugins: [router],
      stubs: { SiteHeader: true, SiteFooter: true, ToastHost: true },
    },
  })
  // 等 onMounted 里那几个 promise 走完
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  tokenStore.clear()
  clearHintCookie()
  vi.spyOn(siteApi, 'profile').mockResolvedValue({ owner_name: '个人博客' } as never)
  vi.spyOn(siteApi, 'stats').mockResolvedValue({} as never)
})

afterEach(() => {
  clearHintCookie()
  vi.restoreAllMocks()
})

describe('DefaultLayout 挂载时的身份恢复', () => {
  it('匿名访客（无提示 Cookie）→ 一次 /auth/refresh 都不发', async () => {
    const refresh = vi.spyOn(authApi, 'refresh')
    const me = vi.spyOn(authApi, 'me')

    await mountLayout()

    // 这是本文件存在的唯一理由：公开页面上绝大多数访客是匿名的，
    // 让他们每人每次进站白吃一个 401 是不可接受的。
    expect(refresh).not.toHaveBeenCalled()
    expect(me).not.toHaveBeenCalled()
  })

  it('提示 Cookie 存在（path=/）→ 静默续期一次并恢复登录态', async () => {
    setHintCookie('/')
    const refresh = vi
      .spyOn(authApi, 'refresh')
      .mockResolvedValue({ access_token: 'fresh', token_type: 'bearer', expires_in: 7200 })
    const me = vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    await mountLayout()

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(1)
  })

  it('提示 Cookie 被收窄到 /api/v1/auth（读不到）→ 不发请求（path 语义护栏）', async () => {
    // 后端若把提示 Cookie 的 path "对齐"成 refresh Cookie 的 /api/v1/auth，
    // 页面 JS 就读不到它 —— 那样公开页永远不恢复登录态。这条让那种改动立刻变红。
    setHintCookie('/api/v1/auth')
    const refresh = vi.spyOn(authApi, 'refresh')

    await mountLayout()

    expect(document.cookie).not.toContain('blog_session')
    expect(refresh).not.toHaveBeenCalled()
  })
})
