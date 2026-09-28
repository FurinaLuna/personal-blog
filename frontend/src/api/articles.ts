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

  /**
   * 全文检索：**按相关度排序**（标题权重最高，其次摘要，最后正文）。
   *
   * 与 `list({ keyword })` 的区别在语义：列表接口的关键词是「筛选」，
   * 结果仍按时间/热度排；这个接口回答的是「哪篇最相关」。
   */
  search(q: string, query: Omit<ArticleListQuery, 'keyword'> = {}) {
    return api.get<Page<ArticleSummary>>('/articles/search', { q, ...query })
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

  /**
   * 记一次阅读，返回**最新**阅读数。
   *
   * 为什么阅读计数不在详情 GET 里：详情响应体带每次 +1 的 `view_count` 时，
   * 基于响应体算的 ETag 必然每次都变，条件请求永远命中不了 304、
   * `max-age=60` 形同虚设。把计数挪到独立端点后，详情可以被真正缓存，
   * 而计数照旧每次访问 +1。前端在详情渲染后调它，把页面上那个（可能被缓存了
   * 60 秒的）数字刷新成准确值。
   */
  view(id: number) {
    return api.post<{ view_count: number }>(`/articles/${id}/view`)
  },

  archive() {
    return api.get<ArchiveGroup[]>('/articles/archive')
  },
}
