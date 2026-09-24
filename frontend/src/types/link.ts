/** 友情链接。 */

export interface FriendLink {
  id: number
  name: string
  /** 目标地址；后端已保证是 http/https 的绝对地址 */
  url: string
  description: string | null
  /** 站点头像/图标；为空时前端用首字占位 */
  avatar_url: string | null
  /** 展示顺序（升序）；相同则按 id */
  sort_order: number
  /** 未启用的条目只在后台可见 */
  is_active: boolean
  created_at: string
}

/** 新建时的必填项。 */
export interface FriendLinkPayload {
  name: string
  url: string
  description?: string | null
  avatar_url?: string | null
  sort_order?: number
  is_active?: boolean
}

/** 部分更新：只提交要改的字段（后端按 exclude_unset 处理）。 */
export type FriendLinkUpdatePayload = Partial<FriendLinkPayload>
