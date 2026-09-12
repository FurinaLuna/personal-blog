/**
 * auth store 回归测试。
 *
 * 守护的是实测踩过的登录缺陷：login 成功必须返回 user 对象（非 undefined），
 * 否则 LoginView 的 `ok === undefined` 会把「登录成功」误判为失败，
 * 导致用户卡在登录页（API 200 + toast 已弹，但不跳转）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { authApi, tokenStore } from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types'

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

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('auth store 登录成功语义', () => {
  it('login 成功后返回 user 对象（非 undefined），登录态已就绪', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue({
      access_token: 'at', refresh_token: 'rt', token_type: 'bearer', expires_in: 7200,
    })
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
})
