/** 评论领域。 */

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
