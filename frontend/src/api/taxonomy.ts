/** 分类与标签接口。 */
import { api } from './http'

import type { Category, CategoryPayload, Message, Tag, TagPayload } from '@/types'

export const categoryApi = {
  list(withCounts = true) {
    return api.get<Category[]>('/categories', { with_counts: withCounts })
  },
  detail(slugOrId: string | number) {
    return api.get<Category>(`/categories/${slugOrId}`)
  },
  create(payload: CategoryPayload) {
    return api.post<Category>('/categories', payload)
  },
  update(id: number, payload: Partial<CategoryPayload>) {
    return api.patch<Category>(`/categories/${id}`, payload)
  },
  remove(id: number) {
    return api.delete<Message>(`/categories/${id}`)
  },
}

export const tagApi = {
  list(options: { limit?: number; minCount?: number; withCounts?: boolean } = {}) {
    return api.get<Tag[]>('/tags', {
      limit: options.limit,
      min_count: options.minCount,
      with_counts: options.withCounts ?? true,
    })
  },
  create(payload: TagPayload) {
    return api.post<Tag>('/tags', payload)
  },
  update(id: number, payload: Partial<TagPayload>) {
    return api.patch<Tag>(`/tags/${id}`, payload)
  },
  remove(id: number) {
    return api.delete<Message>(`/tags/${id}`)
  },
  cleanup() {
    return api.post<Message>('/tags/cleanup')
  },
}
