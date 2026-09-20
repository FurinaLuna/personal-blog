/**
 * 路由守卫回归测试。
 *
 * 这块此前**完全没有测试**，而它是「后台是否会被未授权访问」的唯一一道前端闸门。
 * 这里守护四条契约：
 *
 * 1. 未登录访问 /admin/* → 跳登录页，并把原地址带在 redirect 上（登录后能回得去）；
 * 2. 已登录但角色不够（author 访问 requiresAdmin 页面）→ 回到首页，
 *    **而不是跳登录页**，否则用户会误以为自己没登录；
 * 3. 本地有 token 但服务端判定失效（/auth/me 401）→ 按未登录处理；
 * 4. 公开页面不做多余的 /auth/me 请求。
 *
 * 用的是真实的 router 实例与真实的 auth store，只 mock 掉 `/auth/me` 这一个网络出口。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { authApi, tokenStore } from '@/api'
import { ApiError } from '@/api/http'
import { router } from '@/router'
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
  // 从一个公开页起跳，避免上一个用例的落地路由影响下一次导航判定
  await router.push('/')
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
    tokenStore.save({ access_token: 'at', refresh_token: 'rt' })
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    await router.push('/admin/users')

    expect(router.currentRoute.value.name).toBe('admin-users')
  })

  it('已登录但角色不足 → 回首页（不跳登录页，避免误以为没登录）', async () => {
    tokenStore.save({ access_token: 'at', refresh_token: 'rt' })
    vi.spyOn(authApi, 'me').mockResolvedValue(AUTHOR)

    await router.push('/admin/users')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('本地有 token 但服务端判定失效 → 按未登录处理', async () => {
    tokenStore.save({ access_token: 'stale-token', refresh_token: 'rt' })
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))

    await router.push('/admin/articles')

    expect(router.currentRoute.value.name).toBe('login')
    // 凭证失效后不应把过期 token 留在本地
    expect(tokenStore.access).toBeNull()
  })

  it('普通后台页面（仅 requiresAuth）对已登录作者放行', async () => {
    tokenStore.save({ access_token: 'at', refresh_token: 'rt' })
    vi.spyOn(authApi, 'me').mockResolvedValue(AUTHOR)

    await router.push('/admin/comments')

    expect(router.currentRoute.value.name).toBe('admin-comments')
  })

  it('/auth/me 只是网络抖动时，不会被永久缓存成「未登录」', async () => {
    tokenStore.save({ access_token: 'at', refresh_token: 'rt' })
    const me = vi
      .spyOn(authApi, 'me')
      // 一次抖动会让恢复失败（它自己会重试一次），但绝不能把结果钉死
      .mockRejectedValueOnce(new ApiError('网络异常', 0, 'network_error'))
      .mockRejectedValueOnce(new ApiError('网络异常', 0, 'network_error'))
      .mockResolvedValue(ADMIN)

    await router.push('/admin/users')
    expect(router.currentRoute.value.name).toBe('login')

    // 下一次导航必须自动再试一次，而不是把作者永远关在登录页外
    await router.push('/admin/users')
    expect(router.currentRoute.value.name).toBe('admin-users')
    expect(me.mock.calls.length).toBeGreaterThanOrEqual(3)
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
