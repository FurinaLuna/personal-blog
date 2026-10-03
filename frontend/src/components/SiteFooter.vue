<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import SiteUptime from '@/components/SiteUptime.vue'
import { useSiteStore } from '@/stores/site'
import { safeExternalUrl } from '@/utils/format'

const site = useSiteStore()

const year = new Date().getFullYear()

/**
 * 只保留能安全放进 `href` 的社交链接。
 *
 * 后端已按 `app.utils.url` 的口径校验（http/https），这里不是重复劳动：
 * 库里的值可能来自「后端补校验之前」的写入，属于存量脏数据。
 * 渲染在**每一页的页脚**上，一旦有 `javascript:` 值，点一下就在本站源内执行，
 * 所以判定放在渲染前，不安全的整条链接不渲染（`v-if`）。
 */
const links = computed(() =>
  site.socialLinks
    .map((link) => ({ ...link, href: safeExternalUrl(link.url) }))
    .filter((link): link is typeof link & { href: string } => link.href !== null),
)

/**
 * 个人介绍的首段（页脚用）。
 *
 * `bio_md` 是 Markdown，页脚只放得下一句话，所以取**第一个段落**：
 * 先去掉代码块（旧站的个人介绍里紧跟一段 C 代码的比喻），
 * 再取第一个非空行，最后抹掉 `**加粗**` 这类行内标记。
 *
 * 为什么不用 MarkdownRenderer：页脚是每个页面都渲染的地方，
 * 为一行文字引入完整渲染 + 消毒管线，收益远小于成本；
 * 而且渲染出的 `<p>` 嵌在 `<p>` 里会产生非法 HTML。
 */
const intro = computed(() => {
  const firstParagraph = (site.profile.bio_md ?? '')
    // 围栏代码块整体丢弃（``` 到 ``` 之间，含语言标注那行）
    .replace(/```[\s\S]*?```/g, '')
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.length > 0)

  return (firstParagraph ?? '').replace(/\*\*/g, '')
})
</script>

<template>
  <footer class="mt-24 border-t border-border bg-surface/60">
    <div class="mx-auto max-w-shell px-4 py-12 sm:px-6">
      <div class="flex flex-col gap-8 sm:flex-row sm:items-start sm:justify-between">
        <div class="max-w-md">
          <div class="flex items-center gap-2.5">
            <span
              class="flex h-7 w-7 items-center justify-center rounded-lg text-xs font-bold text-white shadow-sm"
              style="background-image: var(--gradient-brand)"
            >
              {{ site.title.slice(0, 1) }}
            </span>
            <p class="text-sm font-semibold text-ink">{{ site.title }}</p>
          </div>
          <p class="mt-3 text-sm leading-relaxed text-ink-soft">
            {{ site.headline || '记录技术、生活，以及一切值得写下来的东西。' }}
          </p>
          <!-- 个人介绍（旧站「关于」页首段），只取第一段：
               `bio_md` 里还跟着一段 C 代码的比喻，两行正文放不下，
               完整内容在「关于」页。这里用 firstParagraph 而不是直接渲染 markdown ——
               页脚只需要一句话，引入渲染器会把 `site-uptime` 这类无害内容也变成 DOM。 -->
          <p
            v-if="intro"
            class="mt-2 text-sm leading-relaxed text-ink-soft"
          >
            {{ intro }}
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
          <RouterLink to="/archive" class="text-ink-soft transition-colors hover:text-link">
            归档
          </RouterLink>
          <RouterLink to="/guestbook" class="text-ink-soft transition-colors hover:text-link">
            留言板
          </RouterLink>
          <!-- RSS 是后端直出的真实文件，不走前端路由，所以用 <a> 而不是 RouterLink -->
          <a href="/feed.xml" target="_blank" rel="noopener noreferrer" class="text-ink-soft transition-colors hover:text-link">
            RSS
          </a>
          <a
            v-for="link in links"
            :key="link.url"
            :href="link.href"
            target="_blank"
            rel="noopener noreferrer"
            class="text-ink-soft transition-colors hover:text-link"
          >
            {{ link.label }}
          </a>
        </div>
      </div>

      <div
        class="mt-10 flex flex-col gap-2 border-t border-border pt-6 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between"
      >
        <div class="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
          <p>© {{ year }} {{ site.title }}. 保留所有权利。</p>
          <!-- 建站时长：对应旧站页脚由 /js/timeDate.js 写入的那一行 -->
          <SiteUptime />
        </div>
        <p v-if="site.profile.icp">{{ site.profile.icp }}</p>
      </div>
    </div>
  </footer>
</template>
