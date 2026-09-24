/** 友情链接接口。 */
import { api } from './http'

import type { FriendLink, FriendLinkPayload, FriendLinkUpdatePayload, Message } from '@/types'

export const linkApi = {
  /**
   * 公开列表：只含已启用的条目，按 sort_order 升序。
   *
   * 这是匿名 GET，会被后端标成可公开缓存（ETag + max-age=60 + Vary: Authorization）——
   * 所以**不要**在这里附加任何随用户变化的数据，否则会污染共享缓存。
   */
  list() {
    return api.get<FriendLink[]>('/links')
  },

  /** 后台列表：含未启用的条目（路径带 /manage，不会被公开缓存）。 */
  listManaged() {
    return api.get<FriendLink[]>('/links/manage')
  },

  create(payload: FriendLinkPayload) {
    return api.post<FriendLink>('/links', payload)
  },

  /** 部分更新：只提交要改的字段。 */
  update(id: number, payload: FriendLinkUpdatePayload) {
    return api.patch<FriendLink>(`/links/${id}`, payload)
  },

  remove(id: number) {
    return api.delete<Message>(`/links/${id}`)
  },
}
