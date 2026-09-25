/** 用户与认证领域。 */

export type UserRole = 'admin' | 'author'

export interface UserBrief {
  id: number
  username: string
  nickname: string | null
  avatar_url: string | null
}

export interface User extends UserBrief {
  email: string
  bio: string | null
  role: UserRole
  is_active: boolean
  created_at: string
}

export interface Token {
  access_token: string
  /**
   * 默认**没有**：浏览器走 httpOnly Cookie，响应体里不再下发
   * （后端只在 `REFRESH_TOKEN_IN_BODY=true` 时给非浏览器客户端返回）。
   * 前端即使收到了也不保存它（见 `api/http.ts` 的 `tokenStore.save`）。
   */
  refresh_token?: string
  token_type: string
  expires_in: number
}

export interface LoginRequest {
  username: string
  password: string
}

export interface UserCreatePayload {
  username: string
  email: string
  password: string
  nickname?: string | null
  role?: UserRole
}

export interface UserUpdatePayload {
  nickname?: string | null
  email?: string
  avatar_url?: string | null
  bio?: string | null
  role?: UserRole
  is_active?: boolean
}
