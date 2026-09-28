/**
 * 按需代码高亮 —— **增强期**这一半（含 highlight.js，约 88KB 未压缩）。
 *
 * ## 这个模块只会被需要它的页面加载
 *
 * 它**不被任何渲染路径静态 import**：`utils/codeHighlight.ts` 的
 * `loadEnhancer()` 在「确认存在待高亮代码块」之后才 `import()` 它。
 * 因此没有代码块的文章永远不会下载这里的 highlight.js。
 *
 * ⚠️ 改动本文件时请守住这条不变量：**不要**从 `utils/markdown.ts`、
 * `codeHighlight.ts` 或任何被它们静态依赖的模块里 import 本文件，
 * 否则那 88KB 会被重新拉回所有正文页面（打包器只能看到静态依赖）。
 * 需要共享的东西（属性名、语言集合）放在 `codeHighlight.ts`。
 *
 * ## 高亮是纯装饰，失败必须静默
 *
 * 动态 import 可能因网络/离线而失败。失败时保持纯文本代码块即可 ——
 * 正文完整、复制按钮照常可用，只是没有颜色。
 * **绝不能让一个装饰性增强把正文拖垮。**
 */
import { LANG_ATTR, PENDING_ATTR, registerEnhancer } from './codeHighlight'

/** 已 import 的语言（语言名 → loader）。用惰性函数是为了让打包器切出独立小分包。 */
type LanguageLoader = () => Promise<{ default: unknown }>

const LOADERS: Record<string, LanguageLoader> = {
  bash: () => import('highlight.js/lib/languages/bash'),
  css: () => import('highlight.js/lib/languages/css'),
  dockerfile: () => import('highlight.js/lib/languages/dockerfile'),
  go: () => import('highlight.js/lib/languages/go'),
  ini: () => import('highlight.js/lib/languages/ini'),
  java: () => import('highlight.js/lib/languages/java'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  json: () => import('highlight.js/lib/languages/json'),
  markdown: () => import('highlight.js/lib/languages/markdown'),
  nginx: () => import('highlight.js/lib/languages/nginx'),
  plaintext: () => import('highlight.js/lib/languages/plaintext'),
  python: () => import('highlight.js/lib/languages/python'),
  rust: () => import('highlight.js/lib/languages/rust'),
  sql: () => import('highlight.js/lib/languages/sql'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  xml: () => import('highlight.js/lib/languages/xml'), // html 是 xml 的别名
  yaml: () => import('highlight.js/lib/languages/yaml'),
}

type Hljs = typeof import('highlight.js/lib/core').default

/** 已注册进 hljs 实例的语言，避免重复 registerLanguage。 */
const registered = new Set<string>()
let hljsInstance: Hljs | null = null

async function getHljs(): Promise<Hljs> {
  if (!hljsInstance) {
    // 样式（github.css 亮色 + vendor.css 暗色覆盖）由 main.ts 全局引入，
    // 不在这里 import：它必须与首屏一起到达，否则代码块会先以无样式闪烁
    hljsInstance = (await import('highlight.js/lib/core')).default
  }
  return hljsInstance
}

/** 按需加载并注册一个语言；语言文件缺失或加载失败时返回 false。 */
async function ensureLanguage(hljs: Hljs, name: string): Promise<boolean> {
  if (registered.has(name)) return true
  const loader = LOADERS[name]
  if (!loader) return false
  const mod = await loader()
  hljs.registerLanguage(name, mod.default as never)
  registered.add(name)
  return true
}

/**
 * 真正的增强：加载 hljs 与语言，逐块高亮。
 *
 * @param root 已挂载的容器（`v-html` 渲染出的 `.prose` 节点或其祖先）
 * @returns 是否真的做了高亮（没有待处理块、或依赖加载失败时返回 false）
 */
async function enhanceCodeBlocks(root: ParentNode): Promise<boolean> {
  const blocks = root.querySelectorAll<HTMLElement>(`code[${PENDING_ATTR}]`)
  if (blocks.length === 0) return false

  let hljs: Hljs
  try {
    hljs = await getHljs()
    const names = new Set<string>()
    blocks.forEach((block) => {
      const name = block.getAttribute(LANG_ATTR) ?? ''
      if (name) names.add(name)
    })
    await Promise.all([...names].map((name) => ensureLanguage(hljs, name)))
  } catch (error) {
    console.debug('[markdown] 代码高亮依赖加载失败，保持纯文本', error)
    return false
  }

  blocks.forEach((block) => {
    if (!block.hasAttribute(PENDING_ATTR)) return
    block.removeAttribute(PENDING_ATTR)
    try {
      // highlightElement 自己读 `language-xxx` 类决定语法，只操作 DOM 与 class，
      // 不产出 HTML 字符串 —— 不引入新的注入面
      hljs.highlightElement(block)
    } catch (error) {
      // 单块失败不影响其它块
      console.debug('[markdown] 代码块高亮失败，该块保持纯文本', error)
    }
  })
  return true
}

// 模块求值时把自己注册给轻量的 codeHighlight 模块（见其 loadEnhancer）
registerEnhancer(enhanceCodeBlocks)
