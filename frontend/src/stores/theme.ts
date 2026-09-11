/** 主题（亮/暗）Store。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type ThemeMode = 'light' | 'dark'

const STORAGE_KEY = 'blog-theme'

function readInitial(): ThemeMode {
  // 与 index.html 里的内联脚本读同一个 key，保证首屏不闪白
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readInitial())

  const isDark = computed(() => mode.value === 'dark')

  function apply(next: ThemeMode): void {
    mode.value = next
    document.documentElement.classList.toggle('dark', next === 'dark')
    localStorage.setItem(STORAGE_KEY, next)
  }

  function toggle(): void {
    apply(mode.value === 'dark' ? 'light' : 'dark')
  }

  return { mode, isDark, apply, toggle }
})
