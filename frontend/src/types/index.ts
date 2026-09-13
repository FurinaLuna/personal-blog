/**
 * 类型出口：与后端 Pydantic Schema 一一对应的类型定义，按领域拆分。
 *
 * 组件统一从 `@/types` 引入（本文件纯 re-export），领域文件内部可互相
 * import（如 ArticleSummary 引用 UserBrief），但方向必须是叶子向根部。
 *
 * 这里刻意手写而不是自动生成：接口数量不多，手写的类型读起来更直观，
 * 也方便加注释说明字段的**业务含义**（自动生成的只有字段名）。
 * 一旦后端 schema 变更，对应领域文件必须同步改——这是唯一的维护成本。
 */
export type { Message, Page, ValidationIssue } from './common'
export type {
  ArticleDetail,
  ArticleListQuery,
  ArticleNeighbor,
  ArticlePayload,
  ArticleSort,
  ArticleStatus,
  ArticleSummary,
  ArticleUpdatePayload,
  ArchiveGroup,
  ArchiveItem,
  ManagedArticleQuery,
} from './article'
export type { Attachment, BackfillResult, ImageVariant } from './attachment'
export type { Comment, CommentPayload } from './comment'
export type { Series, SeriesBrief, SeriesPayload, SeriesWithArticles } from './series'
export type { Category, CategoryBrief, CategoryPayload, Tag, TagBrief, TagPayload } from './taxonomy'
export type {
  LoginRequest,
  Token,
  User,
  UserBrief,
  UserCreatePayload,
  UserRole,
  UserUpdatePayload,
} from './user'
export type { SiteProfile, SiteProfilePayload, SiteStats, SocialLink } from './site'
export type { DailyViewStats } from './stats'
