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
 *
 * ## 代码高亮的加载时机（这里做过一次性能取舍）
 *
 * 语法高亮**不在这里做**，而是拆到 `utils/codeHighlight.ts` 按需加载。
 * 原因：highlight.js 与注册语言共约 49.5KB gzip，原先被本文件静态 import，
 * 于是**每一篇文章**（哪怕一个代码块都没有）都要先下载解析完才能显示第一段正文。
 * 现在本文件只做同步的「准备」（`.code-block` 骨架 + lang 标记），
 * 真正的 import 推迟到正文挂载后、且确认存在代码块时。
 */
import DOMPurify from 'dompurify'
import { marked } from 'marked'

import { prepareCodeBlocks, resetPendingLanguages } from './codeHighlight'
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
  // `class` 在这里、但**值**由下面的钩子按白名单裁剪（只留 marked 生成的
  // `language-*`，代码块的高亮与语言角标要用）。
  //
  // 为什么不是把 class 从允许列表里删掉：那样连 `language-*` 一起没了，
  // 代码高亮与角标全部失效（既有的"代码高亮"用例会当场抓住）。
  // 为什么不能什么都不做地放行：作者在正文里写
  // `class="fixed inset-0 z-50 bg-white"` 就能伪造一个全屏浮层
  // （配一个密码输入框就是站内钓鱼页），而这些工具类**在本站确实存在**
  // （ConfirmDialog 的遮罩就是 `fixed inset-0 z-50`）。
  //
  // `id` 则完全没有必要：正文不需要它，标题锚点由渲染后自己生成（见下方第 1 步）。
  ALLOWED_ATTR: [
    'href', 'title', 'alt', 'src', 'class', 'target', 'rel',
    'colspan', 'rowspan', 'align', 'start', 'type', 'checked', 'disabled', 'loading',
  ],
  // 允许 data-* （代码高亮、任务列表都在用），但不允许 style ——
  // 内联 style 可以做覆盖式钓鱼（伪造一个全屏"登录框"）
  ALLOW_DATA_ATTR: true,
  FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick'],
  // 禁止协议：阻断 javascript: / data: 这类伪协议的链接与图片
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
}

/** 唯一的合法 class 形状：`language-<名字>`（marked 为 ```lang 生成的声明）。 */
const SAFE_CLASS_NAME = /^language-[a-z0-9+#._-]{1,20}$/i

// 钩子注册在模块级（DOMPurify 是单例）：整个应用只注册一次。
DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
  if (data.attrName !== 'class') return
  const kept = String(data.attrValue)
    .split(/\s+/)
    .filter((name) => SAFE_CLASS_NAME.test(name))
  if (kept.length === 0) {
    data.keepAttr = false
    return
  }
  data.attrValue = kept.join(' ')
  // 这里**不能**用 `forceKeepAttr`。它在 DOMPurify 内部是 `continue`
  // （"属性已获批，跳过后续检查"），而"把裁剪后的值写回 DOM"恰恰在它之后 ——
  // 用它等于原样保留整个 class 值，`evil-class fixed inset-0` 全都会被留下。
  // 正确姿势：让 class 留在 ALLOWED_ATTR 里（属性本身合法），只改值，
  // 由 DOMPurify 的 `value !== initValue` 分支完成写回。
})

DOMPurify.addHook('afterSanitizeElements', (node) => {
  if (node.nodeName !== 'INPUT') return
  // 钩子的签名给的是 Node，而 remove() 只在 Element 上（TS 会当场报错，
  // 这类"类型说不通"的地方往往正是运行时也会炸的地方，别用 as any 糊过去）
  if (!(node instanceof Element)) return
  const element = node
  // 只保留任务列表的复选框。`<input type="password">` 是钓鱼页的关键零件：
  // 即使 class 被剥掉，一个原生密码框配几句 HTML 文案也足以骗到输入。
  if ((element.getAttribute('type') ?? '').toLowerCase() !== 'checkbox') {
    element.remove()
    return
  }
  // 复选框只需要这三个属性；name/form/autofocus 之类没有理由保留
  for (const attr of Array.from(element.attributes)) {
    if (!['type', 'checked', 'disabled'].includes(attr.name)) {
      element.removeAttribute(attr.name)
    }
  }
})

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

  // 2) 代码块骨架（.code-block + lang 角标 + 复制按钮）。
  //
  //    这里**不做高亮**：highlight.js 约 49.5KB gzip，放进同步渲染管线等于
  //    每篇文章（含无代码块的）都要先付这个代价。高亮由 MarkdownRenderer 挂载后
  //    调用 `enhanceCodeBlocks` 按需加载并应用（见 utils/codeHighlight.ts）。
  //
  //    结构与行为分离：这里只生成 DOM 与标记，复制交互由 MarkdownRenderer
  //    用事件委托绑定 —— 工具函数不该掺和 UI 行为，否则后台编辑器复用这段
  //    渲染逻辑时会被迫引入 toast 依赖。
  resetPendingLanguages()
  prepareCodeBlocks(container)

  // 3) 外链新开窗口并补 rel：没有 noopener 时，新页面能通过 window.opener 反向操作本页。
  //
  // 用 `new URL(href, origin)` 判同源，而不是 `/^https?:\/\//` 正则：
  // 后者漏掉了**协议相对地址** —— `<a href="//evil.com" target="_blank">`
  // 是合法外链（浏览器会补上当前协议），正则不匹配于是拿不到 rel，
  // 而它照样会开新窗口、照样能通过 window.opener 反向操作本页。
  container.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((link) => {
    const href = link.getAttribute('href') ?? ''
    let target: URL
    try {
      target = new URL(href, window.location.origin)
    } catch {
      return // 连 URL 都解析不了：交给 DOMPurify 的白名单，不动它
    }
    if (target.origin !== window.location.origin) {
      link.setAttribute('target', '_blank')
      link.setAttribute('rel', 'noopener noreferrer')
    } else {
      // 站内链接明确去掉 target/rel：作者手写的 target="_blank" 会让站内跳转
      // 也开新标签页，与「目录锚点/相关文章」的预期导航行为冲突
      link.removeAttribute('target')
      link.removeAttribute('rel')
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
