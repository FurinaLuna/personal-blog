<script setup lang="ts">
/** 系列管理：技术连载 / 合集的增改删。作者可建/改，删除需站长（后端强制）。 */
import { onMounted, ref } from 'vue'

import { seriesApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useAction } from '@/composables/useAction'
import { useAsyncData, toErrorMessage } from '@/composables/useAsyncData'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useToast } from '@/composables/useToast'
import type { Series } from '@/types'

const toast = useToast()
const action = useAction()

const series = useAsyncData<Series[]>(() => seriesApi.list(true), [])

/* -------------------------------------------------- 新建 */

const newName = ref('')
const newDescription = ref('')

async function createSeries(): Promise<void> {
  const name = newName.value.trim()
  if (!name) {
    toast.error('请填写系列名称')
    return
  }
  const created = await action.run(
    () => seriesApi.create({ name, description: newDescription.value.trim() || null }),
    { success: '系列已创建', errorMessage: '创建失败' },
  )
  if (created === undefined) return
  newName.value = ''
  newDescription.value = ''
  await series.run()
}

/* -------------------------------------------------- 编辑 */

const editingId = ref<number | null>(null)
const editDraft = ref({ name: '', description: '' })

function startEdit(item: Series): void {
  editingId.value = item.id
  editDraft.value = { name: item.name, description: item.description ?? '' }
}

async function saveEdit(): Promise<void> {
  const id = editingId.value
  if (id === null) return
  const saved = await action.run(
    () =>
      seriesApi.update(id, {
        name: editDraft.value.name.trim(),
        description: editDraft.value.description.trim() || null,
      }),
    { success: '系列已更新', errorMessage: '更新失败' },
  )
  if (saved === undefined) return
  editingId.value = null
  await series.run()
}

/* -------------------------------------------------- 删除 */

const pendingDelete = useConfirmDelete<Series>({
  remove: (item) => seriesApi.remove(item.id),
  success: '系列已删除，其下文章变为普通文章',
  // 作者点删除会拿到 403，给出明确解释
  mapError: (error) => (error instanceof Error && /403|站长/.test(String(error)) ? '只有站长可以删除系列' : undefined),
  onDeleted: () => void series.run(),
})

onMounted(() => {
  void series.run()
})
</script>

<template>
  <div class="space-y-5">
    <!-- 新建系列 -->
    <div class="card p-4">
      <h2 class="mb-3 text-sm font-medium text-ink">新建系列</h2>
      <div class="flex flex-wrap items-start gap-2">
        <input v-model="newName" class="input w-48" placeholder="系列名称（如 SQLite 踩坑记）" maxlength="100" />
        <input v-model="newDescription" class="input w-64" placeholder="一句话简介（选填）" maxlength="200" />
        <button type="button" class="btn--primary" :disabled="action.running.value" @click="createSeries">
          {{ action.running.value ? '创建中…' : '创建' }}
        </button>
      </div>
    </div>

    <!-- 系列列表 -->
    <div class="card overflow-hidden">
      <div class="border-b border-border px-5 py-3">
        <h2 class="text-sm font-medium text-ink">
          全部系列 <span class="text-xs font-normal text-ink-faint">{{ series.data.value.length }}</span>
        </h2>
      </div>

      <div v-if="series.loading.value && !series.ready.value" class="space-y-2 p-5">
        <div v-for="index in 3" :key="index" class="skeleton h-10 rounded-lg"></div>
      </div>

      <p v-else-if="series.error.value" class="p-6 text-center text-sm text-ink-soft">
        {{ toErrorMessage(series.error.value) }}
      </p>

      <EmptyState
        v-else-if="!series.data.value.length"
        title="还没有系列"
        description="把连载文章挂到系列下，详情页就会出现系列导航，读者可以按顺序读完整个系列。"
      />

      <ul v-else class="divide-y divide-border">
        <li v-for="item in series.data.value" :key="item.id" class="px-5 py-3">
          <div v-if="editingId === item.id" class="space-y-2">
            <input v-model="editDraft.name" class="input" maxlength="100" />
            <input v-model="editDraft.description" class="input" placeholder="简介" maxlength="200" />
            <div class="flex gap-2">
              <button type="button" class="btn--primary flex-1 text-xs" @click="saveEdit">保存</button>
              <button type="button" class="btn--ghost flex-1 text-xs" @click="editingId = null">取消</button>
            </div>
          </div>

          <div v-else class="flex items-center gap-3">
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-ink">
                {{ item.name }}
                <span class="ml-2 text-xs text-ink-faint">{{ item.article_count }} 篇</span>
              </p>
              <p class="mt-0.5 font-mono text-xs text-ink-faint">/{{ item.slug }}</p>
              <p v-if="item.description" class="mt-1 truncate text-xs text-ink-soft">
                {{ item.description }}
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-2 text-xs">
              <button type="button" class="text-ink-soft hover:text-ink" @click="startEdit(item)">
                编辑
              </button>
              <button type="button" class="text-red-500 hover:text-red-600" @click="pendingDelete.request(item)">
                删除
              </button>
            </div>
          </div>
        </li>
      </ul>
    </div>

    <ConfirmDialog
      :open="pendingDelete.pending.value !== null"
      danger
      :loading="pendingDelete.running.value"
      title="删除系列"
      :message="`删除「${pendingDelete.pending.value?.name ?? ''}」不会删除其下文章，它们会变回普通文章。`"
      confirm-label="删除系列"
      @cancel="pendingDelete.cancel"
      @confirm="pendingDelete.confirm"
    />
  </div>
</template>
