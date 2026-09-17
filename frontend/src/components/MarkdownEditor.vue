<script setup lang="ts">
/**
 * Markdown 编辑器。
 *
 * 刻意只做「文本域 + 工具栏 + 预览」这一档，不引入 CodeMirror/Vditor 这类大型编辑器：
 * 这个项目里写文章的人只有一个（站长自己），为一个用户引入几百 KB 的编辑器不划算。
 * 真需要更复杂的能力（协同编辑、公式、思维导图）再换，替换成本也就是这一个组件。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { useUpload } from '@/composables/useUpload'
import { renderMarkdown } from '@/utils/markdown'

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
    minHeight?: number
  }>(),
  { placeholder: '开始写点什么…支持 Markdown 语法', minHeight: 420 },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const textarea = ref<HTMLTextAreaElement | null>(null)
const mode = ref<'edit' | 'preview' | 'split'>('edit')
const fileInput = ref<HTMLInputElement | null>(null)
/** 上传交互（校验/提示/计数）交给 useUpload；这里只负责把结果插入光标处 */
const upload = useUpload()

/**
 * 预览防抖。
 *
 * 整条渲染管线（marked 解析 → DOMPurify 消毒 → hljs 逐块高亮 → 重新序列化）
 * 在一篇长文上是几十毫秒级。跟着每次按键同步跑，敲起来会明显发黏。
 * 200ms 是「打字停顿」与「感觉不到延迟」之间的常用折中。
 */
const PREVIEW_DEBOUNCE_MS = 200

/** 供预览使用的正文。与 modelValue 之间有防抖延迟。 */
const previewSource = ref(props.modelValue)
let previewTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.modelValue,
  (value) => {
    if (previewTimer) clearTimeout(previewTimer)
    previewTimer = setTimeout(() => {
      previewSource.value = value
    }, PREVIEW_DEBOUNCE_MS)
  },
)

onBeforeUnmount(() => {
  if (previewTimer) clearTimeout(previewTimer)
})

/**
 * 预览 HTML。
 *
 * 注意：模板里必须用 `v-if` 而不是 `v-show` 控制它的挂载 —— computed 是惰性的，
 * 只有真正被渲染时才会求值。用 `v-show` 的话子树照样渲染，**编辑模式下每敲一个字
 * 都会白跑一遍完整渲染管线**（而 'edit' 正是默认模式）。
 */
const html = computed(() => renderMarkdown(previewSource.value).html)
const charCount = computed(() => props.modelValue.length)

/** 在光标处包裹一层语法；没有选中内容时插入占位文本并选中它，省一次手动选中 */
function wrap(before: string, after = '', placeholder = '文字'): void {
  const element = textarea.value
  if (!element) return

  const start = element.selectionStart
  const end = element.selectionEnd
  const selected = props.modelValue.slice(start, end) || placeholder
  const next =
    props.modelValue.slice(0, start) + before + selected + after + props.modelValue.slice(end)

  emit('update:modelValue', next)

  // DOM 更新后再设置选区，否则会被 Vue 的重新渲染覆盖
  requestAnimationFrame(() => {
    element.focus()
    element.setSelectionRange(start + before.length, start + before.length + selected.length)
  })
}

function insertLine(prefix: string): void {
  const element = textarea.value
  if (!element) return
  const start = element.selectionStart
  const lineStart = props.modelValue.lastIndexOf('\n', start - 1) + 1
  const next =
    props.modelValue.slice(0, lineStart) + prefix + props.modelValue.slice(lineStart)
  emit('update:modelValue', next)
  requestAnimationFrame(() => {
    element.focus()
    element.setSelectionRange(start + prefix.length, start + prefix.length)
  })
}

async function uploadImage(file: File): Promise<void> {
  const result = await upload.upload(file, {
    successMessage: '图片已插入',
    errorMessage: '图片上传失败',
  })
  try {
    if (!result) return
    // 上传完直接把 Markdown 片段插到光标处，「上传即插入」
    const element = textarea.value
    const at = element ? element.selectionStart : props.modelValue.length
    const snippet = `\n${result.markdown}\n`
    emit('update:modelValue', props.modelValue.slice(0, at) + snippet + props.modelValue.slice(at))
  } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}

function onFileChange(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) void uploadImage(file)
}

/** 把粘贴板里的图片直接走上传，比「先存到本地再选文件」顺手得多 */
function onPaste(event: ClipboardEvent): void {
  const item = Array.from(event.clipboardData?.items ?? []).find((entry) =>
    entry.type.startsWith('image/'),
  )
  const file = item?.getAsFile()
  if (!file) return
  event.preventDefault()
  void uploadImage(file)
}
</script>

<template>
  <div class="overflow-hidden rounded-xl border border-border bg-surface">
    <!-- 工具栏 -->
    <div class="flex flex-wrap items-center gap-1 border-b border-border px-2 py-1.5">
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="加粗" @click="wrap('**', '**', '加粗文字')">
        <strong>B</strong>
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs italic" title="斜体" @click="wrap('*', '*', '斜体文字')">
        I
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="行内代码" @click="wrap('`', '`', 'code')">
        &lt;/&gt;
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="链接" @click="wrap('[', '](https://)', '链接文字')">
        链接
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="引用" @click="insertLine('> ')">
        引用
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="二级标题" @click="insertLine('## ')">
        H2
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="列表" @click="insertLine('- ')">
        列表
      </button>
      <button type="button" class="btn--ghost px-2 py-1 text-xs" title="代码块" @click="wrap('\n```\n', '\n```\n', 'code')">
        代码块
      </button>

      <span class="mx-1 h-4 w-px bg-border"></span>

      <button
        type="button"
        class="btn--ghost px-2 py-1 text-xs"
        :disabled="upload.uploading.value"
        title="上传图片并插入"
        @click="fileInput?.click()"
      >
        {{ upload.uploading.value ? '上传中…' : '图片' }}
      </button>
      <input
        ref="fileInput"
        type="file"
        accept="image/*"
        class="hidden"
        aria-label="插入图片"
        @change="onFileChange"
      />

      <div class="ml-auto flex items-center gap-1">
        <button
          v-for="option in (['edit', 'split', 'preview'] as const)"
          :key="option"
          type="button"
          class="rounded-md px-2 py-1 text-xs transition-colors"
          :class="mode === option ? 'bg-surface-muted font-medium text-brand-600' : 'text-ink-soft hover:text-ink'"
          @click="mode = option"
        >
          {{ option === 'edit' ? '编辑' : option === 'split' ? '分栏' : '预览' }}
        </button>
      </div>
    </div>

    <div class="flex" :class="mode === 'split' ? 'divide-x divide-border' : ''">
      <!-- 编辑区 -->
      <textarea
        v-show="mode !== 'preview'"
        ref="textarea"
        class="w-full resize-y border-0 bg-transparent p-4 font-mono text-sm leading-relaxed text-ink placeholder:text-ink-faint focus:outline-none"
        :class="mode === 'split' ? 'w-1/2' : ''"
        :style="{ minHeight: `${minHeight}px` }"
        :value="modelValue"
        :placeholder="placeholder"
        aria-label="正文（Markdown）"
        spellcheck="false"
        @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
        @paste="onPaste"
      />

      <!-- 预览区 -->
      <div
        v-show="mode !== 'edit'"
        class="w-full overflow-auto p-4"
        :class="mode === 'split' ? 'w-1/2' : ''"
        :style="{ minHeight: `${minHeight}px` }"
      >
        <div
          v-if="mode === 'preview' && !modelValue.trim()"
          class="text-sm text-ink-faint"
        >
          还没有内容。
        </div>
        <!-- 与 MarkdownRenderer 一样，内容已由 utils/markdown.ts 消毒。

             这里用 `v-else-if="mode !== 'edit'"` 而不是裸 `v-else`，是为了让
             编辑模式下**根本不渲染**这个节点：computed 惰性求值，
             节点不存在就不会去算 `html`，也就不会每敲一个字都跑一遍渲染管线。 -->
        <div
          v-else-if="mode !== 'edit'"
          class="prose prose-slate max-w-none dark:prose-invert"
          v-html="html"
        />
      </div>
    </div>

    <div class="border-t border-border px-4 py-1.5 text-xs text-ink-faint">
      {{ charCount }} 字符 · 粘贴图片可直接上传
    </div>
  </div>
</template>
