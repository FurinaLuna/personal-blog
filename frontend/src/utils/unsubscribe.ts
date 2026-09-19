/**
 * 从退订链接里取出 token。
 *
 * ## 为什么 token 在 fragment 而不是 query
 *
 * fragment（`#` 之后）由浏览器保留在本地，**不会随请求发给服务器**，
 * 因此不会落进 nginx 访问日志、Referer 头或任何中间代理的日志里。
 * query string 则会 —— 而这是一个有效期 10 年、凭它就能为任意邮箱退订的凭证。
 *
 * ## 为什么还兼容 query
 *
 * 改成 fragment 之前发出的邮件里是 `?token=xxx`，那些链接有 10 年有效期。
 * 只认新格式等于让已发出的邮件全部失效，而用户点进去只会看到「链接不完整」。
 */

/** 从 `#token=xxx` 形式的 hash 里取 token；取不到返回空串。 */
function fromHash(hash: string): string {
  const matched = /(?:^#|&)token=([^&]*)/.exec(hash)
  if (!matched?.[1]) return ''
  try {
    return decodeURIComponent(matched[1])
  } catch {
    // 手工改坏的 hash 里可能有非法的百分号转义，解码失败就当没取到
    return ''
  }
}

/** 从 `?token=xxx` 形式的 query 里取 token；数组只取第一个。 */
function fromQuery(query: unknown): string {
  if (Array.isArray(query)) return typeof query[0] === 'string' ? query[0] : ''
  return typeof query === 'string' ? query : ''
}

/**
 * 优先读 fragment（当前格式），读不到再回退到 query（历史邮件）。
 *
 * 两个来源同时存在时以 fragment 为准：它是当前格式，且不会被服务端记录。
 */
export function extractUnsubscribeToken(hash: string | undefined, query: unknown): string {
  return fromHash(hash ?? '') || fromQuery(query)
}
