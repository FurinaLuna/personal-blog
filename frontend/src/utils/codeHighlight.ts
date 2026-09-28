/**
 * 按需代码高亮 —— **渲染期**这一半（不含 highlight.js）。
 *
 * ## 这个文件为什么必须"轻"
 *
 * highlight.js 与它的核心约 88KB（未压缩），原先被 `utils/markdown.ts` 静态 import，
 * 于是**每一篇文章**（哪怕正文里一个代码块都没有）都要先下载解析它才能显示第一段正文。
 *
 * 现在拆成两半：
 * - **本文件**：同步的「准备」。只搬 DOM、写语言标记，不 import 任何 hljs；
 * - `utils/codeHighlightEngine.ts`：异步的「增强」。按需 `import('highlight.js')`。
 *
 * 拆分不是"代码更整齐"的洁癖，而是**打包结果的硬要求**：只要本文件（它被
 * `markdown.ts` 静态依赖、因而进入每个用到正文的路由分包）里出现任何对 hljs 的
 * 静态引用，那 88KB 就会被静态拉进 `AboutView` / `ArticleDetailView` 等分包，
 * 即使整篇没有代码块。所以两个属性常量也放在这里，由 engine 反向 import ——
 * 常量是字符串，不带来体积。
 */

/** 代码块声明的高亮语言（归一后的 hljs 语言名）。 */
export const LANG_ATTR = 'data-language'
/** 待高亮标记：渲染期打上，增强期消费后移除。 */
export const PENDING_ATTR = 'data-hljs-pending'

/** marked 常用别名 → 已注册的语言名。别名缺失时不高亮，不去猜。 */
const ALIASES: Record<string, string> = {
  html: 'xml',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  console: 'bash',
  yml: 'yaml',
  md: 'markdown',
  text: 'plaintext',
  txt: 'plaintext',
  conf: 'ini',
  toml: 'ini',
  cfg: 'ini',
}

/**
 * 支持高亮的语言名（与 `codeHighlightEngine.ts` 的 LOADERS 一一对应）。
 *
 * 为什么这里再列一遍而不是从 engine import：import engine 会把 hljs 静态拉进本分包
 * （见文件头说明）。这里只放字符串，成本是零；两处不一致时由
 * `codeHighlight.spec.ts` 的别名用例兜住。
 */
const SUPPORTED = new Set([
  'bash',
  'css',
  'dockerfile',
  'go',
  'ini',
  'java',
  'javascript',
  'json',
  'markdown',
  'nginx',
  'plaintext',
  'python',
  'rust',
  'sql',
  'typescript',
  'xml',
  'yaml',
])

/** 渲染期记下的「本页用到了哪些语言」，供预热/测试查看。 */
let queuedLanguages = new Set<string>()

/**
 * 从 `language-xxx` 类名解析出语言名（渲染期用，不需要 hljs）。
 *
 * 只认 marked 写在 `<code>` 上的声明。
 */
export function resolveLanguage(declared: string | undefined): string {
  const name = (declared ?? '').trim().toLowerCase()
  if (!name) return ''
  const aliased = ALIASES[name] ?? name
  return SUPPORTED.has(aliased) ? aliased : ''
}

/**
 * 渲染阶段的「准备」：把 `<pre><code>` 包成 `.code-block` 骨架并记下语言。
 *
 * 刻意**不做**高亮 —— 那是 engine 的事，因为它需要 network。
 * 这里顺带保证输出里带 `hljs` 类：主题样式（`main.ts` 引入的 github.css 与
 * `styles/vendor.css` 的暗色覆盖）挂在 `.hljs` 上，纯文本状态也要可读。
 */
export function prepareCodeBlocks(container: HTMLElement): void {
  container.querySelectorAll<HTMLElement>('pre').forEach((block) => {
    const code = block.querySelector('code')
    const declared = Array.from(code?.classList ?? [])
      .find((name) => name.startsWith('language-'))
      ?.replace('language-', '')

    const language = resolveLanguage(declared)
    // 角标文案：作者声明什么就显示什么（可读性优先），无法高亮时也显示原始声明
    const label = (declared || 'text').slice(0, 16)
    if (language) queuedLanguages.add(language)

    const wrapper = document.createElement('div')
    wrapper.className = 'code-block'
    wrapper.dataset.copyable = 'true'
    wrapper.dataset.lang = label
    block.replaceWith(wrapper)
    wrapper.append(block)

    // 代码块本体：始终带 hljs 类（主题样式的挂载点，见文件头）
    if (code) {
      code.classList.add('hljs')
      // 语言标记与"待高亮"标记都挂在 **code** 上，而不是外层 wrapper：
      // 增强阶段按 `code[data-hljs-pending]` 找块，两处若分开写，
      // 读语言时就得先 closest('.code-block') 再往回找 —— 徒增一层间接。
      if (language) {
        code.setAttribute(LANG_ATTR, language)
        code.setAttribute(PENDING_ATTR, '')
      }
    }

    const tag = document.createElement('span')
    tag.className = 'code-block__lang'
    tag.textContent = label
    wrapper.append(tag)

    // 按钮本体在这里生成（它属于渲染产物，要跟着 v-html 一起走），
    // 但点击行为留给 MarkdownRenderer 用事件委托绑定 —— 结构与行为分离。
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'code-block__copy'
    button.textContent = '复制'
    button.setAttribute('aria-label', `复制 ${label} 代码`)
    wrapper.append(button)
  })
}

/** 本次渲染用到的语言名（供测试与预热使用）。 */
export function pendingLanguages(): string[] {
  return [...queuedLanguages]
}

/** 清空「本次渲染的语言」记录。渲染按篇进行，跨篇累积没有意义。 */
export function resetPendingLanguages(): void {
  queuedLanguages = new Set()
}

/** 是否存在待高亮的代码块（不加载任何依赖即可判定）。 */
export function hasPendingBlocks(root: ParentNode): boolean {
  return root.querySelector(`code[${PENDING_ATTR}]`) !== null
}

/**
 * 增强函数的注入点。
 *
 * `MarkdownRenderer` 挂载后调用 `scheduleEnhancement`，后者**动态 import**
 * `codeHighlightEngine` 来拿到真正的实现 —— 这样 hljs 只会被那些真的出现代码块的
 * 页面拉取。engine 在模块求值时把自己注册进来（见其文件末尾）。
 */
type Enhancer = (root: ParentNode) => Promise<boolean>

let enhancer: Enhancer | null = null
// 注意类型是 `Enhancer | null`：加载失败时缓存的是 null（不重复重试），
// 所以调用方必须判空 —— `MarkdownRenderer` 用的是 `fn?.(root)`。
let enginePromise: Promise<Enhancer | null> | null = null

/** engine 模块自注册用（不要从业务代码调用）。 */
export function registerEnhancer(fn: Enhancer): void {
  enhancer = fn
}

/** 取增强实现：已注册则直接返回，否则动态 import engine 模块。 */
export async function loadEnhancer(): Promise<Enhancer | null> {
  if (enhancer) return enhancer
  enginePromise ??= import('./codeHighlightEngine')
    .then(() => enhancer)
    .catch((error: unknown) => {
      // 装饰性增强的依赖加载失败不该影响正文：保持纯文本即可
      console.debug('[markdown] 代码高亮模块加载失败，保持纯文本', error)
      return null
    })
  return enginePromise
}

/**
 * 等到有代码块进入视口（或视口附近）再加载并应用高亮。
 *
 * **只有真的存在待高亮代码块时才会 import engine** —— 没有代码块的文章
 * 完全不碰那 88KB。没有 `IntersectionObserver` 的环境（老浏览器、jsdom）
 * 直接增强，保证功能不因缺少观察者而消失。
 *
 * @returns 取消函数（组件卸载时调用）
 */
export function scheduleEnhancement(root: HTMLElement): () => void {
  if (!hasPendingBlocks(root)) return () => {}

  const run = (): void => {
    void loadEnhancer().then((fn) => fn?.(root))
  }

  if (typeof IntersectionObserver === 'undefined') {
    run()
    return () => {}
  }

  const observer = new IntersectionObserver(
    (entries, self) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      // 一次性：高亮发生在首次接近视口时，之后正文增删不再重新观察
      self.disconnect()
      run()
    },
    // 提前 200px 预取：等真的滚到才加载会有可见的"变色"跳变
    { rootMargin: '200px' },
  )

  root.querySelectorAll(`code[${PENDING_ATTR}]`).forEach((block) => observer.observe(block))
  return () => observer.disconnect()
}
