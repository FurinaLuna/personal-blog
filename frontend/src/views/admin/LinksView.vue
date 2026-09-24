<script setup lang="ts">
/**
 * 友链管理（仅站长）。
 *
 * 与分类/标签的后台页同一个套路：顶部一个新建表单，下面一个列表，
 * 每行可原地编辑、可删除。刻意**不抽公共「管理页」组件** —— 三个页面的
 * 字段与校验规则各不相同，抽象出来的配置项会比省下的代码还长。
 */
import { computed, onMounted, ref } from 'vue'

import { linkApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useToast } from '@/composables/useToast'
import type { FriendLink } from '@/types'

const toast = useToast()
const action = useAction()

const links = useAsyncData<FriendLink[]>(() => linkApi.listManaged(), [])

/** 已启用的条数：后台要能一眼看出"前台会显示几条"。 */
const activeCount = computed(() => links.data.value.filter((item) => item.is_active).length)

/* -------------------------------------------------- 新建 */

const draft = ref({ name: '', url: '', description: '', avatar_url: '', sort_order: 0 })

function resetDraft(): void {
  draft.value = { name: '', url: '', description: '', avatar_url: '', sort_order: 0 }
}

async function createLink(): Promise<void> {
  const name = draft.value.name.trim()
  const url = draft.value.url.trim()
  // 前端只拦"明显没填"；协议白名单与结构校验以后端为准（只有一处实现）
  if (!name) {
    toast.error('请填写站点名称')
    return
  }
  if (!url) {
    toast.error('请填写站点地址')
    return
  }
  const created = await action.run(
    () =>
      linkApi.create({
        name,
        url,
        description: draft.value.description.trim() || null,
        avatar_url: draft.value.avatar_url.trim() || null,
        sort_order: draft.value.sort_order,
      }),
    { success: '友链已添加', errorMessage: '添加失败' },
  )
  if (created === undefined) return
  resetDraft()
  await links.run()
}

/* -------------------------------------------------- 原地编辑 */

const editingId = ref<number | null>(null)
const editDraft = ref({ name: '', url: '', description: '', avatar_url: '', sort_order: 0 })

function startEdit(link: FriendLink): void {
  editingId.value = link.id
  editDraft.value = {
    name: link.name,
    url: link.url,
    description: link.description ?? '',
    avatar_url: link.avatar_url ?? '',
    sort_order: link.sort_order,
  }
}

async function saveEdit(): Promise<void> {
  const id = editingId.value
  if (id === null) return
  const name = editDraft.value.name.trim()
  const url = editDraft.value.url.trim()
  // 与新建同一道校验：清空再保存不该把空值发给后端（只能靠 422 兜底）
  if (!name) {
    toast.error('请填写站点名称')
    return
  }
  if (!url) {
    toast.error('请填写站点地址')
    return
  }
  const saved = await action.run(
    () =>
      linkApi.update(id, {
        name,
        url,
        description: editDraft.value.description.trim() || null,
        avatar_url: editDraft.value.avatar_url.trim() || null,
        sort_order: editDraft.value.sort_order,
      }),
    { success: '友链已更新', errorMessage: '更新失败' },
  )
  if (saved === undefined) return
  editingId.value = null
  await links.run()
}

/* -------------------------------------------------- 启用 / 停用 */

async function toggleActive(link: FriendLink): Promise<void> {
  const next = !link.is_active
  const done = await action.run(() => linkApi.update(link.id, { is_active: next }), {
    success: next ? '已在前台显示' : '已从前台隐藏',
    errorMessage: '操作失败',
  })
  if (done === undefined) return
  await links.run()
}

/* -------------------------------------------------- 删除 */

const pendingDelete = useConfirmDelete<FriendLink>({
  remove: (item) => linkApi.remove(item.id),
  success: (item) => `《${item.name}》已删除`,
  onDeleted: () => void links.run(),
})

onMounted(() => {
  void links.run()
})
</script>

<template>
  <div class="mx-auto max-w-content space-y-5">
    <div>
      <h2 class="text-sm text-ink-soft">
        共 {{ links.data.value.length }} 条，其中
        <span class="font-medium text-ink">{{ activeCount }}</span> 条在前台显示
      </h2>
    </div>

    <!-- 新建 -->
    <form class="card grid gap-3 p-5 sm:grid-cols-2" @submit.prevent="createLink">
      <h3 class="text-sm font-medium text-ink sm:col-span-2">添加友链</h3>

      <label class="block text-sm">
        <span class="text-ink-soft">站点名称</span>
        <input v-model="draft.name" class="input mt-1.5" maxlength="50" aria-label="站点名称" />
      </label>
      <label class="block text-sm">
        <span class="text-ink-soft">站点地址</span>
        <input
          v-model="draft.url"
          class="input mt-1.5"
          placeholder="example.com 或 https://example.com"
          maxlength="500"
          aria-label="站点地址"
        />
      </label>
      <label class="block text-sm">
        <span class="text-ink-soft">头像地址（选填）</span>
        <input v-model="draft.avatar_url" class="input mt-1.5" maxlength="500" aria-label="头像地址" />
      </label>
      <label class="block text-sm">
        <span class="text-ink-soft">排序（小的在前）</span>
        <input
          v-model.number="draft.sort_order"
          type="number"
          min="0"
          class="input mt-1.5"
          aria-label="排序"
        />
      </label>
      <label class="block text-sm sm:col-span-2">
        <span class="text-ink-soft">一句话介绍（选填）</span>
        <input v-model="draft.description" class="input mt-1.5" maxlength="200" aria-label="介绍" />
      </label>

      <div class="sm:col-span-2">
        <button type="submit" class="btn--primary" :disabled="action.running.value">
          {{ action.running.value ? '提交中…' : '添加友链' }}
        </button>
      </div>
    </form>

    <div v-if="links.loading.value && !links.ready.value" class="space-y-2">
      <div v-for="index in 4" :key="index" class="skeleton h-16 rounded-lg"></div>
    </div>

    <div
      v-else-if="links.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(links.error.value) }}</span>
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="links.run()">重试</button>
    </div>

    <EmptyState
      v-else-if="!links.data.value.length"
      title="还没有友链"
      description="在上面添加第一条，前台 /links 页面就会显示它。"
    />

    <ul v-else class="card divide-y divide-border">
      <li v-for="item in links.data.value" :key="item.id" class="px-4 py-3">
        <div v-if="editingId === item.id" class="space-y-2">
          <div class="grid gap-2 sm:grid-cols-2">
            <input v-model="editDraft.name" class="input" maxlength="50" aria-label="编辑站点名称" />
            <input v-model="editDraft.url" class="input" maxlength="500" aria-label="编辑站点地址" />
            <input
              v-model="editDraft.avatar_url"
              class="input"
              maxlength="500"
              placeholder="头像地址（选填）"
              aria-label="编辑头像地址"
            />
            <input
              v-model.number="editDraft.sort_order"
              type="number"
              min="0"
              class="input"
              aria-label="编辑排序"
            />
            <input
              v-model="editDraft.description"
              class="input sm:col-span-2"
              maxlength="200"
              placeholder="一句话介绍（选填）"
              aria-label="编辑介绍"
            />
          </div>
          <div class="flex gap-2">
            <button type="button" class="btn--primary text-xs" @click="saveEdit">保存</button>
            <button type="button" class="btn--ghost text-xs" @click="editingId = null">取消</button>
          </div>
        </div>

        <div v-else class="flex items-center gap-3">
          <img
            v-if="item.avatar_url"
            :src="item.avatar_url"
            alt=""
            class="h-8 w-8 shrink-0 rounded object-cover"
            loading="lazy"
          />
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium text-ink">
              {{ item.name }}
              <span
                v-if="!item.is_active"
                class="ml-1.5 rounded bg-surface-muted px-1.5 py-0.5 text-[11px] text-ink-faint"
              >
                已隐藏
              </span>
            </p>
            <p class="mt-0.5 truncate font-mono text-xs text-ink-faint">{{ item.url }}</p>
            <p v-if="item.description" class="mt-0.5 truncate text-xs text-ink-soft">
              {{ item.description }}
            </p>
          </div>
          <span class="shrink-0 text-xs text-ink-faint">排序 {{ item.sort_order }}</span>
          <div class="flex shrink-0 items-center gap-2 text-xs">
            <button type="button" class="text-ink-soft hover:text-ink" @click="startEdit(item)">
              编辑
            </button>
            <button
              type="button"
              class="text-ink-soft hover:text-ink"
              @click="toggleActive(item)"
            >
              {{ item.is_active ? '隐藏' : '显示' }}
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

    <ConfirmDialog
      :open="pendingDelete.pending.value !== null"
      danger
      :loading="pendingDelete.running.value"
      title="删除友链"
      confirm-label="删除友链"
      :message="`确定要删除「${pendingDelete.pending.value?.name ?? ''}」吗？删除后前台不再显示。`"
      @cancel="pendingDelete.cancel"
      @confirm="pendingDelete.confirm"
    />
  </div>
</template>
