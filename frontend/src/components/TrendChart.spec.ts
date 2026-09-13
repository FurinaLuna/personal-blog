/**
 * TrendChart 测试。
 *
 * 锁住四条渲染契约：数据点数与输入一致、空数据出空态不出图、
 * 全零序列不产生 NaN（max=0 是除零陷阱）、峰值归一化正确。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TrendChart from '@/components/TrendChart.vue'
import type { DailyViewStats } from '@/types'

function makeRows(views: number[], uvs: number[] = views.map(() => 1)): DailyViewStats[] {
  return views.map((views, index) => ({
    // 从固定日期起排，避免用「今天」导致断言随时间漂移
    date: `2026-09-${String(index + 1).padStart(2, '0')}`,
    views,
    unique_visitors: uvs[index] ?? 0,
  }))
}

describe('TrendChart', () => {
  it('每个数据点渲染一个可见圆点（点数 = 输入条数）', () => {
    const wrapper = mount(TrendChart, {
      props: { data: makeRows([3, 7, 1, 9, 4]) },
    })
    // 可见点 r=3；命中区 r=10 与可见点成对，只按可见点数
    expect(wrapper.findAll('circle[r="3"]')).toHaveLength(5)
    // 悬停提示带原样日期与数值
    const titles = wrapper.findAll('title')
    expect(titles).toHaveLength(5)
    expect(titles.at(-1)?.text()).toContain('2026-09-05 · 阅读 4')
  })

  it('空数据渲染空态文案，不出 SVG', () => {
    const wrapper = mount(TrendChart, { props: { data: [] } })
    expect(wrapper.find('svg').exists()).toBe(false)
    expect(wrapper.text()).toContain('还没有访问数据')
  })

  it('全零序列不产生 NaN（max=0 除零陷阱），全部贴底线', () => {
    const wrapper = mount(TrendChart, {
      props: { data: makeRows([0, 0, 0, 0], [0, 0, 0, 0]) },
    })
    // 两条线（PV + UV）都不能带 NaN
    for (const path of wrapper.findAll('path')) {
      expect(path.attributes('d')).not.toContain('NaN')
      // BOTTOM=182：零值全部贴底线
      expect(path.attributes('d')).toContain('182.0')
    }
  })

  it('峰值归一化：最大值点在最高处（y 最小），单调数据 y 递减', () => {
    const wrapper = mount(TrendChart, {
      props: { data: makeRows([1, 5, 10]) },
    })
    // PV 线是品牌色那条（UV 虚线渲染在下层）
    const path = wrapper.find('path.stroke-brand-500').attributes('d')!
    // path 形如 "M0.0 y1 L360.0 y2 L720.0 y3" → [x1, y1, x2, y2, x3, y3]
    const [x1, y1, , y2, x3, y3] = path.match(/-?\d+(?:\.\d+)?/g)!.map(Number)
    expect(x1).toBe(0)
    expect(x3).toBe(720)
    expect(y3).toBeLessThan(y2)
    expect(y2).toBeLessThan(y1)
    expect(y3).toBeLessThan(14 + 5) // 峰值贴顶部（TOP=14 留少量容差）
  })
})
