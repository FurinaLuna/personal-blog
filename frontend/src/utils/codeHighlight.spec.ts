/**
 * 按需代码高亮的测试。
 *
 * 这条管线是从 `utils/markdown.ts` 拆出来的（highlight.js 约 49.5KB gzip，
 * 静态 import 会让每篇文章都先付这个代价）。拆出来之后有两个契约必须守住：
 *
 * 1. **同步的"准备"阶段不打折扣**：`.code-block` 骨架、lang 角标、复制按钮、
 *    以及"没写语言就不猜"这条老规矩，都与高亮无关，必须仍然成立 ——
 *    否则等于用性能换掉了功能。
 * 2. **异步的"增强"阶段必须失败可降级**：装饰性增强挂了不能影响正文。
 *
 * 测试环境说明：`src/test/setup.ts` 把 `IntersectionObserver` 打成了 no-op 桩，
 * 所以 `scheduleEnhancement` 不会真的触发增强 —— 这不是缺陷，而是让测试
 * 能分别验证"准备"与"增强"两段。需要验证增强时直接调 `enhanceCodeBlocks`。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { renderMarkdown } from '@/utils/markdown'
import { loadEnhancer, pendingLanguages, prepareCodeBlocks } from '@/utils/codeHighlight'

function mount(html: string): HTMLElement {
  const host = document.createElement('div')
  host.innerHTML = html
  document.body.append(host)
  return host
}

/**
 * 取增强实现。
 *
 * `enhanceCodeBlocks` 现在住在 `codeHighlightEngine.ts` 里（含 hljs，约 88KB），
 * 由轻量的 `codeHighlight.ts` 动态 import —— 这样没有代码块的页面完全不下载它。
 * 生产代码走 `scheduleEnhancement`，测试里直接取实现来验证行为。
 */
async function enhancer() {
  const fn = await loadEnhancer()
  expect(fn).not.toBeNull()
  return fn as (root: ParentNode) => Promise<boolean>
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('prepareCodeBlocks · 渲染期的结构', () => {
  it('把 pre>code 包成 .code-block，并生成角标与复制按钮', () => {
    const host = mount('<pre><code class="language-python">print(1)</code></pre>')

    prepareCodeBlocks(host)

    const wrapper = host.querySelector('.code-block')
    expect(wrapper).not.toBeNull()
    expect(wrapper?.getAttribute('data-copyable')).toBe('true')
    expect(wrapper?.getAttribute('data-lang')).toBe('python')
    // 复制按钮属于渲染产物（要跟着 v-html 一起走），点击行为由组件委托绑定
    expect(wrapper?.querySelector('.code-block__copy')?.textContent).toBe('复制')
    expect(wrapper?.querySelector('.code-block__lang')?.textContent).toBe('python')
    // 节点是被"挪"进去的，不是重新序列化 —— 代码文本必须原样保留
    expect(wrapper?.querySelector('code')?.textContent).toBe('print(1)')
  })

  it('代码块本体带 hljs 类（主题样式挂载点）', () => {
    const host = mount('<pre><code class="language-go">func main(){}</code></pre>')

    prepareCodeBlocks(host)

    expect(host.querySelector('code')?.classList.contains('hljs')).toBe(true)
  })

  it('标注待高亮，并记下本次用到的语言', () => {
    const host = mount('<pre><code class="language-typescript">const a = 1</code></pre>')

    prepareCodeBlocks(host)

    const code = host.querySelector('code')
    expect(code?.hasAttribute('data-hljs-pending')).toBe(true)
    expect(code?.getAttribute('data-language')).toBe('typescript')
    expect(pendingLanguages()).toContain('typescript')
  })

  it('未标注语言：不加待高亮标记，也不记入语言集合（不猜语言）', () => {
    const host = mount('<pre><code>no language</code></pre>')

    prepareCodeBlocks(host)

    const code = host.querySelector('code')
    expect(code?.hasAttribute('data-hljs-pending')).toBe(false)
    expect(host.querySelector('.code-block')?.getAttribute('data-lang')).toBe('text')
  })

  it('未知语言：角标显示作者写的原文，但不尝试高亮', () => {
    const host = mount('<pre><code class="language-brainfuck">+++</code></pre>')

    prepareCodeBlocks(host)

    const code = host.querySelector('code')
    // 角标可读性优先：作者声明什么就显示什么
    expect(host.querySelector('.code-block')?.getAttribute('data-lang')).toBe('brainfuck')
    expect(code?.hasAttribute('data-hljs-pending')).toBe(false)
  })

  it('别名归一：html 归到 xml，sh 归到 bash', () => {
    const host = mount(
      '<pre><code class="language-html">&lt;p&gt;</code></pre>' +
        '<pre><code class="language-sh">echo 1</code></pre>',
    )

    prepareCodeBlocks(host)

    const langs = [...host.querySelectorAll('code')].map((c) => c.getAttribute('data-language'))
    expect(langs).toEqual(['xml', 'bash'])
    // 角标仍显示作者写的别名，而不是内部语言名
    const labels = [...host.querySelectorAll('.code-block__lang')].map((s) => s.textContent)
    expect(labels).toEqual(['html', 'sh'])
  })
})

describe('增强实现 · 异步高亮', () => {
  it('没有待高亮的块时直接返回 false，不加载依赖', async () => {
    const host = mount('<pre><code>plain</code></pre>')
    prepareCodeBlocks(host)

    const fn = await enhancer()
    await expect(fn(host)).resolves.toBe(false)
  })

  it('有代码块时加载 hljs 并真的注入高亮标记（token span）', async () => {
    const host = mount(renderMarkdown('```python\ndef f(x):\n    return x\n```').html)

    const fn = await enhancer()
    const did = await fn(host)

    expect(did).toBe(true)
    const code = host.querySelector('code')
    // 待处理标记被清掉（幂等：不会重复高亮）
    expect(code?.hasAttribute('data-hljs-pending')).toBe(false)
    // 真的产生了 highlighter 的 token 结构，而不是只加了个类名
    expect(host.querySelector('.hljs-keyword, .hljs-built_in, .hljs-string')).not.toBeNull()
    // 源码文本不因高亮而改变 —— 复制按钮取的就是 textContent
    expect(code?.textContent).toContain('def f(x):')
  })

  it('未注册的语言不抛异常，块保持纯文本', async () => {
    // 构造一个"有标记但语言不在 LOADERS 里"的块，模拟未知语言边界
    const host = mount(
      '<pre class="code-block"><code class="hljs" data-hljs-pending data-language="brainfuck">+++</code></pre>',
    )

    const fn = await enhancer()
    await expect(fn(host)).resolves.toBe(true)

    // 语言没加载成功时仍把标记清掉，避免每次挂载都重试
    expect(host.querySelector('code')?.hasAttribute('data-hljs-pending')).toBe(false)
  })
})
