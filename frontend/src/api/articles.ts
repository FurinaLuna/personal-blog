/** 文章接口。 */
import { api, request } from './http'

import type {
  ArticleDetail,
  ArticleListQuery,
  ArticlePayload,
  ArticleSummary,
  ArticleUpdatePayload,
  ArchiveGroup,
  ManagedArticleQuery,
  Message,
  Page,
} from '@/types'

/** 去掉值为 undefined / 空串的参数，避免发出 `?keyword=` 这种空筛选。 */
function clean(params: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    result[key] = value
  }
  return result
}

export const articleApi = {
  /** 前台列表：只返回已发布文章 */
  list(query: ArticleListQuery = {}) {
    return api.get<Page<ArticleSummary>>('/articles', clean({ ...query }))
  },

  /** 后台列表：作者看自己的（含草稿），站长看全站 */
  listManaged(query: ManagedArticleQuery = {}) {
    return api.get<Page<ArticleSummary>>('/articles/manage/list', clean({ ...query }))
  },

  detail(slugOrId: string | number) {
    return api.get<ArticleDetail>(`/articles/${slugOrId}`)
  },

  related(id: number, limit = 5) {
    return api.get<ArticleSummary[]>(`/articles/${id}/related`, { params: { limit } })
  },

  create(payload: ArticlePayload) {
    return api.post<ArticleDetail>('/articles', payload)
  },

  update(id: number, payload: ArticleUpdatePayload) {
    return api.patch<ArticleDetail>(`/articles/${id}`, payload)
  },

  remove(id: number) {
    return api.delete(`/articles/${id}`)
  },

  like(id: number) {
    return api.post<{ like_count: number }>(`/articles/${id}/like`)
  },

  archive() {
    return api.get<ArchiveGroup[]>('/articles/archive')
  },

  /** 上传封面图等场景：直接把文件当 multipart 发出去，并汇报进度 */
  uploadWithProgress<T>(url: string, file: File, onProgress?: (percent: number) => void) {
    const form = new FormData()
    form.append('file', file)
    return request<T>({
      method: 'POST',
      url,
      data: form,
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        if (!onProgress || !event.total) return
        onProgress(Math.round((event.loaded / event.total) * 100))
      },
    })
  },
}

export type { ArticleDetail, ArticleSummary, Message, Page }
