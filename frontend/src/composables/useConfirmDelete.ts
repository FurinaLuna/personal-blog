/**
 * 删除确认的通用封装。
 *
 * 后台 5 个视图（文章 / 用户 / 分类 / 标签 / 评论 / 媒体）各自重复着同一套
 * 「pendingDelete 状态 + ConfirmDialog 模板 + confirmDelete 函数」三件套。
 * 抽出来的收益：对话框开关语义（何时开、何时关、失败时是否保留）只有一处实现。
 *
 * 语义约定：
 * - `request(item)` 只记录目标并打开对话框，**不发请求**；
 * - `confirm()` 执行删除，成功才关闭对话框（失败保留，让用户看到错误后可重试）；
 * - `cancel()` 无条件关闭。
 */
import { ref, type ComputedRef, type Ref } from 'vue'

import { useAction } from '@/composables/useAction'

export interface ConfirmDeleteOptions<T> {
  /** 真正执行删除的函数（通常是 `api.remove(item.id)` 的包装） */
  remove: (item: T) => Promise<unknown>
  /** 成功提示文案；支持按目标生成（如带上文章标题） */
  success?: string | ((item: T) => string)
  /** 失败兜底文案 */
  errorMessage?: string
  /** 自定义错误映射（如 403 转成人话），语义同 useAction 的 mapError */
  mapError?: (error: unknown) => string | undefined
  /** 删除成功后的回调（通常是刷新列表） */
  onDeleted?: (item: T) => void
}

export interface ConfirmDelete<T> {
  /** 待删除目标；非空即对话框应打开 */
  pending: Ref<T | null>
  /** 删除请求是否执行中（喂给 ConfirmDialog 的 loading） */
  running: ComputedRef<boolean>
  /** 请求删除某项（打开对话框） */
  request: (item: T) => void
  /** 取消（关闭对话框，不发请求） */
  cancel: () => void
  /** 确认删除（发请求，成功才关闭） */
  confirm: () => Promise<void>
}

export function useConfirmDelete<T>(options: ConfirmDeleteOptions<T>): ConfirmDelete<T> {
  const action = useAction()
  // 显式断言绕过 UnwrapRef<T>：T 只来自调用方的具体类型（如 ArticleSummary），
  // 不会出现嵌套 ref 需要解包的场景
  const pending = ref<T | null>(null) as Ref<T | null>

  function request(item: T): void {
    pending.value = item
  }

  function cancel(): void {
    pending.value = null
  }

  async function confirm(): Promise<void> {
    const target = pending.value
    if (target === null) return
    const successMessage =
      typeof options.success === 'function' ? options.success(target) : options.success
    const done = await action.run(() => options.remove(target), {
      success: successMessage,
      errorMessage: options.errorMessage ?? '删除失败',
      mapError: options.mapError,
    })
    if (done === undefined) return
    // 成功才关：失败时保留对话框，用户看到错误提示后可以重试或取消
    pending.value = null
    options.onDeleted?.(target)
  }

  return { pending, running: action.running, request, cancel, confirm }
}
