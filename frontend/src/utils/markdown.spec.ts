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
    expect(html).toContain('code-block__lang')
    expect(html).toContain('python')
    // 复制按钮由渲染流水线生成，行为绑定在组件层
    expect(html).toContain('code-block__copy')
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

/**
 * 覆盖式钓鱼：`style` 早就被禁了，但 `class` 曾被**整体放行**——
 * 而 `fixed inset-0 z-50 bg-white` 正是本站 ConfirmDialog 在用的工具类，
 * 作者在正文里写这几个词就能伪造一个盖住全站的浮层，配个密码框就是站内钓鱼页。
 * 这一组用例锁住的是"只放行 language-*"这条白名单。
 */
describe('renderMarkdown 覆盖式钓鱼防护', () => {
  it('剥掉伪造浮层的工具类', () => {
    const { html } = renderMarkdown(
      '<div class="fixed inset-0 z-50 bg-white flex items-center justify-center">假的登录框</div>',
    )
    expect(html).not.toContain('fixed')
    expect(html).not.toContain('inset-0')
    expect(html).not.toContain('z-50')
    // 文案留着（它只是文本），但没有任何定位能力
    expect(html).toContain('假的登录框')
  })

  it('只放行 language-*，其它 class 一律丢弃', () => {
    const { html } = renderMarkdown(
      '<pre><code class="language-js evil-class fixed inset-0">const a = 1</code></pre>',
    )
    expect(html).toContain('language-js')
    expect(html).not.toContain('evil-class')
    expect(html).not.toContain('inset-0')
  })

  it('剥掉 id（正文不需要，锚点由渲染器自己生成）', () => {
    const { html } = renderMarkdown('<div id="login-dialog">看起来像系统弹窗</div>')
    expect(html).not.toContain('login-dialog')
  })

  it('密码输入框被整段删除', () => {
    const { html } = renderMarkdown('<input type="password" placeholder="请输入密码">')
    expect(html).not.toContain('<input')
    expect(html).not.toContain('password')
  })

  it('任务列表的复选框保留（别把正常功能一起修坏）', () => {
    const { html } = renderMarkdown('- [x] 已完成\n- [ ] 未完成')
    expect(html).toContain('type="checkbox"')
    expect(html.match(/<input/g)?.length).toBe(2)
  })
})

describe('renderMarkdown 外链处理', () => {
  it('外链加 target=_blank 与 noopener', () => {
    const { html } = renderMarkdown('[外站](https://example.com/a)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('noopener')
  })

  it('协议相对地址（//evil.com）也拿到 rel —— 正则版本漏掉的那一类', () => {
    const { html } = renderMarkdown('[协议相对](//evil.example.com/x)')
    expect(html).toContain('//evil.example.com/x')
    expect(html).toContain('noopener')
  })

  it('站内链接不加 target（目录/相关文章不该开新标签页）', () => {
    const { html } = renderMarkdown('[归档](/archive)')
    expect(html).toContain('href="/archive"')
    expect(html).not.toContain('target="_blank"')
  })

  it('作者手写的 target=_blank 在站内链接上被移除', () => {
    const { html } = renderMarkdown('<a href="/about" target="_blank">关于</a>')
    expect(html).not.toContain('target="_blank"')
  })
})

describe('renderMarkdown 其它行为', () => {
  it('图片补懒加载与异步解码', () => {
    const { html } = renderMarkdown('![图](/media/a.png)')
    expect(html).toContain('loading="lazy"')
    expect(html).toContain('decoding="async"')
  })

  it('GFM 表格与删除线可用', () => {
    const { html } = renderMarkdown('| a | b |\n| - | - |\n| 1 | 2 |\n\n~~删掉~~')
    expect(html).toContain('<table>')
    expect(html).toContain('<del>删掉</del>')
  })

  it('相同输入命中缓存（渲染是纯函数）', () => {
    const source = '# 缓存命中测试\n\n正文'
    expect(renderMarkdown(source)).toBe(renderMarkdown(source))
  })
})
