/** 全局轻提示。用一个模块级响应式数组承载，任何组件都能 push。 */
import { ref } from 'vue'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

const items = ref<ToastItem[]>([])
let seed = 0

function push(kind: ToastKind, message: string, duration = 3200): number {
  const id = ++seed
  items.value = [...items.value, { id, kind, message }]
  window.setTimeout(() => dismiss(id), duration)
  return id
}

function dismiss(id: number): void {
  items.value = items.value.filter((item) => item.id !== id)
}

export function useToast() {
  return {
    items,
    dismiss,
    success: (message: string) => push('success', message),
    error: (message: string) => push('error', message, 4500),
    info: (message: string) => push('info', message),
  }
}
