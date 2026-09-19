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
import { useSiteStore } from '@/stores/site'
import type { ArticleSort, ArticleSummary, Category, Page, Tag } from '@/types'
import { emptyPage as emptyPageOf } from '@/utils/pagination'
import { buildSiteJsonLd } from '@/utils/structured-data'

const route = useRoute()
const router = useRouter()
const site = useSiteStore()

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

const emptyPage = emptyPageOf<ArticleSummary>(PAGE_SIZE)
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
    // 侧边栏的分类/标签是辅助信息，取不到就留空（文章列表本身有独立的错误处理），
    // 不因此打断首页主流程
    console.debug('[home] 侧边栏分类/标签加载失败，已留空')
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

/**
 * 首页的 head：标题跟随筛选状态，外加站点级结构化数据。
 *
 * 必须是**一次** useHead 调用。每次调用都会注册一个独立的 watcher 并整体
 * 覆盖 head，写成两次的话后一次会把前一次设的标题冲掉——
 * 表现就是「点标签筛选后标签页标题不再跟着变」。
 *
 * `WebSite` / `Person` 描述的是**站点**而不是某个页面，每个页面都塞一份
 * 属于重复声明；首页是搜索引擎确认「这个站是谁的」最自然的落点。
 * 用 computed 让它跟随站点档案（后台改昵称/头像后无需刷新即可生效）。
 */
useHead(
  computed(() => ({
    // 浏览器标签页标题跟随当前筛选状态，多开几个标签页时不会分不清谁是谁
    title: activeFilterLabel.value || undefined,
    jsonLd: site.loaded ? buildSiteJsonLd(site.profile) : null,
    // 筛选/排序/翻页都只是同一份内容的不同视图，规范地址始终指向首页，
    // 否则 ?tag=x&sort=hottest&page=3 会被当成一个独立页面参与排名
    canonical: new URL('/', window.location.origin).href,
  })),
)

const hasFilter = computed(() => Boolean(activeTag.value || activeCategory.value || keyword.value))

/** hero 只在「无筛选的第一页」出现：筛选/翻页后用户关注的是结果，不是站点介绍 */
const showHero = computed(() => !hasFilter.value && page.value === 1)

/** 头条：只在无筛选的第一页取第一篇（列表默认置顶优先，所以就是作者置顶的那篇）。 */
const lead = computed(() =>
  showHero.value ? (articles.data.value.items[0] ?? null) : null,
)

/** 其余文章。没有头条时就是全部（筛选/翻页时的行为与之前完全一致）。 */
const rest = computed(() =>
  lead.value ? articles.data.value.items.slice(1) : articles.data.value.items,
)

/** 首屏卡片 stagger 渐入的延迟。封顶 8 条，避免长列表尾部等太久 */
function cardDelay(index: number): string {
  return `${Math.min(index, 8) * 40}ms`
}

function clearFilters(): void {
  keyword.value = ''
  updateQuery({ tag: undefined, category: undefined, keyword: undefined, page: undefined })
}

/**
 * 首页的搜索框**直接跳到搜索页**，而不是在本页做关键词筛选。
 *
 * 这里曾经是「在列表里筛」：结果按时间排、置顶优先，一篇只在正文里顺带
 * 提一句的置顶文章会压过标题命中的那篇。而 `/search` 走的是相关度排序。
 * 同一个搜索框在用户眼里就是同一个功能，给它两套排序规则只会让人困惑
 * （「为什么我搜这个词出来的是这篇？」）——所以收敛成一个入口。
 *
 * 首页仍保留 `?keyword=` 的**筛选**能力（从标签/分类进来的链接可能带着它），
 * 只是不再由这个输入框产生。要「浏览式筛选」的用户走标签云与分类页。
 */
function search(): void {
  const value = keyword.value.trim()
  if (!value) return
  void router.push({ path: '/search', query: { q: value } })
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
    <!-- 主内容：lg 以下侧边栏隐藏，主列收窄居中，避免平板宽度「贴左空旷」 -->
    <section class="mx-auto w-full max-w-content lg:mx-0 lg:max-w-none">
      <div class="mb-5 flex flex-wrap items-center gap-3">
        <div>
          <!-- 首页唯一的 h1。
               这里曾经是一个「站名 + 标语 + 细线」的 hero（broadsheet 模板的
               标准头部），而顶栏是 sticky 的、已经用品牌标记 + 站名承担了身份 ——
               同一屏 100px 内把同样的字渲染两遍，hero 并没有挣到它那份空间。
               现在把第一屏让给真正有信息量的东西：文章本身。 -->
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
            class="btn--ghost btn--sm"
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
          aria-label="搜索文章"
          maxlength="100"
        />
        <button type="submit" class="btn--primary shrink-0">搜索</button>
      </form>

      <LoadingSkeleton v-if="articles.loading.value && !articles.ready.value" :rows="4" />

      <div
        v-else-if="articles.error.value"
        class="card p-6 text-center text-sm text-ink-soft"
      >
        <p>{{ toErrorMessage(articles.error.value) }}</p>
        <button type="button" class="btn--ghost mt-4" @click="articles.run()">重试</button>
      </div>

      <EmptyState
        v-else-if="!articles.data.value.items.length"
        :title="hasFilter ? '没有匹配的文章' : '还没有发布任何文章'"
        :description="hasFilter ? '试试换个关键词，或清除筛选条件。' : ''"
        :action-label="hasFilter ? '清除筛选' : ''"
        @action="clearFilters"
      />

      <template v-else>
        <!-- 头条：作者置顶的那一篇（列表默认置顶优先）。
             在此之前「置顶」只表现为卡片上一个 11px 的徽标 ——
             作者明确表达了「先看这篇」，界面却没接住。
             只在无筛选的第一页出现：翻页或筛选后用户找的是结果，不是推荐。 -->
        <ArticleCard
          v-if="lead"
          class="card-enter"
          :article="lead"
          featured
          priority
        />

        <div v-if="rest.length" :class="lead ? 'mt-8' : ''">
          <!-- 措辞刻意用「更多」而不是「更早」：排序可以切成最热/标题，
               那时它们并不是「更早的文章」 -->
          <h2 v-if="lead" class="mb-4 text-sm font-medium text-ink-faint">更多文章</h2>
          <div class="space-y-6">
            <ArticleCard
              v-for="(item, index) in rest"
              :key="item.id"
              class="card-enter"
              :style="{ animationDelay: cardDelay(index) }"
              :article="item"
            />
          </div>
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
              class="nav-link flex items-center justify-between px-2 py-1.5"
              :class="activeCategory === item.slug ? 'nav-link--active' : ''"
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
          <RouterLink to="/archive" class="nav-link block px-2 py-1.5">
            按月归档
          </RouterLink>
          <RouterLink to="/about" class="nav-link block px-2 py-1.5">
            关于本站
          </RouterLink>
          <RouterLink to="/guestbook" class="nav-link block px-2 py-1.5">
            留言板
          </RouterLink>
        </div>
      </div>
    </aside>
  </div>
</template>
