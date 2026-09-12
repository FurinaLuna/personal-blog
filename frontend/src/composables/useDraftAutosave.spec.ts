/**
 * 草稿自动保存测试。
 *
 * 这个 composable 的价值全在「异常情况」上：隐私模式、配额用尽、JSON 损坏、
 * 草稿过期、切换文章。正常路径反而是最容易写对的那部分。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'

import { useDraftAutosave } from './useDraftAutosave'

interface Form {
  title: string
  content: string
}

const KEY = 'draft:test'

/** 把 composable 挂进一个真实组件，这样 watch / onBeforeUnmount 都会生效。 */
function mountAutosave(options: {
  key?: () => string
  form: { value: Form }
  enabled?: { value: boolean }
  debounceMs?: number
  maxAgeMs?: number
}) {
  const state: Record<string, unknown> = {}
  const Host = defineComponent({
    setup() {
      Object.assign(
        state,
        useDraftAutosave<Form>({
          key: options.key ?? (() => KEY),
          snapshot: () => ({ ...options.form.value }),
          enabled: () => options.enabled?.value ?? true,
          debounceMs: options.debounceMs ?? 10,
          maxAgeMs: options.maxAgeMs,
        }),
      )
      return () => h('div')
    },
  })
  const wrapper = mount(Host)
  return { wrapper, state }
}

beforeEach(() => {
  window.localStorage.clear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('落盘时机', () => {
  it('输入后延迟落盘，而不是每次按键都写', async () => {
    const form = ref<Form>({ title: '', content: '' })
    mountAutosave({ form })

    form.value = { title: '标', content: '' }
    await nextTick()
    form.value = { title: '标题', content: '' }
    await nextTick()

    // 防抖窗口内还没写
    expect(window.localStorage.getItem(KEY)).toBeNull()

    vi.advanceTimersByTime(20)
    const raw = window.localStorage.getItem(KEY)
    expect(raw).not.toBeNull()
    expect(JSON.parse(raw as string).payload.title).toBe('标题')
  })

  it('内容没变时不重复写盘', async () => {
    const form = ref<Form>({ title: '', content: '' })
    mountAutosave({ form })

    form.value = { title: 'A', content: '' }
    await nextTick()
    vi.advanceTimersByTime(20)
    const first = window.localStorage.getItem(KEY) as string
    expect(first).not.toBeNull()

    // 再触发一次「变化」但内容相同：savedAt 不应被刷新
    form.value = { title: 'A', content: '' }
    await nextTick()
    vi.advanceTimersByTime(20)

    expect(window.localStorage.getItem(KEY)).toBe(first)
  })

  it('表单未就绪（加载中）时不写：否则空表单会覆盖已有草稿', async () => {
    const form = ref<Form>({ title: '', content: '' })
    const enabled = ref(false)
    mountAutosave({ form, enabled })

    form.value = { title: '加载中', content: '' }
    await nextTick()
    vi.advanceTimersByTime(20)
    expect(window.localStorage.getItem(KEY)).toBeNull()

    enabled.value = true
    await nextTick()
    vi.advanceTimersByTime(20)
    expect(window.localStorage.getItem(KEY)).not.toBeNull()
  })

  it('flush() 立即落盘（关页面前补写）', async () => {
    const form = ref<Form>({ title: '未保存', content: '' })
    const { state } = mountAutosave({ form })

    form.value = { title: '未保存内容', content: '' }
    await nextTick()
    ;(state.flush as () => void)()

    expect(JSON.parse(window.localStorage.getItem(KEY) as string).payload.title).toBe('未保存内容')
  })
})

describe('读取与恢复', () => {
  it('无草稿时返回 null，不报错', async () => {
    const form = ref<Form>({ title: '', content: '' })
    const { state } = mountAutosave({ form })
    expect((state.checkExisting as () => unknown)()).toBeNull()
  })

  it('能读回草稿内容与时间', async () => {
    const savedAt = Date.now() - 60_000
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ savedAt, key: KEY, payload: { title: '旧标题', content: '旧正文' } }),
    )

    const form = ref<Form>({ title: '', content: '' })
    const { state } = mountAutosave({ form })
    const found = (state.checkExisting as () => { payload: Form; savedAt: number } | null)()

    expect(found?.payload.title).toBe('旧标题')
    expect(found?.savedAt).toBe(savedAt)
    expect(state.hasDraft).toBeDefined()
  })

  it('JSON 损坏时当作没有草稿，并顺手清掉坏数据', async () => {
    window.localStorage.setItem(KEY, '{ this is not json')
    const form = ref<Form>({ title: '', content: '' })
    const { state } = mountAutosave({ form })

    expect((state.checkExisting as () => unknown)()).toBeNull()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('结构不对（缺 payload）时当作没有草稿', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now() }))
    const form = ref<Form>({ title: '', content: '' })
    const { state } = mountAutosave({ form })

    expect((state.checkExisting as () => unknown)()).toBeNull()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('超过 30 天的草稿视为过期并清理', async () => {
    const stale = Date.now() - 31 * 24 * 60 * 60 * 1000
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ savedAt: stale, key: KEY, payload: { title: '很久以前', content: '' } }),
    )

    const form = ref<Form>({ title: '', content: '' })
    const { state } = mountAutosave({ form })

    expect((state.checkExisting as () => unknown)()).toBeNull()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })
})

describe('清除与分桶', () => {
  it('clear() 同时清掉存储与内存状态', async () => {
    const form = ref<Form>({ title: '', content: '' })
    const { state } = mountAutosave({ form })

    form.value = { title: 'x', content: '' }
    await nextTick()
    vi.advanceTimersByTime(20)
    expect(window.localStorage.getItem(KEY)).not.toBeNull()

    ;(state.clear as () => void)()
    expect(window.localStorage.getItem(KEY)).toBeNull()
    expect((state.savedAt as { value: number | null }).value).toBeNull()
    expect((state.hasDraft as { value: boolean }).value).toBe(false)
  })

  it('切换 key（换文章）时不把新内容写进旧 key', async () => {
    const form = ref<Form>({ title: '', content: '' })
    const key = ref('article:1')
    const { state } = mountAutosave({ form, key: () => key.value })

    form.value = { title: 'A 的内容', content: '' }
    await nextTick()
    vi.advanceTimersByTime(20)
    expect(JSON.parse(window.localStorage.getItem('article:1') as string).payload.title).toBe(
      'A 的内容',
    )

    // 切到另一篇并继续编辑
    key.value = 'article:2'
    await nextTick()
    form.value = { title: 'B 的内容', content: '' }
    await nextTick()
    vi.advanceTimersByTime(20)

    expect(JSON.parse(window.localStorage.getItem('article:2') as string).payload.title).toBe(
      'B 的内容',
    )
    // 旧 key 没被 B 的内容覆盖
    expect(JSON.parse(window.localStorage.getItem('article:1') as string).payload.title).toBe(
      'A 的内容',
    )
    expect(state.available).toBeDefined()
  })
})

describe('不可用环境', () => {
  it('localStorage 不可用（隐私模式）时降级为关闭自动保存，且不抛错', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })

    const form = ref<Form>({ title: '写点东西', content: '' })
    const { state } = mountAutosave({ form })
    ;(state.checkExisting as () => unknown)()
    vi.advanceTimersByTime(20)

    expect((state.available as { value: boolean }).value).toBe(false)
    setItem.mockRestore()
  })

  it('快照无法序列化时关闭自动保存（避免每 3 秒抛一次）', async () => {
    // BigInt 会让 JSON.stringify 抛错，用来模拟「快照不可序列化」
    const nonce = ref(0)
    const state: Record<string, unknown> = {}

    const Host = defineComponent({
      setup() {
        Object.assign(
          state,
          useDraftAutosave<Record<string, unknown>>({
            key: () => KEY,
            snapshot: () => ({ n: nonce.value, bad: 10n }),
            enabled: () => true,
            debounceMs: 10,
          }),
        )
        return () => h('div')
      },
    })
    mount(Host)

    nonce.value = 1
    await nextTick()
    vi.advanceTimersByTime(20)

    expect((state.available as { value: boolean }).value).toBe(false)
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })
})
