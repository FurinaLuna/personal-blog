/** 评论接口。附件相关的接口在 `./attachments`，两者是不同的领域模块。 */
import { api } from './http'

import type { Comment, CommentPayload, Page } from '@/types'

export const commentApi = {
  listForArticle(articleId: number) {
    return api.get<Comment[]>(`/comments/article/${articleId}`)
  },

  create(articleId: number, payload: CommentPayload) {
    return api.post<Comment>(`/comments/article/${articleId}`, payload)
  },

  listModeration(params: { page?: number; pageSize?: number; approved?: boolean; articleId?: number }) {
    return api.get<Page<Comment>>('/comments', {
      page: params.page,
      page_size: params.pageSize,
      approved: params.approved,
      article_id: params.articleId,
    })
  },

  moderate(id: number, approved: boolean) {
    return api.patch<Comment>(`/comments/${id}`, { is_approved: approved })
  },

  remove(id: number) {
    return api.delete(`/comments/${id}`)
  },
}
