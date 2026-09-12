/**
 * useAction 测试。
 *
 * 重点在「错误分流」：字段级错误该贴输入框、其余该弹 toast、silent 该闭嘴。
 * 这三条是上一轮重构的核心契约，也是重构最容易改坏的地方。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api'
import { useAction } from '@/composables/useAction'
import { useToast } from '@/composables/useToast'

const toast = useToast()

beforeEach(() => {
  toast.items.value = []
})

describe('useAction.run 成功路径', () => {
  it('返回任务结果并弹成功提示', async () => {
    const { run } = useAction()
    const result = await run(async () => 42, { success: '保存成功' })
    expect(result).toBe(42)
    expect(toast.items.value.map((item) => item.message)).toContain('保存成功')
  })

  it('未传 success 时不弹提示', async () => {
    const { run } = useAction()
    await run(async () => undefined)
    expect(toast.items.value).toHaveLength(0)
  })

  it('onSuccess 执行，且能读到返回值之外的副作用', async () => {
    const { run } = useAction()
    const onSuccess = vi.fn()
    await run(async () => 'ok', { onSuccess })
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })
})

describe('useAction.run 失败路径', () => {
  it('失败返回 undefined 并弹错误提示', async () => {
    const { run } = useAction()
    const result = await run(async () => {
      throw new ApiError('标题不能为空', 422, 'validation_error')
    })
    expect(result).toBeUndefined()
    expect(toast.items.value.at(-1)?.message).toBe('标题不能为空')
    expect(toast.items.value.at(-1)?.kind).toBe('error')
  })

  it('后端没给可读消息时用 errorMessage 兜底', async () => {
    const { run } = useAction()
    await run(
      async () => {
        throw new Error('')
      },
      { errorMessage: '删除失败' },
    )
    expect(toast.items.value.at(-1)?.message).toBe('删除失败')
  })

  it('字段级错误交给 onFieldErrors，且不再弹 toast', async () => {
    const { run } = useAction()
    const onFieldErrors = vi.fn()
    await run(
      async () => {
        throw new ApiError('校验失败', 422, 'validation_error', { title: '标题重复' })
      },
      { onFieldErrors },
    )
    expect(onFieldErrors).toHaveBeenCalledWith({ title: '标题重复' })
    expect(toast.items.value).toHaveLength(0)
  })

  it('silent 时既不弹 toast 也仍触发 onError', async () => {
    const { run } = useAction()
    const onError = vi.fn()
    await run(
      async () => {
        throw new ApiError('用户名或密码错误', 401, 'unauthorized')
      },
      { silent: true, onError },
    )
    expect(onError).toHaveBeenCalledTimes(1)
    expect(toast.items.value).toHaveLength(0)
  })

  it('mapError 能覆盖默认文案（403 场景）', async () => {
    const { run } = useAction()
    await run(
      async () => {
        throw new ApiError('Forbidden', 403, 'forbidden')
      },
      {
        mapError: (error) =>
          error instanceof ApiError && error.isForbidden ? '只有站长可以删除分类' : undefined,
      },
    )
    expect(toast.items.value.at(-1)?.message).toBe('只有站长可以删除分类')
  })

  it('mapError 返回 undefined 时回落到默认文案', async () => {
    const { run } = useAction()
    await run(
      async () => {
        throw new ApiError('服务异常', 500, 'internal_error')
      },
      { mapError: () => undefined, errorMessage: '操作失败' },
    )
    expect(toast.items.value.at(-1)?.message).toBe('服务异常')
  })
})

describe('useAction.running 并发语义', () => {
  it('并发执行期间保持 true，全部结束后才回 false', async () => {
    const { run, running } = useAction()
    const gate: Array<() => void> = []
    const task = () =>
      new Promise<void>((resolve) => {
        gate.push(resolve)
      })

    const first = run(task)
    const second = run(task)
    expect(running.value).toBe(true)

    gate[0]()
    await first
    // 还有一个在跑，不能被误判成「已空闲」——这正是用计数而非布尔的原因
    expect(running.value).toBe(true)

    gate[1]()
    await second
    expect(running.value).toBe(false)
  })

  it('单飞锁不存在：并发的两个请求都会真正执行', async () => {
    const { run } = useAction()
    const executed: number[] = []
    const make = (id: number) => async () => {
      executed.push(id)
      return id
    }
    const [a, b] = await Promise.all([run(make(1)), run(make(2))])
    expect(executed).toEqual([1, 2])
    expect([a, b]).toEqual([1, 2])
  })
})
