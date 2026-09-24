<script setup lang="ts">
/** 媒体库：上传、预览、复制 Markdown、删除。 */
import { onMounted, ref } from 'vue'

import { attachmentApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { useAsyncData, toErrorMessage } from '@/composables/useAsyncData'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useToast } from '@/composables/useToast'
import { useUpload } from '@/composables/useUpload'
import { useAuthStore } from '@/stores/auth'
import type { Attachment, Page } from '@/types'
import { formatBytes, formatDateTime } from '@/utils/format'
import { emptyPage as emptyPageOf } from '@/utils/pagination'

const toast = useToast()
const auth = useAuthStore()

const PAGE_SIZE = 24
const page = ref(1)
const kind = ref<'all' | 'image' | 'file'>('all')
const fileInput = ref<HTMLInputElement | null>(null)

const emptyPage = emptyPageOf<Attachment>(PAGE_SIZE)
const attachments = useAsyncData<Page<Attachment>>(
  () => attachmentApi.list(page.value, PAGE_SIZE, kind.value === 'all' ? undefined : kind.value),
  emptyPage,
)

const pendingDelete = useConfirmDelete<Attachment>({
  remove: (item) => attachmentApi.remove(item.id),
  success: '已删除',
  onDeleted: () => void attachments.run(),
})

/** 批量上传走逐个 upload：批量里「部分失败」是常态，要逐文件汇报而不是一句笼统提示 */
const upload = useUpload()

async function onUploadChange(event: Event): Promise<void> {
  const files = (event.target as HTMLInputElement).files
  if (!files?.length) return
  let succeeded = 0
  for (const file of Array.from(files)) {
    const result = await upload.upload(file, { successMessage: false })
    if (result) succeeded += 1
  }
  if (fileInput.value) fileInput.value.value = ''
  if (succeeded) toast.success(`成功上传 ${succeeded} 个文件`)
  if (succeeded) void attachments.run()
}

async function copyMarkdown(item: Attachment): Promise<void> {
  try {
    await navigator.clipboard.writeText(item.markdown)
    toast.success('Markdown 片段已复制')
  } catch {
    // 非 HTTPS 环境下 clipboard API 不可用，降级提示用户手动复制
    toast.error('浏览器不允许自动复制，请手动复制链接')
  }
}

/** 存量图片变体回填（仅站长）。success 文案带计数，比固定文案有用得多。 */
const backfillAction = useAction()

async function backfillVariants(): Promise<void> {
  const result = await backfillAction.run(() => attachmentApi.backfillVariants(), {
    errorMessage: '回填失败',
  })
  if (result === undefined) return
  if (result.updated > 0) {
    // 按后端给的真实剩余数说话：以前无论是否还有待处理，都固定提示
    // "还剩待处理可再次运行" —— 已经跑完的时候这句话是错的，用户会白跑一趟
    const tail = result.remaining > 0 ? `，还剩 ${result.remaining} 张可再次运行` : '，已全部处理完'
    toast.success(`回填完成：生成 ${result.updated} 张，跳过 ${result.skipped} 张${tail}`)
    void attachments.run()
  } else {
    toast.success('没有需要回填的图片')
  }
}

onMounted(() => {
  void attachments.run()
})
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <div class="inline-flex rounded-lg border border-border bg-surface p-0.5">
        <button
          v-for="option in (['all', 'image', 'file'] as const)"
          :key="option"
          type="button"
          class="rounded-md px-3 py-1.5 text-sm transition-colors"
          :class="kind === option ? 'bg-surface-muted font-medium text-brand-600' : 'text-ink-soft'"
          @click="kind = option; page = 1; attachments.run()"
        >
          {{ option === 'all' ? '全部' : option === 'image' ? '图片' : '附件' }}
        </button>
      </div>

      <p class="text-sm text-ink-faint">共 {{ attachments.data.value.total }} 个</p>

      <button
        type="button"
        class="btn--primary ml-auto"
        :disabled="upload.uploading.value"
        @click="fileInput?.click()"
      >
        {{ upload.uploading.value ? '上传中…' : '上传文件' }}
      </button>

      <!--
        上传进度：以前只有一个「上传中…」文案，大图在慢上行要等几十秒，
        用户既不知道在动、也不知道传到哪了（attachmentApi 早就支持 onProgress，
        只是没人接线）。
      -->
      <div
        v-if="upload.uploading.value"
        class="flex items-center gap-2"
        role="progressbar"
        :aria-valuenow="upload.progress.value"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="上传进度"
      >
        <div class="h-1.5 w-24 overflow-hidden rounded-full bg-line">
          <div
            class="h-full rounded-full bg-brand transition-[width] duration-200"
            :style="{ width: `${upload.progress.value}%` }"
          />
        </div>
        <span class="text-xs tabular-nums text-ink-faint">{{ upload.progress.value }}%</span>
      </div>
      <button
        v-if="auth.isAdmin"
        type="button"
        class="btn--ghost"
        :disabled="backfillAction.running.value"
        title="为功能上线前上传的存量图片批量生成多尺寸变体，可重复执行"
        @click="backfillVariants"
      >
        {{ backfillAction.running.value ? '回填中…' : '回填变体' }}
      </button>
      <input
        ref="fileInput"
        type="file"
        multiple
        accept="image/*,.pdf,.zip,.txt,.md,.csv,.json,.docx,.xlsx,.pptx,.epub"
        class="hidden"
        aria-label="选择要上传的文件"
        @change="onUploadChange"
      />
    </div>

    <div v-if="attachments.loading.value && !attachments.ready.value" class="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <div v-for="index in 8" :key="index" class="skeleton aspect-square rounded-xl"></div>
    </div>

    <div
      v-else-if="attachments.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(attachments.error.value) }}</span>
      <!-- 与文章列表一致：失败态必须给出重试入口，而不是只留一行文案 -->
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="attachments.run()">
        重试
      </button>
    </div>

    <EmptyState
      v-else-if="!attachments.data.value.items.length"
      title="媒体库是空的"
      description="上传的图片和附件都会出现在这里。"
      action-label="上传文件"
      @action="fileInput?.click()"
    />

    <template v-else>
      <ul class="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <li v-for="item in attachments.data.value.items" :key="item.id" class="card overflow-hidden">
          <div class="relative aspect-square bg-surface-muted">
            <img
              v-if="item.kind === 'image'"
              :src="item.thumbnail_url || item.url"
              :alt="item.original_name"
              loading="lazy"
              class="h-full w-full object-cover"
            />
            <div v-else class="flex h-full items-center justify-center text-2xl text-ink-faint">
              <svg class="h-10 w-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
                <path d="M14 3v5h5M6 3h9l5 5v13H6z" stroke-linejoin="round" />
              </svg>
            </div>
          </div>

          <div class="p-3">
            <p class="truncate text-xs font-medium text-ink" :title="item.original_name">
              {{ item.original_name }}
            </p>
            <p class="mt-1 text-[11px] text-ink-faint">
              {{ formatBytes(item.size) }}
              <template v-if="item.width && item.height">
                · {{ item.width }}×{{ item.height }}
              </template>
            </p>
            <p v-if="item.kind === 'image'" class="mt-0.5 text-[11px]">
              <span v-if="item.variants.length" class="text-ink-faint">
                变体 {{ item.variants.map((v) => v.width).join('/') }}
              </span>
              <span v-else class="text-ink-faint/60">无变体</span>
            </p>
            <p class="mt-0.5 text-[11px] text-ink-faint">{{ formatDateTime(item.created_at) }}</p>

            <div class="mt-2 flex items-center gap-2 text-[11px]">
              <button type="button" class="text-brand-600 hover:text-brand-700" @click="copyMarkdown(item)">
                复制 MD
              </button>
              <a
                :href="item.url"
                target="_blank"
                rel="noopener noreferrer"
                class="text-ink-soft hover:text-ink"
              >
                查看
              </a>
              <button
                type="button"
                class="ml-auto text-red-500 hover:text-red-600"
                @click="pendingDelete.request(item)"
              >
                删除
              </button>
            </div>
          </div>
        </li>
      </ul>

      <Pagination
        :page="attachments.data.value.page"
        :page-size="attachments.data.value.page_size"
        :total="attachments.data.value.total"
        @change="(next) => { page = next; attachments.run() }"
      />
    </template>

    <ConfirmDialog
      :open="pendingDelete.pending.value !== null"
      danger
      :loading="pendingDelete.running.value"
      title="删除文件"
      message="删除后会同时移除磁盘上的文件。如果它正被文章引用，文章里的图片会变成裂图。"
      confirm-label="删除"
      @cancel="pendingDelete.cancel"
      @confirm="pendingDelete.confirm"
    />
  </div>
</template>
