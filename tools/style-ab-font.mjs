/**
 * 针对性 A/B：证明「--font-mono 修复」是唯一影响渲染的改动。
 *
 * 做法：临时让 --font-mono 变成未定义（复现修复前的行为），采集详情页角标样式，
 * 再还原定义并重新采集，比对差异。预期差异**只有** .code-block__lang 的 font-family。
 *
 * 为什么需要它：全量基线比对覆盖的 7 个路由里没有代码块，
 * 而这次唯一有意的视觉修正恰好只影响代码块角标。不做这个 A/B 就等于
 * 「最该验证的地方没验证」。
 *
 * 用法：node tools/style-ab-font.mjs <baseUrl>
 */
import { spawn } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const BASE = process.argv[2] ?? 'http://127.0.0.1:5173'
const TOKENS = 'frontend/src/styles/tokens.css'
const PORT = 9241
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const ORIGINAL = readFileSync(TOKENS, 'utf-8')

/** 把 --font-mono 定义换成「未定义」的等价形态：直接删掉那一行定义。 */
function breakFontToken() {
  const broken = ORIGINAL.replace(/^\s*--font-mono:[\s\S]*?;\n/m, '')
  if (broken === ORIGINAL) throw new Error('未能定位 --font-mono 定义，A/B 中止')
  writeFileSync(TOKENS, broken, 'utf-8')
}
function restoreFontToken() {
  writeFileSync(TOKENS, ORIGINAL, 'utf-8')
}

async function getWsUrl(port) {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch {
      /* 轮询 */
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
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${join(tmpdir(), `style-ab-${Date.now()}`)}`,
    '--window-size=1440,1200',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

try {
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
    return res?.exceptionDetails ? undefined : res?.result?.value
  }

  await send('Page.enable')
  await send('Runtime.enable')

  const first = await (await fetch(`${BASE}/api/v1/articles?page_size=1`)).json()
  const slug = first.items[0].slug

  /** 采集详情页上代码块两个角标的字体与常用样式。 */
  async function capture(label) {
    // 等 HMR 把样式改动应用下去
    await send('Page.navigate', { url: `${BASE}/article/${encodeURIComponent(slug)}` })
    await sleep(2500)
    const data = await evalJs(`(() => {
      const pick = (el) => {
        if (!el) return null
        const cs = getComputedStyle(el)
        return {
          fontFamily: cs.fontFamily,
          fontSize: cs.fontSize,
          lineHeight: cs.lineHeight,
          borderRadius: cs.borderTopLeftRadius,
          color: cs.color,
          backgroundColor: cs.backgroundColor,
          position: cs.position,
        }
      }
      const block = document.querySelector('[class*="code-block"]')
      if (!block) return { found: false }
      return {
        found: true,
        lang: pick(block.querySelector('[class*="__lang"]')),
        copy: pick(block.querySelector('[class*="__copy"]')),
      }
    })()`)
    console.log(`  ${label}: ${JSON.stringify(data)}`)
    return data
  }

  console.log('1) 复现修复前（--font-mono 未定义）')
  breakFontToken()
  await sleep(2000)
  const before = await capture('修复前')

  console.log('2) 还原修复后（--font-mono 已定义）')
  restoreFontToken()
  await sleep(2000)
  const after = await capture('修复后')

  console.log('')
  if (!before?.found || !after?.found) {
    console.log('⚠️  未找到代码块角标，A/B 无法判定')
    process.exitCode = 1
  } else {
    const diffs = []
    for (const part of ['lang', 'copy']) {
      for (const key of Object.keys(before[part] ?? {})) {
        if (before[part][key] !== after[part][key]) {
          diffs.push(`${part}.${key}: ${before[part][key]}  →  ${after[part][key]}`)
        }
      }
    }
    console.log(`差异 ${diffs.length} 处：`)
    for (const d of diffs) console.log('  -', d)
    const onlyFont = diffs.every((d) => d.includes('fontFamily'))
    console.log(onlyFont ? '\n✅ 唯一差异是字体族（即本次有意修正）' : '\n⚠️  存在非字体差异，需人工判断')
  }
  ws.close()
} finally {
  restoreFontToken()
  chrome.kill()
}
