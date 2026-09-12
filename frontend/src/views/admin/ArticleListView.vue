<script setup lang="ts">
/** 后台文章列表：作者看自己的（含草稿），站长看全站并可按作者/状态过滤。 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { articleApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useAuthStore } from '@/stores/auth'
import type { ArticleStatus, ArticleSummary, Page } from '@/types'
import { formatDateTime, formatStatus } from '@/utils/format'

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

const status = ref<ArticleStatus | ''>((route.query.status as ArticleStatus) || '')
const keyword = ref('')
const page = ref(1)

const emptyPage: Page<ArticleSummary> = { items: [], total: 0, page: 1, page_size: PAGE_SIZE, pages: 0 }
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

const pendingDelete = ref<ArticleSummary | null>(null)

function reload(resetPage = true): void {
  if (resetPage) page.value = 1
  void articles.run()
}

async function confirmDelete(): Promise<void> {
  const target = pendingDelete.value
  if (!target) return
  const done = await action.run(() => articleApi.remove(target.id), {
    success: `《${target.title}》已删除`,
    errorMessage: '删除失败',
  })
  if (done === undefined) return
  pendingDelete.value = null
  reload()
}

async function togglePublish(item: ArticleSummary): Promise<void> {
  const next: ArticleStatus = item.status === 'published' ? 'draft' : 'published'
  const done = await action.run(() => articleApi.update(item.id, { status: next }), {
    success: next === 'published' ? '已发布' : '已转为草稿',
    errorMessage: '操作失败',
  })
  if (done !== undefined) reload(false)
}

/** 状态徽标配色。涨红跌绿不适用，这里只求语义清晰。 */
const STATUS_CLASS: Record<string, string> = {
  published: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200',
  draft: 'bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200',
  archived: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
}

const isAdmin = computed(() => auth.isAdmin)

onMounted(() => {
  void articles.run()
})
</script>

<template>
  <div class="space-y-5">
    <!-- 工具条 -->
    <div class="flex flex-wrap items-center gap-3">
      <form class="flex flex-1 gap-2 sm:max-w-md" @submit.prevent="reload()">
        <input v-model="keyword" class="input" placeholder="搜索标题或正文…" />
        <button type="submit" class="btn-ghost shrink-0">搜索</button>
      </form>

      <select
        v-model="status"
        class="rounded-lg border border-border bg-surface px-2.5 py-2 text-sm text-ink"
        @change="reload()"
      >
        <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>

      <RouterLink to="/admin/articles/new" class="btn-primary ml-auto">写文章</RouterLink>
    </div>

    <div v-if="articles.loading.value && !articles.ready.value" class="space-y-2">
      <div v-for="index in 6" :key="index" class="skeleton h-12 rounded-lg"></div>
    </div>

    <p v-else-if="articles.error.value" class="card p-6 text-center text-sm text-ink-soft">
      {{ toErrorMessage(articles.error.value) }}
    </p>

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
                  <span class="rounded-md px-2 py-0.5 text-xs" :class="STATUS_CLASS[item.status]">
                    {{ formatStatus(item.status) }}
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
                      @click="pendingDelete = item"
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
                :class="STATUS_CLASS[item.status]"
              >
                {{ formatStatus(item.status) }}
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
                @click="pendingDelete = item"
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
        @change="(next) => { page = next; articles.run() }"
      />
    </template>

    <ConfirmDialog
      :open="pendingDelete !== null"
      danger
      :loading="action.running.value"
      title="删除文章"
      :message="`确定要删除《${pendingDelete?.title ?? ''}》吗？该操作不可撤销。`"
      confirm-label="删除"
      @cancel="pendingDelete = null"
      @confirm="confirmDelete"
    />
  </div>
</template>
