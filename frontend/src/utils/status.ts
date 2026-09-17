/**
 * 文章状态的**唯一出处**：文案 + 徽标配色。
 *
 * 之前这套表达散在 6 个地方（format.ts 的文案表、ArticleListView 的配色表、
 * ArticleCard 与 ArticleDetailView 的内联三元），改一次要搜六个文件，
 * 而且很容易只改一半。现在所有视图都从这里取。
 */
import type { ArticleStatus } from '@/types'

export interface StatusMeta {
  /** 常规文案 */
  label: string
  /** 作者预览自己草稿时的文案（只有 draft 需要区分语境） */
  previewLabel: string
  /** 徽标配色 class */
  badgeClass: string
}

const NEUTRAL_BADGE = 'bg-surface-muted text-ink-soft'
/** 定时发布是「已发布」的一个子状态，用独立配色让它一眼可辨 */
const SCHEDULED_BADGE = 'bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-200'
const SCHEDULED_LABEL = '定时发布'
const SCHEDULED_PREVIEW_LABEL = '定时发布（仅你可见）'

const META: Record<ArticleStatus, StatusMeta> = {
  published: {
    label: '已发布',
    previewLabel: '已发布',
    badgeClass: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200',
  },
  draft: {
    label: '草稿',
    previewLabel: '草稿（仅你可见）',
    badgeClass: 'bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200',
  },
  archived: {
    label: '已归档',
    previewLabel: '已归档',
    // 归档是「退出视野」的状态，用中性色而不是彩色；语义令牌自动适配亮暗
    badgeClass: NEUTRAL_BADGE,
  },
}

export interface StatusLabelOptions {
  /** 作者视角：草稿文案变成「仅你可见」，避免作者以为访客也看得到 */
  preview?: boolean
  /**
   * 已排期但还没到发布时间。
   *
   * 后台列表里这类文章的 `status` 就是 `published`，不额外标记的话
   * 界面显示「已发布」而前台又搜不到，作者会以为是自己搞错了。
   */
  scheduled?: boolean
}

/**
 * 状态文案。
 *
 * 未知状态（后端加了新状态而前端还没跟上）原样返回，不抛错也不显示空白——
 * 界面降级成显示原始值，比整块徽标消失更容易发现。
 */
export function statusLabel(status: ArticleStatus | string, options: StatusLabelOptions = {}): string {
  // 定时发布优先于普通状态：它描述的是「什么时候可见」，比「已发布」更具体
  if (options.scheduled && status === 'published') {
    return options.preview ? SCHEDULED_PREVIEW_LABEL : SCHEDULED_LABEL
  }
  const meta = META[status as ArticleStatus]
  if (!meta) return status
  return options.preview ? meta.previewLabel : meta.label
}

/** 状态徽标配色；未知状态用中性色。 */
export function statusBadgeClass(status: ArticleStatus | string, scheduled = false): string {
  if (scheduled && status === 'published') return SCHEDULED_BADGE
  return META[status as ArticleStatus]?.badgeClass ?? NEUTRAL_BADGE
}
