/** 站点档案与统计领域。 */

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
