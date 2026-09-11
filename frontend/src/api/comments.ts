/** 附件与评论接口。 */
import { api, http } from './http'

import type { Attachment, Comment, CommentPayload, Message, Page } from '@/types'

export const attachmentApi = {
  /**
   * 上传文件。
   *
   * 用 multipart 直接发 FormData，不手动设置 Content-Type ——
   * 手写会丢掉浏览器自动生成的 `boundary`，后端会解析失败。
   */
  async upload(file: File, onProgress?: (percent: number) => void): Promise<Attachment> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await http.post<Attachment>('/attachments/upload', form, {
      onUploadProgress: (event) => {
        if (!onProgress || !event.total) return
        onProgress(Math.round((event.loaded / event.total) * 100))
      },
    })
    return data
  },

  list(page = 1, pageSize = 24, kind?: 'image' | 'file') {
    return api.get<Page<Attachment>>('/attachments', {
      page,
      page_size: pageSize,
      kind,
    })
  },

  remove(id: number) {
    return api.delete<Message>(`/attachments/${id}`)
  },
}

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
    return api.delete<Message>(`/comments/${id}`)
  },
}
