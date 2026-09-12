/**
 * useConfirmDelete 测试。
 *
 * 核心契约：request 只开门不发请求；confirm 失败保留对话框、成功才关门并回调。
 * 这几条是 5 个后台视图共用的语义，改坏一处等于全站删除交互退化。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useToast } from '@/composables/useToast'

const toast = useToast()

beforeEach(() => {
  toast.items.value = []
})

describe('useConfirmDelete 对话框开关语义', () => {
  it('request 只记录目标，不触发删除请求', () => {
    const remove = vi.fn()
    const { pending, request } = useConfirmDelete<number>({ remove })
    request(7)
    expect(pending.value).toBe(7)
    expect(remove).not.toHaveBeenCalled()
  })

  it('cancel 关闭对话框且不发请求', () => {
    const remove = vi.fn()
    const { pending, request, cancel } = useConfirmDelete<number>({ remove })
    request(7)
    cancel()
    expect(pending.value).toBeNull()
    expect(remove).not.toHaveBeenCalled()
  })

  it('未选择目标时 confirm 是空操作', async () => {
    const remove = vi.fn()
    const { confirm } = useConfirmDelete<number>({ remove })
    await confirm()
    expect(remove).not.toHaveBeenCalled()
  })
})

describe('useConfirmDelete confirm 语义', () => {
  it('成功：关闭对话框、弹成功提示、执行 onDeleted', async () => {
    const onDeleted = vi.fn()
    const { pending, request, confirm } = useConfirmDelete<{ id: number; title: string }>({
      remove: (item) => Promise.resolve(item.id),
      success: (item) => `《${item.title}》已删除`,
      onDeleted,
    })
    request({ id: 1, title: '测试文章' })
    await confirm()
    expect(pending.value).toBeNull()
    expect(toast.items.value.at(-1)?.message).toBe('《测试文章》已删除')
    expect(onDeleted).toHaveBeenCalledWith({ id: 1, title: '测试文章' })
  })

  it('失败：对话框保留（可重试），错误由 useAction 统一提示', async () => {
    const onDeleted = vi.fn()
    const { pending, request, confirm } = useConfirmDelete<number>({
      remove: () => {
        throw new ApiError('服务异常', 500, 'internal_error')
      },
      onDeleted,
    })
    request(3)
    await confirm()
    expect(pending.value).toBe(3)
    expect(toast.items.value.at(-1)?.kind).toBe('error')
    expect(onDeleted).not.toHaveBeenCalled()
  })

  it('mapError 透传给 useAction（403 场景）', async () => {
    const { request, confirm } = useConfirmDelete<number>({
      remove: () => {
        throw new ApiError('Forbidden', 403, 'forbidden')
      },
      mapError: (error) =>
        error instanceof ApiError && error.isForbidden ? '只有站长可以删除分类' : undefined,
    })
    request(1)
    await confirm()
    expect(toast.items.value.at(-1)?.message).toBe('只有站长可以删除分类')
  })

  it('confirm 执行期间 running 为真（喂给对话框 loading）', async () => {
    let release: (() => void) | undefined
    const { running, request, confirm } = useConfirmDelete<number>({
      remove: () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    })
    request(1)
    const inflight = confirm()
    expect(running.value).toBe(true)
    release?.()
    await inflight
    expect(running.value).toBe(false)
  })
})
