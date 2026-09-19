<script setup lang="ts">
/**
 * 文章版本历史面板。
 *
 * 独立成组件而不是塞进编辑器：编辑页已经很长了，而版本历史是一个
 * 自闭环的功能（自己的加载态、自己的错误态、自己的确认流程）。
 *
 * 交互上刻意做的一件事：**点开某一版才请求它的正文**。
 * 列表接口不返回 content_md，所以即使一篇文章有几十版也不会拖慢面板。
 *
 * 挂载时机由父组件用 v-if 控制：默认不挂载，点「历史」才挂。
 * 这样不进历史面板的人一次请求都不会多发。
 */
import { computed, ref, watch } from 'vue'

import { revisionApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useToast } from '@/composables/useToast'
import type { ArticleDetail, Revision } from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'

const props = defineProps<{ articleId: number }>()

const emit = defineEmits<{ restored: [article: ArticleDetail] }>()

const toast = useToast()
const action = useAction()

const versions = useAsyncData<Revision[]>(() => revisionApi.list(props.articleId), [])

/** 当前展开预览的版本（含正文，单独请求）。 */
const previewId = ref<number | null>(null)
const preview = ref<Revision | null>(null)
const previewError = ref('')
const previewLoading = ref(false)

/** 待确认恢复的版本。不直接用 window.confirm：它会阻塞主线程且样式突兀。 */
const pendingRestore = ref<Revision | null>(null)

const hasVersions = computed(() => versions.data.value.length > 0)

// 挂载即加载。**由父组件用 v-if 控制是否挂载**，而不是在这里判 open 再跳过请求——
// 那样面板会一直占着侧栏显示「还没有历史版本」，用户在点「历史」之前
// 就被告知了答案，而且那个答案还是没加载过的假象。
watch(
  () => props.articleId,
  () => {
    previewId.value = null
    preview.value = null
    void versions.run()
  },
  { immediate: true },
)

async function togglePreview(revision: Revision): Promise<void> {
  if (previewId.value === revision.id) {
    previewId.value = null
    preview.value = null
    return
  }
  previewId.value = revision.id
  preview.value = null
  previewError.value = ''
  previewLoading.value = true
  try {
    preview.value = await revisionApi.get(props.articleId, revision.id)
  } catch (error) {
    previewError.value = toErrorMessage(error, '版本内容加载失败')
  } finally {
    previewLoading.value = false
  }
}

function reasonLabel(reason: string): string {
  if (reason === 'restore') return '恢复前快照'
  return '保存前快照'
}

async function confirmRestore(): Promise<void> {
  const target = pendingRestore.value
  if (!target) return

  const restored = await action.run(() => revisionApi.restore(props.articleId, target.id), {
    success: `已恢复到「${target.title}」`,
    errorMessage: '恢复失败',
  })
  pendingRestore.value = null
  if (!restored) return

  // 交给父组件刷新表单，而不是在面板里自己改 —— 表单是父组件的状态
  emit('restored', restored)
  void versions.run()
  previewId.value = null
  preview.value = null
  // 恢复本身也会留下一版，所以列表一定变了，必须重拉
  toast.info('恢复前的内容也已保存为一版，可以再退回来')
}
</script>

<template>
  <div class="card p-4">
    <div class="mb-3 flex items-center justify-between">
      <h3 class="text-sm font-medium text-ink">
        版本历史
        <span v-if="hasVersions" class="ml-1 text-xs font-normal text-ink-faint">
          共 {{ versions.data.value.length }} 版
        </span>
      </h3>
      <button
        type="button"
        class="text-xs text-ink-faint transition-colors hover:text-ink"
        :disabled="versions.loading.value"
        @click="versions.run()"
      >
        刷新
      </button>
    </div>

    <p v-if="versions.loading.value && !versions.ready.value" class="text-xs text-ink-faint">
      加载中…
    </p>

    <p v-else-if="versions.error.value" class="text-xs text-ink-soft">
      {{ toErrorMessage(versions.error.value) }}
    </p>

    <p v-else-if="!hasVersions" class="text-xs leading-relaxed text-ink-faint">
      还没有历史版本。每次改动标题、摘要或正文时，系统会自动把**改动前**的内容存为一版。
    </p>

    <ul v-else class="space-y-2">
      <li v-for="item in versions.data.value" :key="item.id" class="rounded-lg border border-border">
        <div class="flex flex-wrap items-center gap-2 px-3 py-2">
          <button
            type="button"
            class="min-w-0 flex-1 text-left"
            :aria-expanded="previewId === item.id"
            @click="togglePreview(item)"
          >
            <span class="block truncate text-sm text-ink">{{ item.title }}</span>
            <span class="mt-0.5 block text-[11px] text-ink-faint">
              {{ formatDateTime(item.created_at) }} · {{ formatRelative(item.created_at) }}
              · {{ item.author?.nickname || item.author?.username || '未知' }}
              · {{ item.content_length }} 字符
            </span>
          </button>

          <span
            v-if="item.reason === 'restore'"
            class="shrink-0 rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-200"
          >
            {{ reasonLabel(item.reason) }}
          </span>

          <button
            type="button"
            class="btn--ghost shrink-0 text-xs"
            :disabled="action.running.value"
            @click="pendingRestore = item"
          >
            恢复
          </button>
        </div>

        <!-- 正文按需加载：列表接口刻意不返回 content_md -->
        <div v-if="previewId === item.id" class="border-t border-border px-3 py-2">
          <p v-if="previewLoading" class="text-xs text-ink-faint">加载中…</p>
          <p v-else-if="previewError" class="text-xs text-ink-soft">{{ previewError }}</p>
          <template v-else-if="preview">
            <p v-if="preview.summary" class="mb-2 text-xs text-ink-soft">{{ preview.summary }}</p>
            <pre
              class="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-muted p-2 font-mono text-[11px] leading-relaxed text-ink-soft"
            >{{ preview.content_md || '（这一版正文为空）' }}</pre>
          </template>
        </div>
      </li>
    </ul>

    <ConfirmDialog
      :open="pendingRestore !== null"
      title="恢复到这一版？"
      :message="`将把文章内容替换为「${pendingRestore?.title ?? ''}」。当前内容会先自动存为一版，恢复错了可以再退回来。`"
      confirm-label="恢复"
      :loading="action.running.value"
      @confirm="confirmRestore"
      @cancel="pendingRestore = null"
    />
  </div>
</template>
