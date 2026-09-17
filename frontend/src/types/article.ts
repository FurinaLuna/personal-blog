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
  /** 搜索结果专用：正文里命中关键词的那一段（纯文本，高亮由前端做）。其余接口为 null。 */
  snippet?: string | null
  /**
   * 已排期但还没到发布时间（后端派生字段）。
   *
   * 这类文章的 `status` 仍是 `published`，只是 `published_at` 在未来。
   * 后台靠它显示「定时发布」，否则界面写「已发布」而前台搜不到，很费解。
   */
  is_scheduled: boolean
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
  /**
   * 发布时间（ISO 字符串）。
   *
   * 传**未来时间**即为定时发布：到点前对访客不可见（详情 404、列表不出现、
   * 不接受评论），到点后自动浮现，不需要任何定时任务。
   */
  published_at?: string | null
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
