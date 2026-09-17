/**
 * 状态元数据与分页工具测试。
 *
 * 这两个模块的价值全在「边界」上：状态元数据要能扛住后端加了新状态、
 * 分页工厂要保证与请求用的 pageSize 一致（否则骨架屏会算出错误页数）。
 */
import { describe, expect, it } from 'vitest'

import { emptyPage } from '@/utils/pagination'
import { statusBadgeClass, statusLabel } from '@/utils/status'

describe('statusLabel', () => {
  it('三种已知状态各有文案', () => {
    expect(statusLabel('draft')).toBe('草稿')
    expect(statusLabel('published')).toBe('已发布')
    expect(statusLabel('archived')).toBe('已归档')
  })

  it('作者预览草稿时文案不同（仅你可见）', () => {
    expect(statusLabel('draft', { preview: true })).toBe('草稿（仅你可见）')
    // 其余状态在两种语境下一致
    expect(statusLabel('archived', { preview: true })).toBe('已归档')
    expect(statusLabel('published', { preview: true })).toBe('已发布')
  })

  it('未知状态原样返回，不抛错也不留空', () => {
    // 后端加了新状态而前端还没跟上时，界面降级成显示原始值
    expect(statusLabel('scheduled')).toBe('scheduled')
    expect(statusLabel('')).toBe('')
  })
})

describe('statusBadgeClass', () => {
  it('每种状态有独立的配色', () => {
    const classes = new Set([
      statusBadgeClass('draft'),
      statusBadgeClass('published'),
      statusBadgeClass('archived'),
    ])
    expect(classes.size).toBe(3)
  })

  it('未知状态回落到中性色而不是 undefined', () => {
    const unknown = statusBadgeClass('whatever')
    expect(unknown).toBeTruthy()
    expect(unknown).not.toContain('undefined')
    expect(unknown).toBe(statusBadgeClass('另一个不存在的状态'))
  })
})

describe('emptyPage', () => {
  it('page_size 与传入值一致（骨架屏分页数依赖它）', () => {
    expect(emptyPage(24).page_size).toBe(24)
  })

  it('初始为「无数据且不在加载中」的合理状态', () => {
    const page = emptyPage<{ id: number }>(10)
    expect(page.items).toEqual([])
    expect(page.total).toBe(0)
    expect(page.page).toBe(1)
    expect(page.pages).toBe(0)
  })

  it('每次调用返回新对象（避免多个列表共享同一份初始值）', () => {
    const first = emptyPage(10)
    const second = emptyPage(10)
    expect(first).not.toBe(second)
    first.items.push({} as never)
    expect(second.items).toEqual([])
  })
})

describe('定时发布的展示', () => {
  it('已发布但未到点：文案是「定时发布」而不是「已发布」', () => {
    // 界面上两者都是 status=published，不区分的话前台搜不到会让人以为是自己搞错了
    expect(statusLabel('published', { scheduled: true })).toBe('定时发布')
  })

  it('作者预览视角下附带「仅你可见」', () => {
    expect(statusLabel('published', { scheduled: true, preview: true })).toBe('定时发布（仅你可见）')
  })

  it('定时发布用独立配色，与普通「已发布」区分开', () => {
    expect(statusBadgeClass('published', true)).not.toBe(statusBadgeClass('published', false))
  })

  it('scheduled 只对 published 生效，草稿不会被误标', () => {
    expect(statusLabel('draft', { scheduled: true })).toBe('草稿')
    expect(statusBadgeClass('draft', true)).toBe(statusBadgeClass('draft', false))
  })

  it('不传 scheduled 时行为完全不变', () => {
    expect(statusLabel('published')).toBe('已发布')
    expect(statusBadgeClass('published')).toBe(statusBadgeClass('published', false))
  })
})
