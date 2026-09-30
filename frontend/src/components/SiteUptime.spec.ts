/**
 * SiteUptime 测试。
 *
 * 这个组件只有两件可能出错的事，两条都要测：
 *
 * 1. **定时器必须被清理**。页脚出现在每一页，路由来回切就是稳定的定时器泄漏 ——
 *    而泄漏在单页里看不出来，只有卸载后断言 `clearInterval` 才看得见。
 * 2. **文案格式**：起点与格式的换算在 `@/utils/uptime`（那边有更细的边界用例），
 *    这里只确认组件把结果渲染出来了、并且是从固定起点算的。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SiteUptime from './SiteUptime.vue'

/** 造一个可控的"现在"：距建站起点 offset 毫秒。 */
function nowAfter(milliseconds: number): number {
  return Date.parse('2025-11-14T13:14:52+08:00') + milliseconds
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SiteUptime', () => {
  it('按固定起点渲染「N 天 HH 小时 MM 分 SS 秒」', () => {
    const SECOND = 1000
    const MINUTE = 60 * SECOND
    const HOUR = 60 * MINUTE
    const DAY = 24 * HOUR
    vi.setSystemTime(nowAfter(3 * DAY + 4 * HOUR + 5 * MINUTE + 6 * SECOND))

    const wrapper = mount(SiteUptime)

    expect(wrapper.text()).toContain('本站自从搭建已经历 3 天 04 小时 05 分 06 秒')
  })

  it('每秒推进（而不是停在挂载那一刻）', async () => {
    vi.setSystemTime(nowAfter(0))
    const wrapper = mount(SiteUptime)
    expect(wrapper.text()).toContain('0 天 00 小时 00 分 00 秒')

    // 假定时器下推进时钟只推一次：`advanceTimersByTime` 会既走系统时间又跑回调，
    // 所以这里**不要**先 `setSystemTime` —— 那样会累计成 6 秒（本用例第一版就是这么错的）。
    vi.advanceTimersByTime(5000)
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('0 天 00 小时 00 分 05 秒')
  })

  it('卸载后清掉定时器（页脚每页都渲染，泄漏影响面最大）', () => {
    const clearSpy = vi.spyOn(globalThis, 'clearInterval')
    vi.setSystemTime(nowAfter(0))

    const wrapper = mount(SiteUptime)
    wrapper.unmount()

    expect(clearSpy).toHaveBeenCalled()
    // 卸载后再推进时间不应抛错（定时器已在推进中被清掉，回调不再执行）
    expect(() => vi.advanceTimersByTime(5000)).not.toThrow()
  })
})
