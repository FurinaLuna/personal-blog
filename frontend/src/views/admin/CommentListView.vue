<script setup lang="ts">
/** 评论审核。默认只看待审，因为那是唯一需要"处理"的队列。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { commentApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import type { Comment, Page } from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'
import { emptyPage as emptyPageOf } from '@/utils/pagination'

const route = useRoute()
const action = useAction()

const PAGE_SIZE = 20

/** '' 表示不过滤；true/false 表示按审核状态过滤 */
const filter = ref<'pending' | 'approved' | 'all'>(
  route.query.approved === 'false' ? 'pending' : 'all',
)
const page = ref(1)

const approvedParam = computed<boolean | undefined>(() => {
  if (filter.value === 'pending') return false
  if (filter.value === 'approved') return true
  return undefined
})

const emptyPage = emptyPageOf<Comment>(PAGE_SIZE)
const comments = useAsyncData<Page<Comment>>(
  () =>
    commentApi.listModeration({
      page: page.value,
      pageSize: PAGE_SIZE,
      approved: approvedParam.value,
    }),
  emptyPage,
)

const pendingDelete = useConfirmDelete<Comment>({
  remove: (item) => commentApi.remove(item.id),
  success: '评论已删除',
  onDeleted: () => {
    // 删掉当前页最后一条时要把页码退回去：否则会停在一个空页上，
    // 空态分支里没有分页器，而 total 仍然大于 0 —— 用户没有任何入口回到上一页。
    const wasLastOnPage = comments.data.value.items.length <= 1
    if (wasLastOnPage && page.value > 1) page.value -= 1
    void comments.run()
  },
})
/** 行级忙碌标记：审核是按行进行的，需要知道「哪一行」在转，这个不属于通用 action 语义 */
const busyId = ref<number | null>(null)

async function moderate(comment: Comment, approved: boolean): Promise<void> {
  busyId.value = comment.id
  const done = await action.run(() => commentApi.moderate(comment.id, approved), {
    success: approved ? '已通过' : '已撤下',
    errorMessage: '操作失败',
  })
  busyId.value = null
  if (done !== undefined) void comments.run()
}

onMounted(() => {
  void comments.run()
})
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <div class="inline-flex rounded-lg border border-border bg-surface p-0.5">
        <button
          v-for="option in (['pending', 'approved', 'all'] as const)"
          :key="option"
          type="button"
          class="rounded-md px-3 py-1.5 text-sm transition-colors"
          :class="filter === option ? 'bg-surface-muted font-medium text-brand-600' : 'text-ink-soft'"
          @click="filter = option; page = 1; comments.run()"
        >
          {{ option === 'pending' ? '待审核' : option === 'approved' ? '已通过' : '全部' }}
        </button>
      </div>

      <p class="text-sm text-ink-faint">共 {{ comments.data.value.total }} 条</p>
    </div>

    <div v-if="comments.loading.value && !comments.ready.value" class="space-y-2">
      <div v-for="index in 5" :key="index" class="skeleton h-20 rounded-lg"></div>
    </div>

    <div
      v-else-if="comments.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(comments.error.value) }}</span>
      <!-- 以前失败只有一行文案：除了"切一下筛选"没有别的办法把列表拉回来 -->
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="comments.run()">
        重试
      </button>
    </div>

    <EmptyState
      v-else-if="!comments.data.value.items.length"
      :title="filter === 'pending' ? '没有待审核的评论' : '没有评论'"
      :description="filter === 'pending' ? '都处理完了，很清爽。' : ''"
    />

    <template v-else>
      <ul class="space-y-3">
        <li v-for="item in comments.data.value.items" :key="item.id" class="card p-4">
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span class="font-medium text-ink">{{ item.author_name }}</span>
            <span
              v-if="item.is_admin_reply"
              class="rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
            >
              站长
            </span>
            <span
              class="rounded-md px-1.5 py-0.5 text-[11px]"
              :class="
                item.is_approved
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200'
              "
            >
              {{ item.is_approved ? '已通过' : '待审核' }}
            </span>
            <span v-if="item.parent_id" class="text-[11px] text-ink-faint">回复 #{{ item.parent_id }}</span>
            <span class="ml-auto text-xs text-ink-faint" :title="formatDateTime(item.created_at)">
              {{ formatRelative(item.created_at) }}
            </span>
          </div>

          <p class="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
            {{ item.content }}
          </p>

          <div class="mt-3 flex flex-wrap items-center gap-3 text-xs">
            <RouterLink
              :to="`/article/${item.article_id}`"
              class="text-ink-faint hover:text-brand-600"
            >
              查看文章 #{{ item.article_id }}
            </RouterLink>

            <div class="ml-auto flex items-center gap-3">
              <button
                v-if="!item.is_approved"
                type="button"
                class="text-emerald-600 hover:text-emerald-700 disabled:opacity-50"
                :disabled="busyId === item.id"
                @click="moderate(item, true)"
              >
                通过
              </button>
              <button
                v-else
                type="button"
                class="text-ink-soft hover:text-ink disabled:opacity-50"
                :disabled="busyId === item.id"
                @click="moderate(item, false)"
              >
                撤下
              </button>
              <button
                type="button"
                class="text-red-500 hover:text-red-600"
                @click="pendingDelete.request(item)"
              >
                删除
              </button>
            </div>
          </div>
        </li>
      </ul>

      <Pagination
        :page="comments.data.value.page"
        :page-size="comments.data.value.page_size"
        :total="comments.data.value.total"
        @change="(next) => { page = next; comments.run() }"
      />
    </template>

    <ConfirmDialog
      :open="pendingDelete.pending.value !== null"
      danger
      :loading="pendingDelete.running.value"
      title="删除评论"
      message="删除后无法恢复。如果这条评论有回复，回复也会一起被删除。"
      confirm-label="删除"
      @cancel="pendingDelete.cancel"
      @confirm="pendingDelete.confirm"
    />
  </div>
</template>
