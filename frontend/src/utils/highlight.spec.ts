import { describe, expect, it } from 'vitest'

import { highlightSegments } from '@/utils/highlight'

/** 把分段拼回纯文本，方便断言"切分没有丢字"。 */
const join = (segments: { text: string }[]) => segments.map((s) => s.text).join('')

describe('highlightSegments', () => {
  it('把命中部分单独切出来', () => {
    const result = highlightSegments('数据层重写记录', '数据层')
    expect(result).toEqual([
      { text: '数据层', match: true },
      { text: '重写记录', match: false },
    ])
  })

  it('切分不丢字也不加字', () => {
    const source = '为什么我把博客的数据层重写了一遍'
    expect(join(highlightSegments(source, '数据层'))).toBe(source)
  })

  it('多处命中都标出来', () => {
    const result = highlightSegments('SQLite 与 sqlite 的区别', 'sqlite')
    expect(result.filter((s) => s.match)).toHaveLength(2)
    expect(join(result)).toBe('SQLite 与 sqlite 的区别')
  })

  it('大小写不敏感，但保留原文大小写', () => {
    const result = highlightSegments('PostgreSQL', 'postgresql')
    expect(result).toEqual([{ text: 'PostgreSQL', match: true }])
  })

  it('关键词为空时原样返回一段', () => {
    expect(highlightSegments('正文', '')).toEqual([{ text: '正文', match: false }])
  })

  it('空文本返回空数组（不产生空的 mark）', () => {
    expect(highlightSegments('', 'x')).toEqual([])
    expect(highlightSegments(null, 'x')).toEqual([])
    expect(highlightSegments(undefined, 'x')).toEqual([])
  })

  it('没有命中时整段返回，标为未命中', () => {
    expect(highlightSegments('完全无关的文本', '找不到')).toEqual([
      { text: '完全无关的文本', match: false },
    ])
  })

  it('正则元字符不会抛错，也不会被当成模式', () => {
    // 不转义的话 `(` 会让 RegExp 直接抛 SyntaxError，页面白屏
    expect(() => highlightSegments('a(b)*c 与 aXbbc', 'a(b)*c')).not.toThrow()
    const result = highlightSegments('a(b)*c', 'a(b)*c')
    expect(result).toEqual([{ text: 'a(b)*c', match: true }])
    // `.*` 在没有转义时会把整段都匹配掉——转义后它只是普通字符
    expect(highlightSegments('abc', '.*')).toEqual([{ text: 'abc', match: false }])
  })
})
