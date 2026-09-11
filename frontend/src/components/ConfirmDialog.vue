<script setup lang="ts">
/**
 * 确认对话框。
 *
 * 不用 window.confirm 的原因：它无法定制文案、在移动端样式突兀，
 * 而且会阻塞主线程。风险操作（删除）值得一个像样的确认步骤。
 */
import { onBeforeUnmount, onMounted, watch } from 'vue'

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

/** ESC 关闭。绑在 document 上而不是元素上——用户焦点可能在任意位置。 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) emit('cancel')
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
  (open) => lockScroll(open),
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
        class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4 backdrop-blur-sm"
        role="dialog"
        aria-modal="true"
        @click.self="emit('cancel')"
      >
        <div class="card w-full max-w-sm p-5 shadow-xl">
          <h2 class="text-base font-semibold text-ink">{{ title }}</h2>
          <p v-if="message" class="mt-2 text-sm leading-relaxed text-ink-soft">{{ message }}</p>

          <div class="mt-5 flex justify-end gap-2">
            <button type="button" class="btn-ghost" :disabled="loading" @click="emit('cancel')">
              {{ cancelLabel }}
            </button>
            <button
              type="button"
              :class="danger ? 'btn-danger' : 'btn-primary'"
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
