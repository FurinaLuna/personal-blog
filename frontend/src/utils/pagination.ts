/**
 * 分页工具。
 *
 * `useAsyncData` 需要一个初始值，早先四个列表页各自手写了一遍
 * `{ items: [], total: 0, page: 1, page_size: PAGE_SIZE, pages: 0 }`。
 * 收敛到工厂函数后，将来改分页结构（比如加游标）只需要改这里。
 */
import type { Page } from '@/types'

/**
 * 造一个空分页对象。
 *
 * @param pageSize 列表页的每页条数，必须与请求时用的值一致——
 *                 不一致会让首帧骨架屏算出错误的分页器页数。
 */
export function emptyPage<T>(pageSize: number): Page<T> {
  return { items: [], total: 0, page: 1, page_size: pageSize, pages: 0 }
}
