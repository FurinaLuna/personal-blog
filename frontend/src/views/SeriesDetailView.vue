<script setup lang="ts">
/** 系列详情：该系列的文章按阅读顺序排列（series_order 升序）。 */
import { computed, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { seriesApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { SeriesWithArticles } from '@/types'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const slugOrId = computed(() => route.params.slug as string)

const detail = useAsyncData<SeriesWithArticles>(
  () => seriesApi.detail(slugOrId.value, 1, 50),
  { series: null, articles: { items: [], total: 0, page: 1, page_size: 50, pages: 0 } } as unknown as SeriesWithArticles,
)

useHead(() => ({ title: detail.data.value?.series?.name ?? '系列' }))

onMounted(() => {
  void detail.run()
})
</script>

<template>
  <div class="space-y-6">
    <header v-if="detail.data.value?.series" class="border-b border-border pb-6">
      <div class="flex items-center gap-2 text-xs text-ink-faint">
        <RouterLink to="/series" class="hover:text-brand-600">系列</RouterLink>
        <span>/</span>
        <span>{{ detail.data.value.series.article_count }} 篇</span>
      </div>
      <h1 class="mt-2 text-xl font-semibold text-ink sm:text-2xl">
        {{ detail.data.value.series.name }}
      </h1>
      <p v-if="detail.data.value.series.description" class="mt-2 text-sm text-ink-soft">
        {{ detail.data.value.series.description }}
      </p>
    </header>

    <div v-if="detail.loading.value && !detail.ready.value" class="space-y-2">
      <div v-for="index in 5" :key="index" class="skeleton h-14 rounded-lg"></div>
    </div>

    <p v-else-if="detail.error.value" class="card p-6 text-center text-sm text-ink-soft">
      {{ toErrorMessage(detail.error.value) }}
    </p>

    <EmptyState
      v-else-if="!detail.data.value?.articles.items.length"
      title="这个系列还没有文章"
      description="文章发布后会自动出现在这里。"
    />

    <ol v-else class="card divide-y divide-border">
      <li v-for="(item, index) in detail.data.value.articles.items" :key="item.id">
        <RouterLink
          :to="`/article/${item.slug}`"
          class="flex items-center gap-4 px-5 py-4 transition-colors hover:bg-surface-muted/50"
        >
          <span class="w-7 shrink-0 text-center text-sm font-medium text-ink-faint">
            {{ index + 1 }}
          </span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-ink">{{ item.title }}</span>
            <span v-if="item.summary" class="mt-0.5 block truncate text-xs text-ink-soft">
              {{ item.summary }}
            </span>
          </span>
          <time class="shrink-0 text-xs text-ink-faint" :datetime="item.published_at ?? item.created_at">
            {{ formatDateTime(item.published_at ?? item.created_at) }}
          </time>
        </RouterLink>
      </li>
    </ol>
  </div>
</template>
