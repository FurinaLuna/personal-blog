/**
 * JSON-LD 结构化数据（schema.org）。
 *
 * 为什么值得做：本站已经产出了 RSS、sitemap 与 Open Graph 卡片，
 * 唯独缺了「告诉搜索引擎这是什么内容」这一层。有了它，搜索结果里才可能
 * 出现富摘要——带作者、发布日期、所属站点。对一篇技术文章来说，
 * 「几年前的旧文」和「上周刚更新」在点击率上差别很大，而这正是
 * dateModified 能表达的东西。
 *
 * 两个刻意的取舍：
 * 1. **只输出真实存在的字段**。schema.org 允许缺字段，但填 `null` 或空串
 *    会被判为无效数据，反而降低整体可信度。
 * 2. 相对 URL 统一补成绝对地址。JSON-LD 里给相对路径等于没给——
 *    消费方（搜索引擎）没有「当前页面」这个概念去解析它。
 */

import type { ArticleDetail, SiteProfile } from '@/types'

/** 把站内相对地址补成绝对地址；已经是绝对地址的原样返回。 */
export function absoluteUrl(value: string): string {
  if (!value) return ''
  if (/^https?:\/\//i.test(value)) return value
  return new URL(value, window.location.origin).href
}

/** 文章的规范地址：一律用 slug，绝不用 id（两者是同一篇的两个可达 URL）。 */
export function articleCanonicalUrl(slug: string): string {
  return new URL(`/article/${encodeURIComponent(slug)}`, window.location.origin).href
}

/** 去掉 undefined / null / 空串 / 空数组的字段。 */
function compact(input: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined || value === null) continue
    if (typeof value === 'string' && !value.trim()) continue
    if (Array.isArray(value) && value.length === 0) continue
    result[key] = value
  }
  return result
}

/** 文章详情的 `BlogPosting`。 */
export function buildArticleJsonLd(
  article: ArticleDetail,
  options: { siteName?: string; siteUrl?: string } = {},
): Record<string, unknown> {
  const url = articleCanonicalUrl(article.slug)

  return compact({
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: article.title,
    // description 用摘要；没有摘要时**不要**截正文——截断的正文当摘要
    // 在搜索结果里读起来很突兀，不如让搜索引擎自己取首段
    description: article.summary ?? undefined,
    url,
    mainEntityOfPage: { '@type': 'WebPage', '@id': url },
    // datePublished 缺失（草稿）时整个字段省略，而不是填 null
    datePublished: article.published_at ?? undefined,
    dateModified: article.updated_at || undefined,
    image: article.cover_image ? [absoluteUrl(article.cover_image)] : undefined,
    inLanguage: 'zh-CN',
    author: article.author
      ? compact({
          '@type': 'Person',
          name: article.author.nickname || article.author.username,
          url: new URL(`/about`, window.location.origin).href,
        })
      : undefined,
    publisher: options.siteName
      ? compact({
          '@type': 'Organization',
          name: options.siteName,
          url: options.siteUrl,
        })
      : undefined,
    // 分类与标签映射成 keywords / articleSection，让搜索引擎理解主题归属
    articleSection: article.category?.name ?? undefined,
    keywords: article.tags.map((tag) => tag.name),
    // 系列 → isPartOf，让连载能被识别成一组内容
    isPartOf: article.series
      ? compact({
          '@type': 'CreativeWorkSeries',
          name: article.series.name,
          url: new URL(
            `/series/${encodeURIComponent(article.series.slug)}`,
            window.location.origin,
          ).href,
        })
      : undefined,
    commentCount: article.comment_count || undefined,
    interactionStatistic: compact({
      '@type': 'InteractionCounter',
      interactionType: 'https://schema.org/LikeAction',
      userInteractionCount: article.like_count || undefined,
    }),
  })
}

/** 站点首页的 `WebSite` + `Person`（用 @graph 组合，一份数据描述两件事）。 */
export function buildSiteJsonLd(profile: SiteProfile): Record<string, unknown> {
  const siteUrl = new URL('/', window.location.origin).href
  const person = compact({
    '@type': 'Person',
    '@id': `${siteUrl}#owner`,
    name: profile.owner_name,
    description: profile.headline ?? undefined,
    image: profile.avatar_url ? absoluteUrl(profile.avatar_url) : undefined,
    email: profile.email ?? undefined,
    // 社交链接映射成 sameAs：这是搜索引擎确认「同一个人」的主要依据
    sameAs: (profile.social_links ?? [])
      .map((link) => link.url)
      .filter((url) => /^https?:\/\//i.test(url)),
  })

  return {
    '@context': 'https://schema.org',
    '@graph': [
      compact({
        '@type': 'WebSite',
        '@id': `${siteUrl}#website`,
        url: siteUrl,
        name: profile.owner_name,
        description: profile.headline ?? undefined,
        inLanguage: 'zh-CN',
        author: { '@id': `${siteUrl}#owner` },
      }),
      person,
    ],
  }
}
