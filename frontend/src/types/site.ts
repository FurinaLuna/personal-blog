/** 站点档案与统计领域。 */

export interface SocialLink {
  label: string
  url: string
  icon?: string | null
}

/** 联系方式二维码的种类。决定弹层里的图标与缺省文案。 */
export type ContactQrcodeKind = 'wechat' | 'qq'

export interface ContactQrcode {
  /** 决定图标与默认文案 */
  kind: ContactQrcodeKind
  /** 展示名，缺省按 kind 显示「微信」/「QQ」 */
  label: string
  /** 二维码图片地址；与 value 至少填一个 */
  image_url: string | null
  /** 微信号/QQ号，展示并提供一键复制 */
  value: string | null
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
  /** 电梯栏「联系站长」弹层的数据源；为 null / 空数组时整个入口不渲染。 */
  contact_qrcodes: ContactQrcode[] | null
  comment_need_approval: boolean
  allow_guest_comment: boolean
  /** 前台顶栏是否显示「登录」入口。只是入口开关，`/login` 路由始终可用。 */
  show_login_entry: boolean
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
