import { describe, expect, it } from 'vitest'

import { extractUnsubscribeToken } from '@/utils/unsubscribe'

describe('extractUnsubscribeToken', () => {
  it('从 fragment 里取（当前格式）', () => {
    expect(extractUnsubscribeToken('#token=abc.def.ghi', undefined)).toBe('abc.def.ghi')
  })

  it('从 query 里取（历史邮件里的旧格式，10 年内仍可能被点到）', () => {
    expect(extractUnsubscribeToken('', 'abc.def.ghi')).toBe('abc.def.ghi')
  })

  it('query 是数组时只取第一个', () => {
    expect(extractUnsubscribeToken('', ['first', 'second'])).toBe('first')
    expect(extractUnsubscribeToken('', [])).toBe('')
  })

  it('两者都有时以 fragment 为准', () => {
    expect(extractUnsubscribeToken('#token=new', 'old')).toBe('new')
  })

  it('百分号转义会被解码', () => {
    expect(extractUnsubscribeToken('#token=a%2Bb', undefined)).toBe('a+b')
  })

  it('非法转义不抛异常，按取不到处理', () => {
    expect(() => extractUnsubscribeToken('#token=%E0%A4%A', undefined)).not.toThrow()
    expect(extractUnsubscribeToken('#token=%E0%A4%A', undefined)).toBe('')
  })

  it('hash 里 token 不在首位时也能取到', () => {
    expect(extractUnsubscribeToken('#foo=1&token=xyz', undefined)).toBe('xyz')
  })

  it('什么都没有时返回空串（页面据此显示「链接不完整」）', () => {
    expect(extractUnsubscribeToken('', undefined)).toBe('')
    expect(extractUnsubscribeToken(undefined, null)).toBe('')
    expect(extractUnsubscribeToken('#other=1', undefined)).toBe('')
  })

  it('不会把别的 fragment 参数误当成 token', () => {
    // `mytoken=` 不算命中，正则要求 token 出现在 # 之后或 & 之后
    expect(extractUnsubscribeToken('#mytoken=bad', undefined)).toBe('')
  })
})
