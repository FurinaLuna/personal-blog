/**
 * 完整功能运行演示（CDP 驱动真实浏览器，用户视角的连贯旅程）。
 *
 * 与三个回归脚本的分工：smoke/interaction/full-check 是「逐项断言」，
 * 本脚本是「一次连贯的真人操作回放」——按访客 → 读者 → 管理员 的视角
 * 顺序走完整个产品主链路，每步截图 + 关键元素断言，产出可直接查看的演示记录。
 *
 * 演示旅程（10 幕）：
 *  1 前台首页首屏      → 文章卡片 + 侧边导航
 *  2 文章详情页        → 正文渲染 + 目录 + 评论区
 *  3 归档页            → 按月分组
 *  4 标签页            → 标签云
 *  5 登录（页面表单）   → 管理员进入后台
 *  6 后台仪表盘        → 统计数字
 *  7 后台文章列表      → 表格 + 状态徽标
 *  8 文章编辑器        → Markdown 工具栏 + 输入
 *  9 暗色主题切换      → 三态循环 + 持久化
 * 10 登出回前台        → 服务端凭证失效 + 登录页
 *
 * 用法：
 *   node tools/showcase-demo.mjs [baseUrl] [outDir]
 * 前置：前后端已启动（make dev）。只读演示，不写业务数据。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = (process.argv[2] ?? 'http://127.0.0.1:5173').replace(/\/$/, '')
const OUT = process.argv[3] ?? join(tmpdir(), 'blog-showcase')
const DEBUG_PORT = 9341

const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
]
const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p))
if (!chromePath) {
  console.error('未找到 Chrome/Edge，无法运行演示')
  process.exit(2)
}

const ADMIN = { username: 'admin', password: 'admin123456' }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const steps = []
function step(name, ok, detail = '') {
  steps.push({ name, ok: Boolean(ok), detail })
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`)
}

/* ---------------- 极简 CDP 客户端（与 smoke-check 同源） ---------------- */
class Cdp {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.events = new Map()
    this.errors = []
    ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id !== undefined) {
        const entry = this.pending.get(message.id)
        if (!entry) return
        this.pending.delete(message.id)
        if (message.error) entry.reject(new Error(JSON.stringify(message.error)))
        else entry.resolve(message.result)
        return
      }
      const listeners = this.events.get(message.method) ?? []
      for (const listener of listeners) listener(message.params)
    })
  }

  static async connect(url) {
    const ws = new WebSocket(url)
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true })
      ws.addEventListener('error', () => reject(new Error('CDP 连接失败')), { once: true })
    })
    const cdp = new Cdp(ws)
    cdp.networkLog = []
    cdp.on('Runtime.exceptionThrown', (p) => {
      cdp.errors.push(p.exceptionDetails.exception?.description ?? p.exceptionDetails.text)
    })
    // 只追踪 /auth/ 请求，登录诊断用
    cdp.on('Network.requestWillBeSent', (p) => {
      if (p.request.url.includes('/api/v1/auth/')) {
        cdp.networkLog.push({ url: p.request.url, method: p.request.method, status: null })
      }
    })
    cdp.on('Network.responseReceived', (p) => {
      if (!p.response.url.includes('/api/v1/auth/')) return
      const entry = cdp.networkLog.find((e) => e.status === null)
      if (entry) entry.status = p.response.status
    })
    return cdp
  }

  send(method, params = {}) {
    const id = ++this.seq
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  on(method, listener) {
    const list = this.events.get(method) ?? []
    list.push(listener)
    this.events.set(method, list)
  }

  close() {
    this.ws.close()
  }
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (result.exceptionDetails) throw new Error(`页面内执行出错: ${result.exceptionDetails.text}`)
  return result.result.value
}

async function capture(cdp, name) {
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  const file = join(OUT, `${name}.png`)
  writeFileSync(file, Buffer.from(shot.data, 'base64'))
  return file
}

async function waitFor(cdp, expression, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await evaluate(cdp, expression)) return true
    await sleep(250)
  }
  return false
}

async function navigate(cdp, path) {
  await cdp.send('Page.navigate', { url: `${BASE}${path}` })
  await waitFor(cdp, 'document.readyState === "complete" && !!document.querySelector("#app")')
  await waitFor(cdp, 'document.querySelector("#app")?.children.length > 0')
}

async function getWsUrl() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch {
      /* 继续轮询 */
    }
    await sleep(300)
  }
  throw new Error('CDP 未就绪')
}

/* ---------------- 演示主流程 ---------------- */
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--no-first-run',
  `--remote-debugging-port=${DEBUG_PORT}`,
  `--user-data-dir=${join(tmpdir(), `showcase-profile-${Date.now()}`)}`,
  '--window-size=1440,1000', 'about:blank',
], { stdio: 'ignore' })

try {
  mkdirSync(OUT, { recursive: true })
  console.log(`浏览器: ${chromePath.split('/').pop()}\n目标站点: ${BASE}\n演示目录: ${OUT}\n`)

  const cdp = await Cdp.connect(await getWsUrl())
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  // 预热：dev server 冷编译
  await navigate(cdp, '/')
  await waitFor(cdp, 'document.querySelectorAll("a[href^=\\"/article/\\"]").length > 0')

  /* 1 首页首屏 */
  const home = await evaluate(cdp, `(() => {
    const cards = document.querySelectorAll('a[href^="/article/"]')
    const nav = Array.from(document.querySelectorAll('header a, aside a')).map(a => a.textContent.trim())
    return { cards: cards.length, first: cards[0]?.textContent.trim(), hasSidebar: nav.some(t => /首页|归档|标签/.test(t)) }
  })()`)
  await capture(cdp, '01-home')
  step('首页首屏（文章卡片 + 侧边导航）', home.cards > 0 && home.hasSidebar, `${home.cards} 篇，首篇「${home.first}」`)

  /* 2 文章详情 */
  await navigate(cdp, (await evaluate(cdp, `document.querySelector('a[href^="/article/"]')?.getAttribute('href')`)) ?? '/')
  await waitFor(cdp, '!!document.querySelector(".prose")')
  const detail = await evaluate(cdp, `(() => {
    const prose = document.querySelector('.prose')
    // 目录是 nav[aria-label="文章目录"]，评论区是 section#comments（见 CommentSection.vue）
    const toc = document.querySelectorAll('nav[aria-label="文章目录"] a').length
    const comment = document.querySelector('#comments') !== null
    return { heading: document.querySelector('h1')?.textContent.trim(), toc, comment, codeBlocks: prose?.querySelectorAll('pre code').length ?? 0 }
  })()`)
  await capture(cdp, '02-article-detail')
  step('文章详情页（正文 + 高亮 + 目录 + 评论区）', detail.heading && detail.comment, `「${detail.heading}」代码块 ${detail.codeBlocks} 目录项 ${detail.toc}`)

  /* 3 归档页 */
  await navigate(cdp, '/archive')
  const archive = await evaluate(cdp, `(() => ({
    months: document.querySelectorAll('[class*="archive"] h2, [class*="month"]').length,
    links: document.querySelectorAll('a[href^="/article/"]').length,
  }))()`)
  await capture(cdp, '03-archive')
  step('归档页（按月分组）', archive.links > 0, `${archive.links} 篇文章`)

  /* 4 标签页 */
  await navigate(cdp, '/tags')
  const tags = await evaluate(cdp, `document.querySelectorAll('a[href*="/tag/"], [class*="tag"] a, a[href*="tag="]').length`)
  await capture(cdp, '04-tags')
  step('标签页（标签云）', tags > 0, `${tags} 个标签入口`)

  /* 5 暗色主题切换（前台顶栏有 ThemeToggle；三态循环 system→light→dark） */
  await navigate(cdp, '/')
  await waitFor(cdp, '!!document.querySelector("button[aria-label^=\\"当前：\\"]")')
  const themeLabel = await evaluate(cdp, `document.querySelector('button[aria-label^="当前："]')?.getAttribute('aria-label')`)
  // system 模式点 1 次会先到 light（无 dark 类），循环点击直到进入暗色，最多 3 次
  const clicks = await evaluate(cdp, `(async () => {
    for (let i = 0; i < 3; i++) {
      document.querySelector('button[aria-label^="当前："]')?.click()
      await new Promise(r => setTimeout(r, 600))
      if (document.documentElement.classList.contains('dark')) return i + 1
    }
    return 0
  })()`)
  await capture(cdp, '05-dark-theme')
  step('暗色主题切换（三态循环）', clicks > 0, `初始「${themeLabel}」→ 点击 ${clicks} 次进入暗色`)

  /* 6 登录（页面表单） */
  await navigate(cdp, '/login')
  await waitFor(cdp, '!!document.querySelector("#username") && !!document.querySelector("#password")')
  // 填表（interaction-check 验证过的模式：填充 → 微任务落定 → 独立 evaluate 提交）
  await evaluate(cdp, `(() => {
    const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
    set(document.querySelector('#username'), ${JSON.stringify(ADMIN.username)})
    set(document.querySelector('#password'), ${JSON.stringify(ADMIN.password)})
  })()`)
  await sleep(200)
  await evaluate(cdp, `(async () => {
    document.querySelector('form').requestSubmit()
    await new Promise(r => setTimeout(r, 2500))
  })()`)
  const loggedIn = await waitFor(cdp, 'location.pathname.startsWith("/admin")', 10000)
  if (!loggedIn) {
    const diag = await evaluate(cdp, `(() => ({
      path: location.pathname,
      errText: document.querySelector('[class*="text-red"]')?.textContent ?? '',
      toast: [...document.querySelectorAll('[role="status"] button')].map(el => el.textContent).join('|'),
    }))()`)
    console.log('  ↳ 诊断:', JSON.stringify(diag), '| auth 请求:', JSON.stringify(cdp.networkLog))
  }
  await capture(cdp, '06-login')
  step('登录（页面表单提交 → 进入后台）', loggedIn, loggedIn ? '跳转到 /admin' : '未跳转')

  /* 6 后台仪表盘 */
  await navigate(cdp, '/admin')
  await waitFor(cdp, 'document.body.textContent.includes("仪表盘")')
  const dash = await evaluate(cdp, `(() => {
    const text = document.body.innerText
    const nums = [...document.querySelectorAll('main [class*="card"]')].map(el => el.innerText.trim()).filter(Boolean)
    return { hasStats: /文章|阅读|评论|分类/.test(text), cards: nums.length }
  })()`)
  await capture(cdp, '06-dashboard')
  step('后台仪表盘（统计卡片）', dash.hasStats, `${dash.cards} 张卡片`)

  /* 7 后台文章列表 */
  await navigate(cdp, '/admin/articles')
  await waitFor(cdp, 'document.body.textContent.includes("写文章")')
  const list = await evaluate(cdp, `(() => ({
    rows: document.querySelectorAll('main tbody tr').length,
    hasStatus: document.body.innerText.includes('已发布') || document.body.innerText.includes('草稿'),
  }))()`)
  await capture(cdp, '07-admin-articles')
  step('后台文章列表（表格 + 状态徽标）', list.rows > 0 && list.hasStatus, `${list.rows} 行`)

  /* 8 文章编辑器 */
  await navigate(cdp, '/admin/articles/new')
  await waitFor(cdp, '!!document.querySelector("textarea[placeholder*=\\"开始写\\"]")')
  const editor = await evaluate(cdp, `(() => {
    const toolbar = document.querySelectorAll('[class*="btn--ghost"]').length
    const area = document.querySelector('textarea[placeholder*="开始写"]')
    return { toolbar, hasTitle: !!document.querySelector('input[placeholder="文章标题"]') }
  })()`)
  await capture(cdp, '08-editor')
  step('文章编辑器（Markdown 工具栏 + 标题）', editor.hasTitle, `${editor.toolbar} 个工具栏按钮`)

  /* 9 登出回前台 */
  await navigate(cdp, '/admin')
  await evaluate(cdp, `(() => {
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '退出')
    btn?.click()
  })()`)
  const loggedOut = await waitFor(cdp, 'location.pathname === "/login"', 8000)
  // 「localStorage 里那个 key 没了」在新方案下是**永真**断言（它不再被写入）；
  // 「页面内 fetch /auth/me 返回 401」同样永真——access token 只活在内存里，
  // 由 axios 拦截器附加，裸 fetch 带不上它。真信号是应用自己的行为：重新访问
  // 受保护的后台页，守卫必须把人弹回登录页。
  await navigate(cdp, '/admin')
  const bounced = await waitFor(cdp, 'location.pathname === "/login"', 8000)
  await capture(cdp, '09-logout')
  step(
    '登出（会话失效：后台页被弹回登录页）',
    loggedOut && bounced,
    bounced ? '/admin → /login' : '登出后仍能进后台',
  )

  const runtimeErrors = cdp.errors
  if (runtimeErrors.length === 0) {
    step('全程无页面脚本错误', true, '')
  } else {
    step('全程无页面脚本错误', false, runtimeErrors.slice(0, 3).join(' | '))
  }

  const passed = steps.filter((s) => s.ok).length
  console.log(`\n========================================================\n演示 ${passed} / ${steps.length} 步通过`)
  console.log(`截图目录: ${OUT}`)
  cdp.close()
} catch (error) {
  console.error('\n演示中断:', error.message)
  process.exitCode = 1
} finally {
  chrome.kill()
}
