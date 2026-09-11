/** 认证与用户管理接口。 */
import { api, tokenStore } from './http'

import type { LoginRequest, Message, Token, User, UserCreatePayload, UserUpdatePayload } from '@/types'

export const authApi = {
  async login(payload: LoginRequest): Promise<Token> {
    const tokens = await api.post<Token>('/auth/login', payload)
    tokenStore.save(tokens)
    return tokens
  },

  async logout(): Promise<void> {
    // 即使服务端调用失败也必须清掉本地凭证，否则用户会卡在"看起来已登录但请求都 401"
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
