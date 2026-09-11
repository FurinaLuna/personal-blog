/**
 * 端到端冒烟验证：用 Chrome DevTools Protocol 驱动真实浏览器。
 *
 * 为什么不用 Playwright / Puppeteer：
 * 本机只要装了 Chrome 或 Edge 就能跑，不需要往项目里塞几百 MB 的浏览器自动化依赖。
 * Node 22 自带全局 WebSocket，直连 CDP 即可完成「打开页面 → 等渲染 → 取 DOM → 截图」。
 *
 * 这一层不是可选项。本项目有两个 bug 是**单元测试全绿、类型检查全过、构建成功**，
 * 但真实打开页面就是坏的：
 *   1. 登录后访问后台被莫名弹回登录页（restore() 的并发竞态）；
 *   2. 后台侧边栏渲染两遍、子页面完全不显示（布局被 meta 和路由嵌套双重包裹）。
 * 详见 docs/DESIGN.md 第 6.4 节。
 *
 * 前置条件：后端与前端 dev server 都已在运行。
 *
 * 用法：
 *   node tools/smoke-check.mjs [baseUrl] [outDir]
 *   node tools/smoke-check.mjs http://127.0.0.1:5173 ./smoke-shots
 *
 * 退出码：全部通过为 0，有失败为 1（可直接用于 CI）。
 */

import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE_URL = process.argv[2] ?? 'http://127.0.0.1:5173'
const OUT_DIR = process.argv[3] ?? join(tmpdir(), 'blog-smoke')
const DEBUG_PORT = 9333

const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
]

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function waitForDevTools(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`)
      if (response.ok) return await response.json()
    } catch {
      /* 还没起来，继续等 */
    }
    await sleep(300)
  }
  throw new Error('Chrome DevTools 端口未就绪')
}

/** 极简 CDP 客户端：只实现我们需要的几个命令。 */
class Cdp {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.events = new Map()
    this.consoleMessages = []
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
    this.networkLog = []
  }

  /** 订阅控制台与未捕获异常，冒烟过程中真实收集，而不是靠页面自己埋点 */
  attachDiagnostics() {
    this.on('Runtime.consoleAPICalled', (p) => {
      const text = p.args.map((a) => a.value ?? a.description ?? '').join(' ')
      if (p.type === 'error' || p.type === 'warning') {
        this.consoleMessages.push(`[${p.type}] ${text}`)
      }
    })
    this.on('Runtime.exceptionThrown', (p) => {
      this.consoleMessages.push(
        `[exception] ${p.exceptionDetails.text} ${p.exceptionDetails.exception?.description ?? ''}`,
      )
    })
    // 网络追踪：只关心 API 请求，用来确认「守卫到底有没有发出 /auth/me、返回了什么」
    this.on('Network.requestWillBeSent', (p) => {
      if (p.request.url.includes('/api/')) {
        this.networkLog.push({
          id: p.requestId,
          url: p.request.url.replace(/^https?:\/\/[^/]+/, ''),
          method: p.request.method,
          status: null,
        })
      }
    })
    this.on('Network.responseReceived', (p) => {
      if (!p.response.url.includes('/api/')) return
      const entry = this.networkLog.find((e) => e.id === p.requestId && e.status === null)
      if (entry) entry.status = p.response.status
    })
    this.on('Network.loadingFailed', (p) => {
      const entry = this.networkLog.find((e) => e.id === p.requestId && e.status === null)
      if (entry) entry.status = `FAILED: ${p.errorText}`
    })
  }

  static async connect(url) {
    const ws = new WebSocket(url)
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true })
      ws.addEventListener('error', () => reject(new Error('CDP WebSocket 连接失败')), { once: true })
    })
    return new Cdp(ws)
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

  waitFor(method, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`等待事件 ${method} 超时`)), timeoutMs)
      this.on(method, (params) => {
        clearTimeout(timer)
        resolve(params)
      })
    })
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
  if (result.exceptionDetails) {
    throw new Error(`页面内执行出错: ${result.exceptionDetails.text}`)
  }
  return result.result.value
}

async function capture(cdp, name) {
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  const file = join(OUT_DIR, `${name}.png`)
  writeFileSync(file, Buffer.from(shot.data, 'base64'))
  return file
}

async function openPage(wsUrl) {
  const cdp = await Cdp.connect(wsUrl)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  cdp.attachDiagnostics()
  // 每个 新文档 加载前先装上错误收集器，这样首屏渲染阶段的异常也不会漏
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source:
      'window.__smokeErrors = [];' +
      'window.addEventListener("error", e => window.__smokeErrors.push(String(e.message || e.error)));' +
      'window.addEventListener("unhandledrejection", e => window.__smokeErrors.push("unhandled: " + String(e.reason)));',
  })
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  })
  // headless Chrome 默认上报「系统偏好暗色」，站点会正确跟随——但截图看起来
  // 不像默认可视化。这里显式声明亮色偏好，截图才代表大多数用户的默认观感；
  // 暗色模式由后面的用例单独通过 localStorage 覆盖验证。
  await cdp.send('Emulation.setEmulatedMedia', {
    features: [{ name: 'prefers-color-scheme', value: 'light' }],
  })
  return cdp
}

/** 从 /json/list 里取一个可用的 page target 的 WebSocket 地址 */
async function newTarget(url) {
  const response = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/new?${encodeURIComponent(url)}`, {
    method: 'PUT',
  })
  if (!response.ok) throw new Error(`新建标签页失败: HTTP ${response.status}`)
  return (await response.json()).webSocketDebuggerUrl
}

async function navigate(cdp, url) {
  const loaded = cdp.waitFor('Page.loadEventFired')
  await cdp.send('Page.navigate', { url })
  await loaded
  // 等 Vue 挂载。真正的"页面就绪"由 waitFor() 按条件轮询，
  // 这里只给一个最短的起步时间。
  await sleep(800)
}

/**
 * 轮询等待页面满足条件，而不是死等固定时长。
 *
 * dev server 首次访问某个路由时要现编译该 chunk（Pinia store、组件、依赖整条链），
 * 冷启动可能要好几秒，热了之后又只要几十毫秒。固定 sleep 要么浪费时间、
 * 要么在冷启动时误判失败，所以必须用条件轮询。
 */
async function waitFor(cdp, expression, timeoutMs = 25000, label = expression) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await evaluate(cdp, `(() => { try { return !!(${expression}) } catch { return false } })()`)
    if (value) return true
    await sleep(400)
  }
  throw new Error(`等待条件超时: ${label}`)
}

const results = []

function record(name, ok, detail) {
  results.push({ name, ok, detail })
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`)
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true })

  const chromePath = CHROME_CANDIDATES.find((candidate) => existsSync(candidate))
  if (!chromePath) throw new Error('找不到 Chrome / Edge 可执行文件')

  const userDataDir = join(tmpdir(), `blog-smoke-profile-${Date.now()}`)
  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${userDataDir}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-gpu',
      '--hide-scrollbars',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  let cdp
  try {
    const version = await waitForDevTools()
    console.log(`浏览器: ${version.Browser}\n目标站点: ${BASE_URL}\n`)

    // ---------------------------------------------------------- 首页
    cdp = await openPage(await newTarget(`${BASE_URL}/`))
    await navigate(cdp, `${BASE_URL}/`)

    const home = await evaluate(
      cdp,
      `(() => {
        const cards = document.querySelectorAll('article')
        return {
          title: document.title,
          heading: document.querySelector('h1')?.textContent?.trim() ?? '',
          cardCount: cards.length,
          firstTitle: cards[0]?.querySelector('h2')?.textContent?.trim() ?? '',
          hasSidebar: !!document.querySelector('aside'),
          navLinks: Array.from(document.querySelectorAll('header nav a')).map(a => a.textContent.trim()),
          bodyText: document.body.innerText.slice(0, 200),
        }
      })()`,
    )
    record('首页渲染', home.cardCount > 0, `文章卡片 ${home.cardCount} 篇，首篇「${home.firstTitle}」`)
    record('站点标题', home.title.includes('首页'), home.title)
    record('侧边栏存在', home.hasSidebar, `导航项: ${home.navLinks.join(' / ')}`)
    ;(await capture(cdp, '01-home')) && record('首页截图', true, '01-home.png')

    // ---------------------------------------------------------- 搜索 / 排序（URL 驱动）
    await navigate(cdp, `${BASE_URL}/?sort=hottest&page=1`)
    const sorted = await evaluate(
      cdp,
      `Array.from(document.querySelectorAll('article h2')).map(h => h.textContent.trim())`,
    )
    record('排序参数生效', sorted.length > 0, `hottest 顺序: ${sorted.slice(0, 2).join(' | ')}`)

    // ---------------------------------------------------------- 文章详情页
    const slug = await evaluate(
      cdp,
      `document.querySelector('article h2 a')?.getAttribute('href') ?? ''`,
    )
    await navigate(cdp, `${BASE_URL}${slug}`)
    const detail = await evaluate(
      cdp,
      `(() => ({
        heading: document.querySelector('h1')?.textContent?.trim() ?? '',
        hasProse: !!document.querySelector('.prose'),
        codeBlocks: document.querySelectorAll('.prose pre code.hljs').length,
        headings: document.querySelectorAll('.prose h2, .prose h3').length,
        tocItems: document.querySelectorAll('nav[aria-label="文章目录"] button').length,
        commentSection: !!document.querySelector('#comments'),
        likes: !!document.querySelector('#comments'),
      }))()`,
    )
    record('详情页正文渲染', detail.hasProse, `标题「${detail.heading}」`)
    record(
      'Markdown 代码高亮',
      detail.codeBlocks > 0,
      `${detail.codeBlocks} 个高亮代码块，${detail.headings} 个标题`,
    )
    record('目录生成', detail.tocItems > 0, `${detail.tocItems} 个目录项`)
    record('评论区挂载', detail.commentSection, '')
    ;(await capture(cdp, '02-detail')) && record('详情页截图', true, '02-detail.png')

    // ---------------------------------------------------------- 归档页
    await navigate(cdp, `${BASE_URL}/archive`)
    const archive = await evaluate(
      cdp,
      `(() => ({
        months: Array.from(document.querySelectorAll('section h2')).map(h => h.textContent.trim()),
        links: document.querySelectorAll('a[href^="/article/"]').length,
      }))()`,
    )
    record('归档页分组', archive.months.length > 0, `月份: ${archive.months.join(', ')}，${archive.links} 篇文章`)

    // ---------------------------------------------------------- 标签页
    await navigate(cdp, `${BASE_URL}/tags`)
    const tags = await evaluate(cdp, `document.querySelectorAll('a[href^="/?"]').length`)
    record('标签云', tags > 0, `${tags} 个标签`)

    // ---------------------------------------------------------- 关于页
    await navigate(cdp, `${BASE_URL}/about`)
    const about = await evaluate(
      cdp,
      `document.querySelector('.prose')?.innerText?.slice(0, 60) ?? ''`,
    )
    record('关于页内容来自站点档案', about.length > 0, about.replace(/\n/g, ' '))

    // ---------------------------------------------------------- 404
    await navigate(cdp, `${BASE_URL}/this-page-does-not-exist`)
    const notFound = await evaluate(cdp, `document.body.innerText.includes('404')`)
    record('404 兜底页', notFound, '')

    // ---------------------------------------------------------- 登录 → 后台
    await navigate(cdp, `${BASE_URL}/login`)
    const loginUi = await evaluate(
      cdp,
      `(() => ({
        hasUser: !!document.querySelector('#username'),
        hasPass: !!document.querySelector('#password'),
        heading: document.querySelector('h1')?.textContent?.trim() ?? '',
      }))()`,
    )
    record('登录页渲染', loginUi.hasUser && loginUi.hasPass, loginUi.heading)
    ;(await capture(cdp, '03-login')) && record('登录页截图', true, '03-login.png')

    // 记录登录前的位置，便于只看 /admin 阶段的请求
    const adminMark = cdp.networkLog.length

    // 通过页面内的 fetch 走完整登录流程，并把 token 写进 localStorage
    // （等价于用户手动填表提交，只是省略了逐步输入）
    const loginResult = await evaluate(
      cdp,
      `(async () => {
        const res = await fetch('/api/v1/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: 'admin', password: 'admin123456' }),
        })
        if (!res.ok) return { ok: false, status: res.status }
        const data = await res.json()
        localStorage.setItem('blog-access-token', data.access_token)
        localStorage.setItem('blog-refresh-token', data.refresh_token)
        return { ok: true, expiresIn: data.expires_in }
      })()`,
    )
    record('登录接口', loginResult.ok, `token 有效期 ${loginResult.expiresIn}s`)

    await navigate(cdp, `${BASE_URL}/admin`)
    await waitFor(
      cdp,
      `location.pathname.startsWith('/admin') && document.querySelector('h1')`,
      25000,
      '仪表盘渲染',
    )
    const dashboard = await evaluate(
      cdp,
      `(() => ({
        url: location.pathname + location.search,
        heading: document.querySelector('h1')?.textContent?.trim() ?? '',
        cards: document.querySelectorAll('.card').length,
        text: document.body.innerText.slice(0, 300),
        hasStats: document.body.innerText.includes('总阅读量'),
        token: !!localStorage.getItem('blog-access-token'),
      }))()`,
    )
    record(
      '后台仪表盘',
      dashboard.url.startsWith('/admin') && dashboard.heading === '仪表盘',
      `url=${dashboard.url}，标题「${dashboard.heading}」，token=${dashboard.token ? '在' : '无'}`,
    )
    // 统计数字要等 /site/stats 回来，同样用轮询
    await waitFor(cdp, `document.body.innerText.includes('总阅读量')`, 20000, '统计数字').catch(() => {})
    const statsLoaded = await evaluate(cdp, `document.body.innerText.includes('总阅读量')`)
    record('统计数字加载', statsLoaded, '')
    if (!dashboard.url.startsWith('/admin')) {
      console.log('\n  [/admin 阶段 API 请求]')
      for (const entry of cdp.networkLog.slice(adminMark)) {
        console.log(`    ${entry.method} ${entry.url} → ${entry.status}`)
      }
    }
    ;(await capture(cdp, '04-admin-dashboard')) && record('仪表盘截图', true, '04-admin-dashboard.png')

    // ---------------------------------------------------------- 后台文章列表
    await navigate(cdp, `${BASE_URL}/admin/articles`)
    await waitFor(
      cdp,
      `document.querySelector('table tbody tr') || document.body.innerText.includes('没有匹配的文章')`,
      25000,
      '文章列表渲染',
    )
    const adminArticles = await evaluate(
      cdp,
      `(() => ({
        rows: document.querySelectorAll('table tbody tr').length,
        hasTable: !!document.querySelector('table'),
      }))()`,
    )
    record('后台文章列表', adminArticles.hasTable, `${adminArticles.rows} 行`)
    ;(await capture(cdp, '05-admin-articles')) && record('文章列表截图', true, '05-admin-articles.png')

    // ---------------------------------------------------------- 后台编辑器
    await navigate(cdp, `${BASE_URL}/admin/articles/new`)
    await waitFor(
      cdp,
      `document.querySelector('input[placeholder="文章标题"]') && document.querySelector('textarea')`,
      25000,
      '编辑器挂载',
    )
    const editor = await evaluate(
      cdp,
      `(() => ({
        hasTextarea: !!document.querySelector('textarea'),
        toolbarButtons: document.querySelectorAll('button').length,
        hasTitleInput: !!document.querySelector('input[placeholder="文章标题"]'),
      }))()`,
    )
    record('后台编辑器', editor.hasTextarea && editor.hasTitleInput, `${editor.toolbarButtons} 个按钮`)
    ;(await capture(cdp, '06-admin-editor')) && record('编辑器截图', true, '06-admin-editor.png')

    // ---------------------------------------------------------- 分类标签 / 媒体库 / 用户 / 设置
    for (const [path, name, marker] of [
      ['/admin/taxonomy', '分类标签管理', '新建分类'],
      ['/admin/media', '媒体库', '媒体库'],
      ['/admin/users', '用户管理', '新建用户'],
      ['/admin/settings', '站点设置', '保存设置'],
      ['/admin/comments', '评论管理', '待审核'],
    ]) {
      await navigate(cdp, `${BASE_URL}${path}`)
      await waitFor(cdp, `document.body.innerText.includes(${JSON.stringify(marker)})`, 25000, `${name} 渲染`)
      const text = await evaluate(cdp, 'document.body.innerText')
      record(`后台「${name}」页`, text.includes(marker), `路径 ${path}`)
    }
    await capture(cdp, '07-admin-settings')

    // ---------------------------------------------------------- 暗色主题
    await evaluate(cdp, `localStorage.setItem('blog-theme','dark')`)
    await navigate(cdp, `${BASE_URL}/`)
    const dark = await evaluate(
      cdp,
      `(() => ({
        isDark: document.documentElement.classList.contains('dark'),
        bg: getComputedStyle(document.body).backgroundColor,
        color: getComputedStyle(document.body).color,
      }))()`,
    )
    record('暗色主题', dark.isDark, `背景 ${dark.bg}，文字 ${dark.color}`)
    ;(await capture(cdp, '08-home-dark')) && record('暗色截图', true, '08-home-dark.png')

    // ---------------------------------------------------------- 移动端
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true,
    })
    await navigate(cdp, `${BASE_URL}/`)
    const mobile = await evaluate(
      cdp,
      `(() => {
        const burger = Array.from(document.querySelectorAll('header button'))
          .find(b => b.getAttribute('aria-label') === '切换导航菜单')
        return { hasBurger: !!burger, burgerVisible: burger ? getComputedStyle(burger).display !== 'none' : false }
      })()`,
    )
    record('移动端汉堡菜单', mobile.hasBurger && mobile.burgerVisible, '')
    ;(await capture(cdp, '09-home-mobile')) && record('移动端截图', true, '09-home-mobile.png')

    // ---------------------------------------------------------- 控制台错误
    const pageErrors = await evaluate(cdp, `window.__smokeErrors ?? []`)
    const allErrors = [...pageErrors, ...cdp.consoleMessages]
    record('页面脚本错误', allErrors.length === 0, allErrors.slice(0, 3).join(' | ') || '无')

    // ---------------------------------------------------------- 汇总
    const failed = results.filter((item) => !item.ok)
    console.log(`\n${'='.repeat(56)}`)
    console.log(`通过 ${results.length - failed.length} / ${results.length}`)
    console.log(`截图目录: ${OUT_DIR}`)
    if (allErrors.length) {
      console.log('\n页面错误/警告:')
      for (const line of allErrors.slice(0, 10)) console.log(`  - ${line.slice(0, 200)}`)
    }
    if (failed.length) {
      console.log('\n失败项:')
      for (const item of failed) console.log(`  - ${item.name} ${item.detail}`)
      process.exitCode = 1
    }
  } finally {
    cdp?.close()
    chrome.kill()
  }
}

main().catch((error) => {
  console.error('冒烟验证失败:', error.message)
  process.exitCode = 1
})
