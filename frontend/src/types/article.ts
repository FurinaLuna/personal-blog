/** 文章领域（含按月归档）。 */

import type { ImageVariant } from './attachment'
import type { SeriesBrief } from './series'
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
  /** 封面多尺寸变体（按宽度升序），拼 srcset 用；外链封面或无变体时为空 */
  cover_variants: ImageVariant[]
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
  series: SeriesBrief | null
  series_order: number
  tags: TagBrief[]
  comment_count: number
}

export interface ArticleDetail extends ArticleSummary {
  content_md: string
  prev: ArticleNeighbor | null
  next: ArticleNeighbor | null
  /** 同系列内按 series_order 相邻的文章（详情页系列导航条） */
  series_prev: ArticleNeighbor | null
  series_next: ArticleNeighbor | null
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
  series_id?: number | null
  series_order?: number
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
