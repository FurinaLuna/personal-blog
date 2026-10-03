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
 *   node tools/smoke-check.mjs http://127.0.0.1:5173 ./shots/smoke
 *
 * 退出码：全部通过为 0，有失败为 1（可直接用于 CI）。
 */

import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { chromeArgs, findChrome } from './lib/chrome.mjs'

const BASE_URL = process.argv[2] ?? 'http://127.0.0.1:5173'
const OUT_DIR = process.argv[3] ?? join(tmpdir(), 'blog-smoke')
const DEBUG_PORT = 9333

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

  const chromePath = findChrome()
  const userDataDir = join(tmpdir(), `blog-smoke-profile-${Date.now()}`)
  const chrome = spawn(
    chromePath,
    chromeArgs({ port: DEBUG_PORT, userDataDir, hideScrollbars: true }),
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
          // 分类列表的可滚性：分类多起来（本站 19 个）会把侧栏撑到比视口还高，
          // 而侧栏自身不可滚 —— 那时「标签」「快捷入口」在首屏被截掉、
          // 分类列表末尾也够不到。所以给这一块加上限 + 自身滚动，这里守住它。
          // 判据取「有上限」「overflow 是 auto/scroll」「内容确实超出」三件事：
          // 只断言类名的话，类名对了但规则没生效（或上限大到不触发）照样是坏的。
          //
          // 「内容确实超出」有一条环境边界（CI 上真实踩到，run #68 起稳定红）：
          // 本断言跑在**种子库**上（CI 每次全新，只有 3 个分类，104px < 320px 上限），
          // 而完整验证它需要分类多到超出上限（本地正式库 19 个分类即可）。
          // 所以「未超出」时不判失败，改为说明性通过；机制三件套（上限/overflow/
          // overscroll）在任何数据量下都必须成立，仍然必查。
          catList: (() => {
            const aside = document.querySelector('aside')
            const ul = aside ? aside.querySelector('div:first-child ul') : null
            if (!ul) return null
            const cs = getComputedStyle(ul)
            return {
              maxHeight: cs.maxHeight,
              overflowY: cs.overflowY,
              overscroll: cs.overscrollBehaviorY,
              scrollHeight: ul.scrollHeight,
              clientHeight: ul.clientHeight,
              itemCount: ul.children.length,
              hasCap: cs.maxHeight !== 'none',
              scrollable: ul.scrollHeight > ul.clientHeight,
            }
          })(),
          navLinks: Array.from(document.querySelectorAll('header nav a')).map(a => a.textContent.trim()),
          bodyText: document.body.innerText.slice(0, 200),
        }
      })()`,
    )
    record('首页渲染', home.cardCount > 0, `文章卡片 ${home.cardCount} 篇，首篇「${home.firstTitle}」`)
    record('站点标题', home.title.includes('首页'), home.title)
    record('侧边栏存在', home.hasSidebar, `导航项: ${home.navLinks.join(' / ')}`)
    record(
      'E-H1 首页分类列表独立滚动（有高度上限 + 自身可滚 + 不串联页面）',
      home.catList !== null &&
        home.catList.hasCap === true &&
        ['auto', 'scroll'].includes(home.catList.overflowY) &&
        home.catList.overscroll === 'contain',
      home.catList === null
        ? '找不到分类列表（aside > div > ul）'
        : `分类列表 ${home.catList.scrollHeight}/${home.catList.clientHeight}` +
          `，max-height=${home.catList.maxHeight}，overflow-y=${home.catList.overflowY}` +
          `，overscroll=${home.catList.overscroll}` +
          `${home.catList.hasCap ? '' : ' ❌ 没有高度上限'}` +
          (home.catList.scrollable
            ? ''
            : ` — 分类仅 ${home.catList.itemCount} 项未达上限，滚动分支由分类充足的环境覆盖（机制断言已过）`),
    )
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
    // 注：这里原本还有一个 likes 字段，值也指向 #comments（复制粘贴遗留），
    // 且从未被任何断言消费，已删除。点赞交互有副作用（自增且无 unlike 接口），
    // 改由 tools/full-check.mjs 的 D1 用例在临时文章上真实点击并回收。
    const detail = await evaluate(
      cdp,
      `(() => ({
        heading: document.querySelector('h1')?.textContent?.trim() ?? '',
        hasProse: !!document.querySelector('.prose'),
        codeBlocks: document.querySelectorAll('.prose pre code.hljs').length,
        headings: document.querySelectorAll('.prose h2, .prose h3').length,
        // 目录项已从 button 改为锚点链接（章节可深链），选择器跟随着组件实现
        tocItems: document.querySelectorAll('nav[aria-label="文章目录"] a[href^="#"]').length,
        commentSection: !!document.querySelector('#comments'),
      }))()`,
    )
    record('详情页正文渲染', detail.hasProse, `标题「${detail.heading}」`)
    record(
      'Markdown 代码高亮',
      detail.codeBlocks > 0,
      `${detail.codeBlocks} 个高亮代码块，${detail.headings} 个标题`,
    )
    record('目录生成', detail.tocItems > 0, `${detail.tocItems} 个目录项`)

    // ---------------------------------------------------------- 目录折叠（本轮目录改造）
    //
    // 判据刻意不用"按钮点了有没有反应"：那对"按钮在、但什么都不做"的实现是绿的。
    // 这里同时看三件事 —— 条目数真的变化、按钮文案随之翻转、折叠按钮的 aria-expanded
    // 与条目数互为证据（全部收起后每个折叠按钮都必须是 false）。
    //
    // 两分支是必要的：首页第一篇不一定是嵌套标题的文章（种子数据里有两篇只有 h2），
    // 而"没有可折叠节点"时正确行为是**幂等**（按钮点不动、条目数不变）。
    // 硬套"条目必须变少"会在那种文章上给出假红，所以按折叠按钮的数量分流，
    // 并在详情里写清楚走的是哪一支。
    //
    // 只读口径：折叠是纯客户端交互，不写数据；跑完先收起再展开，目录回到默认展开态。
    const tocFold = await evaluate(
      cdp,
      `(async () => {
        const nav = document.querySelector('nav[aria-label="文章目录"]')
        if (!nav) return { ok: false, reason: 'nav[aria-label="文章目录"] 未渲染' }
        const count = () => nav.querySelectorAll('a[data-toc-id]').length
        const toggles = () => [...nav.querySelectorAll('button[aria-expanded]')]
        const toggleAll = () => [...nav.querySelectorAll('button')].find(b => /全部(展开|收起)/.test(b.textContent.trim()))
        const all = toggleAll()
        if (!all) return { ok: false, reason: '找不到「全部展开/全部收起」按钮' }
        const before = count()
        const labelsValid = toggles().every(b => ['true', 'false'].includes(b.getAttribute('aria-expanded')))
        all.click()
        await new Promise(r => setTimeout(r, 500))
        const folded = {
          links: count(),
          label: toggleAll().textContent.trim(),
          allFalse: toggles().every(b => b.getAttribute('aria-expanded') === 'false'),
        }
        toggleAll().click()
        await new Promise(r => setTimeout(r, 700))
        const unfolded = { links: count(), label: toggleAll().textContent.trim() }
        return { ok: true, before, toggles: toggles().length, labelsValid, folded, unfolded }
      })()`,
    )
    // 走哪一支要写进详情里：否则"0 个折叠按钮"这条分支看起来和"折叠没生效"一模一样
    const tocFoldDetail =
      tocFold.ok !== true
        ? `（目录状态没读回来：${tocFold?.reason ?? '页面内异常'}）`
        : tocFold.toggles === 0
          ? '该文没有嵌套标题（0 个折叠按钮），开关按设计幂等'
          : `折叠按钮全为 false=${tocFold.folded.allFalse}`
    record(
      '目录折叠开关（全部收起 → 全部展开）',
      tocFold.ok === true &&
        tocFold.labelsValid === true &&
        tocFold.before > 0 &&
        // 收起之后按钮一定翻成「全部展开」：这一条两支都成立
        tocFold.folded.label === '全部展开' &&
        (tocFold.toggles === 0
          ? // 该文章没有嵌套标题：没有可折叠节点，开关点不动 —— 条目数不变、
            // 按钮**恒为**「全部展开」（allCollapsed 在没有可折叠节点时永远是 true），
            // 这里若照抄"展开后应变成全部收起"，对这类文章就是一条假红
            tocFold.folded.links === tocFold.before && tocFold.unfolded.links === tocFold.before
          : tocFold.folded.links < tocFold.before &&
            tocFold.folded.allFalse === true &&
            tocFold.unfolded.links >= tocFold.before &&
            tocFold.unfolded.label === '全部收起'),
      tocFold.ok !== true
        ? tocFold.reason
        : `折叠按钮 ${tocFold.toggles} 个（aria-expanded 取值合法=${tocFold.labelsValid}，${tocFoldDetail}）；` +
          `条目 ${tocFold.before} → 全部收起 ${tocFold.folded.links}，按钮=「${tocFold.folded.label}」` +
          `→ 全部展开 ${tocFold.unfolded.links}，按钮=「${tocFold.unfolded.label}」`,
    )
    record('评论区挂载', detail.commentSection, '')
    ;(await capture(cdp, '02-detail')) && record('详情页截图', true, '02-detail.png')

    // ---------------------------------------------------------- 电梯栏（返回顶部阈值 + 联系站长一致性）
    //
    // 阈值 300（决策 D-电梯-2）必须**双向**断言：只写"滚过之后出现了"的话，一个常驻渲染
    // 按钮的实现照样绿 —— 所以先证 scrollY=0 时它不存在，再证滚过之后出现，最后证它真的回顶。
    const elevator = await evaluate(
      cdp,
      `(async () => {
        const topBtns = () => document.querySelectorAll('button[aria-label="返回顶部"]').length
        window.scrollTo({ top: 0, behavior: 'instant' })
        await new Promise(r => setTimeout(r, 400))
        const atZero = topBtns()
        window.scrollTo({ top: 600, behavior: 'instant' })
        let appeared = 0
        for (let i = 0; i < 20; i++) {
          await new Promise(r => setTimeout(r, 200))
          appeared = topBtns()
          if (appeared) break
        }
        const scrolled = Math.round(window.scrollY)
        // 真的点一下：回顶是 smooth 滚动，轮询等它走完（而不是固定 sleep 赌）
        document.querySelector('button[aria-label="返回顶部"]')?.click()
        let back = scrolled
        for (let i = 0; i < 25; i++) {
          await new Promise(r => setTimeout(r, 200))
          back = Math.round(window.scrollY)
          if (back < 10) break
        }
        return { atZero, appeared, scrolled, back }
      })()`,
    )
    record(
      '电梯栏「返回顶部」阈值（≤300 不渲染 / 滚过即出现 / 点击真回顶）',
      elevator.atZero === 0 && elevator.appeared === 1 && elevator.scrolled > 300 && elevator.back < 10,
      `scrollY=0 时 ${elevator.atZero} 个 → 滚到 ${elevator.scrolled} 后 ${elevator.appeared} 个 → 点击后回到 ${elevator.back}`,
    )

    // 「联系站长」入口的渲染必须与 /site/profile 的配置**一致**，两个方向都要能红：
    //   配置有可用项却不渲染按钮（功能坏了）、配置为空却渲染出一颗点了没反应的按钮（脏入口）。
    // 这样写不依赖"当前演示库到底配没配二维码"，也顺带覆盖了本轮新增的弹层内容。
    const contact = await evaluate(
      cdp,
      `(async () => {
        const profile = await (await fetch('/api/v1/site/profile', { cache: 'no-store' })).json()
        const list = Array.isArray(profile.contact_qrcodes) ? profile.contact_qrcodes : []
        const hasText = (v) => String(v ?? '').trim().length > 0
        // 前端的过滤规则：image_url 与 value 全空的条目不渲染（后台点了"添加"但还没填）
        const usable = list.filter(i => hasText(i.image_url) || hasText(i.value))
        const expectImages = list.filter(i => hasText(i.image_url)).length
        const expectCopy = list.filter(i => hasText(i.value)).length
        const btn = document.querySelector('button[aria-label="联系站长"]')
        const result = { configured: usable.length, expectImages, expectCopy, buttons: btn ? 1 : 0, opened: null, images: null, copy: null, closed: null }
        if (!btn) return result
        btn.click()
        await new Promise(r => setTimeout(r, 500))
        const dialog = document.querySelector('[role="dialog"][aria-label="联系方式"]')
        result.opened = Boolean(dialog)
        result.images = dialog ? dialog.querySelectorAll('img').length : 0
        result.copy = dialog ? [...dialog.querySelectorAll('button')].filter(b => b.textContent.trim() === '复制').length : 0
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
        await new Promise(r => setTimeout(r, 300))
        result.closed = !document.querySelector('[role="dialog"][aria-label="联系方式"]')
        return result
      })()`,
    )
    // 配置为空时只断言"按钮 0 个"；配置非空时还要弹层真的打得开、图与复制按钮的数量与配置对得上
    // （逐个 count 比对，而不是"≥1 就算过"：只有号的条目本来就不该有 <img>）
    //
    // 按钮个数是 `configured > 0 ? 1 : 0` —— **整个弹层只有一颗入口按钮**，
    // 配 1 条和配 2 条都是 1 个按钮。这里原先写成 `configured === buttons`，
    // 那只有在"恰好配 1 条"时成立（配 0 条时靠短路分支绕过、配 2 条时直接红），
    // 是一条潜伏的假断言 —— 实测：演示库配了 2 条时它报红，而渲染其实完全正确。
    record(
      '电梯栏「联系站长」与站点配置一致',
      contact.buttons === (contact.configured > 0 ? 1 : 0) &&
        (contact.buttons === 0 ||
          (contact.opened === true &&
            contact.images === contact.expectImages &&
            contact.copy === contact.expectCopy &&
            contact.closed === true)),
      `配置可用 ${contact.configured} 条（应渲染图 ${contact.expectImages} 张、复制按钮 ${contact.expectCopy} 个）` +
        `→ 按钮 ${contact.buttons} 个` +
        (contact.buttons
          ? `；点击展开=${contact.opened} 图=${contact.images} 复制=${contact.copy} Esc 关闭=${contact.closed}`
          : '；配置为空，按设计整颗按钮不渲染'),
    )

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

    // ---------------------------------------------------------- 友链页
    await navigate(cdp, `${BASE_URL}/links`)
    const links = await evaluate(cdp, `(() => {
      const heading = document.querySelector('h1')?.textContent?.trim() ?? ''
      const cards = document.querySelectorAll('ul > li > a[href^="http"]')
      return { heading, count: cards.length, first: cards[0]?.textContent?.trim() ?? '' }
    })()`)
    record('友链页渲染', links?.heading?.includes('友情链接') === true, links?.heading ?? '')
    // 演示数据里有 3 条友链；数量为 0 说明 seed 没跑或列表被前端过滤掉了
    record('友链页显示演示友链', (links?.count ?? 0) >= 1, `卡片 ${links?.count ?? 0} 张：${links?.first ?? ''}`)

    // ---------------------------------------------------------- 留言板
    // 与友链页同类：这一页此前是占位页，现在有真实数据与写入入口。
    // 断言分两层：页面结构（表格/表单）与**演示数据确实渲染出来了** ——
    // 只断言"页面打开了"的话，接口 500、列表被前端过滤、seed 没跑都会漏过。
    await navigate(cdp, `${BASE_URL}/guestbook`)
    const guestbook = await evaluate(cdp, `(() => {
      const heading = document.querySelector('h1')?.textContent?.trim() ?? ''
      const form = document.querySelector('[aria-label="留言内容"]')
      const submit = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '发表留言')
      // 留言列表的 li 直接子节点是留言卡片；站长回复块是 li 内的 div，不会重复计数。
      // 分页器渲染的是 <nav><button>，也不在这个选择器里（已确认它的模板）。
      const messages = document.querySelectorAll('section ul > li').length
      const replies = document.body.innerText.includes('站长回复')
      return { heading, form: Boolean(form), submit: Boolean(submit), messages, replies }
    })()`)
    record('留言板页渲染', guestbook?.heading?.includes('留言板') === true, guestbook?.heading ?? '')
    record('留言板发表框可用', guestbook?.form === true && guestbook?.submit === true, '')
    // 演示数据里有 2 条已过审留言，其中一条带站长回复
    record(
      '留言板显示演示留言',
      (guestbook?.messages ?? 0) >= 1,
      `列表 ${guestbook?.messages ?? 0} 条，站长回复块 ${guestbook?.replies ? '有' : '无'}`,
    )

    // ---------------------------------------------------------- 关于页
    await navigate(cdp, `${BASE_URL}/about`)
    await sleep(1200)
    const about = await evaluate(
      cdp,
      `(() => ({
        prose: document.querySelector('.prose')?.innerText?.slice(0, 60) ?? '',
        bodyText: document.body.innerText,
        title: document.title,
        ogSiteName: document.head.querySelector('meta[property="og:site_name"]')?.content ?? null,
        description: document.head.querySelector('meta[name="description"]')?.content ?? null,
        footerText: document.querySelector('footer')?.innerText ?? '',
      }))()`,
    )
    record('关于页内容来自站点档案', about.prose.length > 0, about.prose.replace(/\n/g, ' '))

    // 站点名必须**一处改、处处跟着改**。
    //
    // 这条守的是一个真实的「改一半」：站点名此前在三个地方各写死了一遍 ——
    // `router/index.ts` 的 afterEach、`useHead.ts` 的 SITE_SUFFIX（还兼 og:site_name）、
    // 以及 useHead 里那个写死的默认描述。于是把站点名改成真实站名后，
    // 顶栏和页脚变了、**浏览器标签页还是「关于 · 个人博客」**、分享卡片上也是旧名。
    // 现在三者共用 `useSiteName` / `useSiteDescription`，这里逐步对齐校验。
    //
    // 「真实站点名」直接取接口（**必须 `cache: 'no-store'`**：
    // `/site/profile` 带 `max-age=60`，否则刚改完站点名会读到上一版资料）。
    // 不用 `cdp.networkLog`：它只记 url 与 status，**不记响应体**。
    let profileBody = null
    try {
      const resp = await fetch(`${BASE_URL}/api/v1/site/profile`, { cache: 'no-store' })
      profileBody = resp.ok ? await resp.json() : null
    } catch {
      profileBody = null
    }
    const siteChecks = []
    if (!profileBody) {
      siteChecks.push('取不到 /site/profile，无法确定真实站点名')
    } else {
      const realName = profileBody.owner_name
      if (!about.title.endsWith(realName)) {
        siteChecks.push(`标签页标题「${about.title}」结尾不是站名「${realName}」（可能写死在代码里）`)
      }
      if (about.ogSiteName !== realName) {
        siteChecks.push(`og:site_name=「${about.ogSiteName}」≠ 站名「${realName}」`)
      }
      if (!about.footerText.includes(realName)) {
        siteChecks.push(`页脚里没有站名「${realName}」`)
      }
      if (profileBody.headline && about.description !== profileBody.headline) {
        siteChecks.push(`默认描述「${about.description}」≠ 副标题「${profileBody.headline}」`)
      }
      if (profileBody.bio_md && !about.footerText.includes(profileBody.bio_md.slice(0, 12))) {
        siteChecks.push('页脚里没有个人介绍的首段')
      }
    }
    record(
      'E-H2 站点名/副标题在标签页、og:site_name、页脚三处一致（不再各自写死）',
      siteChecks.length === 0,
      siteChecks.length === 0
        ? `站名「${profileBody.owner_name}」；标题「${about.title}」、og:site_name、页脚均已对齐` +
            `；默认描述=「${about.description}」`
        : siteChecks.join('；'),
    )

    // 建站时长：对应旧站页脚由 /js/timeDate.js 写入的那一行。
    // 判据用**格式**而不是具体数字（数字每秒都在变）：能抓住"渲染成了空"或"格式跑偏"。
    const uptimeMatch = about.bodyText.match(/本站自从搭建已经历\s*(\d+)\s*天\s*(\d{2})\s*小时\s*(\d{2})\s*分\s*(\d{2})\s*秒/)
    record(
      'E-H3 页脚显示建站时长（天 + 两位时分秒）',
      uptimeMatch !== null,
      uptimeMatch ? uptimeMatch[0].trim() : '页脚里找不到「本站自从搭建已经历 … 天 … 小时 … 分 … 秒」',
    )

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

    // 走**真实登录页**提交表单。
    //
    // 旧做法是页面内 fetch 登录再把 token 塞进 localStorage，现在这条路已经断了：
    // refresh token 只存在于 httpOnly Cookie 里（JS 读不到，也就不可能转发），
    // access token 只留在内存里（写不进 localStorage，写了前端也不认）。
    // 只有让应用自己走一遍登录页，Cookie 与内存 token 才会被同时安排好。
    await waitFor(
      cdp,
      `!!document.querySelector('#username') && !!document.querySelector('#password')`,
      25000,
      '登录页表单',
    )
    await evaluate(
      cdp,
      `(() => {
        const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
        set(document.querySelector('#username'), 'admin')
        set(document.querySelector('#password'), 'admin123456')
      })()`,
    )
    await sleep(200)
    await evaluate(
      cdp,
      `(async () => {
        document.querySelector('#username')?.closest('form')?.requestSubmit()
        await new Promise((resolve) => setTimeout(resolve, 300))
      })()`,
    )
    // 用轮询等跳转而不是死等：dev server 首次进 /admin 要现编译那一条 chunk，
    // 冷启动要好几秒，热了只要几十毫秒。
    const loginResult = await waitFor(
      cdp,
      `location.pathname.startsWith('/admin')`,
      25000,
      '登录后跳转后台',
    ).then(
      () => ({ ok: true }),
      () => ({ ok: false }),
    )
    record('登录接口', loginResult.ok, loginResult.ok ? '已跳转到 /admin' : '提交后未进入后台')

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
        // 「localStorage 里有 token」已经不能证明登录态了（那两个 key 不再写入）；
        // 「页面内 fetch /auth/me」也**问不出答案**——access token 只活在 axios
        // 拦截器里，裸 fetch 带不上它，登没登录都回 401。
        // 能证明"会话在刷新后恢复成功"的只有页面本身：restore() 拿到 user，
        // 后台外壳才会渲染出「退出」。
        hasSession: [...document.querySelectorAll('button')].some(b => b.textContent.trim() === '退出'),
      }))()`,
    )
    record(
      '后台仪表盘',
      dashboard.url.startsWith('/admin') && dashboard.heading === '仪表盘' && dashboard.hasSession,
      `url=${dashboard.url}，标题「${dashboard.heading}」，会话=${dashboard.hasSession ? '已恢复' : '未恢复'}`,
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
      ['/admin/guestbook', '留言板管理', '待审核'],
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

    // ---------------------------------------------------------- 移动端：电梯栏与目录按钮不重叠
    //
    // 计划 §3.4 的避让口径：移动端电梯栏 bottom-24（96px = 24 底边距 + 48 目录按钮 + 12 间距），
    // 「返回顶部」必须落在目录按钮正上方。原文验收写的是"截图比对：间距 ≥ 12px"，
    // 这里换成几何量：两个按钮的 getBoundingClientRect() 竖直间距 ≥ 12px。
    // 两个判据缺一不可 —— 竖直间距之外还要**水平确实重叠**，否则"间距"说的是两个
    // 根本不在同一列的按钮，数字再大也没有意义。
    // 注意：目录按钮要在详情页（有目录的长文）上才渲染，首页没有它，所以这里要换页。
    await navigate(cdp, `${BASE_URL}${slug}`)
    const gutter = await evaluate(
      cdp,
      `(async () => {
        const toc = document.querySelector('button[aria-label="打开文章目录"]')
        if (!toc) return { ok: false, reason: 'MobileToc 目录按钮未渲染（xl 断点以下才显示）' }
        window.scrollTo({ top: 0, behavior: 'instant' })
        await new Promise(r => setTimeout(r, 400))
        const atZero = document.querySelectorAll('button[aria-label="返回顶部"]').length
        window.scrollTo({ top: 600, behavior: 'instant' })
        let top = null
        for (let i = 0; i < 25 && !top; i++) {
          await new Promise(r => setTimeout(r, 200))
          const el = document.querySelector('button[aria-label="返回顶部"]')
          // 淡入期间 opacity 还是 0，等它真正可见再量
          if (el && Number(getComputedStyle(el).opacity) > 0.5) top = el
        }
        if (!top) return { ok: false, reason: '滚过 300px 后返回顶部按钮未出现/未完成淡入', atZero }
        const a = top.getBoundingClientRect()
        const b = toc.getBoundingClientRect()
        return {
          ok: true,
          atZero,
          vw: window.innerWidth,
          vh: window.innerHeight,
          scrollY: Math.round(window.scrollY),
          gap: Math.round(b.top - a.bottom),
          overlap: Math.round(Math.min(a.right, b.right) - Math.max(a.left, b.left)),
          topBottom: Math.round(a.bottom),
          topSize: Math.round(a.width) + 'x' + Math.round(a.height),
          tocTop: Math.round(b.top),
          tocSize: Math.round(b.width) + 'x' + Math.round(b.height),
        }
      })()`,
    )
    record(
      '移动端：电梯栏与目录按钮不重叠（竖直间距 ≥ 12px）',
      gutter.ok === true && gutter.atZero === 0 && gutter.gap >= 12 && gutter.overlap > 0,
      gutter.ok !== true
        ? `原因=${gutter?.reason ?? '几何量没量到（页面内异常）'}`
        : `视口 ${gutter.vw}×${gutter.vh}，scrollY=${gutter.scrollY}；返回顶部 bottom=${gutter.topBottom}（${gutter.topSize}），` +
          `目录按钮 top=${gutter.tocTop}（${gutter.tocSize}）：竖直间距=${gutter.gap}px（阈值 12px），水平重叠=${gutter.overlap}px`,
    )

    // 回到首页：最后一条「页面脚本错误」读的是当前文档上的收集器，而上一条截图也是首页，
    // 别把落地页留在文章详情页上
    await navigate(cdp, `${BASE_URL}/`)

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
