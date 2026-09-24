/** 留言板接口。 */
import { api } from './http'

import type {
  GuestbookMessage,
  GuestbookMessageAdmin,
  GuestbookMessagePayload,
  Message,
  Page,
} from '@/types'

export const guestbookApi = {
  /**
   * 公开列表：只含已过审的留言，最新的在最前。
   *
   * 这是匿名 GET，会被后端标成可公开缓存（ETag + max-age=60 + Vary: Authorization）——
   * 所以**不要**在这里附加任何随用户变化的数据，否则会污染共享缓存。
   */
  list(page = 1, pageSize = 10) {
    return api.get<Page<GuestbookMessage>>('/guestbook', { page, page_size: pageSize })
  },

  /** 发表留言（匿名可用）。返回值里的 `is_approved` 决定前端提示"已发布"还是"待审核"。 */
  create(payload: GuestbookMessagePayload) {
    return api.post<GuestbookMessage>('/guestbook', payload)
  },

  /** 后台列表：含待审留言（路径带 /manage，不会被公开缓存）。 */
  listManaged(page = 1, pageSize = 10, approved?: boolean) {
    // 注意第二参是**扁平**的查询参数对象：写成 { params: {...} } 会被序列化成
    // params[page]=1，后端永远收不到（本项目在文章相关接口上踩过这个坑）
    return api.get<Page<GuestbookMessageAdmin>>('/guestbook/manage', {
      page,
      page_size: pageSize,
      approved,
    })
  },

  /** 审核：`is_approved=true` 放行，`false` 撤回。 */
  moderate(id: number, isApproved: boolean) {
    return api.patch<GuestbookMessageAdmin>(`/guestbook/${id}`, { is_approved: isApproved })
  },

  /** 写回复；传空字符串表示清除回复（后端会把回复时间与回复人一并清空）。 */
  reply(id: number, replyContent: string) {
    return api.put<GuestbookMessageAdmin>(`/guestbook/${id}/reply`, {
      reply_content: replyContent,
    })
  },

  remove(id: number) {
    return api.delete<Message>(`/guestbook/${id}`)
  },
}
