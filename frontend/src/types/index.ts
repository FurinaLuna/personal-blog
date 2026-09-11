/**
 * 与后端 Pydantic Schema 一一对应的类型定义。
 *
 * 这里刻意手写而不是自动生成：接口数量不多，手写的类型读起来更直观，
 * 也方便加注释说明字段的**业务含义**（自动生成的只有字段名）。
 * 一旦后端 schema 变更，这里必须同步改——这是唯一的维护成本。
 */

/* ------------------------------------------------------------------ 通用 */

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface Message {
  detail: string
}

export interface ValidationIssue {
  field: string
  message: string
  type: string
}

/* ------------------------------------------------------------------ 用户 */

export type UserRole = 'admin' | 'author'

export interface UserBrief {
  id: number
  username: string
  nickname: string | null
  avatar_url: string | null
}

export interface User extends UserBrief {
  email: string
  bio: string | null
  role: UserRole
  is_active: boolean
  created_at: string
}

export interface Token {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface LoginRequest {
  username: string
  password: string
}

export interface UserCreatePayload {
  username: string
  email: string
  password: string
  nickname?: string | null
  role?: UserRole
}

export interface UserUpdatePayload {
  nickname?: string | null
  email?: string
  avatar_url?: string | null
  bio?: string | null
  role?: UserRole
  is_active?: boolean
}

/* ------------------------------------------------------------------ 分类标签 */

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

/* ------------------------------------------------------------------ 文章 */

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

/* ------------------------------------------------------------------ 附件 */

export interface Attachment {
  id: number
  original_name: string
  mime_type: string
  size: number
  kind: 'image' | 'file'
  width: number | null
  height: number | null
  url: string
  thumbnail_url: string | null
  created_at: string
  /** 可直接插入正文的 Markdown 片段 */
  markdown: string
}

/* ------------------------------------------------------------------ 评论 */

export interface Comment {
  id: number
  article_id: number
  parent_id: number | null
  author_name: string
  author_site: string | null
  content: string
  is_admin_reply: boolean
  is_approved: boolean
  created_at: string
  replies: Comment[]
}

export interface CommentPayload {
  author_name?: string | null
  author_email?: string | null
  author_site?: string | null
  content: string
  parent_id?: number | null
}

/* ------------------------------------------------------------------ 站点 */

export interface SocialLink {
  label: string
  url: string
  icon?: string | null
}

export interface SiteProfile {
  owner_name: string
  headline: string | null
  avatar_url: string | null
  bio_md: string | null
  about_md: string | null
  email: string | null
  location: string | null
  icp: string | null
  social_links: SocialLink[] | null
  skills: string[] | null
  comment_need_approval: boolean
  allow_guest_comment: boolean
  updated_at: string
}

export type SiteProfilePayload = Partial<Omit<SiteProfile, 'updated_at'>>

export interface SiteStats {
  article_total: number
  published_total: number
  draft_total: number
  category_total: number
  tag_total: number
  comment_total: number
  pending_comment_total: number
  total_views: number
  latest_published_at: string | null
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
