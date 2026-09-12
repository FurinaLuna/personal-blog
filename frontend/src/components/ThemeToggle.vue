<script setup lang="ts">
/** 主题切换：亮 → 暗 → 跟随系统，循环切换。 */
import { computed } from 'vue'

import { useThemeStore, type ThemeMode } from '@/stores/theme'

const theme = useThemeStore()

const LABELS: Record<ThemeMode, string> = {
  light: '当前：亮色模式（点击切换到暗色）',
  dark: '当前：暗色模式（点击切换到跟随系统）',
  system: '当前：跟随系统（点击切换到亮色）',
}

const label = computed(() => LABELS[theme.mode])
</script>

<template>
  <button
    type="button"
    class="btn-icon inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
    :title="label"
    :aria-label="label"
    @click="theme.cycle()"
  >
    <!-- 太阳：亮色 -->
    <svg
      v-if="theme.mode === 'light'"
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
    <!-- 月亮：暗色 -->
    <svg
      v-else-if="theme.mode === 'dark'"
      class="h-[18px] w-[18px]"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
    <!-- 显示器：跟随系统 -->
    <svg
      v-else
      class="h-[18px] w-[18px]"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8m-4-4v4" />
    </svg>
  </button>
</template>
