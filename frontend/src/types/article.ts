/** 文章领域（含按月归档）。 */

import type { CategoryBrief, TagBrief } from './taxonomy'
import type { UserBrief } from './user'

export type ArticleStatus = 'draft' | 'published' | 'archived'

/** 排序白名单，与后端 ArticleSort 枚举保持一致 */
export type ArticleSort = 'latest' | 'oldest' | 'hottest' | 'updated' | 'title'

export interface ArticleNeighbor {
  id: number
  title: string
  slug: string
}

export interface ArticleSummary {
  id: number
  title: string
  slug: string
  summary: string | null
  cover_image: string | null
  status: ArticleStatus
  is_top: boolean
  allow_comment: boolean
  view_count: number
  like_count: number
  reading_time: number
  published_at: string | null
  created_at: string
  updated_at: string
  author: UserBrief | null
  category: CategoryBrief | null
  tags: TagBrief[]
  comment_count: number
}

export interface ArticleDetail extends ArticleSummary {
  content_md: string
  prev: ArticleNeighbor | null
  next: ArticleNeighbor | null
}

export interface ArticlePayload {
  title: string
  slug?: string | null
  summary?: string | null
  content_md: string
  cover_image?: string | null
  status?: ArticleStatus
  is_top?: boolean
  allow_comment?: boolean
  category_id?: number | null
  tags?: string[]
}

export type ArticleUpdatePayload = Partial<ArticlePayload>

export interface ArticleListQuery {
  page?: number
  page_size?: number
  keyword?: string
  category?: string
  tag?: string
  author_id?: number
  sort?: ArticleSort
}

export interface ManagedArticleQuery extends ArticleListQuery {
  status?: ArticleStatus
}

export interface ArchiveItem {
  id: number
  title: string
  slug: string
  published_at: string | null
}

export interface ArchiveGroup {
  year_month: string
  count: number
  items: ArchiveItem[]
}
