/**
 * Markdown 渲染测试。
 *
 * 这里是全站最该有测试的地方：正文渲染同时承担「安全消毒」与「阅读增强」
 * 两个职责，一旦回退（比如有人图省事把 DOMPurify 去掉），后果是存储型 XSS。
 */
import { describe, expect, it } from 'vitest'

import { slugifyHeading } from './format'
import { renderMarkdown } from './markdown'

describe('renderMarkdown 消毒', () => {
  it('剥掉 <script> 标签', () => {
    const { html } = renderMarkdown('正常文字\n\n<script>alert(1)</script>\n\n后面')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('alert(1)')
  })

  it('剥掉内联事件属性（onerror / onclick）', () => {
    const { html } = renderMarkdown('<img src=x onerror="alert(1)">\n\n<a href="#" onclick="evil()">x</a>')
    expect(html).not.toMatch(/onerror/i)
    expect(html).not.toMatch(/onclick/i)
  })

  it('剥掉 javascript: 协议链接', () => {
    const { html } = renderMarkdown('[点我](javascript:alert(1))')
    expect(html).not.toMatch(/javascript:/i)
  })

  it('保留正常的 Markdown 结构', () => {
    const { html } = renderMarkdown('# 标题\n\n- 一\n- 二\n\n**粗体**')
    expect(html).toContain('<h1')
    expect(html).toContain('<ul>')
    expect(html).toContain('<strong>粗体</strong>')
  })
})

describe('renderMarkdown 目录抽取', () => {
  it('按层级抽取标题并生成 id', () => {
    const { toc } = renderMarkdown('## 第一节\n\n正文\n\n### 小节\n\n## 第二节')
    expect(toc.map((item) => item.text)).toEqual(['第一节', '小节', '第二节'])
    expect(toc.map((item) => item.depth)).toEqual([2, 3, 2])
    expect(toc.every((item) => item.id.length > 0)).toBe(true)
  })

  it('标题 id 与正文标题一致（深链锚点不会指空）', () => {
    const { html, toc } = renderMarkdown('## 一个标题')
    expect(html).toContain(`id="${toc[0].id}"`)
  })

  it('重复标题也生成唯一 id', () => {
    const { toc } = renderMarkdown('## 重复\n\n## 重复')
    expect(new Set(toc.map((item) => item.id)).size).toBe(2)
  })

  it('slugifyHeading 收敛空白与标点', () => {
    expect(slugifyHeading('Hello   World')).not.toContain(' ')
    expect(slugifyHeading('中文标题')).toBe('中文标题')
  })
})

describe('renderMarkdown 代码块增强', () => {
  it('包上 .code-block 并给出语言标签与复制按钮', () => {
    const { html } = renderMarkdown('```python\nprint(1)\n```')
    expect(html).toContain('code-block')
    expect(html).toContain('code-lang')
    expect(html).toContain('python')
    // 复制按钮由渲染流水线生成，行为绑定在组件层
    expect(html).toContain('code-copy')
    expect(html).toContain('data-copyable="true"')
  })

  it('未标注语言的代码块不编造语言名', () => {
    const { html } = renderMarkdown('```\nplain\n```')
    expect(html).toContain('code-block')
    expect(html).toContain('data-lang="text"')
  })

  it('代码块内容被高亮（hljs 类名注入）', () => {
    const { html } = renderMarkdown('```python\nprint(1)\n```')
    expect(html).toContain('hljs')
  })
})
