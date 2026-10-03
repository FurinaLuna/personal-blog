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
    <!-- 失败态：必须显式区分「加载中」和「加载失败」。
         以前两者共用同一个分支，接口一挂 KPI 卡片就永远停在骨架屏上，
         既看不出出错了，也没有重试入口。 -->
    <div v-if="site.statsError" class="card p-6 text-center text-sm text-ink-soft" role="alert">
      <p>{{ site.statsError }}</p>
      <button type="button" class="btn--ghost mt-4" @click="site.loadStats()">重试</button>
    </div>

    <div v-else-if="!site.stats" class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div v-for="index in 4" :key="index" class="card p-5">
        <div class="skeleton h-3.5 w-16"></div>
        <div class="skeleton mt-3 h-7 w-12"></div>
      </div>
    </div>

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div class="card p-5">
          <div class="flex items-center justify-between">
            <p class="text-xs font-medium text-ink-faint">已发布</p>
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-link dark:bg-brand-900/30 dark:text-brand-300">
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>
              </svg>
            </span>
          </div>
          <p class="mt-3 text-3xl font-semibold tabular-nums text-ink">{{ site.stats.published_total }}</p>
          <p class="mt-1 text-xs text-ink-faint">共 {{ site.stats.article_total }} 篇</p>
        </div>

        <div class="card p-5">
          <div class="flex items-center justify-between">
            <p class="text-xs font-medium text-ink-faint">草稿</p>
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>
              </svg>
            </span>
          </div>
          <p class="mt-3 text-3xl font-semibold tabular-nums text-ink">{{ site.stats.draft_total }}</p>
          <RouterLink to="/admin/articles" class="mt-1 inline-block text-xs text-link hover:text-link-hover">
            去处理 →
          </RouterLink>
        </div>

        <div class="card p-5">
          <div class="flex items-center justify-between">
            <p class="text-xs font-medium text-ink-faint">总阅读量</p>
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="2.5"/>
              </svg>
            </span>
          </div>
          <p class="mt-3 text-3xl font-semibold tabular-nums text-ink">
            {{ formatCount(site.stats.total_views) }}
          </p>
          <p class="mt-1 text-xs text-ink-faint">
            最近发布 {{ formatDateTime(site.stats.latest_published_at) || '—' }}
          </p>
        </div>

        <div class="card p-5">
          <div class="flex items-center justify-between">
            <p class="text-xs font-medium text-ink-faint">待审评论</p>
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.4-4.2A8 8 0 1 1 21 12Z"/>
              </svg>
            </span>
          </div>
          <p
            class="mt-3 text-3xl font-semibold tabular-nums"
            :class="site.stats.pending_comment_total > 0 ? 'text-amber-600' : 'text-ink'"
          >
            {{ site.stats.pending_comment_total }}
          </p>
          <RouterLink
            v-if="site.stats.pending_comment_total > 0"
            to="/admin/comments?approved=false"
            class="mt-1 inline-block text-xs text-link hover:text-link-hover"
          >
            去审核 →
          </RouterLink>
          <p v-else class="mt-1 text-xs text-ink-faint">共 {{ site.stats.comment_total }} 条</p>
        </div>
      </div>

      <div class="card p-5">
        <div class="mb-4 flex items-baseline justify-between">
          <h2 class="text-xs font-semibold uppercase tracking-wider text-ink-soft">访问趋势（近 30 天）</h2>
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
          <p class="text-xs font-medium text-ink-faint">分类</p>
          <p class="mt-2 text-2xl font-semibold tabular-nums text-ink">{{ site.stats.category_total }}</p>
        </div>
        <div class="card p-5">
          <p class="text-xs font-medium text-ink-faint">标签</p>
          <p class="mt-2 text-2xl font-semibold tabular-nums text-ink">{{ site.stats.tag_total }}</p>
        </div>
        <div class="card p-5">
          <p class="text-xs font-medium text-ink-faint">评论总数</p>
          <p class="mt-2 text-2xl font-semibold tabular-nums text-ink">{{ site.stats.comment_total }}</p>
        </div>
      </div>

      <div class="card p-5">
        <h2 class="mb-4 text-xs font-semibold uppercase tracking-wider text-ink-soft">快捷操作</h2>
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
