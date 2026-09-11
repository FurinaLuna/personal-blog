/** 主题 Store：亮色 / 暗色 / 跟随系统 三态。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'blog-theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')

function readInitial(): ThemeMode {
  // 与 index.html 里的内联脚本读同一个 key，保证首屏不闪白
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  return 'system'
}

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode === 'system') return media.matches ? 'dark' : 'light'
  return mode
}

export const useThemeStore = defineStore('theme', () => {
  /** 用户的选择（亮 / 暗 / 跟随系统）。 */
  const mode = ref<ThemeMode>(readInitial())
  /** 实际呈现的主题（system 会被解析成 light 或 dark）。 */
  const resolved = ref<ResolvedTheme>(resolve(mode.value))

  const isDark = computed(() => resolved.value === 'dark')

  function paint(value: ResolvedTheme): void {
    resolved.value = value
    document.documentElement.classList.toggle('dark', value === 'dark')
  }

  function apply(next: ThemeMode): void {
    mode.value = next
    localStorage.setItem(STORAGE_KEY, next)
    paint(resolve(next))
  }

  // 「跟随系统」模式下，系统主题切换要实时跟上。
  // 监听器只注册一次，但在手动亮/暗时命中条件直接跳过，开销可以忽略。
  media.addEventListener('change', () => {
    if (mode.value === 'system') paint(resolve('system'))
  })

  /** 按 亮 → 暗 → 跟随系统 的顺序循环切换。 */
  function cycle(): void {
    const order: ThemeMode[] = ['light', 'dark', 'system']
    const next = order[(order.indexOf(mode.value) + 1) % order.length]
    apply(next)
  }

  return { mode, resolved, isDark, apply, cycle }
})
