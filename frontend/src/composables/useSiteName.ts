/**
 * 站点名与分享描述的**唯一来源**。
 *
 * ## 为什么单独抽出来
 *
 * 这两处此前各自写死了 '个人博客'：
 *
 * 1. `router/index.ts` 的 `afterEach`：`const base = '个人博客'` —— 写 `document.title`；
 * 2. `composables/useHead.ts` 的 `SITE_SUFFIX` / `DEFAULT_DESCRIPTION` —— 写
 *    `<title>`、`og:title`、`og:site_name`，以及没传 description 时的兜底描述。
 *
 * 于是把站点名改成旧站的名字（`芙芙\`s Home`）时，浏览器标签页仍然是
 * 「关于 · 个人博客」、分享卡片上仍写着旧站名 —— **改一半等于没改**，
 * 而且这类"两处常量"迟早会漂移。现在只有这里一份。
 *
 * ## 为什么是 composable 而不是把常量挪进 store
 *
 * 兜底值的判断（空串、只有空白、资料还没加载）是**展示层**的事：
 * store 里 `title` 已经把空值兜成 '个人博客'，但那个兜底本身也是写死的。
 * 放在这里，路由层（不在组件里也能调，Pinia 实例已经是激活的）与
 * `useHead` 都能用，且测试只需 mock 一次。
 */
import { computed } from 'vue'

import { useSiteStore } from '@/stores/site'

/**
 * 站点资料还没加载出来时的兜底名。
 *
 * 静态站名无从得知（它在数据库里），所以只能给一个中性值：
 * 资料到位后 `siteName` 会变成真实名字，`useHead` 的 watch 会重写那几个标签。
 */
export const FALLBACK_SITE_NAME = '个人博客'

/** 站点名（响应式）。资料未加载或为空时回落到 `FALLBACK_SITE_NAME`。 */
export function useSiteName() {
  const site = useSiteStore()
  return computed(() => site.title.trim() || FALLBACK_SITE_NAME)
}

/**
 * 分享卡片（OG / Twitter）没有显式描述时的兜底文案。
 *
 * 取站点副标题（旧站的 `slogan`，如 "Something for nothing"）；没有就再兜一层。
 * 此前这里写死的是「记录技术、生活，以及一切值得写下来的东西。」——
 * 那是演示数据的文案，会在**每一页**的分享卡片上冒充站点描述。
 */
export function useSiteDescription() {
  const site = useSiteStore()
  return computed(
    () => site.headline.trim() || '记录技术、生活，以及一切值得写下来的东西。',
  )
}
