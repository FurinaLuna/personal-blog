<script setup lang="ts">
/**
 * 确认对话框。
 *
 * 不用 window.confirm 的原因：它无法定制文案、在移动端样式突兀，
 * 而且会阻塞主线程。风险操作（删除）值得一个像样的确认步骤。
 *
 * 无障碍要点（声明了 aria-modal 就必须兑现，否则屏幕阅读器用户会以为
 * 自己还在页面上，键盘 Tab 也会跑到遮罩背后的控件上去）：
 * 1. 打开时把焦点移进对话框；
 * 2. Tab / Shift+Tab 在对话框内循环；
 * 3. 关闭时把焦点还给触发它的那个元素；
 * 4. 用 aria-labelledby / aria-describedby 关联标题与正文。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    message?: string
    confirmLabel?: string
    cancelLabel?: string
    danger?: boolean
    loading?: boolean
  }>(),
  {
    title: '确认操作',
    message: '',
    confirmLabel: '确认',
    cancelLabel: '取消',
    danger: false,
    loading: false,
  },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

// useId 而不是手写随机串：SSR / 多实例下天然唯一，且不会造成水合不一致
const titleId = useId()
const messageId = useId()

const panel = ref<HTMLElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
const confirmButton = ref<HTMLButtonElement | null>(null)

/** 打开前焦点在哪 —— 关闭时要还回去，否则键盘用户会掉到页面开头。 */
let previouslyFocused: HTMLElement | null = null

function focusableElements(): HTMLElement[] {
  if (!panel.value) return []
  return Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  )
}

/** 打开：记录来源焦点，并把焦点移进对话框。 */
function handleOpen(): void {
  previouslyFocused = document.activeElement as HTMLElement | null
  void nextTick(() => {
    // 危险操作默认聚焦「取消」：用户敲下 Enter 的肌肉记忆不该直接删掉数据
    const target = props.danger ? cancelButton.value : (confirmButton.value ?? cancelButton.value)
    target?.focus()
  })
}

/** 关闭：把焦点还给打开它的那个元素。 */
function handleClose(): void {
  const target = previouslyFocused
  previouslyFocused = null
  void nextTick(() => {
    // 元素可能已经因为列表刷新被移除了，先确认它还在文档里
    if (target && document.contains(target)) target.focus()
  })
}

/**
 * 键盘处理。
 *
 * ESC 绑在 document 上（用户焦点可能在任意位置）；
 * Tab 循环绑在面板上，事件不会外泄到页面其余部分。
 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) {
    emit('cancel')
    return
  }
  if (event.key !== 'Tab' || !props.open) return

  const items = focusableElements()
  if (items.length === 0) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement

  // 焦点跑到对话框外面（或还没进来）时，先拉回第一个
  if (!panel.value?.contains(active)) {
    event.preventDefault()
    first.focus()
    return
  }
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

/** 打开时锁滚动，否则移动端背景会跟着滑动，体感很糟 */
function lockScroll(locked: boolean): void {
  document.body.style.overflow = locked ? 'hidden' : ''
}

onMounted(() => document.addEventListener('keydown', onKeydown))

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  lockScroll(false)
})

watch(
  () => props.open,
  (open) => {
    lockScroll(open)
    if (open) handleOpen()
    else handleClose()
  },
)
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0"
      leave-active-class="transition duration-100 ease-in"
      leave-to-class="opacity-0"
    >
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4 backdrop-blur-sm"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="message ? messageId : undefined"
        @click.self="emit('cancel')"
      >
        <div ref="panel" class="card w-full max-w-sm p-5 shadow-xl">
          <h2 :id="titleId" class="text-base font-semibold text-ink">{{ title }}</h2>
          <p v-if="message" :id="messageId" class="mt-2 text-sm leading-relaxed text-ink-soft">
            {{ message }}
          </p>

          <div class="mt-5 flex justify-end gap-2">
            <button
              ref="cancelButton"
              type="button"
              class="btn--ghost"
              :disabled="loading"
              @click="emit('cancel')"
            >
              {{ cancelLabel }}
            </button>
            <button
              ref="confirmButton"
              type="button"
              :class="danger ? 'btn--danger' : 'btn--primary'"
              :disabled="loading"
              @click="emit('confirm')"
            >
              {{ loading ? '处理中…' : confirmLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
