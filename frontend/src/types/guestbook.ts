/** 留言板。
 *
 * 与评论的区别（决定了这里为什么是独立领域而不是复用评论）：
 * - 留言不属于任何文章，因此没有 `article_id` / 两级回复树；
 * - 一条留言只有**一个**站长回复，直接挂在留言记录上（`reply_content`）——
 *   结构上就不可能出现"回复的回复"，前端也不必渲染缩进层级；
 * - 访客可以留邮箱（不公开），站长回复时用它发一封通知。
 */

export interface GuestbookMessage {
  id: number
  author_name: string
  /** 后端已保证是 http/https 的绝对地址；为空表示访客没留 */
  author_site: string | null
  content: string
  /** 先审后发：未过审的只有站长看得到 */
  is_approved: boolean
  /** 站长回复；为空表示还没回（清空回复也是置回 null） */
  reply_content: string | null
  replied_at: string | null
  created_at: string
}

/** 站长视角的留言：多出联系方式与来源信息（公开接口**不含**这些字段）。 */
export interface GuestbookMessageAdmin extends GuestbookMessage {
  author_email: string | null
  ip_address: string | null
}

/** 发表留言。登录用户可不填昵称（后端取账号昵称）。 */
export interface GuestbookMessagePayload {
  author_name?: string | null
  author_email?: string | null
  author_site?: string | null
  content: string
}
