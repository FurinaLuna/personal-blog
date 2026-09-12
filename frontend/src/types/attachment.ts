/** 附件（媒体库）领域。 */

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
