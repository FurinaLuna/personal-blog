/**
 * 写操作（提交表单 / 删除 / 审核…）的通用封装。
 *
 * 后台 9 个视图里散着 20 多处长得一样的 try / catch / toast / finally。
 * 抽出来的收益不只是少写几行，而是**错误口径统一**：
 * 哪些错误该贴到字段下面、哪些该弹 toast、哪些该静默，全站只有一处判断。
 *
 * 刻意不做的事：**不**给 `run` 加「执行中直接 return」的单飞锁。
 * 行级操作（快速连续审核两条评论）是合法的并发，加了锁会静默丢请求——
 * 「防重复提交」属于表单语义，由视图看 `running` 自行决定（例如禁用按钮）。
 */
import { computed, ref, type ComputedRef } from 'vue'

import { ApiError } from '@/api'
import { toErrorMessage } from '@/composables/useAsyncData'
import { useToast } from '@/composables/useToast'

export interface ActionOptions {
  /** 成功提示文案；不传则不提示 */
  success?: string
  /** 失败时的兜底文案（后端没给可读消息时用） */
  errorMessage?: string
  /** 字段级错误的接收方：表单场景把它接到自己的 fieldErrors 上 */
  onFieldErrors?: (fields: Record<string, string>) => void
  /**
   * 自定义错误文案映射。
   *
   * 用于「同一接口的 403 要给出专门解释」这类场景：返回字符串则用它，
   * 返回 undefined 表示「不特殊处理」，继续走默认的 toErrorMessage。
   */
  mapError?: (error: unknown) => string | undefined
  /**
   * 失败后的额外处理（例如把错误写进页面顶部的提示条、回填字段错误）。
   * 它先于 toast 执行，所以可以和默认提示共存。
   */
  onError?: (error: unknown) => void
  /** 成功后执行（例如 void list.run() 刷新列表） */
  onSuccess?: () => void
  /** 失败时完全不提示，交给调用方自己处理 */
  silent?: boolean
}

export interface Action {
  /** 是否有动作正在执行（并发计数，>0 即为真） */
  running: ComputedRef<boolean>
  /**
   * 执行一个写操作。
   *
   * @returns 成功时返回任务结果；失败时返回 `undefined` 并已提示用户
   */
  run: <T>(task: () => Promise<T>, options?: ActionOptions) => Promise<T | undefined>
}

export function useAction(): Action {
  const toast = useToast()
  // 用计数而不是布尔：同一视图里可能有多个独立动作同时在跑，
  // 布尔会在第一个动作结束时就把「加载中」置回 false，导致按钮提前解禁
  const pending = ref(0)
  const running = computed(() => pending.value > 0)

  async function run<T>(task: () => Promise<T>, options: ActionOptions = {}): Promise<T | undefined> {
    pending.value += 1
    try {
      const result = await task()
      if (options.success) toast.success(options.success)
      options.onSuccess?.()
      return result
    } catch (error) {
      options.onError?.(error)
      if (!options.silent) {
        const fieldErrors = error instanceof ApiError ? error.fields : {}
        const hasFieldErrors = Object.keys(fieldErrors).length > 0

        if (hasFieldErrors && options.onFieldErrors) {
          // 字段级错误贴到输入框下面，比弹一个笼统提示有用得多
          options.onFieldErrors(fieldErrors)
        } else {
          const mapped = options.mapError?.(error)
          toast.error(mapped ?? toErrorMessage(error, options.errorMessage ?? '操作失败'))
        }
      }
      return undefined
    } finally {
      pending.value -= 1
    }
  }

  return { running, run }
}
