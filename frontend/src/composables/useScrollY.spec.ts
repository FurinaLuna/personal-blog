/**
 * useScrollY 测试。
 *
 * ## 这个文件为什么必须存在
 *
 * 它是**模块级单例**：`y` / `consumers` / `ticking` / `frame` 都在模块作用域里，
 * 所以这里要守住三类契约，每一条坏掉的方式都很安静：
 *
 * 1. **引用计数**：多个消费者共享一个 window 监听器；其中一个卸载**不能**把别人的
 *    监听器摘掉；最后一个卸载才真正摘。搞错的表现是「另一个组件的返回顶部按钮突然
 *    不动了」，而且只在两个组件同时存在时复现。
 * 2. **去重**：`y` 只在滚动位置**真的变化**时才写。`scroll` 事件会在同一像素上连续
 *    刷出好几帧（惯性滚动、回弹），每帧都唤醒消费者等于让每个公开页白跑一次次依赖
 *    检查——`computed` 能挡住重复渲染，挡不住每帧一次的触发开销。
 * 3. **卸载要取消在途的那一帧**：否则「最后一个消费者卸载」之后它还会跑一次，
 *    把已经不该更新的值写回去。
 *
 * ## 环境说明
 *
 * 与仓库其它 spec 一致：不 mock 业务模块，只替换浏览器出口（`scrollY` / rAF）。
 * **rAF 桩必须支持取消**（真实的 `requestAnimationFrame` + `cancelAnimationFrame`），
 * 用「同步执行 + 返回常量」的桩会把第 3 条契约整个掩盖掉 —— 实测过：那种桩下
 * 「卸载后仍留下一帧」是测不出来的。
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, h, nextTick, watch } from 'vue'

import { __resetScrollYForTest, useScrollY } from '@/composables/useScrollY'

type ScrollApi = ReturnType<typeof useScrollY>

/** 上一个挂载出来的 composable 句柄：用例通过它断言。 */
let api: ScrollApi | null = null

/** 在途的 rAF 回调：支持取消，见文件头注释。 */
let frames = new Map<number, FrameRequestCallback>()
let nextFrameId = 0

/** 执行所有在途的帧（模拟「浏览器跑到了下一帧」）。 */
function flushFrames(): void {
  const pending = [...frames.values()]
  frames.clear()
  for (const callback of pending) callback(0)
}

/**
 * 把 composable 挂进一个真实组件。
 *
 * `useScrollY()` 必须在 **setup 顶层同步调用**（真实消费者都是这么写的），
 * 这样 `onMounted` / `onBeforeUnmount` 才会注册到实例上，生命周期契约才可验证。
 */
function mountConsumer(): VueWrapper {
  return mount(
    defineComponent({
      setup() {
        api = useScrollY()
        return () => h('div')
      },
    }),
  )
}

/** 派发一次 scroll 并推进一帧（返回时 `y` 已更新）。 */
async function scrollTo(value: number): Promise<void> {
  vi.stubGlobal('scrollY', value)
  window.dispatchEvent(new Event('scroll'))
  flushFrames()
  await nextTick()
}

beforeEach(() => {
  __resetScrollYForTest()
  api = null
  frames = new Map()
  nextFrameId = 0
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback): number => {
    nextFrameId += 1
    frames.set(nextFrameId, callback)
    return nextFrameId
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number): void => {
    frames.delete(id)
  })
  vi.stubGlobal('scrollY', 0)
})

afterEach(() => {
  __resetScrollYForTest()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useScrollY · 共享的监听器', () => {
  it('挂载一个消费者：注册 scroll 与 resize 各一次，且 scroll 是 passive', () => {
    const add = vi.spyOn(window, 'addEventListener')

    mountConsumer()

    const scrolls = add.mock.calls.filter(([type]) => type === 'scroll')
    expect(scrolls).toHaveLength(1)
    expect(add.mock.calls.filter(([type]) => type === 'resize')).toHaveLength(1)
    // passive 是必须的：滚动监听不做 preventDefault，声明它能让浏览器不必等 JS
    expect(scrolls[0]?.[2]).toMatchObject({ passive: true })
  })

  it('两个消费者共享同一个监听器，其中一个卸载不能摘掉它', () => {
    const remove = vi.spyOn(window, 'removeEventListener')
    const add = vi.spyOn(window, 'addEventListener')

    const first = mountConsumer()
    const second = mountConsumer()

    expect(add.mock.calls.filter(([type]) => type === 'scroll')).toHaveLength(1)

    // 第二个卸载：监听器必须还在（第一个还要用）
    second.unmount()
    expect(remove.mock.calls.filter(([type]) => type === 'scroll')).toHaveLength(0)

    // 最后一个卸载才真正摘掉
    first.unmount()
    expect(remove.mock.calls.filter(([type]) => type === 'scroll')).toHaveLength(1)
    expect(remove.mock.calls.filter(([type]) => type === 'resize')).toHaveLength(1)
  })

  it('挂载在「已经滚动过一段」的页面上时，y 立刻是当下真实值', () => {
    window.scrollY = 640

    mountConsumer()

    // attach()（第一个消费者创建时）就已经同步过，不能等用户再动一下才纠正
    expect(api?.y.value).toBe(640)
  })

  it('对外只暴露一个只读的 y（没有第二套订阅 API）', () => {
    mountConsumer()

    // 一次减法：onScroll 曾经存在但零消费者，删掉了。
    // 这条用例防止它被顺手加回来 —— 加回来就要重新维护订阅集合与逐订阅者记账。
    expect(Object.keys(api ?? {})).toEqual(['y'])
    expect(typeof api?.y).toBe('object')
  })
})

describe('useScrollY · 去重（只在位置变化时更新）', () => {
  it('滚动到新位置：y 跟着更新', async () => {
    mountConsumer()

    await scrollTo(120)

    expect(api?.y.value).toBe(120)
  })

  it('同一位置连续派发多次 scroll：消费者只被唤醒一次', async () => {
    mountConsumer()
    const seen: number[] = []
    // 用真实消费方式（computed/watch）验证「值没变就不唤醒」
    const spy = vi.fn()
    watch(api?.y ?? (() => 0), spy)

    await scrollTo(200)
    expect(spy).toHaveBeenCalledTimes(1)
    seen.push(api?.y.value ?? -1)

    // 惯性滚动 / 回弹会在同一像素上连续刷帧，y 取整后完全一样
    await scrollTo(200)
    await scrollTo(200)

    expect(spy).toHaveBeenCalledTimes(1)
    expect(seen).toEqual([200])
  })

  it('resize 但没有滚动位移时不唤醒消费者（同一条管道，常见噪声）', async () => {
    mountConsumer()
    const spy = vi.fn()
    watch(api?.y ?? (() => 0), spy)

    await scrollTo(80)
    expect(spy).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new Event('resize'))
    flushFrames()
    await nextTick()

    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('位置来回变化时每次都通知（去重只针对「没变」，不是节流到只报一次）', async () => {
    mountConsumer()
    const spy = vi.fn()
    watch(api?.y ?? (() => 0), spy)

    await scrollTo(10)
    await scrollTo(20)
    await scrollTo(20)
    await scrollTo(10)

    expect(spy).toHaveBeenCalledTimes(3)
    expect(spy.mock.calls.map(([value]) => value)).toEqual([10, 20, 10])
  })

  it('computed 消费方式：只在跨过阈值时重算（真实消费者的写法）', async () => {
    mountConsumer()
    const showTop = computed(() => (api?.y.value ?? 0) > 300)

    await scrollTo(100)
    expect(showTop.value).toBe(false)

    await scrollTo(301)
    expect(showTop.value).toBe(true)

    await scrollTo(400)
    expect(showTop.value).toBe(true)

    await scrollTo(0)
    expect(showTop.value).toBe(false)
  })
})

describe('useScrollY · 挂载与卸载的边界', () => {
  it('后挂载的消费者拿到当下真实值（不需要任何订阅记账）', async () => {
    const first = mountConsumer()
    await scrollTo(777)
    first.unmount()

    // 第二个消费者此时才挂上来。值就在共享的 ref 上，读即是当下真值——
    // 早期版本这里要用「逐订阅者记上次收到多少」来补初值，删掉订阅 API 后自然成立。
    const second = mountConsumer()

    expect(api?.y.value).toBe(777)

    second.unmount()
  })

  it('最后一个消费者卸载时，取消在途的那一帧（不留「已卸载还在跑」的回调）', () => {
    const wrapper = mountConsumer()

    // 派发 scroll 但**不**推进帧：此刻有一帧在途
    vi.stubGlobal('scrollY', 500)
    window.dispatchEvent(new Event('scroll'))
    expect(frames.size).toBe(1)

    wrapper.unmount()

    expect(frames.size).toBe(0)
    // 组件已经卸载，那个值不该再被写进去
    expect(api?.y.value).toBe(0)
  })

  it('卸载后 y 不再被滚动事件改写', async () => {
    const wrapper = mountConsumer()
    await scrollTo(150)
    expect(api?.y.value).toBe(150)

    wrapper.unmount()

    vi.stubGlobal('scrollY', 900)
    window.dispatchEvent(new Event('scroll'))
    flushFrames()
    await nextTick()

    expect(api?.y.value).toBe(150)
  })
})
