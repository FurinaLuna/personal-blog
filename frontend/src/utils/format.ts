/** 展示层格式化工具。全站日期/数字/图片属性的展示口径都在这里，避免各页面写法不一致。 */

import type { ImageVariant } from '@/types'

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/**
 * 把后端的 ISO 时间串格式化成日期。
 *
 * 后端可能返回带时区的时间（`2026-09-11T21:48:00+08:00`），也可能因为 SQLite
 * 存的是无时区值而返回 `2026-09-11T13:48:00`。后者 `new Date()` 会按本地时区
 * 解析——对这个项目是可接受的（同一台服务器），但要在文档里说清楚：
 * 换成 PostgreSQL 并统一 `timezone=True` 之后行为才完全一致。
 */
function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDate(value: string | null | undefined): string {
  const date = parse(value)
  return date ? dateFormatter.format(date) : ''
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parse(value)
  return date ? dateTimeFormatter.format(date) : ''
}

/** 相对时间：一周内用「3 天前」，更久则回落到具体日期。 */
export function formatRelative(value: string | null | undefined): string {
  const date = parse(value)
  if (!date) return ''

  const diffMs = Date.now() - date.getTime()
  const minutes = Math.floor(diffMs / 60000)

  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`

  return formatDate(value)
}

/** 数字缩写：1234 -> 1.2k。列表页的阅读量用它，避免长数字挤占版面。 */
export function formatCount(value: number | null | undefined): string {
  // 计数字段可能缺失（旧缓存 / 部分接口不返回），null 一律按 0 展示，
  // 否则界面上会出现刺眼的 "null" / "NaN"
  const count = Number(value)
  if (!Number.isFinite(count)) return '0'
  if (count < 1000) return String(count)
  if (count < 10000) return `${(count / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return `${(count / 10000).toFixed(1).replace(/\.0$/, '')}w`
}

export function formatBytes(bytes: number | null | undefined): string {
  const size = Number(bytes)
  if (!Number.isFinite(size) || size < 0) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

export function formatReadingTime(minutes: number | null | undefined): string {
  const value = Number(minutes)
  if (!Number.isFinite(value) || value <= 1) return '少于 1 分钟'
  return `约 ${value} 分钟`
}

/** 把 `2026-09` 显示成 `2026 年 9 月`。 */
export function formatYearMonth(value: string | null | undefined): string {
  const [year, month] = (value ?? '').split('-')
  if (!year || !month) return value ?? ''
  return `${year} 年 ${Number(month)} 月`
}


/** 生成 URL 友好的 id，中文保留（浏览器地址栏仍可读）。 */
export function slugifyHeading(text: string): string {
  return (
    text
      .trim()
      .toLowerCase()
      .replace(/[^\w\u4e00-\u9fff]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60) || 'section'
  )
}

/** 把纯文本按字符数截断，超出补省略号。 */
export function truncate(text: string, limit: number): string {
  const value = (text ?? '').trim()
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`
}

/**
 * 把变体列表拼成 `<img srcset>` 属性值（`url 480w, url 800w, ...`）。
 *
 * 空列表返回空串：配合 `:srcset="buildSrcset(...) || undefined"`，
 * 没有变体的图片（外链封面 / 存量图）会退回 `src` 单图，行为与升级前一致。
 */
export function buildSrcset(variants: ImageVariant[]): string {
  return variants.map((variant) => `${variant.url} ${variant.width}w`).join(', ')
}

/**
 * 过滤出可以安全放进 `href` 的地址，不安全时返回 `null`（调用方渲染成纯文本）。
 *
 * 为什么需要：Vue **不清洗**动态 `href`，`javascript:` / `data:` 这类伪协议
 * 会被原样写进 DOM。评论的「网站」字段完全由访客控制，后端虽然已加了
 * http/https 校验，但**库里可能还留着修复之前存下的脏数据**，
 * 所以前端这道防线不是重复劳动，而是给存量数据兜底。
 */
export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null
  const candidate = value.trim()
  if (!candidate) return null
  try {
    // 用 URL 解析而不是正则：正则很容易被 `java\nscript:`、大小写混写、
    // 前置空白这类变体绕过；URL 解析走的是浏览器同一套规则。
    const url = new URL(candidate)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
