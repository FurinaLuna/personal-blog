/** 文章接口。 */
import { api } from './http'

import type {
  ArticleDetail,
  ArticleListQuery,
  ArticlePayload,
  ArticleSummary,
  ArticleUpdatePayload,
  ArchiveGroup,
  ManagedArticleQuery,
  Page,
} from '@/types'

export const articleApi = {
  /** 前台列表：只返回已发布文章 */
  list(query: ArticleListQuery = {}) {
    // 空参数的清洗统一由 api.get 的 cleanParams 处理
    return api.get<Page<ArticleSummary>>('/articles', { ...query })
  },

  /** 后台列表：作者看自己的（含草稿），站长看全站 */
  listManaged(query: ManagedArticleQuery = {}) {
    return api.get<Page<ArticleSummary>>('/articles/manage/list', { ...query })
  },

  detail(slugOrId: string | number) {
    return api.get<ArticleDetail>(`/articles/${slugOrId}`)
  },

  related(id: number, limit = 5) {
    // 第二参是扁平的查询参数对象（旧实现误写成 { params: { limit } }，
    // axios 会序列化成 params[limit]=5，后端从未收到过这个 limit）
    return api.get<ArticleSummary[]>(`/articles/${id}/related`, { limit })
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
}
