/**
 * 分页工具测试。
 *
 * 契约：emptyPage 产出的对象必须能安全地作为列表页首帧初始值——
 * 骨架屏阶段渲染它不会抛错，分页器算出的页数是 0（不可点），
 * 且 page_size 与请求参数一致（否则首帧分页器页数是错的）。
 */
import { describe, expect, it } from 'vitest'

import { emptyPage } from '@/utils/pagination'

describe('emptyPage', () => {
  it('返回零值分页信封，首页从 1 开始', () => {
    const page = emptyPage(10)
    expect(page.items).toEqual([])
    expect(page.total).toBe(0)
    expect(page.page).toBe(1)
    expect(page.pages).toBe(0)
  })

  it('page_size 原样回传入参，与请求参数保持一致', () => {
    expect(emptyPage(8).page_size).toBe(8)
    expect(emptyPage(20).page_size).toBe(20)
  })

  it('对元素类型透明（泛型不约束内容）', () => {
    const typed: { id: number }[] = emptyPage<{ id: number }>(5).items
    expect(typed).toEqual([])
  })

  it('每次调用返回新对象，调用方改写不会污染其他页面', () => {
    const first = emptyPage(10)
    const second = emptyPage(10)
    first.items.push({ id: 1 } as never)
    expect(second.items).toEqual([])
  })
})
