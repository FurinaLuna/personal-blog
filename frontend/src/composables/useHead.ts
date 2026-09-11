/**
 * per-page <head> 管理：标题 + Open Graph / Twitter meta。
 *
 * SPA 的天然限制要先说清楚：这套 meta 是 JS 执行后才写进 DOM 的，
 * 对「只抓首屏 HTML」的爬虫（部分社交平台的分享卡片抓取器）不可见——
 * 那需要 SSR 或预渲染才能彻底解决。但它依然有价值：
 * 1. Google 等现代爬虫会执行 JS，能读到这些标签；
 * 2. document.title 影响浏览器标签页与历史记录，是纯体验收益；
 * 3. 成本极低，将来迁 SSR 时这套数据结构可以直接平移。
 */
import { onBeforeUnmount, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { useRoute } from 'vue-router'

export interface PageMeta {
  /** 页面标题；留空时回退到路由 meta.title，最终拼成「X · 个人博客」。 */
  title?: string
  description?: string
  /** og:image 的绝对或相对地址（相对地址会保持原样，部署时依赖站点根路径）。 */
  image?: string
  type?: 'website' | 'article'
}

const SITE_SUFFIX = '个人博客'
const DEFAULT_DESCRIPTION = '记录技术、生活，以及一切值得写下来的东西。'

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
    setMeta('meta[name="twitter:card"]', { name: 'twitter:card' }, 'summary')
    if (value.image) {
      setMeta('meta[property="og:image"]', { property: 'og:image' }, value.image)
    } else {
      // 不带封面时不留旧图——否则从详情页退回列表页，分享卡片还挂着上一篇的封面
      document.head.querySelector('meta[property="og:image"]')?.remove()
    }
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
