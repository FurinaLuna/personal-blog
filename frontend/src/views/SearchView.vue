<script setup lang="ts">
/**
 * 站内搜索页。
 *
 * 与首页的关键词筛选是**两件事**，所以有独立页面：
 *
 * - 首页的 `?keyword=` 是「在列表里筛」，结果仍按时间排，适合「我就想翻翻」；
 * - 这里的 `/search?q=` 走 FTS5，回答的是「哪篇最相关」，标题命中排在正文命中前面。
 *
 * 状态仍然完全放在 URL query 里（`q` + `page`），刷新与分享都能还原——
 * 这是全站一致的做法，不在这里破例。
 */
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { articleApi } from '@/api'
import ArticleCard from '@/components/ArticleCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import Pagination from '@/components/Pagination.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { ArticleSummary, Page } from '@/types'
import { emptyPage } from '@/utils/pagination'

const route = useRoute()
const router = useRouter()

const PAGE_SIZE = 10

/** 输入框的即时值；真正的查询以 URL 里的 q 为准（提交后才同步过去）。 */
const draft = ref('')

const query = computed(() => String(route.query.q ?? '').trim())
const page = computed(() => {
  const raw = Number(route.query.page ?? 1)
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1
})

// q/page 变化才重新请求；用 URL 作为唯一数据源，组件不另存一份查询状态
const results = useAsyncData<Page<ArticleSummary>>(
  () => articleApi.search(query.value, { page: page.value, page_size: PAGE_SIZE }),
  emptyPage<ArticleSummary>(PAGE_SIZE),
)

watch(
  () => route.fullPath,
  () => {
    draft.value = query.value
    if (query.value) void results.run()
  },
  { immediate: true },
)

useHead(
  computed(() => ({
    title: query.value ? `搜索：${query.value}` : '搜索',
    description: query.value
      ? `站内搜索「${query.value}」的结果`
      : '搜索这个博客里的全部文章。',
    // 搜索结果页不进搜索引擎索引：同一份内容会随关键词产生无数个 URL，
    // 收录它们只会稀释正文页的权重
    jsonLd: null,
  })),
)

function submit(): void {
  const keyword = draft.value.trim()
  if (!keyword) return
  void router.push({ path: '/search', query: { q: keyword } })
}

function goPage(next: number): void {
  void router.push({ path: '/search', query: { q: query.value, page: String(next) } })
  window.scrollTo({ top: 0 })
}
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <header class="mb-6">
      <h1 class="font-display text-2xl font-semibold tracking-tight text-ink">搜索</h1>
      <p class="mt-1.5 text-sm text-ink-soft">按相关度排序：标题命中的排在正文命中之前。</p>

      <form class="mt-4 flex gap-2" @submit.prevent="submit">
        <input
          v-model="draft"
          class="input"
          type="search"
          placeholder="搜索标题、摘要或正文…"
          aria-label="搜索文章"
          maxlength="100"
        />
        <button type="submit" class="btn--primary shrink-0">搜索</button>
      </form>
    </header>

    <!-- 没输入关键词：给个明确起点，而不是空白页 -->
    <EmptyState
      v-if="!query"
      title="输入关键词开始搜索"
      description="支持中文与英文；两个字的中文词也能搜到。"
    />

    <template v-else>
      <p v-if="results.loading.value" class="mb-4 text-sm text-ink-faint">
        正在搜索「{{ query }}」…
      </p>
      <p v-else-if="!results.error.value" class="mb-4 text-sm text-ink-faint">
        找到 <span class="font-medium text-ink">{{ results.data.value.total }}</span>
        条与「{{ query }}」相关的结果
      </p>

      <LoadingSkeleton v-if="results.loading.value" :rows="3" />

      <div v-else-if="results.error.value" class="card p-6 text-center text-sm text-ink-soft">
        <p>{{ toErrorMessage(results.error.value) }}</p>
        <button type="button" class="btn--ghost mt-4" @click="results.run()">重试</button>
      </div>

      <EmptyState
        v-else-if="!results.data.value.items.length"
        title="没有找到相关文章"
        description="换个词试试，或者回首页浏览全部文章。"
      />

      <template v-else>
        <div class="space-y-4">
          <ArticleCard
            v-for="item in results.data.value.items"
            :key="item.id"
            :article="item"
            :highlight="query"
          />
        </div>

        <Pagination
          v-if="results.data.value.total > PAGE_SIZE"
          class="mt-8"
          :page="page"
          :page-size="PAGE_SIZE"
          :total="results.data.value.total"
          @change="goPage"
        />
      </template>
    </template>

    <p class="mt-8 text-center text-sm text-ink-faint">
      也可以
      <RouterLink to="/" class="text-brand-600 hover:text-brand-700">回首页浏览全部文章</RouterLink>
    </p>
  </div>
</template>
