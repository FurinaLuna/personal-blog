<script setup lang="ts">
/** 系列详情：该系列的文章按阅读顺序排列（series_order 升序）。 */
import { computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { seriesApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { SeriesWithArticles } from '@/types'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const slugOrId = computed(() => route.params.slug as string)

/** 每页文章数。以前这个 50 是硬编码的且没有分页器：60 篇的系列只列前 50 篇，
 *  第 51 篇起在页面上**没有任何入口**（头部还写着"60 篇"）。 */
const PAGE_SIZE = 50

/** 页码来自 URL（与首页/搜索页同一口径：URL 是状态的唯一来源）。 */
const page = computed(() => {
  const raw = Number(route.query.page ?? 1)
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1
})

const detail = useAsyncData<SeriesWithArticles>(
  () => seriesApi.detail(slugOrId.value, page.value, PAGE_SIZE),
  { series: null, articles: { items: [], total: 0, page: 1, page_size: 50, pages: 0 } } as unknown as SeriesWithArticles,
)

useHead(() => ({ title: detail.data.value?.series?.name ?? '系列' }))

onMounted(() => {
  void detail.run()
})

/**
 * 跟随路由参数重拉。
 *
 * `/series/a → /series/b` 是**同一条路由记录**，组件实例被复用 ——
 * 只绑 onMounted 的话页面会停在旧系列的标题与列表上，而且不报任何错
 * （地址栏已经变了，内容没变）。这与编辑页那个竞态是同一类问题。
 */
watch([slugOrId, page], () => {
  void detail.run()
})

/** 翻页写回 URL（第 1 页不带参数，与其它列表页一致）。 */
function goPage(next: number): void {
  void router.replace({
    query: next > 1 ? { ...route.query, page: String(next) } : { ...route.query, page: undefined },
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
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

    <div

      v-else-if="detail.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"

      role="alert"
    >

      <span class="text-ink-soft">{{ toErrorMessage(detail.error.value) }}</span>
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="detail.run()">
        重试

      </button>

    </div>

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

    <Pagination
      v-if="detail.data.value && detail.data.value.articles.total > PAGE_SIZE"
      class="mt-6"
      :page="detail.data.value.articles.page"
      :page-size="PAGE_SIZE"
      :total="detail.data.value.articles.total"
      @change="goPage"
    />
  </div>
</template>
