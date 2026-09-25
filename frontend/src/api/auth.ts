/** 认证与用户管理接口。 */
import { api, refreshSession, tokenStore } from './http'

import type { LoginRequest, Message, Token, User, UserCreatePayload, UserUpdatePayload } from '@/types'

export const authApi = {
  async login(payload: LoginRequest): Promise<Token> {
    const tokens = await api.post<Token>('/auth/login', payload)
    // 只会在内存里留一份 access token；refresh token 由后端经 Set-Cookie 下发
    tokenStore.save(tokens)
    return tokens
  },

  /**
   * 用 httpOnly Cookie 静默续期一枚 access token。
   *
   * 委托给 `refreshSession()` 而不是自己发一次裸请求：那样会绕过它的单飞锁，
   * 与拦截器里 401 触发的续期撞车，滚动轮换下其中一个必然失败。
   */
  refresh(): Promise<Token> {
    return refreshSession()
  },

  async logout(): Promise<void> {
    // 即使服务端调用失败也必须清掉本地凭证，否则用户会卡在"看起来已登录但请求都 401"
    // （服务端会顺带把 refresh Cookie 删掉，前端这边只需清内存里的 access token）
    try {
      await api.post<Message>('/auth/logout')
    } finally {
      tokenStore.clear()
    }
  },

  me() {
    return api.get<User>('/auth/me')
  },

  updateMe(payload: Partial<Pick<User, 'nickname' | 'email' | 'avatar_url' | 'bio'>>) {
    return api.patch<User>('/auth/me', payload)
  },

  changePassword(oldPassword: string, newPassword: string) {
    return api.post<Message>('/auth/me/password', {
      old_password: oldPassword,
      new_password: newPassword,
    })
  },

  listUsers() {
    return api.get<User[]>('/auth/users')
  },

  createUser(payload: UserCreatePayload) {
    return api.post<User>('/auth/users', payload)
  },

  updateUser(id: number, payload: UserUpdatePayload) {
    return api.patch<User>(`/auth/users/${id}`, payload)
  },

  removeUser(id: number) {
    return api.delete(`/auth/users/${id}`)
  },
}
