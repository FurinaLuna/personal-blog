/** 文章版本历史。 */

export interface RevisionAuthorBrief {
  id: number
  username: string
  nickname: string | null
}

export interface Revision {
  id: number
  article_id: number
  title: string
  summary: string | null
  /** 仅单版本接口返回；列表接口为 null（避免响应上兆） */
  content_md: string | null
  content_length: number
  /** save = 保存前的快照；restore = 恢复前的快照 */
  reason: string
  /** 这一版是因为要恢复到哪个版本而产生的 */
  restored_from_id: number | null
  author: RevisionAuthorBrief | null
  created_at: string
}
