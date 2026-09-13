<script setup lang="ts">
/** 后台仪表盘：一次拿全站数字，避免开一堆请求。 */
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { statsApi } from '@/api'
import TrendChart from '@/components/TrendChart.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useSiteStore } from '@/stores/site'
import { formatCount, formatDateTime } from '@/utils/format'

const site = useSiteStore()

/** 近 30 天访问趋势。加载失败不阻塞仪表盘其余数字（错误就地展示）。 */
const trend = useAsyncData(() => statsApi.dailyViews(30), [])
const trendTotal = computed(() =>
  trend.data.value.reduce((sum, day) => sum + day.views, 0),
)

onMounted(() => {
  void site.loadStats()
  void trend.run()
})
</script>

<template>
  <div class="space-y-6">
    <div v-if="!site.stats" class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div v-for="index in 4" :key="index" class="card p-5">
        <div class="skeleton h-3.5 w-16"></div>
        <div class="skeleton mt-3 h-7 w-12"></div>
      </div>
    </div>

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div class="card p-5">
          <p class="text-xs text-ink-faint">已发布</p>
          <p class="mt-2 text-2xl font-semibold text-ink">{{ site.stats.published_total }}</p>
          <p class="mt-1 text-xs text-ink-faint">共 {{ site.stats.article_total }} 篇</p>
        </div>

        <div class="card p-5">
          <p class="text-xs text-ink-faint">草稿</p>
          <p class="mt-2 text-2xl font-semibold text-ink">{{ site.stats.draft_total }}</p>
          <RouterLink to="/admin/articles" class="mt-1 inline-block text-xs text-brand-600 hover:text-brand-700">
            去处理 →
          </RouterLink>
        </div>

        <div class="card p-5">
          <p class="text-xs text-ink-faint">总阅读量</p>
          <p class="mt-2 text-2xl font-semibold text-ink">
            {{ formatCount(site.stats.total_views) }}
          </p>
          <p class="mt-1 text-xs text-ink-faint">
            最近发布 {{ formatDateTime(site.stats.latest_published_at) || '—' }}
          </p>
        </div>

        <div class="card p-5">
          <p class="text-xs text-ink-faint">待审评论</p>
          <p
            class="mt-2 text-2xl font-semibold"
            :class="site.stats.pending_comment_total > 0 ? 'text-amber-600' : 'text-ink'"
          >
            {{ site.stats.pending_comment_total }}
          </p>
          <RouterLink
            v-if="site.stats.pending_comment_total > 0"
            to="/admin/comments?approved=false"
            class="mt-1 inline-block text-xs text-brand-600 hover:text-brand-700"
          >
            去审核 →
          </RouterLink>
          <p v-else class="mt-1 text-xs text-ink-faint">共 {{ site.stats.comment_total }} 条</p>
        </div>
      </div>

      <div class="card p-5">
        <div class="mb-3 flex items-baseline justify-between">
          <h2 class="text-sm font-medium text-ink">访问趋势（近 30 天）</h2>
          <span class="text-xs text-ink-faint">
            合计 {{ formatCount(trendTotal) }} 次阅读 · 虚线为去重访客
          </span>
        </div>
        <div v-if="trend.loading.value && !trend.ready.value" class="skeleton h-48 rounded-lg"></div>
        <p
          v-else-if="trend.error.value"
          class="py-10 text-center text-sm text-ink-soft"
        >
          {{ toErrorMessage(trend.error.value) }}
        </p>
        <TrendChart v-else :data="trend.data.value" />
      </div>

      <div class="grid gap-4 sm:grid-cols-3">
        <div class="card p-5">
          <p class="text-xs text-ink-faint">分类</p>
          <p class="mt-2 text-xl font-semibold text-ink">{{ site.stats.category_total }}</p>
        </div>
        <div class="card p-5">
          <p class="text-xs text-ink-faint">标签</p>
          <p class="mt-2 text-xl font-semibold text-ink">{{ site.stats.tag_total }}</p>
        </div>
        <div class="card p-5">
          <p class="text-xs text-ink-faint">评论总数</p>
          <p class="mt-2 text-xl font-semibold text-ink">{{ site.stats.comment_total }}</p>
        </div>
      </div>

      <div class="card p-5">
        <h2 class="mb-3 text-sm font-medium text-ink">快捷操作</h2>
        <div class="flex flex-wrap gap-3">
          <RouterLink to="/admin/articles/new" class="btn--primary">写新文章</RouterLink>
          <RouterLink to="/admin/taxonomy" class="btn--ghost">管理分类标签</RouterLink>
          <RouterLink to="/admin/media" class="btn--ghost">媒体库</RouterLink>
          <RouterLink v-if="site.stats.pending_comment_total > 0" to="/admin/comments?approved=false" class="btn--ghost">
            审核评论（{{ site.stats.pending_comment_total }}）
          </RouterLink>
        </div>
      </div>
    </template>
  </div>
</template>
