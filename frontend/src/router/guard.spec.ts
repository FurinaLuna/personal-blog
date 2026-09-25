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
 * 5. 公开页面不做多余的 /auth/me 请求。
 *
 * 用的是真实的 router 实例与真实的 auth store，只 mock 掉网络出口。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { authApi, tokenStore } from '@/api'
import { ApiError } from '@/api/http'
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

beforeEach(async () => {
  setActivePinia(createPinia())
  localStorage.clear()
  // token 只活在内存里，清 localStorage 清不掉它
  tokenStore.clear()
  const refresh = vi
    .spyOn(authApi, 'refresh')
    .mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))
  // 从一个公开页起跳，避免上一个用例的落地路由影响下一次导航判定
  await router.push('/')
  // 改造后**公开页也会先静默续期**，所以上面这次导航已经把 `restored` 置上了。
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

  it('公开页面不等 /auth/me（有 token 时也只是后台补一次）', async () => {
    tokenStore.save({ access_token: 'at' })
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
  it('无凭证访问公开页不做身份恢复请求', async () => {
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
