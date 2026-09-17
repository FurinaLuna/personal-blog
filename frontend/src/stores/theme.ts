/** 主题 Store：亮色 / 暗色 / 跟随系统 三态。 */
import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref } from 'vue'

import { readStored, writeStored } from '@/utils/storage'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'blog-theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')

function readInitial(): ThemeMode {
  // 与 index.html 里的内联脚本读同一个 key，保证首屏不闪白。
  // 走 readStored 而不是裸 localStorage：主题 store 在 main.ts 里被**立即实例化**，
  // 模块求值阶段抛异常会让 `router.isReady().then(() => app.mount())` 永远不执行——
  // 用户看到的是一片空白页，而不是「主题没记住」这种小毛病。
  const saved = readStored(STORAGE_KEY)
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
    // 写不进去（配额满 / 隐私模式）只影响「下次能否记住」，不该阻断这次切换
    writeStored(STORAGE_KEY, next)
    paint(resolve(next))
  }

  // 「跟随系统」模式下，系统主题切换要实时跟上。
  // 手动选亮/暗时命中条件直接跳过，开销可以忽略。
  function onSystemChange(): void {
    if (mode.value === 'system') paint(resolve('system'))
  }
  media.addEventListener('change', onSystemChange)
  // Pinia 的 setup store 跑在 effect scope 里，这里能拿到真正的销毁时机。
  // 不清理的话每次重建 store（测试 / HMR）都会多挂一个监听器，
  // 表现为「测试之间互相干扰」——现场和原因隔得很远，很难查。
  onScopeDispose(() => media.removeEventListener('change', onSystemChange))

  /** 按 亮 → 暗 → 跟随系统 的顺序循环切换。 */
  function cycle(): void {
    const order: ThemeMode[] = ['light', 'dark', 'system']
    const next = order[(order.indexOf(mode.value) + 1) % order.length]
    apply(next)
  }

  return { mode, resolved, isDark, apply, cycle }
})
