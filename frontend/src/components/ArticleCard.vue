<script setup lang="ts">
/** 文章卡片。列表页与首页共用，保证同一个视觉语言。 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { ArticleSummary } from '@/types'
import { buildSrcset, formatCount, formatDate, formatReadingTime } from '@/utils/format'
import { highlightSegments } from '@/utils/highlight'
import { statusBadgeClass, statusLabel } from '@/utils/status'
import { prefetchRoute } from '@/utils/prefetch'

const props = withDefaults(
  defineProps<{
    article: ArticleSummary
    /**
     * 首屏第一张卡片：封面用 eager + fetchpriority=high 参与 LCP，
     * 其余卡片保持 lazy。传 true 的应当只有一两条。
     */
    priority?: boolean
    /**
     * 搜索关键词。传入后标题 / 摘要 / 命中片段里命中的部分会被标出来，
     * 让用户一眼看到「为什么这条会出现」。列表页不传，行为与之前完全一致。
     */
    highlight?: string
  }>(),
  { priority: false, highlight: '' },
)

/**
 * 分段交给 `utils/highlight` 做，模板只负责渲染 `<mark>`。
 *
 * 刻意**不拼 HTML 再用 v-html**：正文与搜索词都是外部输入，
 * 拼 HTML 就多了一处必须消毒的地方；分段 + 插值天然转义，没有注入面。
 */
const titleSegments = computed(() => highlightSegments(props.article.title, props.highlight))
const summarySegments = computed(() => highlightSegments(props.article.summary, props.highlight))
const snippetSegments = computed(() => highlightSegments(props.article.snippet, props.highlight))

const publishedLabel = computed(
  () => formatDate(props.article.published_at ?? props.article.created_at),
)

const tagList = computed(() => props.article.tags.slice(0, 4))

/**
 * 悬停/聚焦即预取详情页 chunk。
 *
 * 绑定 mouseenter 与 focusin：键盘用户 Tab 到链接时同样受益，
 * 这两种输入方式在触屏上都不触发，等于零开销。
 */
function warm(): void {
  prefetchRoute('article')
}
</script>

<template>
  <article class="card card--hover group p-6">
    <!-- 移动端封面在上（16:9 横幅），桌面端封面在右；flex-col-reverse 保持 DOM 顺序不变 -->
    <div class="flex flex-col-reverse gap-4 sm:flex-row">
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <span
            v-if="article.is_top"
            class="inline-flex items-center rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
          >
            置顶
          </span>
          <span
            v-if="article.status !== 'published' || article.is_scheduled"
            class="inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium"
            :class="statusBadgeClass(article.status, article.is_scheduled)"
          >
            {{ statusLabel(article.status, { scheduled: article.is_scheduled }) }}
          </span>
          <RouterLink
            v-if="article.category"
            :to="{ path: '/', query: { category: article.category.slug } }"
            class="text-xs font-medium text-brand-600 transition-colors hover:text-brand-700"
          >
            {{ article.category.name }}
          </RouterLink>
        </div>

        <h2 class="mt-2 text-lg font-semibold leading-snug">
          <RouterLink
            :to="`/article/${article.slug}`"
            class="text-ink transition-colors group-hover:text-brand-600"
            @mouseenter="warm"
            @focusin="warm"
          >
            <template v-for="(segment, index) in titleSegments" :key="index">
              <mark v-if="segment.match" class="hl">{{ segment.text }}</mark>
              <template v-else>{{ segment.text }}</template>
            </template>
          </RouterLink>
        </h2>

        <!-- 命中片段优先于摘要：正文命中时标题与摘要里可能一个字都没有，
             不给出处用户只能靠猜"这条为什么会出现"。 -->
        <p
          v-if="snippetSegments.length"
          class="mt-2 line-clamp-2 text-sm leading-relaxed text-ink-soft"
        >
          <template v-for="(segment, index) in snippetSegments" :key="index">
            <mark v-if="segment.match" class="hl">{{ segment.text }}</mark>
            <template v-else>{{ segment.text }}</template>
          </template>
        </p>
        <p
          v-else-if="article.summary"
          class="mt-2 line-clamp-2 text-sm leading-relaxed text-ink-soft"
        >
          <template v-for="(segment, index) in summarySegments" :key="index">
            <mark v-if="segment.match" class="hl">{{ segment.text }}</mark>
            <template v-else>{{ segment.text }}</template>
          </template>
        </p>

        <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint">
          <time :datetime="article.published_at ?? article.created_at">{{ publishedLabel }}</time>
          <span>{{ formatReadingTime(article.reading_time) }}</span>
          <span class="inline-flex items-center gap-1">
            <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z" />
              <circle cx="12" cy="12" r="2.5" />
            </svg>
            {{ formatCount(article.view_count) }}
          </span>
          <span class="inline-flex items-center gap-1">
            <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.4-4.2A8 8 0 1 1 21 12Z" />
            </svg>
            {{ article.comment_count }}
          </span>
        </div>

        <div v-if="tagList.length" class="mt-3 flex flex-wrap gap-1.5">
          <RouterLink
            v-for="tag in tagList"
            :key="tag.id"
            :to="{ path: '/', query: { tag: tag.slug } }"
            class="chip"
          >
            # {{ tag.name }}
          </RouterLink>
        </div>
      </div>

      <RouterLink
        v-if="article.cover_image"
        :to="`/article/${article.slug}`"
        class="block shrink-0 overflow-hidden rounded-lg bg-surface-muted sm:w-40"
        @mouseenter="warm"
        @focusin="warm"
      >
        <!-- aspect 占位：图片加载完成前容器就有正确高度，列表不会因图片到达而上下跳动。
             移动端 16:9 横幅，桌面端 10:7 缩略图。
             srcset 有变体时浏览器按视口/密度自选档位（AVIF/WEBP 远小于原图），
             没有变体时属性不渲染，退回 src 单图。 -->
        <img
          :src="article.cover_image"
          :srcset="buildSrcset(article.cover_variants) || undefined"
          sizes="(min-width: 640px) 160px, 100vw"
          :alt="article.title"
          :loading="priority ? 'eager' : 'lazy'"
          :fetchpriority="priority ? 'high' : 'auto'"
          decoding="async"
          class="aspect-video w-full object-cover transition-transform duration-[var(--duration-slow)] group-hover:scale-[1.03] sm:aspect-[10/7] sm:w-40"
        />
      </RouterLink>
    </div>
  </article>
</template>
