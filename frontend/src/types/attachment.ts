/** 附件（媒体库）领域。 */

/** 图片多尺寸变体的一档，width 可直接拼进 srcset 的 w 描述符。 */
export interface ImageVariant {
  width: number
  url: string
}

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
  /** 按宽度升序的多尺寸变体；小图 / GIF / 功能上线前的存量图片为空 */
  variants: ImageVariant[]
  created_at: string
  /** 可直接插入正文的 Markdown 片段 */
  markdown: string
}

/** 存量图片变体回填结果（站长运维接口）。 */
export interface BackfillResult {
  processed: number
  updated: number
  skipped: number
}
