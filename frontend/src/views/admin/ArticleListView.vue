<script setup lang="ts">
/** 后台文章列表：作者看自己的（含草稿），站长看全站并可按作者/状态过滤。 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { articleApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useAuthStore } from '@/stores/auth'
import type { ArticleStatus, ArticleSummary, Page } from '@/types'
import { formatDateTime } from '@/utils/format'
import { statusBadgeClass, statusLabel } from '@/utils/status'
import { emptyPage as emptyPageOf } from '@/utils/pagination'

const route = useRoute()
const router = useRouter()
const action = useAction()
const auth = useAuthStore()

const PAGE_SIZE = 15

const STATUS_OPTIONS: { value: ArticleStatus | ''; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'published', label: '已发布' },
  { value: 'draft', label: '草稿' },
  { value: 'archived', label: '已归档' },
]

// 白名单必须在 ref 初始化**之前**声明：`const` 不提升，
// 写在后面会在 setup 阶段直接抛 ReferenceError（组件挂载即失败）。
const VALID_STATUSES: ArticleStatus[] = ['published', 'draft', 'archived']

const status = ref<ArticleStatus | ''>(readStatusFromQuery())
const keyword = ref(readKeywordFromQuery())
const page = ref(readPageFromQuery())

function readStatusFromQuery(): ArticleStatus | '' {
  const raw = route.query.status
  // 白名单校验：以前是 `as ArticleStatus` 直接透传，手改 ?status=hacked 会原样
  // 发给后端（拿到 422 或空列表），而前台列表对 page=abc 是有兜底的。
  return typeof raw === 'string' && (VALID_STATUSES as string[]).includes(raw)
    ? (raw as ArticleStatus)
    : ''
}

function readKeywordFromQuery(): string {
  const raw = route.query.q
  return typeof raw === 'string' ? raw : ''
}

function readPageFromQuery(): number {
  const raw = Array.isArray(route.query.page) ? route.query.page[0] : route.query.page
  const parsed = Number(raw)
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1
}

/**
 * 把当前筛选写回地址栏。
 *
 * 与前台列表同一口径：**URL 是筛选状态的唯一来源**。以前这些条件只存在内存 ref 里，
 * 于是「切到草稿 → 刷新」会弹回全部状态，分享出去的链接也带不上筛选条件；
 * 分页与关键词更是完全不进 URL。
 *
 * 用 replace 而不是 push：翻页/切筛选属于同一屏内的状态微调，
 * 每一次都 push 会让返回键变成"一步步倒着翻页"。
 */
function currentQuery(): Record<string, string> {
  const query: Record<string, string> = {}
  if (status.value) query.status = status.value
  const trimmed = keyword.value.trim()
  if (trimmed) query.q = trimmed
  // 第 1 页不写进 URL：?page=1 与不带参数是同一个视图，留着只是噪音
  if (page.value > 1) query.page = String(page.value)
  return query
}

function syncQuery(): void {
  void router.replace({ query: currentQuery() })
}

/** 地址栏是否已经等于"该有的样子"（用来避免挂载时做一次无意义的 replace）。 */
function queryIsCanonical(): boolean {
  const expected = currentQuery()
  const actual: Record<string, string> = {}
  for (const [key, value] of Object.entries(route.query)) {
    if (typeof value === 'string') actual[key] = value
  }
  const keys = new Set([...Object.keys(expected), ...Object.keys(actual)])
  for (const key of keys) {
    if (expected[key] !== actual[key]) return false
  }
  return true
}

const emptyPage = emptyPageOf<ArticleSummary>(PAGE_SIZE)
const articles = useAsyncData<Page<ArticleSummary>>(
  () =>
    articleApi.listManaged({
      page: page.value,
      page_size: PAGE_SIZE,
      status: status.value || undefined,
      keyword: keyword.value || undefined,
      sort: 'updated',
    }),
  emptyPage,
)

const pendingDelete = useConfirmDelete<ArticleSummary>({
  remove: (item) => articleApi.remove(item.id),
  success: (item) => `《${item.title}》已删除`,
  // 与切换状态的口径一致：删掉一条不等于要回到第 1 页。
  // 以前这里走 reload()（默认重置页码），在第 3 页删一条会被弹回第 1 页，
  // 想接着删下一条就得重新翻回去。
  onDeleted: () => reload(false),
})

function reload(resetPage = true): void {
  if (resetPage) page.value = 1
  syncQuery()
  void articles.run()
}

/** 翻页：页号属于 URL 状态的一部分，所以走 reload(false) 而不是直接改 ref。 */
function goToPage(next: number): void {
  page.value = next
  reload(false)
}

async function togglePublish(item: ArticleSummary): Promise<void> {
  const next: ArticleStatus = item.status === 'published' ? 'draft' : 'published'
  const done = await action.run(() => articleApi.update(item.id, { status: next }), {
    success: next === 'published' ? '已发布' : '已转为草稿',
    errorMessage: '操作失败',
  })
  if (done !== undefined) reload(false)
}

const isAdmin = computed(() => auth.isAdmin)

onMounted(() => {
  void articles.run()
  // 规范化地址栏：把 ?page=1 这类噪音清掉，其余条件原样保留。
  // 先比对再写，免得每次进来都做一次多余的 replace。
  if (!queryIsCanonical()) syncQuery()
})

/**
 * 前进/后退、或有人直接改地址栏时，把 URL 的变化拉回视图。
 *
 * 组件实例会被复用（同一条路由记录），所以不能只依赖 onMounted。
 * 这里必须先比对再赋值：否则 syncQuery() 写回 URL 会触发本 watch，
 * 赋值 → 再写 → 再触发，绕成死循环。
 */
watch(
  () => route.query,
  () => {
    const nextStatus = readStatusFromQuery()
    const nextKeyword = readKeywordFromQuery()
    const nextPage = readPageFromQuery()
    if (
      nextStatus === status.value &&
      nextKeyword === keyword.value &&
      nextPage === page.value
    ) {
      return
    }
    status.value = nextStatus
    keyword.value = nextKeyword
    page.value = nextPage
    void articles.run()
  },
)
</script>

<template>
  <div class="space-y-5">
    <!-- 工具条 -->
    <div class="flex flex-wrap items-center gap-3">
      <form class="flex flex-1 gap-2 sm:max-w-md" @submit.prevent="reload()">
        <input
          v-model="keyword"
          class="input"
          placeholder="搜索标题或正文…"
          aria-label="搜索文章"
        />
        <button type="submit" class="btn--ghost shrink-0">搜索</button>
      </form>

      <select
        v-model="status"
        class="rounded-lg border border-border bg-surface px-2.5 py-2 text-sm text-ink"
        aria-label="按状态筛选"
        @change="reload()"
      >
        <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>

      <RouterLink to="/admin/articles/new" class="btn--primary ml-auto">写文章</RouterLink>
    </div>

    <div v-if="articles.loading.value && !articles.ready.value" class="space-y-2">
      <div v-for="index in 6" :key="index" class="skeleton h-12 rounded-lg"></div>
    </div>

    <div
      v-else-if="articles.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(articles.error.value) }}</span>
      <!-- 失败必须给出下一步：只有一行文案时用户只能自己猜"刷新一下试试" -->
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="reload(false)">
        重试
      </button>
    </div>

    <EmptyState
      v-else-if="!articles.data.value.items.length"
      title="没有匹配的文章"
      description="换个筛选条件，或者现在就写一篇。"
      action-label="写文章"
      @action="router.push('/admin/articles/new')"
    />

    <template v-else>
      <div class="card overflow-hidden">
        <!-- 桌面：表格。窄屏下 min-w 会逼出横向滚动，所以 md 以下换成卡片 -->
        <div class="hidden overflow-x-auto md:block">
          <table class="w-full min-w-[720px] text-sm">
            <thead class="border-b border-border bg-surface-muted text-xs text-ink-soft">
              <tr>
                <th class="px-4 py-2.5 text-left font-medium">标题</th>
                <th class="px-4 py-2.5 text-left font-medium">状态</th>
                <th class="w-24 px-4 py-2.5 text-right font-medium">阅读</th>
                <th class="w-24 px-4 py-2.5 text-right font-medium">评论</th>
                <th v-if="isAdmin" class="w-28 px-4 py-2.5 text-left font-medium">作者</th>
                <th class="w-40 px-4 py-2.5 text-left font-medium">更新时间</th>
                <th class="w-40 px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-border">
              <tr v-for="item in articles.data.value.items" :key="item.id" class="hover:bg-surface-muted/60">
                <td class="px-4 py-3">
                  <RouterLink :to="`/article/${item.slug}`" class="font-medium text-ink hover:text-brand-600">
                    {{ item.title }}
                  </RouterLink>
                  <div class="mt-1 flex flex-wrap gap-1.5">
                    <span v-if="item.is_top" class="text-[11px] text-brand-600">置顶</span>
                    <span v-if="item.category" class="text-[11px] text-ink-faint">
                      {{ item.category.name }}
                    </span>
                    <span v-for="tag in item.tags.slice(0, 3)" :key="tag.id" class="text-[11px] text-ink-faint">
                      #{{ tag.name }}
                    </span>
                  </div>
                </td>
                <td class="px-4 py-3">
                  <span
                    class="rounded-md px-2 py-0.5 text-xs"
                    :class="statusBadgeClass(item.status, item.is_scheduled)"
                  >
                    {{ statusLabel(item.status, { scheduled: item.is_scheduled }) }}
                  </span>
                </td>
                <td class="px-4 py-3 text-right text-ink-soft">{{ item.view_count }}</td>
                <td class="px-4 py-3 text-right text-ink-soft">{{ item.comment_count }}</td>
                <td v-if="isAdmin" class="px-4 py-3 text-ink-soft">
                  {{ item.author?.nickname || item.author?.username || '—' }}
                </td>
                <td class="px-4 py-3 text-xs text-ink-faint">{{ formatDateTime(item.updated_at) }}</td>
                <td class="px-4 py-3">
                  <div class="flex items-center justify-end gap-3 text-xs">
                    <button
                      type="button"
                      class="text-brand-600 hover:text-brand-700"
                      @click="togglePublish(item)"
                    >
                      {{ item.status === 'published' ? '转草稿' : '发布' }}
                    </button>
                    <RouterLink
                      :to="`/admin/articles/${item.id}/edit`"
                      class="text-ink-soft hover:text-ink"
                    >
                      编辑
                    </RouterLink>
                    <button
                      type="button"
                      class="text-red-500 hover:text-red-600"
                      @click="pendingDelete.request(item)"
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 移动端：卡片列表。字段与表格一致，只是把「列」竖过来排 -->
        <ul class="divide-y divide-border md:hidden">
          <li v-for="item in articles.data.value.items" :key="item.id" class="p-4">
            <div class="flex items-start justify-between gap-3">
              <RouterLink
                :to="`/article/${item.slug}`"
                class="line-clamp-2 text-sm font-medium text-ink"
              >
                {{ item.title }}
              </RouterLink>
              <span
                class="shrink-0 rounded-md px-2 py-0.5 text-xs"
                :class="statusBadgeClass(item.status, item.is_scheduled)"
              >
                {{ statusLabel(item.status, { scheduled: item.is_scheduled }) }}
              </span>
            </div>

            <p class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint">
              <span>{{ formatDateTime(item.updated_at) }}</span>
              <span>{{ item.view_count }} 阅读</span>
              <span>{{ item.comment_count }} 评论</span>
              <span v-if="isAdmin && item.author">
                {{ item.author.nickname || item.author.username }}
              </span>
            </p>

            <div class="mt-3 flex items-center gap-4 text-xs">
              <button
                type="button"
                class="text-brand-600 hover:text-brand-700"
                @click="togglePublish(item)"
              >
                {{ item.status === 'published' ? '转草稿' : '发布' }}
              </button>
              <RouterLink :to="`/admin/articles/${item.id}/edit`" class="text-ink-soft">
                编辑
              </RouterLink>
              <button
                type="button"
                class="ml-auto text-red-500 hover:text-red-600"
                @click="pendingDelete.request(item)"
              >
                删除
              </button>
            </div>
          </li>
        </ul>
      </div>

      <Pagination
        :page="articles.data.value.page"
        :page-size="articles.data.value.page_size"
        :total="articles.data.value.total"
        @change="goToPage"
      />
    </template>

    <ConfirmDialog
      :open="pendingDelete.pending.value !== null"
      danger
      :loading="pendingDelete.running.value"
      title="删除文章"
      :message="`确定要删除《${pendingDelete.pending.value?.title ?? ''}》吗？该操作不可撤销。`"
      confirm-label="删除"
      @cancel="pendingDelete.cancel"
      @confirm="pendingDelete.confirm"
    />
  </div>
</template>
