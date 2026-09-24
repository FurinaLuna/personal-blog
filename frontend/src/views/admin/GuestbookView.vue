<script setup lang="ts">
/**
 * 留言板管理（仅站长）。
 *
 * 与评论审核同一个套路：默认只看待审（那是唯一需要"处理"的队列），
 * 每行可原地审核、可写回复、可删除。差别在**回复只有一条**：
 * 留言板刻意不做楼中楼（结构上就不可能出现"回复的回复"），
 * 所以回复是一个可编辑的字段，而不是一条新的记录。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { guestbookApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import type { GuestbookMessageAdmin, Page } from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'
import { emptyPage as emptyPageOf } from '@/utils/pagination'

const route = useRoute()
const action = useAction()

const PAGE_SIZE = 20
/** 与后端 `reply_content` 的 max_length 对齐 */
const REPLY_MAX = 2000

// 默认停在**待审**：那是唯一需要「处理」的队列。评论页靠 `?approved=false`
// 表达同样的意图（它通常是从仪表盘的「待审评论」链接进来的），
// 而留言板目前没有那样的入口链接，所以默认值直接选待审。
// `?approved=true` 仍然支持，留给将来的仪表盘深链。
const filter = ref<'pending' | 'approved' | 'all'>(
  route.query.approved === 'true' ? 'approved' : 'pending',
)
const page = ref(1)

const approvedParam = computed<boolean | undefined>(() => {
  if (filter.value === 'pending') return false
  if (filter.value === 'approved') return true
  return undefined
})

const messages = useAsyncData<Page<GuestbookMessageAdmin>>(
  () => guestbookApi.listManaged(page.value, PAGE_SIZE, approvedParam.value),
  emptyPageOf<GuestbookMessageAdmin>(PAGE_SIZE),
)

const pendingDelete = useConfirmDelete<GuestbookMessageAdmin>({
  remove: (item) => guestbookApi.remove(item.id),
  success: '留言已删除',
  onDeleted: () => {
    // 同 CommentListView：删掉当前页最后一条要把页码退回去，
    // 否则会停在一个空页上，而空态里没有分页器、total 仍大于 0。
    const wasLastOnPage = messages.data.value.items.length <= 1
    if (wasLastOnPage && page.value > 1) page.value -= 1
    void messages.run()
  },
})

/** 行级忙碌标记：审核/回复都是按行进行的，需要知道"哪一行"在转 */
const busyId = ref<number | null>(null)
/** 正在编辑回复的留言 id；null 表示没有展开的编辑器 */
const replyingId = ref<number | null>(null)
const replyDraft = ref('')

function openReply(item: GuestbookMessageAdmin): void {
  replyingId.value = item.id
  replyDraft.value = item.reply_content ?? ''
}

function closeReply(): void {
  replyingId.value = null
  replyDraft.value = ''
}

async function moderate(item: GuestbookMessageAdmin, approved: boolean): Promise<void> {
  busyId.value = item.id
  const done = await action.run(() => guestbookApi.moderate(item.id, approved), {
    success: approved ? '已通过' : '已撤下',
    errorMessage: '操作失败',
  })
  busyId.value = null
  if (done !== undefined) await messages.run()
}

/**
 * 保存回复。空字符串表示**清除**回复（后端会把回复时间与回复人一并清空）——
 * 因此这里不做"内容不能为空"的拦截，但要给出明确文案，避免站长以为点错了。
 */
async function saveReply(item: GuestbookMessageAdmin): Promise<void> {
  const content = replyDraft.value.trim()
  const clearing = content.length === 0
  busyId.value = item.id
  const done = await action.run(() => guestbookApi.reply(item.id, content), {
    success: clearing ? '回复已清除' : '回复已保存',
    errorMessage: '保存失败',
  })
  busyId.value = null
  if (done === undefined) return
  closeReply()
  await messages.run()
}

onMounted(() => {
  void messages.run()
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
          @click="filter = option; page = 1; messages.run()"
        >
          {{ option === 'pending' ? '待审核' : option === 'approved' ? '已通过' : '全部' }}
        </button>
      </div>

      <p class="text-sm text-ink-faint">共 {{ messages.data.value.total }} 条</p>
    </div>

    <div v-if="messages.loading.value && !messages.ready.value" class="space-y-2">
      <div v-for="index in 4" :key="index" class="skeleton h-24 rounded-lg"></div>
    </div>

    <div
      v-else-if="messages.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(messages.error.value) }}</span>
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="messages.run()">
        重试
      </button>
    </div>

    <EmptyState
      v-else-if="!messages.data.value.items.length"
      :title="filter === 'pending' ? '没有待审核的留言' : '没有留言'"
      :description="filter === 'pending' ? '都处理完了，很清爽。' : ''"
    />

    <template v-else>
      <ul class="space-y-3">
        <li v-for="item in messages.data.value.items" :key="item.id" class="card p-4">
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span class="font-medium text-ink">{{ item.author_name }}</span>
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
            <span v-if="item.reply_content" class="text-[11px] text-brand-600">已回复</span>
            <span class="ml-auto text-xs text-ink-faint" :title="formatDateTime(item.created_at)">
              {{ formatRelative(item.created_at) }}
            </span>
          </div>

          <p class="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
            {{ item.content }}
          </p>

          <!-- 联系方式与来源：只在后台出现（公开接口刻意不含这些字段） -->
          <div class="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-ink-faint">
            <span v-if="item.author_email">{{ item.author_email }}</span>
            <a
              v-if="item.author_site"
              :href="item.author_site"
              target="_blank"
              rel="noopener noreferrer nofollow"
              class="hover:text-brand-600"
            >
              {{ item.author_site }}
            </a>
            <span v-if="item.ip_address">IP {{ item.ip_address }}</span>
          </div>

          <!-- 已有回复：默认只读展示，点"编辑回复"才展开编辑器 -->
          <div
            v-if="item.reply_content && replyingId !== item.id"
            class="mt-3 rounded-lg border border-border bg-surface-muted px-3 py-2.5"
          >
            <div class="flex items-center gap-2 text-xs">
              <span class="font-medium text-brand-700 dark:text-brand-200">站长回复</span>
              <time
                v-if="item.replied_at"
                class="ml-auto text-ink-faint"
                :datetime="item.replied_at"
              >
                {{ formatRelative(item.replied_at) }}
              </time>
            </div>
            <p class="mt-1.5 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
              {{ item.reply_content }}
            </p>
          </div>

          <!-- 回复编辑器 -->
          <div v-if="replyingId === item.id" class="mt-3 space-y-2">
            <textarea
              v-model="replyDraft"
              class="input min-h-24 resize-y"
              :maxlength="REPLY_MAX"
              placeholder="回复这位访客…（清空后保存＝删除回复）"
              :aria-label="`回复 ${item.author_name} 的留言`"
            ></textarea>
            <div class="flex items-center gap-3">
              <button
                type="button"
                class="btn--primary px-3 py-1.5 text-xs"
                :disabled="busyId === item.id"
                @click="saveReply(item)"
              >
                保存回复
              </button>
              <button
                type="button"
                class="btn--ghost px-3 py-1.5 text-xs"
                :disabled="busyId === item.id"
                @click="closeReply"
              >
                取消
              </button>
              <span class="text-[11px] text-ink-faint">
                清空内容再保存即删除回复（访客侧不再显示）
              </span>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap items-center gap-3 text-xs">
            <button
              v-if="replyingId !== item.id"
              type="button"
              class="text-ink-faint hover:text-brand-600"
              @click="openReply(item)"
            >
              {{ item.reply_content ? '编辑回复' : '回复' }}
            </button>

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
        :page="messages.data.value.page"
        :page-size="messages.data.value.page_size"
        :total="messages.data.value.total"
        @change="(next) => { page = next; messages.run() }"
      />
    </template>

    <ConfirmDialog
      :open="pendingDelete.pending.value !== null"
      danger
      :loading="pendingDelete.running.value"
      title="删除留言"
      message="删除后无法恢复（该留言的站长回复也会一起消失）。"
      confirm-label="删除"
      @cancel="pendingDelete.cancel"
      @confirm="pendingDelete.confirm"
    />
  </div>
</template>
