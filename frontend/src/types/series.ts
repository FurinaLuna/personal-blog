/** 系列（技术连载 / 合集）领域。 */

import type { ArticleSummary } from './article'
import type { Page } from './common'

export interface SeriesBrief {
  id: number
  name: string
  slug: string
}

export interface Series extends SeriesBrief {
  description: string | null
  article_count: number
  created_at: string
  updated_at: string
}

export interface SeriesPayload {
  name: string
  slug?: string | null
  description?: string | null
}

export interface SeriesWithArticles {
  series: Series
  articles: Page<ArticleSummary>
}
