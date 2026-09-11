<script setup lang="ts">
/** 分类与标签管理。 */
import { onMounted, ref } from 'vue'

import { ApiError, categoryApi, tagApi } from '@/api'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { Category, Tag } from '@/types'

const toast = useToast()
const auth = useAuthStore()

const categories = useAsyncData<Category[]>(() => categoryApi.list(true), [])
const tags = useAsyncData<Tag[]>(() => tagApi.list(), [])

/* -------------------------------------------------- 分类 */

const newCategory = ref({ name: '', description: '', sort_order: 0 })
const categoryBusy = ref(false)
const editingCategoryId = ref<number | null>(null)
const categoryDraft = ref({ name: '', description: '' })
const pendingCategoryDelete = ref<Category | null>(null)

async function createCategory(): Promise<void> {
  const name = newCategory.value.name.trim()
  if (!name) {
    toast.error('请填写分类名称')
    return
  }
  categoryBusy.value = true
  try {
    await categoryApi.create({
      name,
      description: newCategory.value.description.trim() || null,
      sort_order: newCategory.value.sort_order,
    })
    newCategory.value = { name: '', description: '', sort_order: 0 }
    toast.success('分类已创建')
    await categories.run()
  } catch (error) {
    toast.error(toErrorMessage(error, '创建失败'))
  } finally {
    categoryBusy.value = false
  }
}

function startEditCategory(item: Category): void {
  editingCategoryId.value = item.id
  categoryDraft.value = { name: item.name, description: item.description ?? '' }
}

async function saveCategory(): Promise<void> {
  const id = editingCategoryId.value
  if (id === null) return
  try {
    await categoryApi.update(id, {
      name: categoryDraft.value.name.trim(),
      description: categoryDraft.value.description.trim() || null,
    })
    editingCategoryId.value = null
    toast.success('分类已更新')
    await categories.run()
  } catch (error) {
    toast.error(toErrorMessage(error, '更新失败'))
  }
}

async function deleteCategory(): Promise<void> {
  const target = pendingCategoryDelete.value
  if (!target) return
  try {
    await categoryApi.remove(target.id)
    toast.success('分类已删除，其下文章变为未分类')
    pendingCategoryDelete.value = null
    await Promise.all([categories.run(), tags.run()])
  } catch (error) {
    // 非站长会拿到 403，这里给出明确解释，而不是一句"操作失败"
    toast.error(
      error instanceof ApiError && error.isForbidden
        ? '只有站长可以删除分类'
        : toErrorMessage(error, '删除失败'),
    )
  }
}

/* -------------------------------------------------- 标签 */

const newTagName = ref('')
const tagBusy = ref(false)
const pendingTagDelete = ref<Tag | null>(null)

async function createTag(): Promise<void> {
  const name = newTagName.value.trim()
  if (!name) return
  tagBusy.value = true
  try {
    await tagApi.create({ name })
    newTagName.value = ''
    toast.success('标签已创建')
    await tags.run()
  } catch (error) {
    toast.error(toErrorMessage(error, '创建失败'))
  } finally {
    tagBusy.value = false
  }
}

async function deleteTag(): Promise<void> {
  const target = pendingTagDelete.value
  if (!target) return
  try {
    await tagApi.remove(target.id)
    toast.success('标签已删除')
    pendingTagDelete.value = null
    await tags.run()
  } catch (error) {
    toast.error(toErrorMessage(error, '删除失败'))
  }
}

async function cleanupTags(): Promise<void> {
  try {
    const result = await tagApi.cleanup()
    toast.success(result.detail)
    await tags.run()
  } catch (error) {
    toast.error(
      error instanceof ApiError && error.isForbidden
        ? '只有站长可以清理标签'
        : toErrorMessage(error, '清理失败'),
    )
  }
}

onMounted(() => {
  void categories.run()
  void tags.run()
})
</script>

<template>
  <div class="grid gap-6 lg:grid-cols-2">
    <!-- 分类 -->
    <section class="space-y-4">
      <div class="card p-4">
        <h2 class="mb-3 text-sm font-medium text-ink">新建分类</h2>
        <div class="space-y-2">
          <input v-model="newCategory.name" class="input" placeholder="分类名称" maxlength="50" />
          <input v-model="newCategory.description" class="input" placeholder="一句话描述（选填）" />
          <button type="button" class="btn-primary w-full" :disabled="categoryBusy" @click="createCategory">
            添加分类
          </button>
        </div>
      </div>

      <div class="card overflow-hidden">
        <div class="border-b border-border px-4 py-3">
          <h2 class="text-sm font-medium text-ink">
            全部分类 <span class="text-xs font-normal text-ink-faint">{{ categories.data.value.length }}</span>
          </h2>
        </div>

        <p v-if="!categories.data.value.length" class="px-4 py-6 text-center text-sm text-ink-soft">
          还没有分类。
        </p>

        <ul v-else class="divide-y divide-border">
          <li v-for="item in categories.data.value" :key="item.id" class="px-4 py-3">
            <div v-if="editingCategoryId === item.id" class="space-y-2">
              <input v-model="categoryDraft.name" class="input" />
              <input v-model="categoryDraft.description" class="input" placeholder="描述" />
              <div class="flex gap-2">
                <button type="button" class="btn-primary flex-1 text-xs" @click="saveCategory">保存</button>
                <button type="button" class="btn-ghost flex-1 text-xs" @click="editingCategoryId = null">
                  取消
                </button>
              </div>
            </div>

            <div v-else class="flex items-center gap-3">
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-ink">{{ item.name }}</p>
                <p class="mt-0.5 font-mono text-xs text-ink-faint">/{{ item.slug }}</p>
                <p v-if="item.description" class="mt-1 truncate text-xs text-ink-soft">
                  {{ item.description }}
                </p>
              </div>
              <span class="shrink-0 text-xs text-ink-faint">{{ item.article_count }} 篇</span>
              <div class="flex shrink-0 items-center gap-2 text-xs">
                <button type="button" class="text-ink-soft hover:text-ink" @click="startEditCategory(item)">
                  编辑
                </button>
                <button
                  v-if="auth.isAdmin"
                  type="button"
                  class="text-red-500 hover:text-red-600"
                  @click="pendingCategoryDelete = item"
                >
                  删除
                </button>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </section>

    <!-- 标签 -->
    <section class="space-y-4">
      <div class="card p-4">
        <h2 class="mb-3 text-sm font-medium text-ink">新建标签</h2>
        <div class="flex gap-2">
          <input
            v-model="newTagName"
            class="input"
            placeholder="标签名称"
            maxlength="50"
            @keydown.enter.prevent="createTag"
          />
          <button type="button" class="btn-primary shrink-0" :disabled="tagBusy" @click="createTag">
            添加
          </button>
        </div>
      </div>

      <div class="card overflow-hidden">
        <div class="flex items-center gap-3 border-b border-border px-4 py-3">
          <h2 class="text-sm font-medium text-ink">
            全部标签 <span class="text-xs font-normal text-ink-faint">{{ tags.data.value.length }}</span>
          </h2>
          <button
            v-if="auth.isAdmin"
            type="button"
            class="btn-ghost ml-auto px-2.5 py-1 text-xs"
            title="删除没有任何文章引用的标签"
            @click="cleanupTags"
          >
            清理空标签
          </button>
        </div>

        <p v-if="!tags.data.value.length" class="px-4 py-6 text-center text-sm text-ink-soft">
          还没有标签。
        </p>

        <ul v-else class="divide-y divide-border">
          <li
            v-for="item in tags.data.value"
            :key="item.id"
            class="flex items-center gap-3 px-4 py-2.5"
          >
            <span class="min-w-0 flex-1 truncate text-sm text-ink">{{ item.name }}</span>
            <span class="shrink-0 text-xs text-ink-faint">{{ item.article_count }} 篇</span>
            <button
              type="button"
              class="shrink-0 text-xs text-red-500 hover:text-red-600"
              @click="pendingTagDelete = item"
            >
              删除
            </button>
          </li>
        </ul>
      </div>
    </section>

    <ConfirmDialog
      :open="pendingCategoryDelete !== null"
      danger
      title="删除分类"
      :message="`删除「${pendingCategoryDelete?.name ?? ''}」后，其下的 ${pendingCategoryDelete?.article_count ?? 0} 篇文章会变成「未分类」，文章本身不会被删除。`"
      confirm-label="删除分类"
      @cancel="pendingCategoryDelete = null"
      @confirm="deleteCategory"
    />

    <ConfirmDialog
      :open="pendingTagDelete !== null"
      danger
      title="删除标签"
      :message="`删除标签「${pendingTagDelete?.name ?? ''}」只会解除它与文章的关联，文章内容不受影响。`"
      confirm-label="删除标签"
      @cancel="pendingTagDelete = null"
      @confirm="deleteTag"
    />
  </div>
</template>
