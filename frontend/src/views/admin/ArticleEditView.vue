<script setup lang="ts">
/** 文章编辑器（新建 / 编辑共用）。 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError, articleApi, attachmentApi, categoryApi, tagApi } from '@/api'
import MarkdownEditor from '@/components/MarkdownEditor.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useDraftAutosave } from '@/composables/useDraftAutosave'
import { useToast } from '@/composables/useToast'
import type { ArticleDetail, ArticleStatus, Category, Tag } from '@/types'
import { formatBytes, formatRelative } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const toast = useToast()

const articleId = computed(() => {
  const raw = route.params.id
  return raw ? Number(raw) : null
})
const isEdit = computed(() => articleId.value !== null)

const form = ref({
  title: '',
  slug: '',
  summary: '',
  content_md: '',
  cover_image: '',
  status: 'draft' as ArticleStatus,
  is_top: false,
  allow_comment: true,
  category_id: null as number | null,
  tags: [] as string[],
})
const tagInput = ref('')
// 保存与封面上传是两个独立动作：用两个实例，避免「上传封面时保存按钮也被禁用」
const action = useAction()
const coverAction = useAction()
const coverInput = ref<HTMLInputElement | null>(null)
/** 字段级错误，key 与后端返回的 field 对齐（如 "title" / "content_md"） */
const fieldErrors = ref<Record<string, string>>({})
const formError = ref('')

const categories = useAsyncData<Category[]>(() => categoryApi.list(false), [])
const allTags = ref<Tag[]>([])

const isDraft = computed(() => form.value.status === 'draft')

/* -------------------------------------------------- 本地草稿保护 */

/** 表单是否已就绪：加载完成前不允许写快照，否则空表单会覆盖掉已有草稿。 */
const formReady = ref(false)

const draft = useDraftAutosave({
  // 新建与编辑分开存：从「写新文章」切到编辑某篇时不会串
  key: () => (articleId.value === null ? 'article:new' : `article:${articleId.value}`),
  snapshot: () => form.value,
  enabled: () => formReady.value,
})

/** 待用户决定是否恢复的本地草稿。 */
const pendingDraft = ref<{ payload: typeof form.value; savedAt: number } | null>(null)

/** 草稿保存时间的人话描述，用于横幅提示。 */
const draftSavedLabel = computed(() => {
  if (!pendingDraft.value) return ''
  return formatRelative(new Date(pendingDraft.value.savedAt).toISOString())
})

function applyDraft(): void {
  if (!pendingDraft.value) return
  form.value = pendingDraft.value.payload
  pendingDraft.value = null
  toast.success('已恢复本地草稿')
}

function discardDraft(): void {
  pendingDraft.value = null
  draft.clear()
  toast.info('已放弃本地草稿')
}

/* -------------------------------------------------- 载入 */

async function loadArticle(): Promise<void> {
  if (articleId.value === null) {
    // 新建页：没有服务端内容可比对，直接看本地有没有草稿
    pendingDraft.value = draft.checkExisting()
    formReady.value = true
    return
  }
  try {
    const detail: ArticleDetail = await articleApi.detail(articleId.value)
    form.value = {
      title: detail.title,
      slug: detail.slug,
      summary: detail.summary ?? '',
      content_md: detail.content_md,
      cover_image: detail.cover_image ?? '',
      status: detail.status,
      is_top: detail.is_top,
      allow_comment: detail.allow_comment,
      category_id: detail.category?.id ?? null,
      tags: detail.tags.map((tag) => tag.name),
    }
    // 本地草稿比服务端更新时才提示：否则用户刚保存完又开一次，会被无意义地打扰
    const found = draft.checkExisting()
    const serverUpdatedAt = Date.parse(detail.updated_at)
    if (found && found.savedAt > serverUpdatedAt) {
      pendingDraft.value = found
    } else if (found) {
      draft.clear()
    }
  } catch (error) {
    formError.value = toErrorMessage(error, '文章加载失败')
  } finally {
    formReady.value = true
  }
}

/* -------------------------------------------------- 标签输入 */

function addTag(name: string): void {
  const value = name.trim()
  if (!value) return
  if (form.value.tags.includes(value)) {
    tagInput.value = ''
    return
  }
  if (form.value.tags.length >= 10) {
    toast.error('一篇文章最多 10 个标签')
    return
  }
  form.value.tags = [...form.value.tags, value]
  tagInput.value = ''
}

function removeTag(name: string): void {
  form.value.tags = form.value.tags.filter((item) => item !== name)
}

/** 键盘操作：Enter / 逗号 添加，Backspace 在输入框为空时删掉最后一个 */
function onTagKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' || event.key === ',') {
    event.preventDefault()
    addTag(tagInput.value)
  } else if (event.key === 'Backspace' && !tagInput.value && form.value.tags.length) {
    form.value.tags = form.value.tags.slice(0, -1)
  }
}

/* -------------------------------------------------- 封面上传 */

async function uploadCover(file: File): Promise<void> {
  const result = await coverAction.run(() => attachmentApi.upload(file), {
    success: '封面已上传',
    errorMessage: '封面上传失败',
  })
  if (result !== undefined) form.value.cover_image = result.url
  if (coverInput.value) coverInput.value.value = ''
}

function onCoverChange(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) {
    if (file.size > 5 * 1024 * 1024) {
      toast.error(`图片过大（${formatBytes(file.size)}），请压缩到 5MB 以内`)
      if (coverInput.value) coverInput.value.value = ''
      return
    }
    void uploadCover(file)
  }
}

/* -------------------------------------------------- 保存 */

function buildPayload(status: ArticleStatus) {
  return {
    title: form.value.title.trim(),
    // 传 null 而不是空串，让后端走「按标题自动生成 slug」
    slug: form.value.slug.trim() || null,
    summary: form.value.summary.trim() || null,
    content_md: form.value.content_md,
    cover_image: form.value.cover_image.trim() || null,
    status,
    is_top: form.value.is_top,
    allow_comment: form.value.allow_comment,
    category_id: form.value.category_id,
    tags: form.value.tags,
  }
}

async function save(status: ArticleStatus): Promise<void> {
  fieldErrors.value = {}
  formError.value = ''

  if (!form.value.title.trim()) {
    fieldErrors.value = { title: '标题不能为空' }
    toast.error('请先填写标题')
    return
  }

  const payload = buildPayload(status)
  const saved = await action.run(
    () =>
      isEdit.value
        ? articleApi.update(articleId.value as number, payload)
        : articleApi.create(payload),
    {
      success: status === 'draft' ? '草稿已保存' : '文章已发布',
      // 字段级错误贴到输入框、整体原因留在页面顶部的提示条，
      // 两个位置都要写，所以走 onError 而不是 onFieldErrors
      onError: (error) => {
        fieldErrors.value = error instanceof ApiError ? error.fields : {}
        formError.value =
          error instanceof ApiError ? error.message : toErrorMessage(error, '保存失败')
      },
    },
  )
  if (saved === undefined) return

  // 服务端已落库，本地快照完成使命：留着只会在下次打开时误报「有未保存内容」
  draft.clear()

  // 新建时把 URL 换成编辑态，避免用户继续点"保存"又创建一篇重复文章
  if (!isEdit.value) {
    await router.replace(`/admin/articles/${saved.id}/edit`)
  }
  // 后端可能会规范化 slug，回填以免下次保存又用旧的
  form.value.slug = saved.slug
}

onMounted(async () => {
  void categories.run()
  try {
    allTags.value = await tagApi.list({ withCounts: false })
  } catch {
    // 标签列表只用于输入联想，拿不到不影响编辑正文；输入框仍可手动输入新标签
    console.debug('[article-edit] 标签列表加载失败，改为手动输入')
    allTags.value = []
  }
  await loadArticle()
})
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="text-sm text-ink-soft">
        {{ isEdit ? '编辑已有文章' : '新建文章' }}
      </h2>
      <div class="ml-auto flex items-center gap-2">
        <span v-if="form.slug" class="hidden font-mono text-xs text-ink-faint sm:inline">
          /article/{{ form.slug }}
        </span>
        <button type="button" class="btn-ghost" :disabled="action.running.value" @click="save('draft')">
          保存草稿
        </button>
        <button type="button" class="btn-primary" :disabled="action.running.value" @click="save('published')">
          {{ action.running.value ? '保存中…' : isDraft ? '发布' : '更新' }}
        </button>
      </div>
    </div>

    <p
      v-if="formError"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/50 dark:text-red-300"
    >
      {{ formError }}
    </p>

    <!-- 本地草稿恢复提示：只在「本地比服务端新」时出现，不主动覆盖用户输入 -->
    <div
      v-if="pendingDraft"
      class="flex flex-wrap items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-800 dark:bg-amber-950/40"
    >
      <span class="text-amber-800 dark:text-amber-200">
        发现本地未保存的草稿（{{ draftSavedLabel }}），可能是上次意外关闭留下的。
      </span>
      <div class="ml-auto flex items-center gap-3">
        <button type="button" class="btn-primary" @click="applyDraft">恢复草稿</button>
        <button
          type="button"
          class="text-amber-800 underline hover:no-underline dark:text-amber-200"
          @click="discardDraft"
        >
          放弃
        </button>
      </div>
    </div>

    <!-- 自动保存状态：让用户知道「写了但没点保存」时内容有没有被兜住 -->
    <p v-if="formReady && draft.available.value && draft.savedAt.value" class="text-xs text-ink-faint">
      本地草稿已于 {{ formatRelative(new Date(draft.savedAt.value).toISOString()) }} 自动保存（仅存于本机）
    </p>
    <p v-else-if="formReady && !draft.available.value" class="text-xs text-ink-faint">
      当前浏览器不支持本地草稿（隐私模式或存储已满），请及时手动保存。
    </p>

    <div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_260px]">
      <!-- 主编辑区 -->
      <div class="space-y-4">
        <div>
          <input
            v-model="form.title"
            class="input text-lg font-medium"
            placeholder="文章标题"
            maxlength="200"
            @input="fieldErrors.title = ''"
          />
          <p v-if="fieldErrors.title" class="mt-1 text-xs text-red-600">{{ fieldErrors.title }}</p>
        </div>

        <MarkdownEditor v-model="form.content_md" :min-height="480" />
        <p v-if="fieldErrors.content_md" class="text-xs text-red-600">
          {{ fieldErrors.content_md }}
        </p>
      </div>

      <!-- 右侧设置 -->
      <aside class="space-y-4">
        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">发布设置</h3>

          <label class="block text-xs text-ink-soft">
            状态
            <select v-model="form.status" class="input mt-1.5">
              <option value="draft">草稿</option>
              <option value="published">已发布</option>
              <option value="archived">已归档</option>
            </select>
          </label>

          <label class="mt-3 flex items-center gap-2 text-sm text-ink-soft">
            <input v-model="form.is_top" type="checkbox" class="rounded border-border" />
            置顶到列表最前
          </label>
          <label class="mt-2 flex items-center gap-2 text-sm text-ink-soft">
            <input v-model="form.allow_comment" type="checkbox" class="rounded border-border" />
            允许评论
          </label>
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">分类</h3>
          <select v-model="form.category_id" class="input">
            <option :value="null">未分类</option>
            <option v-for="item in categories.data.value" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">
            标签 <span class="text-xs font-normal text-ink-faint">{{ form.tags.length }}/10</span>
          </h3>

          <div class="mb-2 flex flex-wrap gap-1.5">
            <span
              v-for="name in form.tags"
              :key="name"
              class="inline-flex items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 text-xs text-ink-soft"
            >
              {{ name }}
              <button
                type="button"
                class="text-ink-faint hover:text-red-500"
                :aria-label="`移除标签 ${name}`"
                @click="removeTag(name)"
              >
                ×
              </button>
            </span>
          </div>

          <input
            v-model="tagInput"
            class="input"
            placeholder="输入后回车添加"
            :list="'tag-suggestions'"
            @keydown="onTagKeydown"
            @blur="addTag(tagInput)"
          />
          <datalist id="tag-suggestions">
            <option v-for="tag in allTags" :key="tag.id" :value="tag.name" />
          </datalist>
          <p class="mt-2 text-xs text-ink-faint">不存在的标签会自动创建。</p>
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">摘要</h3>
          <textarea
            v-model="form.summary"
            class="input min-h-[88px] resize-y"
            placeholder="留空则自动从正文提取"
            maxlength="500"
          />
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">封面图</h3>
          <img
            v-if="form.cover_image"
            :src="form.cover_image"
            alt="封面预览"
            class="mb-2 w-full rounded-lg object-cover"
          />
          <div class="flex gap-2">
            <button
              type="button"
              class="btn-ghost flex-1 text-xs"
              :disabled="coverAction.running.value"
              @click="coverInput?.click()"
            >
              {{ coverAction.running.value ? '上传中…' : form.cover_image ? '更换封面' : '上传封面' }}
            </button>
            <button
              v-if="form.cover_image"
              type="button"
              class="btn-ghost text-xs"
              @click="form.cover_image = ''"
            >
              移除
            </button>
          </div>
          <input
            ref="coverInput"
            type="file"
            accept="image/*"
            class="hidden"
            @change="onCoverChange"
          />
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">URL 别名</h3>
          <input v-model="form.slug" class="input font-mono text-xs" placeholder="留空自动生成" />
          <p class="mt-2 text-xs text-ink-faint">
            已发布文章的别名一旦改动，原有外链会失效，请谨慎修改。
          </p>
        </div>
      </aside>
    </div>

    <div class="flex justify-between">
      <RouterLink to="/admin/articles" class="btn-ghost">← 返回列表</RouterLink>
    </div>
  </div>
</template>
