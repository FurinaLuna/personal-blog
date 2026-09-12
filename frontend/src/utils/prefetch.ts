/**
 * 路由级预取。
 *
 * 详情页是一个独立 chunk（约 18KB），默认要等用户点进去才开始下载——
 * 在弱网下这段时间就是白屏。卡片上悬停/聚焦时提前把 chunk 拉下来，
 * 点击时通常已经命中缓存，体感接近「秒开」。
 *
 * 为什么不用 prefetch 链接标签：`<link rel="prefetch">` 对 SPA 的动态 import
 * 不会自动去重，重复插入还会浪费带宽。这里直接用同一个动态 import 说明符，
 * 打包器会解析到同一 chunk，浏览器也只下载一次。
 */

/** 已触发的预取，避免同一页面内反复调用（模块级缓存，跨组件共享）。 */
const warmed = new Set<string>()

const LOADERS: Record<string, () => Promise<unknown>> = {
  article: () => import('@/views/ArticleDetailView.vue'),
  home: () => import('@/views/HomeView.vue'),
  archive: () => import('@/views/ArchiveView.vue'),
}

/**
 * 预取某个路由对应的 chunk。
 *
 * @param name 逻辑名（见 LOADERS），未知名字静默忽略——预取是优化，
 *             绝不能因为它抛错而影响用户操作
 */
export function prefetchRoute(name: keyof typeof LOADERS | string): void {
  if (warmed.has(name)) return
  const loader = LOADERS[name]
  if (!loader) return
  warmed.add(name)
  // 故意不 await：失败也无所谓，真正的导航会重新触发加载
  void loader().catch(() => {
    // 失败时把标记放回去，下次悬停还能再试一次
    warmed.delete(name)
  })
}
