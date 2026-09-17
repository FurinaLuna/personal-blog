/**
 * per-page <head> 管理：标题 + Open Graph / Twitter meta + canonical + JSON-LD。
 *
 * SPA 的天然限制要先说清楚：这套标签是 JS 执行后才写进 DOM 的，
 * 对「只抓首屏 HTML」的爬虫（部分社交平台的分享卡片抓取器）不可见——
 * 那需要 SSR 或预渲染才能彻底解决。但它依然有价值：
 * 1. Google 等现代爬虫会执行 JS，能读到这些标签；
 * 2. document.title 影响浏览器标签页与历史记录，是纯体验收益；
 * 3. 成本极低，将来迁 SSR 时这套数据结构可以直接平移。
 */
import { onBeforeUnmount, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { useRoute } from 'vue-router'

/** schema.org 的结构化数据对象。字段随类型不同，这里不做收窄。 */
export type JsonLd = Record<string, unknown>

export interface PageMeta {
  /** 页面标题；留空时回退到路由 meta.title，最终拼成「X · 个人博客」。 */
  title?: string
  description?: string
  /** og:image 的绝对或相对地址（相对地址会保持原样，部署时依赖站点根路径）。 */
  image?: string
  type?: 'website' | 'article'
  /**
   * 规范链接（绝对地址）。留空时自动生成「当前路径」的自引用 canonical。
   *
   * 文章详情页**必须显式传自己的 slug 地址**：`/article/{slug}` 与
   * `/article/{id}` 是同一篇文章的两个可达 URL，不指明规范版本
   * 会被搜索引擎当成重复内容，权重被摊薄。
   */
  canonical?: string
  /** JSON-LD 结构化数据（schema.org）。留空表示这个页面没有。 */
  jsonLd?: JsonLd | null
}

const SITE_SUFFIX = '个人博客'
const DEFAULT_DESCRIPTION = '记录技术、生活，以及一切值得写下来的东西。'
/** 固定 id：管理同一个节点比每次插入再清理要可靠得多 */
const JSON_LD_ID = 'blog-json-ld'

/** 找到或创建指定 meta 标签并写入内容。 */
function setMeta(selector: string, attrs: Record<string, string>, content: string): void {
  let el = document.head.querySelector<HTMLMetaElement>(selector)
  if (!el) {
    el = document.createElement('meta')
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value)
    document.head.appendChild(el)
  }
  el.setAttribute('content', content)
}

/** 写入（或移除）`<link rel="canonical">`。 */
function setCanonical(href: string | null): void {
  const existing = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]')
  if (!href) {
    existing?.remove()
    return
  }
  const link = existing ?? document.createElement('link')
  if (!existing) {
    link.rel = 'canonical'
    document.head.appendChild(link)
  }
  link.href = href
}

/**
 * 写入（或移除）JSON-LD 数据块。
 *
 * 说明一点：`<script type="application/ld+json">` 是**数据块不是可执行脚本**，
 * 浏览器不会对它施加 CSP 的 script-src 限制，因此也就没有 XSS 面——
 * 何况这里的值全部来自本站数据，且以 JSON 而非 HTML 的形式写入。
 */
function setJsonLd(data: JsonLd | null | undefined): void {
  const existing = document.getElementById(JSON_LD_ID)
  if (!data) {
    existing?.remove()
    return
  }

  let script: HTMLScriptElement
  if (existing instanceof HTMLScriptElement) {
    script = existing
  } else {
    existing?.remove()
    script = document.createElement('script')
    script.type = 'application/ld+json'
    script.id = JSON_LD_ID
    document.head.appendChild(script)
  }

  try {
    script.textContent = JSON.stringify(data)
  } catch {
    // 循环引用之类：宁可不输出结构化数据，也不能让页面渲染挂掉
    console.debug('[useHead] JSON-LD 序列化失败，已跳过')
    script.remove()
  }
}

/** 当前页面的自引用规范地址（不含查询串）。 */
function selfCanonical(): string {
  return new URL(window.location.pathname, window.location.origin).href
}

export function useHead(meta: MaybeRefOrGetter<PageMeta>): void {
  // 路由 afterEach 也会写 document.title（来自 meta.title），两者不是竞争关系：
  // 本 composable 以路由标题为「底」，业务标题为「盖」——没传 title 时透出路由标题，
  // 卸载时还原到（可能已经切换的）新路由标题，而不是粗暴重置为站点名。
  const route = useRoute()

  function apply(value: PageMeta): void {
    const baseTitle = value.title ?? (route.meta.title as string | undefined)
    const title = baseTitle ? `${baseTitle} · ${SITE_SUFFIX}` : SITE_SUFFIX
    const description = value.description?.trim() || DEFAULT_DESCRIPTION

    document.title = title
    setMeta('meta[name="description"]', { name: 'description' }, description)
    setMeta('meta[property="og:title"]', { property: 'og:title' }, title)
    setMeta('meta[property="og:description"]', { property: 'og:description' }, description)
    setMeta('meta[property="og:type"]', { property: 'og:type' }, value.type ?? 'website')
    setMeta('meta[property="og:url"]', { property: 'og:url' }, window.location.href)
    // 站点名与语言：分享卡片上会显示「来自哪个站」，缺了会显得很粗糙
    setMeta('meta[property="og:site_name"]', { property: 'og:site_name' }, SITE_SUFFIX)
    setMeta('meta[property="og:locale"]', { property: 'og:locale' }, 'zh_CN')
    setMeta('meta[name="twitter:card"]', { name: 'twitter:card' }, 'summary')
    setMeta('meta[name="twitter:title"]', { name: 'twitter:title' }, title)
    setMeta('meta[name="twitter:description"]', { name: 'twitter:description' }, description)
    if (value.image) {
      setMeta('meta[property="og:image"]', { property: 'og:image' }, value.image)
      setMeta('meta[name="twitter:image"]', { name: 'twitter:image' }, value.image)
    } else {
      // 不带封面时不留旧图——否则从详情页退回列表页，分享卡片还挂着上一篇的封面
      document.head.querySelector('meta[property="og:image"]')?.remove()
      document.head.querySelector('meta[name="twitter:image"]')?.remove()
    }

    // canonical 默认自引用当前路径（丢掉查询串：排序/筛选参数不构成独立内容）
    setCanonical(value.canonical ?? selfCanonical())
    setJsonLd(value.jsonLd)
  }

  const stop = watch(
    () => toValue(meta),
    (value) => apply(value),
    { immediate: true, deep: true },
  )

  onBeforeUnmount(() => {
    stop()
    // 卸载时新路由往往已生效，route.meta.title 取到的是新页面的标题，
    // 正好把标题交还给路由层管理
    apply({})
  })
}
