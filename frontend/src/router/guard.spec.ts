/**
 * 路由守卫回归测试。
 *
 * 这块此前**完全没有测试**，而它是「后台是否会被未授权访问」的唯一一道前端闸门。
 * 这里守护五条契约：
 *
 * 1. 未登录访问 /admin/* → 跳登录页，并把原地址带在 redirect 上（登录后能回得去）；
 * 2. 已登录但角色不够（author 访问 requiresAdmin 页面）→ 回到首页，
 *    **而不是跳登录页**，否则用户会误以为自己没登录；
 * 3. 本地有 token 但服务端判定失效（/auth/me 401）→ 按未登录处理；
 * 4. **刷新页面时内存里没有 access token**：守卫仍要先静默续期再问 /auth/me，
 *    不能因为"内存是空的"就把有登录态的用户判成未登录（这是本次改造最致命的回归点）；
 * 5. 公开页面只在**会话提示 Cookie 存在**时才恢复登录态：匿名访客不该为一次
 *    注定 401 的续期请求买单（这是 hint Cookie 存在的全部意义）；
 * 6. **hint 不是授权依据**：有它没它，受保护路由都必须无条件尝试恢复——
 *    否则 hint 一缺失就会把已登录用户判成未登录，变成"偶发被登出"。
 *
 * 用的是真实的 router 实例与真实的 auth store，只 mock 掉网络出口。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { authApi, tokenStore } from '@/api'
import { ApiError, SESSION_HINT_COOKIE_NAME } from '@/api/http'
import { router } from '@/router'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types'

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

const FRESH_TOKENS = { access_token: 'fresh', token_type: 'bearer' as const, expires_in: 7200 }

/**
 * 清掉提示 Cookie。
 *
 * jsdom 的 ``document.cookie`` 会跨用例残留，且带 path 语义——
 * 所以两个 path 都要清（用例里会刻意设一个 ``/api/v1/auth`` 的反例）。
 */
function clearHintCookie(): void {
  for (const path of ['/', '/api/v1/auth']) {
    document.cookie = `${SESSION_HINT_COOKIE_NAME}=; path=${path}; max-age=0`
  }
}

/** 按后端真实形态设置提示 Cookie；``path`` 可传，用来验证 path 语义。 */
function setHintCookie(path = '/'): void {
  document.cookie = `${SESSION_HINT_COOKIE_NAME}=1; path=${path}`
}

beforeEach(async () => {
  setActivePinia(createPinia())
  localStorage.clear()
  // token 只活在内存里，清 localStorage 清不掉它
  tokenStore.clear()
  clearHintCookie()
  const refresh = vi
    .spyOn(authApi, 'refresh')
    .mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))
  // 从一个公开页起跳，避免上一个用例的落地路由影响下一次导航判定。
  // 此刻没有 hint，公开页分支不会触发恢复，这次起跳是干净的。
  await router.push('/')
  // 每个用例都从"还没问过服务端"起步，否则守卫会跳过恢复、一律按未登录处理。
  useAuthStore().restored = false
  // 抹掉这次导航留下的调用记录，用例里再断言"只续期了一次"才是干净的
  refresh.mockClear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('后台访问守卫', () => {
  it('未登录访问后台 → 跳登录页并带上完整 redirect（含 query 参数）', async () => {
    await router.push('/admin/articles?status=draft')

    expect(router.currentRoute.value.name).toBe('login')
    // 必须是 fullPath：筛选条件这类 query 丢了，登录后回来的就不是原来那个页面
    expect(router.currentRoute.value.query.redirect).toBe('/admin/articles?status=draft')
  })

  it('本地无凭证时访问后台，不发送 /auth/me 就直接拦截', async () => {
    const me = vi.spyOn(authApi, 'me')

    await router.push('/admin/articles')

    expect(router.currentRoute.value.name).toBe('login')
    expect(me).not.toHaveBeenCalled()
  })

  it('已登录的站长可以进入 requiresAdmin 页面', async () => {
    tokenStore.save({ access_token: 'at' })
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    await router.push('/admin/users')

    expect(router.currentRoute.value.name).toBe('admin-users')
  })

  it('已登录但角色不足 → 回首页（不跳登录页，避免误以为没登录）', async () => {
    tokenStore.save({ access_token: 'at' })
    vi.spyOn(authApi, 'me').mockResolvedValue(AUTHOR)

    await router.push('/admin/users')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('本地有 token 但服务端判定失效 → 按未登录处理', async () => {
    tokenStore.save({ access_token: 'stale-token' })
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))

    await router.push('/admin/articles')

    expect(router.currentRoute.value.name).toBe('login')
    // 凭证失效后不应把过期 token 留在本地
    expect(tokenStore.access).toBeNull()
  })

  it('普通后台页面（仅 requiresAuth）对已登录作者放行', async () => {
    tokenStore.save({ access_token: 'at' })
    vi.spyOn(authApi, 'me').mockResolvedValue(AUTHOR)

    await router.push('/admin/comments')

    expect(router.currentRoute.value.name).toBe('admin-comments')
  })

  it('/auth/me 只是网络抖动时，不会被永久缓存成「未登录」', async () => {
    tokenStore.save({ access_token: 'at' })
    const me = vi
      .spyOn(authApi, 'me')
      // 一次抖动会让恢复失败（它自己会重试一次），但绝不能把结果钉死
      .mockRejectedValueOnce(new ApiError('网络异常', 0, 'network_error'))
      .mockRejectedValueOnce(new ApiError('网络异常', 0, 'network_error'))
      .mockResolvedValue(ADMIN)

    // 第一次导航：问不出来（网络问题），**放行**而不是当成未登录踢走 ——
    // "网络抖动"和"你没登录"是两件事，混为一谈会让用户以为自己被登出了
    await router.push('/admin/users')
    expect(router.currentRoute.value.name).toBe('admin-users')

    // 恢复结果没有被钉死：下一次导航会自动再试，成功后身份就位
    await router.push('/admin/comments')
    await router.push('/admin/users')
    expect(me.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it('服务端明确否认（401）时仍然按未登录处理并清除凭证', async () => {
    tokenStore.save({ access_token: 'stale' })
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))

    await router.push('/admin/articles')

    expect(router.currentRoute.value.name).toBe('login')
    expect(tokenStore.access).toBeNull()
  })

  it('后台内部的 404 留在后台布局里（不能掉回前台）', async () => {
    tokenStore.save({ access_token: 'at' })
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    await router.push('/admin/does-not-exist')

    expect(router.currentRoute.value.name).toBe('admin-not-found')
    // 没有 layout 就会渲染成前台布局：侧边栏消失、用户以为被登出了
    expect(router.currentRoute.value.meta.layout).toBe('admin')
  })
})

describe('刷新页面后的静默续期（access token 只存内存）', () => {
  it('内存里没有 token 时先续期再问 /auth/me，有登录态的用户留在后台', async () => {
    tokenStore.clear()
    const refresh = vi
      .spyOn(authApi, 'refresh')
      .mockResolvedValue({ access_token: 'fresh', token_type: 'bearer', expires_in: 7200 })
    const me = vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    await router.push('/admin/users')

    // 刷新页面后内存里必然没有 access token，守卫若因此跳过恢复，
    // 有登录态的用户一刷新就被弹去登录页
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('admin-users')
  })

  it('静默续期 401 = 确实没登录：跳登录页，且不再白问 /auth/me', async () => {
    tokenStore.clear()
    const me = vi.spyOn(authApi, 'me')

    await router.push('/admin/articles')

    expect(router.currentRoute.value.name).toBe('login')
    expect(me).not.toHaveBeenCalled()
  })

  it('静默续期只是网络抖动时不会被当成未登录踢走', async () => {
    tokenStore.clear()
    vi.spyOn(authApi, 'refresh').mockRejectedValue(new ApiError('网络异常', 0, 'network_error'))

    await router.push('/admin/users')

    // "问不出来" ≠ "没登录"：放行，让页面自己表达失败
    expect(router.currentRoute.value.name).toBe('admin-users')
  })
})

describe('身份恢复不得阻塞导航', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('公开页面不等 /auth/me（有 hint 时也只是后台补一次）', async () => {
    tokenStore.save({ access_token: 'at' })
    // 有 hint 才触发恢复；它在后台跑，绝不作为导航前提
    setHintCookie()
    // 永不 resolve：如果守卫在等它，这次 push 就永远不会结束
    const me = vi.spyOn(authApi, 'me').mockImplementation(() => new Promise(() => {}))

    await router.push('/about')

    expect(router.currentRoute.value.name).toBe('about')
    // 仍然要在后台发一次（顶栏要靠它变成已登录形态），只是不作为导航前提
    expect(me).toHaveBeenCalled()
  })

  it('恢复请求挂起超过上限时，导航照常放行', async () => {
    vi.useFakeTimers()
    tokenStore.save({ access_token: 'at' })
    vi.spyOn(authApi, 'me').mockImplementation(() => new Promise(() => {}))

    const pending = router.push('/admin/users')
    // 推进到超过守卫的上限（6s）：不能无限等一个不问结果的请求
    await vi.advanceTimersByTimeAsync(6500)
    await pending

    expect(router.currentRoute.value.name).toBe('admin-users')
  })
})

describe('公开页面', () => {
  it('无凭证也无 hint 时，公开页不发任何身份恢复请求', async () => {
    const me = vi.spyOn(authApi, 'me')

    await router.push('/about')

    expect(router.currentRoute.value.name).toBe('about')
    expect(me).not.toHaveBeenCalled()
  })

  it('导航后写入页面标题', async () => {
    await router.push('/archive')

    expect(document.title).toContain('归档')
  })
})

describe('hint Cookie 决定公开页要不要恢复登录态', () => {
  it('匿名访客（无 hint）访问公开页 → 不发 POST /auth/refresh', async () => {
    // 这是本次改动的**唯一目的**：access token 只存内存后，前端无从判断有没有会话，
    // 于是匿名访客每次进站都会先打一次续期请求、白吃一个 401。
    const refresh = vi.spyOn(authApi, 'refresh')
    const me = vi.spyOn(authApi, 'me')

    await router.push('/about')

    expect(router.currentRoute.value.name).toBe('about')
    expect(refresh).not.toHaveBeenCalled()
    expect(me).not.toHaveBeenCalled()
  })

  it('有 hint 访问公开页 → 发一次续期并恢复登录态', async () => {
    setHintCookie()
    const refresh = vi.spyOn(authApi, 'refresh').mockResolvedValue(FRESH_TOKENS)
    vi.spyOn(authApi, 'me').mockResolvedValue(AUTHOR)
    const auth = useAuthStore()

    await router.push('/about')

    // 公开页的恢复是 void 出去的（不阻塞导航），等它跑完再断言
    await vi.waitFor(() => expect(auth.isAuthenticated).toBe(true))
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(auth.displayName).toBe('author')
  })

  it('提示 Cookie 的 path 决定它读不读得到（护栏）', () => {
    // 后端真实 path 就是 "/"：页面在 "/"，document.cookie 读得到
    setHintCookie('/')
    expect(tokenStore.hasSessionHint).toBe(true)

    clearHintCookie()
    // 反例：若把 hint 收窄成 refresh Cookie 的 /api/v1/auth，
    // 页面（在 "/"）的 document.cookie **读不到它** —— 这正是它必须用 "/" 的原因。
    // 谁"为了对齐 path"改回 /api/v1/auth，就会命中这个反例所描述的现实
    // （后端那条 `test_path_is_root_not_the_refresh_path` 会先红）。
    setHintCookie('/api/v1/auth')
    expect(tokenStore.hasSessionHint).toBe(false)
  })
})

describe('hint 不是授权依据（受保护路由无条件恢复）', () => {
  it('无 hint 访问受保护路由 → 仍然尝试续期（不能把已登录用户踢掉）', async () => {
    // 场景：用户手删了 hint Cookie，refresh Cookie 其实还在。
    // 若守卫拿 hint 当判据，这个用户就会被当成未登录弹去登录页 —— "偶发被登出"。
    const refresh = vi.spyOn(authApi, 'refresh')
    const me = vi.spyOn(authApi, 'me')

    await router.push('/admin/articles')

    expect(refresh).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('login')
    // 这里没登录是**续期被 mock 成 401** 的结论，不是"因为没 hint 所以直接判未登录"
    expect(me).not.toHaveBeenCalled()
  })

  it('无 hint 但凭证有效时，受保护路由照样进得去', async () => {
    vi.spyOn(authApi, 'refresh').mockResolvedValue(FRESH_TOKENS)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    await router.push('/admin/users')

    expect(router.currentRoute.value.name).toBe('admin-users')
  })

  it('有 hint 访问受保护路由同样恢复（行为不因 hint 而分叉）', async () => {
    setHintCookie()
    vi.spyOn(authApi, 'refresh').mockResolvedValue(FRESH_TOKENS)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    await router.push('/admin/users')

    expect(router.currentRoute.value.name).toBe('admin-users')
  })
})
