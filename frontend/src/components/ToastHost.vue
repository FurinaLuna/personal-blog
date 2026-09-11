<script setup lang="ts">
/** 全局轻提示宿主。挂在布局里一次，所有页面共用。 */
import { useToast } from '@/composables/useToast'

const { items, dismiss } = useToast()

const STYLES: Record<string, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/60 dark:text-emerald-200',
  error: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900/60 dark:bg-red-950/60 dark:text-red-200',
  info: 'border-border bg-surface text-ink',
}
</script>

<template>
  <!-- 挂在 body 上，避免被祖先元素的 overflow/transform 裁掉 -->
  <Teleport to="body">
    <div
      class="pointer-events-none fixed inset-x-0 bottom-6 z-[60] flex flex-col items-center gap-2 px-4"
      role="status"
      aria-live="polite"
    >
      <TransitionGroup
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="translate-y-2 opacity-0"
        leave-active-class="transition duration-150 ease-in"
        leave-to-class="opacity-0"
      >
        <button
          v-for="item in items"
          :key="item.id"
          type="button"
          class="pointer-events-auto max-w-[min(92vw,420px)] rounded-lg border px-4 py-2.5 text-left text-sm shadow-lg"
          :class="STYLES[item.kind]"
          @click="dismiss(item.id)"
        >
          {{ item.message }}
        </button>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
