/** 通用类型：分页信封、消息体、校验问题（跨领域的通用结构）。 */

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface Message {
  detail: string
}

export interface ValidationIssue {
  field: string
  message: string
  type: string
}
