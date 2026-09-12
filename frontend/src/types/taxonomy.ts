/** 分类与标签领域。 */

export interface Category {
  id: number
  name: string
  slug: string
  description: string | null
  sort_order: number
  created_at: string
  article_count: number
}

export interface CategoryBrief {
  id: number
  name: string
  slug: string
}

export interface CategoryPayload {
  name: string
  slug?: string | null
  description?: string | null
  sort_order?: number
}

export interface Tag {
  id: number
  name: string
  slug: string
  article_count: number
}

export interface TagBrief {
  id: number
  name: string
  slug: string
}

export interface TagPayload {
  name: string
  slug?: string | null
}
