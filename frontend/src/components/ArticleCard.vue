<script setup lang="ts">
/** 文章卡片。列表页与首页共用，保证同一个视觉语言。 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { ArticleSummary } from '@/types'
import { formatCount, formatDate, formatReadingTime } from '@/utils/format'

const props = defineProps<{ article: ArticleSummary }>()

const publishedLabel = computed(
  () => formatDate(props.article.published_at ?? props.article.created_at),
)

const tagList = computed(() => props.article.tags.slice(0, 4))
</script>

<template>
  <article class="card card-hover group p-5 sm:p-6">
    <div class="flex flex-col gap-4 sm:flex-row">
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <span
            v-if="article.is_top"
            class="inline-flex items-center rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
          >
            置顶
          </span>
          <span
            v-if="article.status !== 'published'"
            class="inline-flex items-center rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-900/40 dark:text-amber-200"
          >
            {{ article.status === 'draft' ? '草稿' : '已归档' }}
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
          >
            {{ article.title }}
          </RouterLink>
        </h2>

        <p v-if="article.summary" class="mt-2 line-clamp-2 text-sm leading-relaxed text-ink-soft">
          {{ article.summary }}
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
        class="block shrink-0 overflow-hidden rounded-lg sm:w-40"
      >
        <img
          :src="article.cover_image"
          :alt="article.title"
          loading="lazy"
          decoding="async"
          class="h-32 w-full object-cover transition-transform duration-300 group-hover:scale-[1.03] sm:h-28 sm:w-40"
        />
      </RouterLink>
    </div>
  </article>
</template>
