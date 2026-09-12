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
    badgeClass: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  },
}

export interface StatusLabelOptions {
  /** 作者视角：草稿文案变成「仅你可见」，避免作者以为访客也看得到 */
  preview?: boolean
}

/**
 * 状态文案。
 *
 * 未知状态（后端加了新状态而前端还没跟上）原样返回，不抛错也不显示空白——
 * 界面降级成显示原始值，比整块徽标消失更容易发现。
 */
export function statusLabel(status: ArticleStatus | string, options: StatusLabelOptions = {}): string {
  const meta = META[status as ArticleStatus]
  if (!meta) return status
  return options.preview ? meta.previewLabel : meta.label
}

/** 状态徽标配色；未知状态用中性色。 */
export function statusBadgeClass(status: ArticleStatus | string): string {
  return META[status as ArticleStatus]?.badgeClass ?? NEUTRAL_BADGE
}
