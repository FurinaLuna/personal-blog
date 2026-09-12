/**
 * 样式基线采集：把关键元素的计算样式导出成 JSON，用于重构前后的机械比对。
 *
 * 为什么需要它：CSS 重构最容易出的事是「某个页面的某个角落悄悄变了」，
 * 而截图对比在这个项目里不可靠——详情页每次访问阅读量 +1、相对时间文案会变，
 * 字节级比对必然失败。计算样式比对则只关心「渲染参数有没有变」。
 *
 * 选择器刻意写成「重命名无关」的形式（如 `[class*="code-"]`、`button[class*="btn"]`），
 * 这样即使重构时把 .code-block__lang 改成 .code-block__lang，采集结果依然可比。
 *
 * 用法：node tools/style-baseline.mjs <baseUrl> <outFile>
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const BASE = process.argv[2] ?? 'http://127.0.0.1:5173'
const OUT = process.argv[3] ?? './style-baseline.json'
const PORT = Number(process.env.BASELINE_PORT ?? 9240)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 采集哪些路由。覆盖三种布局与主要组件。
 *
 * 注意必须包含**文章详情页**：代码块角标（语言标签 / 复制按钮）只在那里出现，
 * 漏掉它就会出现「7 个路由零差异」但其实关键改动未被覆盖的假安全感
 * （这个盲点踩过一次）。
 */
const ROUTES = ['/', '/article/__SLUG__', '/archive', '/tags', '/categories', '/about', '/login', '/nosuchpage']


/** 取一篇真实文章 slug，用于详情页采集。 */
async function resolveSlug(base) {
  try {
    const data = await (await fetch(`${base}/api/v1/articles?page_size=1`)).json()
    return data?.items?.[0]?.slug ?? null
  } catch {
    return null
  }
}

/** 采集哪些选择器（重命名无关）与哪些属性。 */
const SELECTORS = [
  'header',
  'footer',
  'main',
  'h1',
  'h2',
  'p',
  'a',
  'button',
  'input',
  'textarea',
  'table',
  'img',
  '.card',
  'button[class*="btn"]',
  'a[class*="chip"]',
  '[class*="skeleton"]',
  '[class*="code-"]',
  'pre',
  'pre code',
  '[role="progressbar"]',
  'nav[aria-label]',
]

const PROPS = [
  'color',
  'backgroundColor',
  'borderTopColor',
  'borderTopWidth',
  'borderTopStyle',
  'borderTopLeftRadius',
  'fontFamily',
  'fontSize',
  'fontWeight',
  'lineHeight',
  'letterSpacing',
  'paddingTop',
  'paddingLeft',
  'marginTop',
  'marginBottom',
  'display',
  'position',
  'opacity',
  'boxShadow',
  'textAlign',
  'gap',
  'width',
  'height',
]

async function getWsUrl(port) {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch {
      /* 继续轮询 */
    }
    await sleep(300)
  }
  throw new Error('CDP 未就绪')
}

const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    // 每次唯一 profile：复用旧 profile 会带上历史登录态与 localStorage，
    // 导致页面渲染出不同的状态（这个坑在 interaction-check 上踩过）
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${join(tmpdir(), `style-baseline-${Date.now()}`)}`,
    '--window-size=1440,1200',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

try {
  if (!existsSync(CHROME)) throw new Error('未找到 Chrome')
  mkdirSync(dirname(OUT), { recursive: true })

  const ws = new WebSocket(await getWsUrl(PORT))
  await new Promise((res, rej) => {
    ws.onopen = res
    ws.onerror = rej
  })
  let seq = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m.result)
      pending.delete(m.id)
    }
  }
  const send = (method, params = {}) =>
    new Promise((res) => {
      const id = ++seq
      pending.set(id, res)
      ws.send(JSON.stringify({ id, method, params }))
    })
  const evalJs = async (expr) => {
    const res = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
    if (res?.exceptionDetails) return undefined
    return res?.result?.value
  }

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1200,
    deviceScaleFactor: 1,
    mobile: false,
  })

  // 预热：等应用挂载（首次导航可能碰上 Vite 依赖优化导致的白屏）
  await send('Page.navigate', { url: BASE })
  for (let i = 0; i < 60; i++) {
    if (await evalJs(`(document.getElementById('app')?.childElementCount ?? 0) > 0`)) break
    await sleep(500)
  }

  const snapshot = { base: BASE, routes: {}, capturedAt: new Date().toISOString() }

  const slug = await resolveSlug(BASE)
  console.log(slug ? `详情页 slug: ${slug}` : '未取到文章 slug，详情页将跳过')

  for (const rawRoute of ROUTES) {
    if (rawRoute.includes('__SLUG__') && !slug) continue
    const route = rawRoute.replace('__SLUG__', encodeURIComponent(slug ?? ''))
    await send('Page.navigate', { url: `${BASE}${route}` })
    await sleep(2000)
    const data = await evalJs(`(() => {
      const SELECTORS = ${JSON.stringify(SELECTORS)}
      const PROPS = ${JSON.stringify(PROPS)}
      const stylesOf = (el) => {
        const cs = getComputedStyle(el)
        const out = {}
        for (const p of PROPS) out[p] = cs[p]
        return out
      }
      const result = {}
      for (const sel of SELECTORS) {
        const nodes = [...document.querySelectorAll(sel)].slice(0, 4)
        result[sel] = nodes.map((el, index) => ({
          index,
          tag: el.tagName.toLowerCase(),
          textLength: (el.textContent ?? '').trim().length,
          styles: stylesOf(el),
        }))
      }
      return result
    })()`)
    snapshot.routes[rawRoute] = data ?? {}
  }

  writeFileSync(OUT, JSON.stringify(snapshot, null, 1), 'utf-8')
  const count = Object.values(snapshot.routes).reduce(
    (sum, r) => sum + Object.values(r).reduce((s, arr) => s + (arr?.length ?? 0), 0),
    0,
  )
  console.log(`已写入基线: ${OUT}（${ROUTES.length} 个路由 / ${count} 条元素样式）`)
  ws.close()
} finally {
  chrome.kill()
}
