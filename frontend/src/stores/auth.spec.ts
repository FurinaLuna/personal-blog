/**
 * auth store 回归测试。
 *
 * 守护的是实测踩过的登录缺陷与本次凭证改造新引入的恢复路径：
 * - login 成功必须返回 user 对象（非 undefined），否则 LoginView 的
 *   `ok === undefined` 会把「登录成功」误判为失败，用户卡在登录页
 *   （API 200 + toast 已弹，但不跳转）；
 * - access token 只存内存后，**刷新页面必然是空的**，所以 restore() 必须先用
 *   httpOnly Cookie 静默续期一枚出来再去问 /auth/me，否则用户一刷新就被登出。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { api, authApi, tokenStore } from '@/api'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import type { Token, User } from '@/types'

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

/** 登录/刷新响应：后端默认**不**在响应体里返回 refresh token（走 httpOnly Cookie）。 */
const TOKENS: Token = { access_token: 'at', token_type: 'bearer', expires_in: 7200 }

beforeEach(() => {
  setActivePinia(createPinia())
  tokenStore.clear()
  vi.clearAllMocks()
})

describe('auth store 登录成功语义', () => {
  it('login 成功后返回 user 对象（非 undefined），登录态已就绪', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKENS)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    const result = await auth.login('admin', 'admin123456')

    // 核心契约：成功返回值不能是 undefined（LoginView 靠它判断是否跳转）
    expect(result).not.toBeUndefined()
    expect(result).toEqual(ADMIN_USER)
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.displayName).toBe('admin')
  })

  it('login 失败时抛出，store 不留下半吊子登录态', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new Error('用户名或密码错误'))
    const auth = useAuthStore()

    await expect(auth.login('admin', 'bad')).rejects.toThrow('用户名或密码错误')
    expect(auth.isAuthenticated).toBe(false)
    expect(tokenStore.access).toBeNull()
  })

  it('登录后令牌只进内存：localStorage 里一个字节都不留', async () => {
    // 走**真实的** authApi.login（只替换 HTTP 出口），否则这条用例就成了
    // "mock 没往 localStorage 写" 的空转。后端只在 REFRESH_TOKEN_IN_BODY=true
    // 时才会把 refresh token 放进响应体；即使带了也不能落进 JS 可读的位置。
    vi.spyOn(api, 'post').mockResolvedValue({ ...TOKENS, refresh_token: 'rt' })
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    await auth.login('admin', 'admin123456')

    expect(tokenStore.access).toBe('at')
    expect(localStorage.getItem('blog-access-token')).toBeNull()
    expect(localStorage.getItem('blog-refresh-token')).toBeNull()
    expect(localStorage.length).toBe(0)
  })
})

describe('auth store 启动恢复（刷新页面场景）', () => {
  it('内存里没有 token 时先用 Cookie 静默续期，再问 /auth/me', async () => {
    tokenStore.clear()
    const refresh = vi
      .spyOn(authApi, 'refresh')
      .mockResolvedValue({ access_token: 'fresh', token_type: 'bearer', expires_in: 7200 })
    const me = vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    await auth.restore()

    // 少了续期这一步，用户刷新页面就会被判成未登录 —— 这是"只存内存"的必补代价
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(1)
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.restored).toBe(true)
  })

  it('内存里已经有 token 时不再多问一次续期', async () => {
    tokenStore.save({ access_token: 'at' })
    const refresh = vi.spyOn(authApi, 'refresh')
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    await auth.restore()

    expect(refresh).not.toHaveBeenCalled()
    expect(auth.isAuthenticated).toBe(true)
  })

  it('静默续期 401 = 确定没登录：restored 就绪、不报错', async () => {
    tokenStore.clear()
    vi.spyOn(authApi, 'refresh').mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))
    const me = vi.spyOn(authApi, 'me')

    const auth = useAuthStore()
    await expect(auth.restore()).resolves.toBeUndefined()

    expect(me).not.toHaveBeenCalled()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.restored).toBe(true)
  })

  it('静默续期只是网络异常：restored 保持 false，下次导航自动重试', async () => {
    tokenStore.clear()
    vi.spyOn(authApi, 'refresh').mockRejectedValue(new ApiError('网络异常', 0, 'network_error'))

    const auth = useAuthStore()
    await auth.restore()

    // 把"网络不好"永久缓存成"你没登录"，用户只能刷新整页才能恢复
    expect(auth.restored).toBe(false)
    expect(auth.isAuthenticated).toBe(false)
  })

  it('并发 restore 只续期一次（共享同一个 Promise）', async () => {
    tokenStore.clear()
    let release!: (value: Token) => void
    const refresh = vi
      .spyOn(authApi, 'refresh')
      .mockImplementation(() => new Promise<Token>((resolve) => {
        release = resolve
      }))
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    const first = auth.restore()
    const second = auth.restore()
    await Promise.resolve()

    // 后端是滚动轮换，并发两个 refresh 中必有一个撞上"旧 refresh 已作废"
    expect(refresh).toHaveBeenCalledTimes(1)

    release({ access_token: 'fresh', token_type: 'bearer', expires_in: 7200 })
    await Promise.all([first, second])
    expect(auth.isAuthenticated).toBe(true)
  })
})

/* ------------------------------------------------------------------ 机会性恢复 */

/**
 * `restoreIfLikely()`：公开页面的入口（`DefaultLayout` 挂载时调的就是它）。
 *
 * 为什么必须单独守：公开页面（首页 / 归档 / 文章详情）上匿名访客占绝大多数，
 * 而 access token 只存内存、刷新后必然为空 —— 那里若调无条件的 `restore()`，
 * 每个匿名访客进站都会先打一次注定 401 的 `/auth/refresh`。服务端因此下发一个
 * 非 httpOnly 的提示 Cookie，这里就是读它的地方。
 *
 * 这组用例的存在还因为踩过一次：当时只在**路由守卫**里加了提示 Cookie 的门控，
 * 而 `DefaultLayout.vue` 挂载时仍在无条件调 `restore()` —— 门控被绕过、目的没达成，
 * 而单测因为在路由层断言而全绿。**测调用方，别只测守卫。**
 */
describe('restoreIfLikely：只在可能有会话时才去问服务端', () => {
  /** 提示 Cookie 必须用 `path=/` 才读得到（后端就是这么发的）。 */
  function setHintCookie(path = '/') {
    document.cookie = `blog_session=1; path=${path}`
  }

  function clearHintCookie() {
    document.cookie = 'blog_session=; path=/; max-age=0'
  }

  beforeEach(() => {
    clearHintCookie()
  })

  it('无提示 Cookie 且内存无 token → 一个请求都不发', async () => {
    tokenStore.clear()
    const refresh = vi.spyOn(authApi, 'refresh')
    const me = vi.spyOn(authApi, 'me')

    const auth = useAuthStore()
    await auth.restoreIfLikely()

    expect(refresh).not.toHaveBeenCalled()
    expect(me).not.toHaveBeenCalled()
  })

  it('短路时**不置 restored**：那是"已问过服务端"的标记，没问过就不能钉结论', async () => {
    tokenStore.clear()

    const auth = useAuthStore()
    await auth.restoreIfLikely()

    // 置成 true 会让受保护路由的守卫跳过无条件恢复，于是"提示位缺失但确实有会话"
    // 的已登录用户（手删了提示 Cookie / 跨域部署读不到）会被直接弹去登录页 ——
    // 正是这次改动要避免的"偶发被登出"。
    expect(auth.restored).toBe(false)
    expect(auth.isAuthenticated).toBe(false)
  })

  it('有提示 Cookie（生产真实 path：/）→ 静默续期并恢复登录态', async () => {
    setHintCookie('/')
    tokenStore.clear()
    const refresh = vi
      .spyOn(authApi, 'refresh')
      .mockResolvedValue({ access_token: 'fresh', token_type: 'bearer', expires_in: 7200 })
    const me = vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    await auth.restoreIfLikely()

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(1)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('提示 Cookie 被收窄到 /api/v1/auth 时读不到 → 不发请求（这条是 path 护栏）', async () => {
    // 后端若"为了与 refresh Cookie 对齐"把它收窄到 /api/v1/auth，页面 JS 就读不到，
    // 于是公开页永远不恢复登录态 —— 净功能回归。这条用例让那种改动立刻变红。
    setHintCookie('/api/v1/auth')
    tokenStore.clear()
    const refresh = vi.spyOn(authApi, 'refresh')

    const auth = useAuthStore()
    await auth.restoreIfLikely()

    expect(document.cookie).not.toContain('blog_session')
    expect(refresh).not.toHaveBeenCalled()
  })

  it('内存已有 access token → 即使没有提示 Cookie 也要恢复（受保护路由的诉求）', async () => {
    clearHintCookie()
    tokenStore.save({ access_token: 'at' })
    const me = vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    await auth.restoreIfLikely()

    expect(me).toHaveBeenCalledTimes(1)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('提示 Cookie 消失时不抹掉内存里已有的登录态', async () => {
    setHintCookie('/')
    tokenStore.clear()
    vi.spyOn(authApi, 'refresh').mockResolvedValue({
      access_token: 'fresh',
      token_type: 'bearer',
      expires_in: 7200,
    })
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN_USER)

    const auth = useAuthStore()
    await auth.restoreIfLikely()
    expect(auth.isAuthenticated).toBe(true)

    // 提示位是"省请求的优化"，不是真相来源：它没了不代表用户被登出
    clearHintCookie()
    await auth.restoreIfLikely()
    expect(auth.isAuthenticated).toBe(true)
  })
})

