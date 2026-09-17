<script setup lang="ts">
/** 文章编辑器（新建 / 编辑共用）。 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError, articleApi, categoryApi, seriesApi, tagApi } from '@/api'
import MarkdownEditor from '@/components/MarkdownEditor.vue'
import RevisionHistory from '@/components/RevisionHistory.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useDraftAutosave } from '@/composables/useDraftAutosave'
import { useTagInput } from '@/composables/useTagInput'
import { useToast } from '@/composables/useToast'
import { useUpload } from '@/composables/useUpload'
import type { ArticleDetail, ArticleStatus, Category, Series, Tag } from '@/types'
import { formatRelative } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const toast = useToast()

const articleId = computed(() => {
  const raw = route.params.id
  return raw ? Number(raw) : null
})
const isEdit = computed(() => articleId.value !== null)

/**
 * 刚由「新建」保存成功后 replace 出来的 id。
 *
 * 用来把「我自己刚触发的路由变化」从「用户切到了另一篇文章」里区分出来：
 * 前者不该重新拉取覆盖表单，后者必须。
 */
let justSavedId: number | null = null

/** 版本历史面板是否展开。收起时不请求，避免每次进编辑页都白拉一遍。 */
const showHistory = ref(false)

const form = ref({
  title: '',
  slug: '',
  summary: '',
  content_md: '',
  cover_image: '',
  status: 'draft' as ArticleStatus,
  /** 发布时间（ISO，UTC）。空串表示「没设过」。 */
  published_at: '',
  is_top: false,
  allow_comment: true,
  category_id: null as number | null,
  series_id: null as number | null,
  series_order: 0,
  tags: [] as string[],
})

/**
 * `<input type="datetime-local">` 与 ISO 字符串之间的双向桥。
 *
 * 两个格式互不兼容，转换必须显式做：
 * - 控件要的是**本地时间** `YYYY-MM-DDTHH:mm`（不能带 Z）；
 * - 接口要的是 ISO（带时区）。
 *
 * 直接 `new Date(iso)` 再取本地分量，浏览器会自动完成时区换算——
 * 作者在 +08:00 填「明早 9 点」，存进去就是 01:00Z，展示回来还是本地 9 点。
 */
const publishAtLocal = computed({
  get(): string {
    const iso = form.value.published_at
    if (!iso) return ''
    const date = new Date(iso)
    if (Number.isNaN(date.getTime())) return ''
    // 减去时区偏移再取 ISO 前缀，得到控件要的本地时间串
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    return local.toISOString().slice(0, 16)
  },
  set(value: string) {
    // 控件给的是本地时间，`new Date('2026-09-20T09:00')` 在浏览器里按本地时区解释
    form.value.published_at = value ? new Date(value).toISOString() : ''
  },
})

/** 表单里是不是一个「排在未来」的时间。 */
const isScheduledForm = computed(() => {
  if (form.value.status !== 'published' || !form.value.published_at) return false
  return new Date(form.value.published_at).getTime() > Date.now()
})
// 标签输入交互（键盘语义/去重/上限）交给 useTagInput；
// 通过桥接 computed 让标签仍属于 form，草稿快照与提交 payload 结构不变
const { input: newTagName, add: addTag, remove: removeTag, onKeydown: onTagKeydown } = useTagInput(
  computed({
    get: () => form.value.tags,
    set: (next: string[]) => {
      form.value.tags = next
    },
  }),
  { max: 10 },
)
// 保存与封面上传是两个独立动作：用两个实例，避免「上传封面时保存按钮也被禁用」
const action = useAction()
const coverUpload = useUpload({ maxSizeMB: 5 })
const coverInput = ref<HTMLInputElement | null>(null)
/** 字段级错误，key 与后端返回的 field 对齐（如 "title" / "content_md"） */
const fieldErrors = ref<Record<string, string>>({})
const formError = ref('')

const categories = useAsyncData<Category[]>(() => categoryApi.list(false), [])
const allTags = ref<Tag[]>([])
/** 系列列表（下拉选择用，不带计数） */
const seriesOptions = useAsyncData<Series[]>(() => seriesApi.list(false), [])

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
      published_at: detail.published_at ?? '',
      is_top: detail.is_top,
      allow_comment: detail.allow_comment,
      category_id: detail.category?.id ?? null,
      series_id: detail.series?.id ?? null,
      series_order: detail.series_order ?? 0,
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

// addTag / removeTag / onTagKeydown 由 useTagInput 提供（见上方解构）

/* -------------------------------------------------- 封面上传 */

async function onCoverChange(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  // 大小预检（5MB）+ 上传 + 提示由 useUpload 统一处理
  const result = await coverUpload.upload(file, {
    successMessage: '封面已上传',
    errorMessage: '封面上传失败',
  })
  if (result) form.value.cover_image = result.url
  if (coverInput.value) coverInput.value.value = ''
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
    // 始终显式传：空串 → null（清掉排期，回到「立即发布/不设时间」）。
    // 不传的话后端会走「不修改」分支，作者就没法取消一个已设定的排期时间。
    published_at: form.value.published_at || null,
    is_top: form.value.is_top,
    allow_comment: form.value.allow_comment,
    category_id: form.value.category_id,
    series_id: form.value.series_id,
    series_order: form.value.series_order,
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

  // 新建时把 URL 换成编辑态，避免用户继续点"保存"又创建一篇重复文章。
  // 记下 id，让上面的 watch 知道这次路由变化是"自己人"，不必重新拉取。
  if (!isEdit.value) {
    justSavedId = saved.id
    await router.replace(`/admin/articles/${saved.id}/edit`)
  }
  // 后端可能会规范化 slug，回填以免下次保存又用旧的
  form.value.slug = saved.slug
  // 状态也必须回填。状态是从「点了哪个按钮」传进来的参数，但界面读的是
  // form.status（下拉框的 v-model、按钮文案都由它驱动）。不回填的话，
  // 发布一篇新文章后下拉框仍显示"草稿"、按钮仍显示"发布"，
  // 作者无法确认到底发出去没有，很可能再点一次或跑去草稿箱里找。
  form.value.status = status
  // 发布时间同理，而且这里**必须**以后端返回值为准：留空提交时后端会补上
  // 「现在」，不回填的话输入框一直空着，作者会以为没设上时间。
  form.value.published_at = saved.published_at ?? ''
}

/**
 * 从版本历史恢复之后：把返回的文章内容填回表单。
 *
 * 不在版本面板里直接改 form —— 表单是这一层拥有的状态，
 * 让子组件去改父组件的数据源，会让「谁改了表单」变得难以追踪。
 */
function onRestored(restored: ArticleDetail): void {
  form.value = {
    title: restored.title,
    slug: restored.slug,
    summary: restored.summary ?? '',
    content_md: restored.content_md,
    cover_image: restored.cover_image ?? '',
    status: restored.status,
    published_at: restored.published_at ?? '',
    is_top: restored.is_top,
    allow_comment: restored.allow_comment,
    category_id: restored.category?.id ?? null,
    series_id: restored.series?.id ?? null,
    series_order: restored.series_order ?? 0,
    tags: restored.tags.map((tag) => tag.name),
  }
  // 恢复后的内容与本地草稿快照已经不一致，清掉以免下次进来提示「恢复未保存草稿」
  draft.clear()
}

onMounted(async () => {
  void categories.run()
  void seriesOptions.run()
  try {
    allTags.value = await tagApi.list({ withCounts: false })
  } catch {
    // 标签列表只用于输入联想，拿不到不影响编辑正文；输入框仍可手动输入新标签
    console.debug('[article-edit] 标签列表加载失败，改为手动输入')
    allTags.value = []
  }
})

/**
 * 跟随路由里的文章 id 加载。
 *
 * 以前只绑在 onMounted 上：组件实例被复用而 id 变化时（直接改地址栏、
 * 或将来任何「从编辑器跳到另一个编辑器」的入口），表单会**静静地继续显示上一篇**，
 * 而保存会 PATCH 到新 id 上 —— 一条链接之遥的数据丢失。
 *
 * 唯一的例外是「新建保存成功后 router.replace 成编辑态」那一次：
 * 那时表单里已经是刚保存的内容，重新拉取只会白跑一趟，
 * 还可能因为草稿时间戳比对弹出无意义的「恢复草稿」提示。
 */
watch(
  articleId,
  (id) => {
    if (id !== null && id === justSavedId) {
      justSavedId = null
      return
    }
    void loadArticle()
  },
  { immediate: true },
)
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
        <button
          v-if="isEdit"
          type="button"
          class="btn--ghost text-xs"
          :aria-pressed="showHistory"
          @click="showHistory = !showHistory"
        >
          {{ showHistory ? '隐藏历史' : '历史' }}
        </button>
        <button type="button" class="btn--ghost" :disabled="action.running.value" @click="save('draft')">
          保存草稿
        </button>
        <button type="button" class="btn--primary" :disabled="action.running.value" @click="save('published')">
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
        <button type="button" class="btn--primary" @click="applyDraft">恢复草稿</button>
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
            aria-label="文章标题"
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

          <!-- 定时发布：填一个未来的时间即可。用 <input type="datetime-local">
               而不是自己搓日期选择器——原生控件自带时区处理、键盘输入与移动端适配。 -->
          <label class="mt-3 block text-xs text-ink-soft">
            发布时间
            <input
              v-model="publishAtLocal"
              type="datetime-local"
              class="input mt-1.5"
              aria-label="发布时间（留空表示立即发布）"
            />
          </label>
          <p class="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
            <template v-if="isScheduledForm">
              到点前访客看不到这篇文章（详情 404、不出现在列表、不接受评论），
              到点后自动出现，不需要你回来手动发布。
            </template>
            <template v-else>留空表示立即发布；填未来时间即为定时发布。</template>
          </p>

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
          <!-- 上方的 <h3> 只是视觉标题，读屏软件不会把它当成这个下拉框的名称 -->
          <select v-model="form.category_id" class="input" aria-label="文章分类">
            <option :value="null">未分类</option>
            <option v-for="item in categories.data.value" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">
            系列 <span class="text-xs font-normal text-ink-faint">（技术连载 / 合集）</span>
          </h3>
          <select v-model="form.series_id" class="input" aria-label="所属系列">
            <option :value="null">未挂系列</option>
            <option v-for="item in seriesOptions.data.value" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
          <label class="mt-3 block text-xs text-ink-soft">
            系列内顺序
            <input
              v-model.number="form.series_order"
              type="number"
              min="0"
              class="input mt-1.5"
              :disabled="form.series_id === null"
              title="小者在前；用于详情页系列导航"
            />
          </label>
          <p v-if="seriesOptions.error.value" class="mt-2 text-xs text-ink-faint">
            系列列表加载失败，仍可手动输入 id（见后台系列管理）。
          </p>
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
            v-model="newTagName"
            class="input"
            placeholder="输入后回车添加"
            aria-label="添加标签"
            :list="'tag-suggestions'"
            @keydown="onTagKeydown"
            @blur="addTag(newTagName)"
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
            aria-label="文章摘要"
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
              class="btn--ghost flex-1 text-xs"
              :disabled="coverUpload.uploading.value"
              @click="coverInput?.click()"
            >
              {{ coverUpload.uploading.value ? '上传中…' : form.cover_image ? '更换封面' : '上传封面' }}
            </button>
            <button
              v-if="form.cover_image"
              type="button"
              class="btn--ghost text-xs"
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
            aria-label="上传封面图"
            @change="onCoverChange"
          />
        </div>

        <div class="card p-4">
          <h3 class="mb-3 text-sm font-medium text-ink">URL 别名</h3>
          <input
            v-model="form.slug"
            class="input font-mono text-xs"
            placeholder="留空自动生成"
            aria-label="URL 别名"
          />
          <p class="mt-2 text-xs text-ink-faint">
            已发布文章的别名一旦改动，原有外链会失效，请谨慎修改。
          </p>
        </div>

        <!-- 版本历史只在「编辑已有文章」时出现：
             新建的文章还没有 id，也还没有任何历史可言 -->
        <RevisionHistory
          v-if="isEdit && articleId !== null && showHistory"
          :article-id="articleId"
          @restored="onRestored"
        />
      </aside>
    </div>

    <div class="flex justify-between">
      <RouterLink to="/admin/articles" class="btn--ghost">← 返回列表</RouterLink>
    </div>
  </div>
</template>
