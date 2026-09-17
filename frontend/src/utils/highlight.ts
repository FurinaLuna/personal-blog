/**
 * 关键词高亮切分。
 *
 * 返回**分段数组**而不是拼好的 HTML 字符串——这是刻意的安全选择：
 * 拼 HTML 就得用 `v-html`，而正文与搜索词都来自外部输入，
 * 消毒一旦漏一处就是 XSS。分段数组由模板用 `<mark>` 渲染，
 * Vue 的插值天然转义，不存在注入面。
 */

export interface HighlightSegment {
  text: string
  /** 这一段是不是命中的关键词（模板据此决定要不要包 <mark>） */
  match: boolean
}

/** 转义正则元字符，否则搜索词里的 `(` `*` `.` 会让 RegExp 抛错或语义全变。 */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 把文本按关键词切成「命中 / 未命中」交替的片段。
 *
 * - 关键词为空或文本为空时，原样返回单段（调用方不必写分支）；
 * - **大小写不敏感**，与后端 LIKE 的行为一致；
 * - 空字符串片段会被丢掉，避免渲染出多余的 `<mark></mark>`。
 */
export function highlightSegments(
  text: string | null | undefined,
  keyword: string,
): HighlightSegment[] {
  const source = text ?? ''
  const needle = keyword.trim()
  if (!source || !needle) return source ? [{ text: source, match: false }] : []

  const pattern = new RegExp(`(${escapeRegExp(needle)})`, 'gi')
  return source
    .split(pattern)
    .filter((part) => part !== '')
    .map((part) => ({ text: part, match: part.toLowerCase() === needle.toLowerCase() }))
}
