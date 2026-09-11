/**
 * Markdown 渲染。
 *
 * 安全模型（这是全站最关键的一环）：
 *
 * 1. 正文以 Markdown **原文** 存在数据库里，渲染完全在前端完成——后端不产生 HTML，
 *    也就不存在「存进去的时候没消毒干净」的问题。
 * 2. 渲染流程固定为：marked 转 HTML → **DOMPurify 消毒** → 再做增强（标题 id、
 *    代码高亮、外链处理）。
 * 3. 消毒排在增强之前，是因为增强步骤只会 setAttribute 我们自己生成的值，
 *    不引入任何新的不可信内容。
 *
 * 只靠 Markdown 语法本身就能写出 `<script>`（Markdown 允许内联 HTML），
 * 所以「用了 Markdown 就安全」是错觉，必须消毒。
 */
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import { marked } from 'marked'

import { slugifyHeading } from './format'

export interface TocItem {
  id: string
  text: string
  depth: number
}

export interface RenderResult {
  html: string
  toc: TocItem[]
}

marked.setOptions({
  gfm: true, // 表格、删除线、任务列表
  breaks: false, // 不把单换行当 <br>，否则中文段落的换行会被拆散
})

/**
 * DOMPurify 的配置类型。
 *
 * 从函数签名反推而不是直接写 `DOMPurify.Config` —— 后者的类型命名空间
 * 在不同 dompurify 版本里导出方式并不一致，而从签名反推永远与当前版本对齐。
 */
type SanitizeConfig = Parameters<typeof DOMPurify.sanitize>[1]

const PURIFY_CONFIG: SanitizeConfig = {
  // 明确列出允许的标签，比「默认允许再加黑名单」更安全
  ALLOWED_TAGS: [
    'p', 'br', 'hr', 'strong', 'em', 'del', 'ins', 'sup', 'sub', 'mark', 'small',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'blockquote', 'pre', 'code', 'kbd', 'samp',
    'a', 'img', 'figure', 'figcaption',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
    'div', 'span', 'input',
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'alt', 'src', 'class', 'id', 'target', 'rel',
    'colspan', 'rowspan', 'align', 'start', 'type', 'checked', 'disabled', 'loading',
  ],
  // 允许 data-* （代码高亮、任务列表都在用），但不允许 style ——
  // 内联 style 可以做覆盖式钓鱼（伪造一个全屏"登录框"）
  ALLOW_DATA_ATTR: true,
  FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick'],
  // 禁止协议：阻断 javascript: / data: 这类伪协议的链接与图片
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
}

/**
 * 变量名必须加 `ReturnType`，因为这是模块级缓存，不是每个请求都用得到。
 * 用 Map 是为了避免同一篇文章反复渲染（比如目录组件和数据同时请求）时重复计算。
 */
const cache = new Map<string, RenderResult>()
const CACHE_LIMIT = 30

export function renderMarkdown(source: string): RenderResult {
  const input = source ?? ''
  const cached = cache.get(input)
  if (cached) return cached

  const rawHtml = marked.parse(input, { async: false }) as string
  // 用 String() 而不是 `as string`：dompurify 的返回类型在启用
  // RETURN_TRUSTED_TYPE 时会变成 TrustedHTML，这里统一收敛成字符串
  const safeHtml = String(DOMPurify.sanitize(rawHtml, PURIFY_CONFIG))

  const container = document.createElement('div')
  container.innerHTML = safeHtml

  // 1) 给标题加锚点 id，顺便收集目录
  const toc: TocItem[] = []
  const usedIds = new Set<string>()
  container.querySelectorAll('h1, h2, h3, h4').forEach((element) => {
    const text = element.textContent?.trim() ?? ''
    if (!text) return

    let id = slugifyHeading(text)
    let suffix = 2
    while (usedIds.has(id)) {
      id = `${slugifyHeading(text)}-${suffix++}`
    }
    usedIds.add(id)

    element.setAttribute('id', id)
    toc.push({ id, text, depth: Number(element.tagName.slice(1)) })
  })

  // 2) 代码高亮 + 语言标签 + 复制标记。
  //    highlight.js 直接操作 DOM，比自己拼字符串安全得多。
  //    这里只负责「标记结构」（data-copyable / .code-lang），真正的复制交互
  //    由 MarkdownRenderer 用事件委托绑定 —— 工具函数不该掺和 UI 行为，
  //    否则后台编辑器复用这段渲染逻辑时会被迫引入 toast 依赖。
  container.querySelectorAll<HTMLElement>('pre code').forEach((block) => {
    block.classList.add('hljs')
    try {
      hljs.highlightElement(block)
    } catch {
      // 语言没注册或识别失败时保持纯文本，不能让高亮失败把正文拖垮
    }
  })

  container.querySelectorAll<HTMLElement>('pre').forEach((block) => {
    const code = block.querySelector('code')
    // 语言名取自 marked 生成的 language-xxx 类（来自 ```语言 井号语法）
    const lang = (Array.from(code?.classList ?? []).find((c) => c.startsWith('language-')) ?? '')
      .replace('language-', '')
      .slice(0, 16)
    if (!lang) return

    const wrapper = document.createElement('div')
    wrapper.className = 'code-block'
    wrapper.dataset.copyable = 'true'
    wrapper.dataset.lang = lang
    // 用原生 DOM API 挪节点（不碰 innerHTML，避免把已高亮的 DOM 重新序列化）
    block.replaceWith(wrapper)
    wrapper.append(block)

    const tag = document.createElement('span')
    tag.className = 'code-lang'
    tag.textContent = lang
    wrapper.append(tag)

    // 按钮本体在这里生成（它属于渲染产物，要跟着 v-html 一起走），
    // 但点击行为留给 MarkdownRenderer 用事件委托绑定 —— 结构与行为分离。
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'code-copy'
    button.textContent = '复制'
    button.setAttribute('aria-label', `复制 ${lang} 代码`)
    wrapper.append(button)
  })

  // 3) 外链新开窗口并补 rel：没有 noopener 时，新页面能通过 window.opener 反向操作本页
  container.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((link) => {
    const href = link.getAttribute('href') ?? ''
    if (/^https?:\/\//i.test(href) && !href.startsWith(window.location.origin)) {
      link.setAttribute('target', '_blank')
      link.setAttribute('rel', 'noopener noreferrer')
    }
  })

  // 4) 图片懒加载：文章里经常一次塞十几张图
  container.querySelectorAll('img').forEach((image) => {
    image.setAttribute('loading', 'lazy')
    image.setAttribute('decoding', 'async')
  })

  const result: RenderResult = { html: container.innerHTML, toc }

  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(input, result)
  return result
}

/** 只取纯文本，用于生成 meta description 或列表摘要。 */
export function markdownToText(source: string, limit = 200): string {
  const { html } = renderMarkdown(source)
  const container = document.createElement('div')
  container.innerHTML = html
  const text = container.textContent?.replace(/\s+/g, ' ').trim() ?? ''
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`
}

export { hljs }
