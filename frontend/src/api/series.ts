/** 系列接口。 */
import { api } from './http'

import type { Message, Series, SeriesPayload, SeriesWithArticles } from '@/types'

export const seriesApi = {
  /** 前台系列列表（带各系列文章数） */
  list(withCounts = true) {
    return api.get<Series[]>('/series', { with_counts: withCounts })
  },

  /** 系列详情：系列信息 + 该系列的文章（按系列内顺序分页） */
  detail(slugOrId: string | number, page = 1, pageSize = 20) {
    return api.get<SeriesWithArticles>(`/series/${slugOrId}`, { page, page_size: pageSize })
  },

  create(payload: SeriesPayload) {
    return api.post<Series>('/series', payload)
  },

  update(id: number, payload: Partial<SeriesPayload>) {
    return api.patch<Series>(`/series/${id}`, payload)
  },

  remove(id: number) {
    return api.delete<Message>(`/series/${id}`)
  },
}
