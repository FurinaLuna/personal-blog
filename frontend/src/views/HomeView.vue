<script setup lang="ts">
/**
 * 文章列表页（首页）。
 *
 * 筛选 / 排序 / 分页状态**完全放在 URL query 里**，组件不额外维护一份。
 * 这样做的三个好处：
 * 1. 刷新、分享链接、浏览器前进后退都能还原到同一个视图；
 * 2. 页面之间跳转（比如点标签）只需要改 URL；
 * 3. 不会出现"URL 和界面对不上"的经典 bug。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { articleApi, categoryApi, tagApi } from '@/api'
import ArticleCard from '@/components/ArticleCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import Pagination from '@/components/Pagination.vue'
import SortSelector from '@/components/SortSelector.vue'
import { useAsyncData, toErrorMessage } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { ArticleSort, ArticleSummary, Category, Page, Tag } from '@/types'

const route = useRoute()
const router = useRouter()

const PAGE_SIZE = 8

/* -------------------------------------------------- 从 URL 派生的查询状态 */

function queryString(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

const keyword = ref(queryString('keyword'))
const page = computed(() => {
  const value = Number(queryString('page') || 1)
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1
})
const sort = computed<ArticleSort>(() => (queryString('sort') as ArticleSort) || 'latest')
const activeTag = computed(() => queryString('tag'))
const activeCategory = computed(() => queryString('category'))

/** 统一的 URL 更新入口：改完 query 后由 watch 触发重新请求 */
function updateQuery(patch: Record<string, string | number | undefined>): void {
  const next = { ...route.query, ...patch }
  for (const [key, value] of Object.entries(next)) {
    if (value === undefined || value === '' || value === null) delete next[key]
  }
  void router.push({ path: '/', query: next })
}

/* -------------------------------------------------- 主列表 */

const emptyPage: Page<ArticleSummary> = { items: [], total: 0, page: 1, page_size: PAGE_SIZE, pages: 0 }
const articles = useAsyncData<Page<ArticleSummary>>(
  () =>
    articleApi.list({
      page: page.value,
      page_size: PAGE_SIZE,
      sort: sort.value,
      keyword: keyword.value || undefined,
      tag: activeTag.value || undefined,
      category: activeCategory.value || undefined,
    }),
  emptyPage,
)

/* -------------------------------------------------- 侧边栏数据 */

const categories = ref<Category[]>([])
const tags = ref<Tag[]>([])

async function loadSidebar(): Promise<void> {
  // 侧边栏属于"锦上添花"，失败就不显示，不影响主列表
  try {
    const [categoryList, tagList] = await Promise.all([
      categoryApi.list(true),
      tagApi.list({ limit: 20, minCount: 1 }),
    ])
    categories.value = categoryList.filter((item) => item.article_count > 0)
    tags.value = tagList
  } catch {
    categories.value = []
    tags.value = []
  }
}

const activeFilterLabel = computed(() => {
  if (activeTag.value) return `标签：${activeTag.value}`
  if (activeCategory.value) return `分类：${activeCategory.value}`
  if (keyword.value) return `搜索：${keyword.value}`
  return ''
})

// 浏览器标签页标题跟随当前筛选状态，多开几个标签页时不会分不清谁是谁
useHead(computed(() => ({ title: activeFilterLabel.value || undefined })))

const hasFilter = computed(() => Boolean(activeTag.value || activeCategory.value || keyword.value))

function clearFilters(): void {
  keyword.value = ''
  updateQuery({ tag: undefined, category: undefined, keyword: undefined, page: undefined })
}

function search(): void {
  updateQuery({ keyword: keyword.value.trim() || undefined, page: undefined })
}

function changePage(next: number): void {
  updateQuery({ page: next === 1 ? undefined : next })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// query 一变就重新取数（tag/category/sort/page/keyword 都在 query 里）
watch(
  () => route.query,
  () => {
    keyword.value = queryString('keyword')
    void articles.run()
  },
  { deep: true },
)

onMounted(() => {
  void articles.run()
  void loadSidebar()
})
</script>

<template>
  <div class="grid gap-8 lg:grid-cols-[minmax(0,1fr)_260px]">
    <!-- 主内容 -->
    <section>
      <div class="mb-5 flex flex-wrap items-center gap-3">
        <div>
          <h1 class="text-xl font-semibold text-ink">
            {{ activeFilterLabel || '最新文章' }}
          </h1>
          <p v-if="!hasFilter && !articles.loading.value" class="mt-1 text-sm text-ink-faint">
            共 {{ articles.data.value.total }} 篇
          </p>
        </div>

        <div class="ml-auto flex items-center gap-2">
          <button
            v-if="hasFilter"
            type="button"
            class="btn-ghost px-2.5 py-1.5 text-xs"
            @click="clearFilters"
          >
            清除筛选
          </button>
          <SortSelector
            :model-value="sort"
            @update:model-value="(value) => updateQuery({ sort: value === 'latest' ? undefined : value, page: undefined })"
          />
        </div>
      </div>

      <!-- 搜索框 -->
      <form class="mb-6 flex gap-2" @submit.prevent="search">
        <input
          v-model="keyword"
          class="input"
          type="search"
          placeholder="搜索标题、摘要或正文…"
          maxlength="100"
        />
        <button type="submit" class="btn-primary shrink-0">搜索</button>
      </form>

      <LoadingSkeleton v-if="articles.loading.value && !articles.ready.value" :rows="4" />

      <div
        v-else-if="articles.error.value"
        class="card p-6 text-center text-sm text-ink-soft"
      >
        <p>{{ toErrorMessage(articles.error.value) }}</p>
        <button type="button" class="btn-ghost mt-4" @click="articles.run()">重试</button>
      </div>

      <EmptyState
        v-else-if="!articles.data.value.items.length"
        :title="hasFilter ? '没有匹配的文章' : '还没有发布任何文章'"
        :description="hasFilter ? '试试换个关键词，或清除筛选条件。' : ''"
        :action-label="hasFilter ? '清除筛选' : ''"
        @action="clearFilters"
      />

      <template v-else>
        <div class="space-y-4">
          <ArticleCard
            v-for="item in articles.data.value.items"
            :key="item.id"
            :article="item"
          />
        </div>

        <div class="mt-8">
          <Pagination
            :page="articles.data.value.page"
            :page-size="articles.data.value.page_size"
            :total="articles.data.value.total"
            @change="changePage"
          />
        </div>
      </template>
    </section>

    <!-- 侧边栏 -->
    <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
      <div v-if="categories.length" class="card p-4">
        <h2 class="mb-3 text-sm font-medium text-ink">分类</h2>
        <ul class="space-y-1">
          <li v-for="item in categories" :key="item.id">
            <RouterLink
              :to="{ path: '/', query: { category: item.slug } }"
              class="flex items-center justify-between rounded-lg px-2 py-1.5 text-sm transition-colors"
              :class="
                activeCategory === item.slug
                  ? 'bg-surface-muted font-medium text-brand-600'
                  : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
              "
            >
              <span>{{ item.name }}</span>
              <span class="text-xs text-ink-faint">{{ item.article_count }}</span>
            </RouterLink>
          </li>
        </ul>
      </div>

      <div v-if="tags.length" class="card p-4">
        <h2 class="mb-3 text-sm font-medium text-ink">标签</h2>
        <div class="flex flex-wrap gap-2">
          <RouterLink
            v-for="tag in tags"
            :key="tag.id"
            :to="{ path: '/', query: { tag: tag.slug } }"
            class="chip"
            :class="activeTag === tag.slug ? 'border-brand-300 text-brand-600' : ''"
          >
            {{ tag.name }}
          </RouterLink>
        </div>
        <RouterLink to="/tags" class="mt-3 inline-block text-xs text-brand-600 hover:text-brand-700">
          查看全部标签 →
        </RouterLink>
      </div>

      <div class="card p-4">
        <h2 class="mb-3 text-sm font-medium text-ink">快捷入口</h2>
        <div class="space-y-1 text-sm">
          <RouterLink to="/archive" class="block rounded-lg px-2 py-1.5 text-ink-soft hover:bg-surface-muted hover:text-ink">
            按月归档
          </RouterLink>
          <RouterLink to="/about" class="block rounded-lg px-2 py-1.5 text-ink-soft hover:bg-surface-muted hover:text-ink">
            关于本站
          </RouterLink>
          <RouterLink to="/guestbook" class="block rounded-lg px-2 py-1.5 text-ink-soft hover:bg-surface-muted hover:text-ink">
            留言板
          </RouterLink>
        </div>
      </div>
    </aside>
  </div>
</template>
