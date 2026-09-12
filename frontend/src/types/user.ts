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
  refresh_token: string
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
