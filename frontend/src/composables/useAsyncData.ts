/**
 * 异步数据加载的通用封装。
 *
 * 解决的是每个列表页都要重复写一遍的三件套：loading / error / data。
 * 尤其重要的是**并发竞态**：用户快速切页时，先发的请求可能后返回，
 * 把新页面的数据覆盖成旧内容。这里用一个自增的 requestId 丢弃过期响应。
 */
import { ref, type Ref } from 'vue'

import { ApiError } from '@/api'

export interface AsyncDataState<T> {
  data: Ref<T>
  loading: Ref<boolean>
  error: Ref<ApiError | null>
  /** 是否已经成功加载过至少一次（用于区分「首次加载」与「刷新中」） */
  ready: Ref<boolean>
  run: () => Promise<void>
}

export function useAsyncData<T>(loader: () => Promise<T>, initial: T): AsyncDataState<T> {
  const data = ref(initial) as Ref<T>
  const loading = ref(false)
  const error = ref<ApiError | null>(null)
  const ready = ref(false)
  let latestRequest = 0

  async function run(): Promise<void> {
    const requestId = ++latestRequest
    loading.value = true
    error.value = null
    try {
      const result = await loader()
      // 只有最后一次发出的请求才允许写入结果
      if (requestId !== latestRequest) return
      data.value = result
      ready.value = true
    } catch (caught) {
      if (requestId !== latestRequest) return
      error.value =
        caught instanceof ApiError ? caught : new ApiError('加载失败，请稍后重试', 0, 'unknown')
    } finally {
      if (requestId === latestRequest) loading.value = false
    }
  }

  return { data, loading, error, ready, run }
}

/** 把任意异常转成可展示的中文提示。 */
export function toErrorMessage(error: unknown, fallback = '操作失败，请稍后重试'): string {
  if (error instanceof ApiError) return error.message || fallback
  if (error instanceof Error && error.message) return error.message
  return fallback
}
