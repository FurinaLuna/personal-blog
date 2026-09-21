/**
 * Pagination 测试。
 *
 * 这个组件被前台列表和后台表格共用，而它的价值全在「页码窗口怎么算」上——
 * 那是一段纯计算，边界极多（首页 / 末页 / 页数刚好等于窗口 / 一侧需要省略号 /
 * 数据为空），手点很难覆盖全。这里把契约钉死：
 *
 * 1. 空数据整体不渲染（而不是渲染一个孤零零的「第 1 页」）；
 * 2. 页数不多时全部展开，不出现省略号；
 * 3. 页数很多时首尾页永远在，省略号只出现在真正被跳过的一侧；
 * 4. 点击当前页与越界目标都不派发事件（否则会出现重复请求 / 页码溢出）；
 * 5. 首尾页的上/下一页按钮禁用；
 * 6. 区间文案的范围端点正确。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Pagination from '@/components/Pagination.vue'

/**
 * 取渲染出来的页码序列，省略号记成 '…'。
 *
 * 注意省略号是 `<span>` 而不是按钮，只查 button 会把它们漏掉；
 * 上一页/下一页按钮里只有 svg、没有文字，靠 aria-label 把它们排掉。
 */
function pageItems(wrapper: ReturnType<typeof mount>): string[] {
  const container = wrapper.get('nav > div')
  return [...container.element.children]
    .filter((el) => !el.hasAttribute('aria-label'))
    .map((el) => (el.textContent ?? '').trim())
    .filter((t) => t !== '')
}

function mountAt(props: { page: number; pageSize: number; total: number; siblings?: number }) {
  return mount(Pagination, { props })
}

describe('Pagination', () => {
  it('没有数据时整体不渲染', () => {
    const wrapper = mountAt({ page: 1, pageSize: 10, total: 0 })
    expect(wrapper.find('nav').exists()).toBe(false)
  })

  it('页数不多时全部展开，不出现省略号', () => {
    // siblings=1 → 窗口 3 页，<= 5 页时全展开
    const wrapper = mountAt({ page: 1, pageSize: 10, total: 50 })
    expect(pageItems(wrapper)).toEqual(['1', '2', '3', '4', '5'])
    expect(wrapper.text()).not.toContain('…')
  })

  it('当前页在中间时，两侧都需要省略号且首尾页保留', () => {
    const wrapper = mountAt({ page: 10, pageSize: 10, total: 200 }) // 共 20 页
    expect(pageItems(wrapper)).toEqual(['1', '…', '9', '10', '11', '…', '20'])
  })

  it('当前页靠近开头时，只有右侧省略号（左侧不该补一个空的省略号）', () => {
    const wrapper = mountAt({ page: 2, pageSize: 10, total: 200 })
    expect(pageItems(wrapper)).toEqual(['1', '2', '3', '…', '20'])
  })

  it('当前页靠近末尾时，只有左侧省略号', () => {
    const wrapper = mountAt({ page: 19, pageSize: 10, total: 200 })
    expect(pageItems(wrapper)).toEqual(['1', '…', '18', '19', '20'])
  })

  it('点击页码派发 change，点当前页不派发（避免重复请求）', async () => {
    const wrapper = mountAt({ page: 5, pageSize: 10, total: 200 })
    const buttons = wrapper.findAll('button').filter((b) => b.text().trim() !== '')

    await buttons.find((b) => b.text().trim() === '6')?.trigger('click')
    await buttons.find((b) => b.text().trim() === '5')?.trigger('click') // 当前页
    expect(wrapper.emitted('change')).toEqual([[6]])
  })

  it('首页禁用上一页、末页禁用下一页', () => {
    const first = mountAt({ page: 1, pageSize: 10, total: 100 })
    expect(first.get('button[aria-label="上一页"]').attributes('disabled')).toBeDefined()
    expect(first.get('button[aria-label="下一页"]').attributes('disabled')).toBeUndefined()

    const last = mountAt({ page: 10, pageSize: 10, total: 100 })
    expect(last.get('button[aria-label="下一页"]').attributes('disabled')).toBeDefined()
    expect(last.get('button[aria-label="上一页"]').attributes('disabled')).toBeUndefined()
  })

  it('当前页带 aria-current，供屏幕阅读器定位', () => {
    const wrapper = mountAt({ page: 3, pageSize: 10, total: 100 })
    const current = wrapper.findAll('button[aria-current="page"]')
    expect(current).toHaveLength(1)
    expect(current[0]?.text().trim()).toBe('3')
  })

  it('区间文案的端点正确（末页不越界到 total 之外）', () => {
    // 第 2 页、每页 10 条、共 25 条 → 11–20 / 共 25 条
    expect(mountAt({ page: 2, pageSize: 10, total: 25 }).text()).toContain('11–20 / 共 25 条')
    // 末页只有 5 条 → to 必须被 total 截断
    expect(mountAt({ page: 3, pageSize: 10, total: 25 }).text()).toContain('21–25 / 共 25 条')
  })

  it('只有一页时也只渲染一页，不出现「第 0 页」或负数', () => {
    const wrapper = mountAt({ page: 1, pageSize: 10, total: 3 })
    expect(pageItems(wrapper)).toEqual(['1'])
  })
})
