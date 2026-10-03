/**
 * 设计令牌的 WCAG 对比度契约测试。
 *
 * tokens.css 是全站颜色的唯一来源，但「改一个色值」从来不只是视觉问题：
 * ink-faint 曾是 168 162 158（在 bg 上仅 2.4:1），被用在文章元信息、评论
 * 时间戳这类 12~14px 的真实文本上，全部低于 AA 的 4.5:1；链接色在暗色
 * 模式下也曾整片只有 2.7:1。这类问题 ESLint 抓不到（无对比度规则），
 * 肉眼在开发时也不敏感——只有把「哪个令牌必须配哪个底」写成断言才能钉住。
 *
 * 与 test_dependency_lock.py 同一思路：把配置漂移变成红灯，而不是留给人肉 review。
 *
 * 这里测的是**令牌层契约**（文字色 × 表面色）。模板里带显式 dark: 变体的
 * 徽标组合（brand-700 on brand-50 / dark: brand-200 on brand-900/40）属于
 * 组件层约定，不在此列。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

// 为什么不用 `import ... from './tokens.css?raw'`：vitest 默认不处理 CSS
// （test.css=false），`?raw` 查询会被 stub 成空内容；jsdom 环境又会把
// import.meta.url 改写成 http:// 协议。测试统一从 frontend/ 目录启动
// （npm run test / CI 同口径），按 cwd 相对路径直接读盘最可靠，
// 且天然拿到磁盘上的真实源文件，不被任何转换管线美化。
const tokensRaw = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8')

/* --------------------------------------------------------------- 解析 */

type Rgb = [number, number, number]

/** 从一段 CSS 块文本里取 `--name: r g b;` 形式的三元组。 */
function extractVar(block: string, name: string): Rgb {
  const match = block.match(new RegExp(`--${name}:\\s*(\\d+)\\s+(\\d+)\\s+(\\d+)`))
  if (!match) throw new Error(`tokens.css 里找不到 --${name}（改名或删掉了？请同步更新本测试）`)
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

/** tokens.css 只有两个顶层块（:root 与 .dark），块内没有嵌套花括号，非贪婪匹配即安全。 */
const rootBlock = tokensRootBlock()
const darkBlock = tokensDarkBlock()

function tokensRootBlock(): string {
  const match = tokensRaw.match(/:root\s*\{([\s\S]*?)\}/)
  if (!match) throw new Error('tokens.css 里找不到 :root 块')
  return match[1]
}

function tokensDarkBlock(): string {
  const match = tokensRaw.match(/\.dark\s*\{([\s\S]*?)\}/)
  if (!match) throw new Error('tokens.css 里找不到 .dark 块')
  return match[1]
}

/* --------------------------------------------------------------- WCAG 计算 */

/** sRGB 通道 → 线性亮度（WCAG 2.x 公式）。 */
function linearize(channel: number): number {
  const c = channel / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(rgb: Rgb): number {
  return 0.2126 * linearize(rgb[0]) + 0.7152 * linearize(rgb[1]) + 0.0722 * linearize(rgb[2])
}

/** WCAG 对比度，1~21，越大越清晰。 */
function contrast(foreground: Rgb, background: Rgb): number {
  const l1 = luminance(foreground)
  const l2 = luminance(background)
  const [lighter, darker] = l1 >= l2 ? [l1, l2] : [l2, l1]
  return (lighter + 0.05) / (darker + 0.05)
}

/** AA 对普通文本的要求。ink-faint 最小用到 11px（代码块语言标签），不适用大字放宽条款。 */
const AA_TEXT = 4.5

/* --------------------------------------------------------------- 用例 */

describe.each([
  ['亮色', rootBlock],
  ['暗色', darkBlock],
] as const)('文字令牌对比度（%s）', (_mode, block) => {
  const bg = extractVar(block, 'c-bg')
  const surface = extractVar(block, 'c-surface')
  const surfaceMuted = extractVar(block, 'c-surface-muted')

  it.each([
    ['正文 ink', 'c-ink', [bg, surface]],
    ['次级 ink-soft', 'c-ink-soft', [bg, surface, surfaceMuted]],
    // ink-faint 的前史：168 162 158 在 bg 上 2.4:1，被大量用于真实文本（P2-2）
    ['最浅 ink-faint', 'c-ink-faint', [bg, surface, surfaceMuted]],
    // 链接的前史：暗色下 brand-600 只有 2.7:1（见 tokens.css --c-link 注释）。
    // surface-muted 也在列：激活态筛选 chip 的文字就落在它上面
    ['链接 link', 'c-link', [bg, surface, surfaceMuted]],
    ['链接 hover', 'c-link-hover', [bg, surface]],
  ])('%s 在全部表面色上 ≥ 4.5:1', (_label, token, surfaces) => {
    const foreground = extractVar(block, token)
    for (const backdrop of surfaces) {
      // 输出比值便于失败时直接看到差多少，而不是只说「不达标」
      expect(contrast(foreground, backdrop)).toBeGreaterThanOrEqual(AA_TEXT)
    }
  })

  it('代码文字 / 成功反馈在代码块底上 ≥ 4.5:1', () => {
    const codeBg = extractVar(block, 'c-code-bg')
    expect(contrast(extractVar(block, 'c-code-ink'), codeBg)).toBeGreaterThanOrEqual(AA_TEXT)
    // 「已复制」反馈文字（components.css .code-block__copy[data-state='done']）
    expect(contrast(extractVar(block, 'c-success'), codeBg)).toBeGreaterThanOrEqual(AA_TEXT)
  })
})

describe('对比度计算本身', () => {
  it('黑底白字 = 21:1（公式自校验）', () => {
    expect(contrast([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 5)
  })

  it('参数顺序无关（前景/背景互换结果不变）', () => {
    const a: Rgb = [110, 105, 100]
    const b: Rgb = [250, 249, 246]
    expect(contrast(a, b)).toBeCloseTo(contrast(b, a), 10)
  })
})
