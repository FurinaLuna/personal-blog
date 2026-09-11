<script setup lang="ts">
/** 亮/暗主题切换。 */
import { computed } from 'vue'

import { useThemeStore } from '@/stores/theme'

const theme = useThemeStore()
const label = computed(() => (theme.isDark ? '切换到亮色模式' : '切换到暗色模式'))
</script>

<template>
  <button
    type="button"
    class="inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
    :title="label"
    :aria-label="label"
    @click="theme.toggle()"
  >
    <svg
      v-if="theme.isDark"
      class="h-[18px] w-[18px]"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
    >
      <circle cx="12" cy="12" r="4" />
      <path
        d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
      />
    </svg>
    <svg
      v-else
      class="h-[18px] w-[18px]"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
  </button>
</template>
