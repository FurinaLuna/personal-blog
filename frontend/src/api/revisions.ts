/** 文章版本历史接口。 */
import { api } from './http'

import type { ArticleDetail, Revision } from '@/types'

export const revisionApi = {
  /** 版本列表（**不含正文**，一屏几十条正文会让响应上兆）。 */
  list(articleId: number) {
    return api.get<Revision[]>(`/articles/${articleId}/revisions`)
  },

  /** 单个版本（含正文）。只在用户点开某一版时才请求。 */
  get(articleId: number, revisionId: number) {
    return api.get<Revision>(`/articles/${articleId}/revisions/${revisionId}`)
  },

  /**
   * 恢复到指定版本。
   *
   * 后端会先为**当前内容**留一版（reason=restore），所以恢复错了还能退回来；
   * 返回的是恢复后的文章详情，调用方直接用它刷新表单。
   */
  restore(articleId: number, revisionId: number) {
    return api.post<ArticleDetail>(`/articles/${articleId}/revisions/${revisionId}/restore`)
  },
}
