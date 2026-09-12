<script setup lang="ts">
/** 媒体库：上传、预览、复制 Markdown、删除。 */
import { onMounted, ref } from 'vue'

import { attachmentApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useToast } from '@/composables/useToast'
import type { Attachment, Page } from '@/types'
import { formatBytes, formatDateTime } from '@/utils/format'
import { emptyPage as emptyPageOf } from '@/utils/pagination'

const toast = useToast()
const action = useAction()

const PAGE_SIZE = 24
const page = ref(1)
const kind = ref<'all' | 'image' | 'file'>('all')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const pendingDelete = ref<Attachment | null>(null)

const emptyPage = emptyPageOf<Attachment>(PAGE_SIZE)
const attachments = useAsyncData<Page<Attachment>>(
  () => attachmentApi.list(page.value, PAGE_SIZE, kind.value === 'all' ? undefined : kind.value),
  emptyPage,
)

async function upload(files: FileList | null): Promise<void> {
  if (!files?.length) return
  uploading.value = true
  let succeeded = 0
  const failures: string[] = []

  // 逐个上传并汇报每个文件的结果：批量上传里"部分失败"是常态，
  // 一句笼统的"上传失败"会让用户完全不知道是哪个文件出了问题。
  for (const file of Array.from(files)) {
    try {
      await attachmentApi.upload(file)
      succeeded += 1
    } catch (error) {
      failures.push(`${file.name}：${toErrorMessage(error, '上传失败')}`)
    }
  }

  uploading.value = false
  if (fileInput.value) fileInput.value.value = ''
  if (succeeded) toast.success(`成功上传 ${succeeded} 个文件`)
  for (const message of failures) toast.error(message)
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

async function confirmDelete(): Promise<void> {
  const target = pendingDelete.value
  if (!target) return
  const done = await action.run(() => attachmentApi.remove(target.id), {
    success: '已删除',
    errorMessage: '删除失败',
  })
  if (done === undefined) return
  pendingDelete.value = null
  void attachments.run()
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
        class="btn-primary ml-auto"
        :disabled="uploading"
        @click="fileInput?.click()"
      >
        {{ uploading ? '上传中…' : '上传文件' }}
      </button>
      <input
        ref="fileInput"
        type="file"
        multiple
        accept="image/*,.pdf,.zip,.txt,.md,.csv,.json,.docx,.xlsx,.pptx,.epub"
        class="hidden"
        @change="upload(($event.target as HTMLInputElement).files)"
      />
    </div>

    <div v-if="attachments.loading.value && !attachments.ready.value" class="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <div v-for="index in 8" :key="index" class="skeleton aspect-square rounded-xl"></div>
    </div>

    <p v-else-if="attachments.error.value" class="card p-6 text-center text-sm text-ink-soft">
      {{ toErrorMessage(attachments.error.value) }}
    </p>

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
                @click="pendingDelete = item"
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
      :open="pendingDelete !== null"
      danger
      :loading="action.running.value"
      title="删除文件"
      message="删除后会同时移除磁盘上的文件。如果它正被文章引用，文章里的图片会变成裂图。"
      confirm-label="删除"
      @cancel="pendingDelete = null"
      @confirm="confirmDelete"
    />
  </div>
</template>
