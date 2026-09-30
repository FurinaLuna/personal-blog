/**
 * 全功能回归 + 数据基线核对（CDP 驱动真实浏览器）。
 *
 * 与另外两个脚本的分工：
 *   smoke-check.mjs        34 项：逐页渲染与关键元素是否存在（只读）
 *   interaction-check.mjs  22 项：失败路径 + 少量写操作（对称还原）
 *   full-check.mjs         本脚本：后台写操作生命周期、认证与主题、列表边界、
 *                          详情页交互、站点元信息（约 35 项）
 *
 * 三条设计纪律（都是踩过坑换来的）：
 *  1. **唯一 RUN 后缀**：所有测试对象命名带 `E2E-<ts36>`，任何残留都能被识别与清扫。
 *  2. **每次唯一 profile**：复用 Chrome profile 会带上历史登录态，导致 /login 被
 *     重定向到 /admin，表现为「登录验证莫名其妙失败」。
 *  3. **只打临时对象**：详情页访问会永久自增 view_count，点赞没有 unlike 接口，
 *     所以详情页交互一律打在本轮创建的临时文章上——删掉它，基线就能精确复原。
 *
 * 用法：
 *   node tools/full-check.mjs [baseUrl] [outDir]
 *   baseUrl 默认 http://127.0.0.1:5173（也可传 4173 验证生产包）
 *   outDir  默认 <tmp>/blog-full
 *
 * 前置：前后端已启动。脚本**会写数据**，但运行前会清扫历史残留、结束后会回收
 * 本轮全部对象并核对数据基线。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { chromeArgs, findChrome } from './lib/chrome.mjs'

const BASE = (process.argv[2] ?? 'http://127.0.0.1:5173').replace(/\/$/, '')
const OUT = process.argv[3] ?? join(tmpdir(), 'blog-full')
const PORT = Number(process.env.FULL_CHECK_PORT ?? 9250)
/** 本轮唯一标识：所有创建的对象都带这个后缀，便于识别与清扫 */
const RUN = `E2E-${Date.now().toString(36)}`
const ADMIN = { username: 'admin', password: 'admin123456' }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const results = []
const registry = {
  articles: [],
  categories: [],
  tags: [],
  users: [],
  attachments: [],
  comments: [],
  friendLinks: [],
  guestbook: [],
}
const uncovered = []
let token = ''

function record(name, ok, detail = '') {
  results.push({ name, ok: Boolean(ok), detail })
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`)
}

/** 轮询等待条件成立（替代固定 sleep：dev server 冷编译耗时差异很大） */
async function waitFor(evalJs, expression, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await evalJs(expression)) return true
    await sleep(250)
  }
  return false
}

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

const chromePath = findChrome()

/**
 * 重新登录并刷新模块级 token。
 *
 * 存在的原因：服务端登出是**真吊销**（`token_version += 1`），登出用例（B1）一跑，
 * 之前取到的这枚 token 立刻失效，后续所有带它的 PATCH / DELETE 一律 401。
 * 后果不只是用例失败——清理阶段全 401 会把整轮造的数据留在真实库里，
 * 而残留的临时账号还会让下一个用例（B4）读到错的 role。
 * 所以凡是「登出之后」或「收尾清理之前」，都必须重新取一枚令牌。
 */
async function relogin() {
  const resp = await fetch(`${BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ADMIN),
  })
  const body = await resp.json().catch(() => ({}))
  if (body?.access_token) token = body.access_token
  return { status: resp.status, ok: Boolean(body?.access_token) }
}

async function api(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  })
  const text = await resp.text()
  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  return { status: resp.status, body }
}

/** 数据基线快照（全部只读） */
async function snapshot() {
  const [pub, managed, cats, tags, users, atts, comments] = await Promise.all([
    api('/api/v1/articles?page_size=1'),
    api('/api/v1/articles/manage/list?page_size=1'),
    api('/api/v1/categories?with_counts=false'),
    api('/api/v1/tags?with_counts=false'),
    api('/api/v1/auth/users'),
    api('/api/v1/attachments?page_size=1'),
    api('/api/v1/comments?page_size=1'),
  ])
  return {
    publishedTotal: pub.body?.total ?? null,
    managedTotal: managed.body?.total ?? null,
    categories: cats.body?.length ?? cats.body?.items?.length ?? null,
    tags: tags.body?.length ?? tags.body?.items?.length ?? null,
    users: Array.isArray(users.body) ? users.body.length : (users.body?.items?.length ?? null),
    attachments: atts.body?.total ?? null,
    comments: comments.body?.total ?? null,
    residual: {
      articles: (managed.body?.items ?? []).filter((a) => a.title?.includes('E2E-')).length,
      categories: (cats.body ?? []).filter?.((c) => c.name?.includes('E2E-')).length ?? 0,
      tags: (tags.body ?? []).filter?.((t) => t.name?.includes('E2E-')).length ?? 0,
    },
  }
}

/** 清扫历史残留（上一轮被强杀也能自愈） */
async function sweepResidual() {
  let removed = 0
  const managed = await api('/api/v1/articles/manage/list?page_size=50')
  for (const a of managed.body?.items ?? []) {
    if (a.title?.includes('E2E-')) {
      if ((await api(`/api/v1/articles/${a.id}`, { method: 'DELETE' })).status === 204) removed++
    }
  }
  const cats = await api('/api/v1/categories?with_counts=false')
  for (const c of cats.body ?? []) {
    if (c.name?.includes('E2E-')) {
      if ((await api(`/api/v1/categories/${c.id}`, { method: 'DELETE' })).status === 204) removed++
    }
  }
  const tags = await api('/api/v1/tags?with_counts=false')
  for (const t of tags.body ?? []) {
    if (t.name?.includes('E2E-')) {
      if ((await api(`/api/v1/tags/${t.id}`, { method: 'DELETE' })).status === 204) removed++
    }
  }
  const users = await api('/api/v1/auth/users')
  for (const u of users.body ?? []) {
    if (u.username?.startsWith('e2e_')) {
      if ((await api(`/api/v1/auth/users/${u.id}`, { method: 'DELETE' })).status === 204) removed++
    }
  }
  const atts = await api('/api/v1/attachments?page_size=50')
  for (const a of atts.body?.items ?? []) {
    // 判定规则必须和 cleanup 的兜底清扫一致（/^e2e[_-]/i）。
    // 之前这里写的是 includes('e2e_')，而 RUN 形如 `e2e-muavvqw1`（连字符），
    // 于是上一轮被强杀留下的附件在开局扫不掉 —— 它们会混进媒体库，
    // 让 A10 的「删除」按钮查找点错卡片（已改成按对话框定位，但残留本身要先清掉）。
    if (/^e2e[_-]/i.test(a.original_name ?? '')) {
      if ((await api(`/api/v1/attachments/${a.id}`, { method: 'DELETE' })).status === 204) removed++
    }
  }
  return removed
}

/** 逆序回收本轮创建的对象（每步独立 try/catch，单点失败不阻断其余） */
async function cleanup() {
  const log = []
  const tryDelete = async (label, url) => {
    try {
      const r = await api(url, { method: 'DELETE' })
      log.push(`${label}:${r.status}`)
    } catch (error) {
      log.push(`${label}:ERR(${error.message})`)
    }
  }
  for (const id of registry.comments.reverse()) await tryDelete(`comment#${id}`, `/api/v1/comments/${id}`)
  for (const id of registry.articles.reverse()) await tryDelete(`article#${id}`, `/api/v1/articles/${id}`)
  for (const id of registry.attachments.reverse())
    await tryDelete(`attachment#${id}`, `/api/v1/attachments/${id}`)
  for (const id of registry.tags.reverse()) await tryDelete(`tag#${id}`, `/api/v1/tags/${id}`)
  for (const id of registry.categories.reverse()) await tryDelete(`category#${id}`, `/api/v1/categories/${id}`)
  for (const id of registry.friendLinks.reverse())
    await tryDelete(`friend-link#${id}`, `/api/v1/links/${id}`)
  for (const id of registry.guestbook.reverse())
    await tryDelete(`guestbook#${id}`, `/api/v1/guestbook/${id}`)
  for (const id of registry.users.reverse()) await tryDelete(`user#${id}`, `/api/v1/auth/users/${id}`)

  // 兜底一：按命名约定清扫遗留的**留言**。
  // interaction-check 以匿名身份留了一条待审留言（那条用例要的正是匿名视角），
  // 而匿名没有删除权限 —— 它自己清不掉，只能在这里以站长身份按命名约定扫掉。
  //
  // ⚠️ page_size 用 50：这是后端的 MAX_PAGE_SIZE。写成 100 会得到 422，
  // 而"扫描请求失败"如果只体现在"没删掉东西"上，就成了一次静默的空操作 ——
  // 所以下面显式把非 200 记进日志（第一次写这条时正是踩了这个：扫描返回 422，
  // 遗留留言安安静静地留在库里，清理日志里一个字都没有）。
  try {
    const managed = await api('/api/v1/guestbook/manage?page_size=50')
    if (managed.status !== 200) log.push(`guestbook-sweep:HTTP(${managed.status})`)
    for (const item of managed.body?.items ?? []) {
      const stray =
        String(item.content ?? '').startsWith('E2E') ||
        String(item.author_name ?? '').startsWith('E2E')
      if (stray) await tryDelete(`stray-guestbook#${item.id}`, `/api/v1/guestbook/${item.id}`)
    }
  } catch (error) {
    log.push(`guestbook-sweep:ERR(${error.message})`)
  }

  // 兜底二：按命名约定清扫漏登记的**附件**。
  // 「编辑器封面上传 / 正文图片上传」拿不到附件 id（响应只回 URL），
  // 只能靠命名前缀兜底——否则会像首次运行那样留下 2 个孤儿附件。

  try {
    const list = await api('/api/v1/attachments?page_size=50')
    for (const item of list.body?.items ?? []) {
      if (/^e2e[_-]/i.test(item.original_name ?? '')) {
        await tryDelete(`orphan-attachment#${item.id}`, `/api/v1/attachments/${item.id}`)
      }
    }
  } catch (error) {
    log.push(`orphan-sweep:ERR(${error.message})`)
  }
  return log
}

const chrome = spawn(
  chromePath,
  // 每次唯一 profile：复用会带上历史登录态（踩过）
  chromeArgs({
    port: PORT,
    userDataDir: join(tmpdir(), `full-check-${RUN}`),
    windowSize: '1440,1200',
  }),
  { stdio: 'ignore' },
)

const startedAt = Date.now()
let before = null
let after = null
let consoleErrors = []
/**
 * 本轮浏览器里 `/api/v1/site/profile` 的响应状态码。
 *
 * E5 断言的是「配置为空 → 联系站长按钮不渲染」，而**档案没加载出来**（接口挂了、
 * 被缓存挡住、被拦截）也会让按钮不渲染 —— 只看 DOM 的话两者一模一样，是一条假绿。
 * 记下响应状态，才能把「读到的是空配置」和「什么都没读到」分开。
 */
const profileResponses = []

try {
  if (!existsSync(OUT)) mkdirSync(OUT, { recursive: true })

  const ws = new WebSocket(await getWsUrl(PORT))
  await new Promise((res, rej) => {
    ws.onopen = res
    ws.onerror = rej
  })
  let seq = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.method === 'Runtime.exceptionThrown') {
      consoleErrors.push(m.params?.exceptionDetails?.exception?.description ?? 'unknown')
    }
    if (
      m.method === 'Network.responseReceived' &&
      String(m.params?.response?.url ?? '').includes('/api/v1/site/profile')
    ) {
      profileResponses.push(m.params.response.status)
    }
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
  /** 执行页面内表达式；页面异常打印到控制台而不是静默返回 undefined */
  const evalJs = async (expr) => {
    const res = await send('Runtime.evaluate', {
      expression: expr,
      returnByValue: true,
      awaitPromise: true,
    })
    if (res?.exceptionDetails) {
      const detail = res.exceptionDetails.exception?.description ?? res.exceptionDetails.text
      console.log('  [页面内异常]', String(detail).split('\n')[0])
      return undefined
    }
    return res?.result?.value
  }
  const shot = async (name) => {
    const { data } = await send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'))
  }
  const goto = async (path, waitMs = 2200) => {
    await send('Page.navigate', { url: `${BASE}${path}` })
    await sleep(waitMs)
  }

  await send('Page.enable')
  await send('Runtime.enable')
  // 登录态现在落在 Cookie 上：脚本要清 Cookie（B3）、查 Cookie（A0 / B1 断言），
  // 这两个命令都要求 Network 域是启用的。
  await send('Network.enable')
  // 剪贴板权限：headless Chrome 默认拒绝 navigator.clipboard.writeText，
  // 会让「代码复制」用例测不到成功路径（只能测到降级提示）。显式授权后才是真验证。
  try {
    await send('Browser.grantPermissions', {
      origin: BASE,
      permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
  } catch {
    /* 该 CDP 版本不支持时降级：复制用例会断言"要么成功、要么给出明确的降级提示" */
  }
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1200,
    deviceScaleFactor: 1,
    mobile: false,
  })

  // ================================================================ 准备
  const health = await (await fetch(`${BASE}/api/v1/site/profile`)).json().catch(() => null)
  void health

  const login = await api('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(ADMIN),
  })
  token = login.body?.access_token ?? ''
  if (!token) {
    record('管理员登录（API）', false, `HTTP ${login.status}`)
    throw new Error('无法登录取 token，后续用例无法进行')
  }

  const swept = await sweepResidual()
  before = await snapshot()

  // 预热：等应用挂载（首次导航可能碰上 Vite 依赖优化白屏）
  await send('Page.navigate', { url: BASE })
  await waitFor(evalJs, `(document.getElementById('app')?.childElementCount ?? 0) > 0`, 30000)

  /**
   * 读取浏览器里的 refresh Cookie。
   *
   * 它是这套凭证方案的**根**：后面每一个 /admin 页面都要靠它在应用启动时
   * 静默续期出 access token。它没种上、或 Path 错了一个字符，A 段会大面积
   * 401，而报错形态是"登录验证莫名其妙失败"——所以断言要直接钉在它身上，
   * 而不是等到某个后台页面挂了再倒推。
   */
  const readRefreshCookie = async () => {
    const jar = await send('Network.getAllCookies').catch(() => ({ cookies: [] }))
    return (jar?.cookies ?? []).find((c) => c.name === 'blog_refresh') ?? null
  }

  /**
   * 清掉浏览器侧的全部登录凭证。
   *
   * Cookie（httpOnly，JS 碰不到，只能靠 CDP）+ localStorage 里那两个**旧** key
   * ——后者在新方案里已经不再写入，但清除动作保留着：脚本要能在"前端改造合入
   * 前后"两种状态下都跑，漏掉它就是给旧状态留一个"以为清干净了"的洞。
   */
  const clearCredentials = async () => {
    await send('Network.clearBrowserCookies').catch(() => {})
    await evalJs(`(() => {
      localStorage.removeItem('blog-access-token')
      localStorage.removeItem('blog-refresh-token')
      return true
    })()`)
  }

  /**
   * 在**浏览器里**以指定账号登录（走真实登录页）。
   *
   * 为什么必须换成这样：refresh token 现在只存在于 httpOnly Cookie、access token
   * 只在内存里 —— 「Node 里登录拿 token，再用 CDP 塞进 localStorage」这条老路
   * 已经彻底失效（塞进去的两个 key 前端根本不再读）。
   *
   * 为什么选**登录页 UI** 而不是「Node 登录 + CDP 种 Cookie 再刷新」：
   * 后者依赖"应用启动流程会拿 Cookie 静默续期"这个前端行为，而 access token 是
   * 在那一步才换出来的——种完 Cookie **不刷新**等于没做，刷新了又要赌续期逻辑
   * 与时序；走登录页则由应用自己把 Cookie 与内存 token 一次安排好，而且它就是
   * 真实用户路径。代价是依赖 #username / #password（LoginView.vue），所以每步都
   * 先轮询等表单出现，失败时把实际落点打出来而不是让调用方拿到 undefined。
   *
   * 登录前一定先清凭证：不清的话 /login 会因为"已登录"被直接弹到 /admin，
   * 表现为填表无效、用例卡在登录页（这个坑脚本注释里已经记过一次）。
   */
  const loginAs = async (username, password) => {
    await clearCredentials()
    await send('Page.navigate', { url: `${BASE}/login` })
    if (!(await waitFor(evalJs, `!!document.querySelector('#username') && !!document.querySelector('#password')`, 25000))) {
      return { ok: false, path: await evalJs(`location.pathname`), reason: '登录页表单未出现' }
    }
    await evalJs(`(() => {
      const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
      set(document.querySelector('#username'), ${JSON.stringify(username)})
      set(document.querySelector('#password'), ${JSON.stringify(password)})
      return true
    })()`)
    await sleep(200)
    await evalJs(`(() => { document.querySelector('#username')?.closest('form')?.requestSubmit(); return true })()`)
    const landed = await waitFor(evalJs, `location.pathname.startsWith('/admin')`, 25000)
    const path = await evalJs(`location.pathname`)
    return { ok: landed, path, reason: landed ? '' : `登录后未进入后台（停在 ${path}）` }
  }

  const adminLogin = await loginAs(ADMIN.username, ADMIN.password)
  const refreshCookie = await readRefreshCookie()
  record(
    'A0 浏览器登录：种下 httpOnly refresh Cookie',
    adminLogin.ok &&
      Boolean(refreshCookie) &&
      refreshCookie.httpOnly === true &&
      String(refreshCookie.path ?? '').endsWith('/api/v1/auth'),
    `落点=${adminLogin.path} cookie=${refreshCookie ? `path=${refreshCookie.path} httpOnly=${refreshCookie.httpOnly} secure=${refreshCookie.secure}` : '未种上'}` +
      `${adminLogin.reason ? ` 原因=${adminLogin.reason}` : ''}`,
  )

  // ================================================================ A. 后台写操作生命周期
  const articleTitle = `E2E 生命周期 ${RUN}`
  const articleBody = [
    '# 测试正文',
    '',
    '## 第一节',
    '',
    '段落一。',
    '',
    '## 第二节',
    '',
    '```python',
    'print("hello e2e")',
    '```',
  ].join('\n')

  // A1 新建 → 保存草稿
  await goto('/admin/articles/new', 3000)
  const a1 = await evalJs(`(async () => {
    const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
    const title = document.querySelector('input[placeholder="文章标题"]')
    const textarea = document.querySelector('textarea')
    if (!title || !textarea) return { ok: false, reason: '编辑器控件未找到' }
    set(title, ${JSON.stringify(articleTitle)})
    set(textarea, ${JSON.stringify(articleBody)})
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '保存草稿')
    if (!btn) return { ok: false, reason: '保存草稿按钮未找到' }
    btn.click()
    await new Promise(r => setTimeout(r, 2600))
    const m = location.pathname.match(/\\/admin\\/articles\\/(\\d+)\\/edit/)
    return { ok: true, id: m ? Number(m[1]) : null, url: location.pathname, toast: document.body.innerText.includes('草稿已保存') }
  })()`, true)
  if (a1?.id) registry.articles.push(a1.id)
  record('A1 新建文章并保存草稿', a1?.ok && a1?.toast && Boolean(a1?.id), `id=${a1?.id} url=${a1?.url}`)

  const articleId = a1?.id

  // A2 草稿 → 发布（含代码块，供 D4 使用）
  const a2 = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => /^发布$|^更新$/.test(b.textContent.trim()))
    if (!btn) return { ok: false, reason: '发布按钮未找到' }
    btn.click()
    await new Promise(r => setTimeout(r, 2800))
    const text = document.body.innerText
    return { ok: true, toast: /已发布/.test(text), slug: document.querySelector('input[placeholder="文章地址 slug"]')?.value ?? '' }
  })()`, true)
  record('A2 草稿发布为已发布', a2?.ok && a2?.toast, `slug=${a2?.slug || '（未回填）'}`)

  // A3 编辑已发布文章
  const editedTitle = `${articleTitle} 改`
  const a3 = await evalJs(`(async () => {
    const title = document.querySelector('input[placeholder="文章标题"]')
    if (!title) return { ok: false, reason: '标题输入框未找到' }
    title.value = ${JSON.stringify(editedTitle)}
    title.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise(r => setTimeout(r, 300))
    const btn = [...document.querySelectorAll('button')].find(b => /^发布$|^更新$/.test(b.textContent.trim()))
    btn?.click()
    await new Promise(r => setTimeout(r, 2600))
    return { ok: true, toast: /已发布/.test(document.body.innerText) }
  })()`, true)
  record('A3 编辑已发布文章并更新', a3?.ok && a3?.toast)

  // A5 分类 增/改/删（先建分类，供 A2 之后的分类筛选使用）
  const catName = `E2E 分类 ${RUN}`
  const catName2 = `${catName} 改`
  await goto('/admin/taxonomy', 2600)
  const a5create = await evalJs(`(async () => {
    const input = [...document.querySelectorAll('input')].find(i => i.placeholder?.includes('分类名称'))
    if (!input) return { ok: false, reason: '分类名称输入框未找到' }
    input.value = ${JSON.stringify(catName)}
    input.dispatchEvent(new Event('input', { bubbles: true }))
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '添加分类')
    btn?.click()
    await new Promise(r => setTimeout(r, 1800))
    return { ok: true, toast: document.body.innerText.includes('分类已创建') }
  })()`, true)
  const catList = await api('/api/v1/categories?with_counts=false')
  const cat = (catList.body ?? []).find((c) => c.name === catName)
  if (cat) registry.categories.push(cat.id)
  record('A5a 新建分类', a5create?.ok && a5create?.toast && Boolean(cat), `id=${cat?.id} slug=${cat?.slug}`)

  // A5b 修改分类。
  //
  // 这条用例踩过两次"失败但说不出哪里失败"的坑（`record` 只传了布尔），
  // 所以现在每一步都留证据：找不到行/找不到编辑按钮/编辑器没打开/保存按钮不在，
  // 分别返回不同的 reason —— 排查时不用再猜是哪一环断了。
  const a5update = await evalJs(`(async () => {
    const row = [...document.querySelectorAll('li,tr,div')].find(el => el.textContent?.includes(${JSON.stringify(catName)}) && el.querySelector('button'))
    if (!row) {
      // 现场取证：此刻分类区块到底渲染了什么（空态/骨架/错误/旧数据）
      const cards = [...document.querySelectorAll('.card')]
      const listCard = cards.find(c => c.textContent?.includes('全部分类')) ?? cards[0]
      return {
        ok: false,
        reason: '没找到包含该分类且带按钮的容器',
        listText: (listCard?.textContent ?? '').replace(/\\s+/g, ' ').trim().slice(0, 200),
        items: [...document.querySelectorAll('li')].map(li => li.textContent.trim().slice(0, 24)),
      }
    }

    const edit = [...row.querySelectorAll('button')].find(b => /编辑|修改/.test(b.textContent))
    if (!edit) return { ok: false, reason: '容器里没有「编辑」按钮', rowText: row.textContent.trim().slice(0, 80) }
    edit.click()
    await new Promise(r => setTimeout(r, 600))

    const inputs = [...document.querySelectorAll('input')]
    const nameInput = inputs.find(i => i.value === ${JSON.stringify(catName)}) ?? inputs[0]
    if (!nameInput) return { ok: false, reason: '页面里没有任何输入框' }
    nameInput.value = ${JSON.stringify(catName2)}
    nameInput.dispatchEvent(new Event('input', { bubbles: true }))

    const save = [...document.querySelectorAll('button')].find(b => /保存|确定/.test(b.textContent.trim()))
    if (!save) return { ok: false, reason: '编辑器没打开（找不到保存按钮）', inputs: inputs.map(i => i.value) }
    save.click()
    await new Promise(r => setTimeout(r, 1800))

    const text = document.body.innerText
    return {
      ok: true,
      toast: text.includes('分类已更新'),
      // 失败时把 toast 区域的文案带回去：区分"没反应"和"报错了"
      tail: text.slice(-200).replace(/\\s+/g, ' '),
    }
  })()`, true)
  // 现场证据只在失败时带出来：通过时把整页文案打进报告只是噪音
  const a5bOk = Boolean(a5update?.ok && a5update?.toast)
  record(
    'A5b 修改分类',
    a5bOk,
    a5bOk
      ? ''
      : a5update?.reason
        ? `${a5update.reason} | 列表=${a5update.listText ?? '-'} | 条目=${JSON.stringify(a5update.items ?? [])}`
        : (a5update?.tail ?? ''),
  )

  // A5c 友链：建 → 前台可见 → 隐藏 → 前台不可见 → 删。
  //
  // 这条链路值得进浏览器回归，因为它跨了「后台写 → 前台缓存读」两层：
  // 公开的 GET /links 带 Cache-Control/ETag，如果 Vary 或缓存口径写错，
  // 站长改完友链在前台看到的仍是旧结果（本项目在文章列表上踩过同类问题）。
  //
  // **URL 必须和名字一样带 RUN**：友链地址上有唯一约束，写成固定值的话，
  // 上一次运行留下的记录（或任何一次异常中断）会把本次创建顶成 409，
  // 表现为 A5c-1 报「id=undefined」、后面三条连锁失败 —— 而失败信息完全不提
  // 「地址重复」，排查方向会被带偏（踩过：见 docs/devlog/2026-09-29.md）。
  const linkName = `E2E 友链 ${RUN}`
  const linkUrl = `https://example.com/e2e-link-${RUN}`
  await goto('/admin/links', 2600)
  const a5cCreate = await evalJs(`(async () => {
    const set = (label, value) => {
      const el = document.querySelector(\`input[aria-label="\${label}"]\`)
      if (!el) return false
      el.value = value
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    }
    if (!set('站点名称', ${JSON.stringify(linkName)})) return { ok: false, reason: '找不到站点名称输入框' }
    if (!set('站点地址', ${JSON.stringify(linkUrl)})) return { ok: false, reason: '找不到站点地址输入框' }
    const submit = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '添加友链')
    if (!submit) return { ok: false, reason: '找不到「添加友链」按钮' }
    submit.click()
    await new Promise(r => setTimeout(r, 1800))
    return { ok: true, toast: document.body.innerText.includes('友链已添加') }
  })()`, true)
  const linkList = await api('/api/v1/links/manage')
  const link = (linkList.body ?? []).find((item) => item.name === linkName)
  if (link) registry.friendLinks.push(link.id)
  record('A5c-1 新建友链', a5cCreate?.ok && a5cCreate?.toast && Boolean(link), a5cCreate?.reason ?? `id=${link?.id}`)

  const publicShows = await evalJs(`(async () => {
    const res = await fetch('/api/v1/links', { cache: 'no-store' })
    const items = await res.json()
    return { count: items.length, has: items.some(i => i.name === ${JSON.stringify(linkName)}) }
  })()`, true)
  record(
    'A5c-2 前台接口立即可见（写后读不走旧缓存）',
    publicShows?.has === true,
    `公开列表 ${publicShows?.count ?? '?'} 条`,
  )

  const a5cHide = await evalJs(`(async () => {
    const row = [...document.querySelectorAll('li')].find(el => el.textContent?.includes(${JSON.stringify(linkName)}))
    const btn = [...(row?.querySelectorAll('button') ?? [])].find(b => b.textContent.trim() === '隐藏')
    if (!btn) return { ok: false, reason: '找不到「隐藏」按钮' }
    btn.click()
    await new Promise(r => setTimeout(r, 1500))
    return { ok: true, toast: document.body.innerText.includes('已从前台隐藏') }
  })()`, true)
  const afterHide = await evalJs(`(async () => {
    const res = await fetch('/api/v1/links', { cache: 'no-store' })
    const items = await res.json()
    return { has: items.some(i => i.name === ${JSON.stringify(linkName)}) }
  })()`, true)
  record(
    'A5c-3 隐藏后前台不可见（后台仍保留）',
    a5cHide?.ok && a5cHide?.toast && afterHide?.has === false,
    a5cHide?.reason ?? `前台仍可见=${afterHide?.has}`,
  )

  // 删除放在 cleanup：这里先确认按钮存在，避免"建了没删"污染基线
  const a5cDeleteVisible = await evalJs(`(() => {
    const row = [...document.querySelectorAll('li')].find(el => el.textContent?.includes(${JSON.stringify(linkName)}))
    return { exists: Boolean([...(row?.querySelectorAll('button') ?? [])].find(b => b.textContent.trim() === '删除')) }
  })()`)
  record('A5c-4 删除入口可用（删除动作在 cleanup 执行）', a5cDeleteVisible?.exists === true)

  // A5d 留言板：匿名发表 → 前台不可见（待审）→ 站长回复 → 过审 → 队列里消失 + 前台可见。
  //
  // 与 A5c 同为「后台写 → 前台缓存读」的跨层链路，但多了一层**审核状态**：
  // 未过审的留言绝不能出现在公开接口里（那是"先审后发"的全部意义），
  // 而这条规则横跨服务层的默认取值与前端缓存两层，只有真跑一遍才看得见。
  //
  // 顺序刻意是「先回复、后过审」：后台默认只筛待审，一旦过审这条就从队列里消失，
  // 再回头找它的回复框会找不到（这不是脚本取巧，而是它本来就该在待审时被处理）。
  const guestbookName = `E2E 留言 ${RUN}`
  const guestbookContent = `E2E 留言内容 ${RUN}`
  const guestbookReply = `E2E 站长回复 ${RUN}`
  // 造一条**待审**留言。刻意用匿名 API 而不是页面表单：这一轮脚本全程以站长身份登录，
  // 而公开页在登录后显示的是「以 XX 的身份留言」——站长发的会直接过审，
  // 于是「匿名提交 → 进入待审队列」这条路径在这里根本走不到。
  // 匿名表单的 UI 行为由 interaction-check（未登录状态）与 vitest 覆盖。
  const a5dPost = await evalJs(`(async () => {
    const res = await fetch('/api/v1/guestbook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // 刻意不带 Authorization：这就是匿名访客的请求
      body: JSON.stringify({
        author_name: ${JSON.stringify(guestbookName)},
        content: ${JSON.stringify(guestbookContent)},
      }),
    })
    const body = await res.json().catch(() => ({}))
    return { status: res.status, approved: body.is_approved }
  })()`, true)
  const guestbookList = await api('/api/v1/guestbook/manage?approved=false&page_size=50')
  const guestbookItem = (guestbookList.body?.items ?? []).find(
    (item) => item.content === guestbookContent,
  )
  if (guestbookItem) registry.guestbook.push(guestbookItem.id)
  record(
    'A5d-1 匿名留言进入待审队列',
    a5dPost?.status === 201 && a5dPost?.approved === false && Boolean(guestbookItem),
    `HTTP ${a5dPost?.status}，is_approved=${a5dPost?.approved}，id=${guestbookItem?.id}`,
  )

  const pendingHidden = await evalJs(`(async () => {
    const res = await fetch('/api/v1/guestbook?page=1&page_size=50', { cache: 'no-store' })
    const body = await res.json()
    return { has: (body.items ?? []).some(i => i.content === ${JSON.stringify(guestbookContent)}) }
  })()`, true)
  record(
    'A5d-2 未过审的留言不在公开列表里',
    pendingHidden?.has === false,
    `公开列表里出现=${pendingHidden?.has}`,
  )

  await goto('/admin/guestbook', 2600)
  const a5dReply = await evalJs(`(async () => {
    const row = [...document.querySelectorAll('li')].find(el => el.textContent?.includes(${JSON.stringify(guestbookContent)}))
    if (!row) return { ok: false, reason: '后台待审队列里找不到这条留言' }
    const open = [...row.querySelectorAll('button')].find(b => ['回复', '编辑回复'].includes(b.textContent.trim()))
    if (!open) return { ok: false, reason: '找不到「回复」按钮' }
    open.click()
    await new Promise(r => setTimeout(r, 300))
    const box = document.querySelector('[aria-label="回复 ${guestbookName} 的留言"]')
    if (!box) return { ok: false, reason: '回复框没有出现' }
    box.value = ${JSON.stringify(guestbookReply)}
    box.dispatchEvent(new Event('input', { bubbles: true }))
    const save = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '保存回复')
    if (!save) return { ok: false, reason: '找不到「保存回复」按钮' }
    save.click()
    await new Promise(r => setTimeout(r, 1500))
    return { ok: true, toast: document.body.innerText.includes('回复已保存') }
  })()`, true)
  record('A5d-3 站长回复待审留言', a5dReply?.ok && a5dReply?.toast, a5dReply?.reason ?? '')

  const a5dApprove = await evalJs(`(async () => {
    const row = [...document.querySelectorAll('li')].find(el => el.textContent?.includes(${JSON.stringify(guestbookContent)}))
    if (!row) return { ok: false, reason: '审核前找不到这条留言' }
    const btn = [...row.querySelectorAll('button')].find(b => b.textContent.trim() === '通过')
    if (!btn) return { ok: false, reason: '找不到「通过」按钮' }
    btn.click()
    await new Promise(r => setTimeout(r, 1600))
    return {
      ok: true,
      toast: document.body.innerText.includes('已通过'),
      // 过审后应当从「待审」队列里消失
      drained: !document.body.innerText.includes(${JSON.stringify(guestbookContent)}),
    }
  })()`, true)
  const afterApprove = await evalJs(`(async () => {
    const res = await fetch('/api/v1/guestbook?page=1&page_size=50', { cache: 'no-store' })
    const body = await res.json()
    const found = (body.items ?? []).find(i => i.content === ${JSON.stringify(guestbookContent)})
    return { visible: Boolean(found), reply: found?.reply_content ?? null }
  })()`, true)
  record(
    'A5d-4 过审后从待审队列消失，前台立即可见且带站长回复',
    a5dApprove?.ok &&
      a5dApprove?.toast &&
      a5dApprove?.drained === true &&
      afterApprove?.visible === true &&
      afterApprove?.reply === guestbookReply,
    a5dApprove?.reason ??
      `队列已清=${a5dApprove?.drained}，前台可见=${afterApprove?.visible}，回复=${afterApprove?.reply}`,
  )

  // 删除放在 cleanup：这里先确认按钮存在，避免"建了没删"污染数据库。
  // 过审之后它已不在待审队列，所以要先把筛选切到「全部」才找得到。
  const a5dDeleteVisible = await evalJs(`(async () => {
    const tab = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '全部')
    if (!tab) return { ok: false, reason: '找不到「全部」筛选' }
    tab.click()
    await new Promise(r => setTimeout(r, 1400))
    const row = [...document.querySelectorAll('li')].find(el => el.textContent?.includes(${JSON.stringify(guestbookContent)}))
    if (!row) return { ok: false, reason: '切到全部后仍找不到这条留言' }
    return {
      ok: true,
      exists: Boolean([...row.querySelectorAll('button')].find(b => b.textContent.trim() === '删除')),
    }
  })()`, true)
  record(
    'A5d-5 删除入口可用（删除动作在 cleanup 执行）',
    a5dDeleteVisible?.ok === true && a5dDeleteVisible?.exists === true,
    a5dDeleteVisible?.reason ?? '',
  )

  // ⚠️ 必须切回分类页：紧跟其后的 A6（标签）与 A7（清理空标签按钮）都假设
  // 当前停留在 /admin/taxonomy。少了这一句，它们会在友链/留言板管理页上找不到元素而失败，
  // 进而让 A6 创建的标签漏登记、污染数据基线 —— 这是真实踩过的（一次 4 条用例连带失败）。
  await goto('/admin/taxonomy', 2000)

  // A6 标签 增（Enter 提交）/ 删  —— 先建，供 C0 种子文章共用
  const tagName = `E2E 标签 ${RUN}`
  const a6create = await evalJs(`(async () => {
    const input = [...document.querySelectorAll('input')].find(i => i.placeholder?.includes('标签'))
    if (!input) return { ok: false, reason: '标签输入框未找到' }
    input.value = ${JSON.stringify(tagName)}
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await new Promise(r => setTimeout(r, 1800))
    return { ok: true, toast: document.body.innerText.includes('标签已创建') }
  })()`, true)
  const tagList = await api('/api/v1/tags?with_counts=false')
  const tag = (tagList.body ?? []).find((t) => t.name === tagName)
  if (tag) registry.tags.push(tag.id)
  record('A6a 新建标签（Enter 提交）', a6create?.ok && a6create?.toast && Boolean(tag), `id=${tag?.id}`)

  // A7 「清理空标签」只做**只读**存在性断言。
  //
  // 为什么不点它：该操作会删除**全站**所有 0 引用的标签，而演示数据里的
  // FastAPI / Vue 恰好没有被任何文章引用——首次运行点下去，它们真的被删了，
  // 数据基线从 7 个标签变成 5 个。在共享数据库上执行全局破坏性操作是脚本的
  // 设计错误，不是"顺手多测一点"。该接口的行为由后端单测覆盖
  // （tests/test_taxonomy.py 的 cleanup 用例）。
  const cleanupBtn = await evalJs(`(() => {
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.includes('清理空标签'))
    return { exists: Boolean(btn) }
  })()`)
  record(
    'A7 清理空标签按钮存在（只读，不点击）',
    cleanupBtn?.exists === true,
    '全局破坏性操作：共享库上只做存在性断言，行为由后端单测覆盖',
  )

  // A8 用户 新建 → 改角色 → 停用 → 启用 → 删除（删除放 cleanup，避免影响 B4）
  const userName = `e2e_${RUN.toLowerCase()}`
  const userResp = await api('/api/v1/auth/users', {
    method: 'POST',
    body: JSON.stringify({
      username: userName,
      email: `${userName}@example.com`,
      password: 'E2ePassw0rd!',
      role: 'author',
    }),
  })
  const userId = userResp.body?.id
  if (userId) registry.users.push(userId)
  record('A8a 新建用户（author）', userResp.status === 201 && Boolean(userId), `id=${userId}`)

  const a8role = await api(`/api/v1/auth/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify({ role: 'admin' }),
  })
  record(
    'A8b 修改用户角色',
    a8role.status === 200 && a8role.body?.role === 'admin',
    `role=${a8role.body?.role}`,
  )

  const a8disable = await api(`/api/v1/auth/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: false }),
  })
  const a8enable = await api(`/api/v1/auth/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: true }),
  })
  record(
    'A8c 停用 / 启用用户',
    a8disable.body?.is_active === false && a8enable.body?.is_active === true,
    `${a8disable.body?.is_active} → ${a8enable.body?.is_active}`,
  )

  // A8d 状态复核：重新查一次库里的真实值。
  // 加这条是因为一次生产包运行里 B4 登录报了 403「账号已被停用」，而 A8c 的响应显示已启用。
  // 响应体只能证明"请求被处理"，复核才能证明"状态真的落库"——先例自证。
  const a8verify = await api('/api/v1/auth/users')
  const a8state = (a8verify.body ?? []).find((u) => u.id === userId)
  record(
    'A8d 复核用户状态已落库（启用）',
    a8state?.is_active === true && a8state?.role === 'admin',
    `实际 role=${a8state?.role} is_active=${a8state?.is_active}`,
  )

  // A9/A10 媒体上传（浏览器内真实触发）/ 删除
  const pngBase64 =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
  await goto('/admin/media', 2600)
  const fileName = `e2e_${RUN.toLowerCase()}.png`
  const a9 = await evalJs(`(async () => {
    const input = document.querySelector('input[type="file"]')
    if (!input) return { ok: false, reason: '文件输入未找到' }
    const bytes = Uint8Array.from(atob(${JSON.stringify(pngBase64)}), c => c.charCodeAt(0))
    const file = new File([bytes], ${JSON.stringify(fileName)}, { type: 'image/png' })
    const dt = new DataTransfer()
    dt.items.add(file)
    input.files = dt.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await new Promise(r => setTimeout(r, 3500))
    const text = document.body.innerText
    return {
      ok: true,
      listed: text.includes(${JSON.stringify(fileName)}),
      toast: /成功上传|已上传/.test(text),
    }
  })()`, true)
  // 注意 page_size 上限是 50（传 100 会 422）
  const attList = await api('/api/v1/attachments?page_size=50')
  const att = (attList.body?.items ?? []).find((a) => a.original_name === fileName)
  if (att) registry.attachments.push(att.id)
  record(
    'A9 媒体库上传图片',
    a9?.ok && (a9?.toast || a9?.listed) && Boolean(att),
    `id=${att?.id} 列表可见=${a9?.listed} toast=${a9?.toast}`,
  )

  if (att) {
    const a10 = await evalJs(`(async () => {
      // 定位「包含该文件名、且内部有『删除』按钮」的最小容器，避免选到外层容器
      const candidates = [...document.querySelectorAll('li, article, div')]
        .filter(el => el.textContent?.includes(${JSON.stringify(fileName)}))
        .filter(el => [...el.querySelectorAll('button')].some(b => b.textContent.trim() === '删除'))
      const card = candidates[candidates.length - 1] ?? candidates[0]
      const del = card && [...card.querySelectorAll('button')].find(b => b.textContent.trim() === '删除')
      if (!del) return { ok: false, reason: '删除按钮未找到' }
      del.click()
      // 确认按钮必须在**对话框内**找。
      // 原先写的是「页面上第一个文案为『删除』且不是刚点的那个按钮」——媒体库里
      // 只要还有别的卡片（例如上一轮残留没清干净），这个查找就会命中另一张卡片的
      // 删除按钮：又弹一次确认、真正要删的那个纹丝不动，用例表现为
      // 「点了却没删掉」，排查时很容易误判成删除接口坏了。
      // 顺便轮询等对话框出现，别靠固定 sleep 赌它已经挂载。
      let dialog = null
      for (let i = 0; i < 20 && !dialog; i += 1) {
        dialog = document.querySelector('[role="dialog"][aria-modal="true"]')
        if (!dialog) await new Promise(r => setTimeout(r, 150))
      }
      const confirm = dialog
        ? [...dialog.querySelectorAll('button')].find(b => b.textContent.trim() === '删除')
        : null
      confirm?.click()
      await new Promise(r => setTimeout(r, 2400))
      return { ok: true, toast: /已删除/.test(document.body.innerText), clicked: Boolean(confirm) }
    })()`, true)
    const gone = !(await api('/api/v1/attachments?page_size=50')).body?.items?.some((a) => a.id === att.id)
    if (gone) registry.attachments = registry.attachments.filter((id) => id !== att.id)
    record('A10 删除媒体文件', a10?.ok && a10?.clicked && gone, `toast=${a10?.toast} 已移除=${gone}`)
  } else {
    record('A10 删除媒体文件', false, '未找到刚上传的附件，跳过')
  }

  // A11 编辑器标签输入：Enter / 逗号 / Backspace / 第 11 个被拒
  await goto('/admin/articles/new', 2600)
  const a11 = await evalJs(`(async () => {
    const box = [...document.querySelectorAll('input')].find(i => i.placeholder?.includes('标签') || i.placeholder?.includes('回车'))
    if (!box) return { ok: false, reason: '标签输入框未找到' }
    const chips = () => document.querySelectorAll('[class*="chip"], span[class*="rounded-full"]').length
    const set = (v) => { box.value = v; box.dispatchEvent(new Event('input', { bubbles: true })) }
    const enter = () => box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    const comma = () => box.dispatchEvent(new KeyboardEvent('keydown', { key: ',', bubbles: true }))
    const back = () => box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }))

    const base = chips()
    set('t1'); enter(); await new Promise(r => setTimeout(r, 250))
    const afterEnter = box.value === '' && chips() === base + 1
    set('t2'); comma(); await new Promise(r => setTimeout(r, 250))
    const afterComma = box.value === '' && chips() === base + 2
    back(); await new Promise(r => setTimeout(r, 250))
    const afterBack = chips() === base + 1

    // 补到正好 10 个再试第 11 个。
    // 注意要**先数当前有几个**：上面 Backspace 删掉了一个，直接加 8 个只会到 9 个，
    // 第 10 个自然被接受（首次运行就是这么误判的）。
    const target = 10
    let idx = 0
    while (chips() < target && idx < 20) { idx++; set('fill' + idx); enter(); await new Promise(r => setTimeout(r, 150)) }
    const atLimit = chips()
    set('overflow'); enter(); await new Promise(r => setTimeout(r, 900))
    const text = document.body.innerText
    return {
      ok: true,
      afterEnter, afterComma, afterBack,
      chipCount: chips(),
      atLimit,
      rejected: /最多\\s*10\\s*个标签/.test(text),
      overflowRejected: box.value === 'overflow',
    }
  })()`, true)
  record(
    'A11 标签输入 Enter/逗号/Backspace 与 10 个上限',
    a11?.ok && a11?.afterEnter && a11?.afterComma && a11?.afterBack && a11?.rejected,
    `Enter新增=${a11?.afterEnter} 逗号新增=${a11?.afterComma} Backspace删除=${a11?.afterBack} 第11个被拒=${a11?.rejected} chip=${a11?.chipCount}`,
  )

  // A12 封面上传 → 预览出现 → 移除
  // 关键：页面里存在**多个** file input（Markdown 编辑器的内联图片 input 在 DOM 里更靠前），
  // 按 "accept 含 image" 盲选会选错，必须锚定「封面图」标题所在容器。
  const a12 = await evalJs(`(async () => {
    const heading = [...document.querySelectorAll('h3')].find(el => el.textContent?.includes('封面图'))
    const scope = heading?.parentElement ?? document
    const input = scope.querySelector('input[type="file"]')
    if (!input) return { ok: false, reason: '封面上传输入未找到' }
    const bytes = Uint8Array.from(atob(${JSON.stringify(pngBase64)}), c => c.charCodeAt(0))
    const file = new File([bytes], 'e2e-cover.png', { type: 'image/png' })
    const dt = new DataTransfer(); dt.items.add(file)
    input.files = dt.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
    // 轮询等待预览（上传 + 回填表单需要时间）
    let hadPreview = false
    for (let i = 0; i < 16; i++) {
      await new Promise(r => setTimeout(r, 500))
      if (scope.querySelector('img[alt="封面预览"]')) { hadPreview = true; break }
    }
    const toast = /封面已上传|图片过大/.test(document.body.innerText)
    const remove = [...scope.querySelectorAll('button')].find(b => /移除/.test(b.textContent))
    remove?.click()
    await new Promise(r => setTimeout(r, 800))
    return { ok: true, hadPreview, toast, removed: !scope.querySelector('img[alt="封面预览"]') }
  })()`, true)
  record(
    'A12 封面上传 → 预览 → 移除',
    a12?.ok && a12?.hadPreview && a12?.removed,
    `预览出现=${a12?.hadPreview} 上传toast=${a12?.toast} 已移除=${a12?.removed}`,
  )

  // A13 正文图片上传（工具栏）
  const a13 = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => /图片/.test(b.textContent))
    if (!btn) return { ok: false, reason: '工具栏图片按钮未找到' }
    btn.click()
    await new Promise(r => setTimeout(r, 500))
    const input = document.querySelector('input[type="file"]')
    if (!input) return { ok: false, reason: '插入图片的文件输入未出现' }
    const bytes = Uint8Array.from(atob(${JSON.stringify(pngBase64)}), c => c.charCodeAt(0))
    const file = new File([bytes], 'e2e-inline.png', { type: 'image/png' })
    const dt = new DataTransfer(); dt.items.add(file)
    input.files = dt.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await new Promise(r => setTimeout(r, 3000))
    const ta = document.querySelector('textarea')
    return { ok: true, inserted: Boolean(ta && ta.value.includes('![')) }
  })()`, true)
  record('A13 正文图片上传并插入 Markdown', a13?.ok && a13?.inserted, `已插入=${a13?.inserted}`)

  // ================================================================ C. 列表与筛选边界
  // C0 建 9 篇种子文章（共享唯一标签），用于分页边界
  const seedTag = tagName
  const seedIds = []
  for (let i = 1; i <= 9; i++) {
    const r = await api('/api/v1/articles', {
      method: 'POST',
      body: JSON.stringify({
        title: `${RUN} 分页种子 #${i}`,
        content_md: `## 种子 ${i}\n\n用于分页边界验证。`,
        status: 'published',
        tags: [seedTag],
      }),
    })
    if (r.body?.id) seedIds.push(r.body.id)
  }
  registry.articles.push(...seedIds)
  record('C0 创建 9 篇分页种子文章', seedIds.length === 9, `成功 ${seedIds.length}/9`)

  // C1 分页首/末页按钮禁用 + 区间文案
  // 注意：按钮的可访问名在 aria-label 上（可见文案只有箭头），按文案找会找不到
  const readPager = `(() => {
    const prev = document.querySelector('[aria-label="上一页"]')
    const next = document.querySelector('[aria-label="下一页"]')
    const current = document.querySelector('[aria-current="page"]')
    return {
      prevDisabled: prev ? prev.disabled : null,
      nextDisabled: next ? next.disabled : null,
      current: current?.textContent?.trim() ?? '',
      text: document.body.innerText.match(/\\d+\\s*[–-]\\s*\\d+\\s*\\/\\s*共\\s*\\d+\\s*条/)?.[0] ?? '',
    }
  })()`
  await goto(`/?tag=${encodeURIComponent(tag?.slug ?? seedTag)}`, 2800)
  const c1page1 = await evalJs(readPager)
  const c1page2 = await evalJs(`(async () => {
    document.querySelector('[aria-label="下一页"]')?.click()
    await new Promise(r => setTimeout(r, 2400))
    const prev = document.querySelector('[aria-label="上一页"]')
    const next = document.querySelector('[aria-label="下一页"]')
    const current = document.querySelector('[aria-current="page"]')
    return {
      prevDisabled: prev ? prev.disabled : null,
      nextDisabled: next ? next.disabled : null,
      current: current?.textContent?.trim() ?? '',
      text: document.body.innerText.match(/\\d+\\s*[–-]\\s*\\d+\\s*\\/\\s*共\\s*\\d+\\s*条/)?.[0] ?? '',
    }
  })()`, true)
  record(
    'C1a 第一页：上一页禁用 / 下一页可用',
    c1page1?.prevDisabled === true && c1page1?.nextDisabled === false,
    `prev禁用=${c1page1?.prevDisabled} next禁用=${c1page1?.nextDisabled} 区间「${c1page1?.text}」`,
  )
  record(
    'C1b 第二页：上一页可用 / 下一页禁用',
    c1page2?.prevDisabled === false && c1page2?.nextDisabled === true,
    `prev禁用=${c1page2?.prevDisabled} next禁用=${c1page2?.nextDisabled} 区间「${c1page2?.text}」`,
  )

  // C2 搜索无结果空态 + 清除筛选
  await goto(`/?keyword=zzz-${RUN}`, 2600)
  const c2empty = await evalJs(`(() => ({
    empty: /没有匹配|没有找到|暂无/.test(document.body.innerText),
    hasClear: [...document.querySelectorAll('button,a')].some(el => /清除筛选/.test(el.textContent)),
  }))()`)
  const c2clear = await evalJs(`(async () => {
    const el = [...document.querySelectorAll('button,a')].find(e => /清除筛选/.test(e.textContent))
    el?.click()
    await new Promise(r => setTimeout(r, 2200))
    return { ok: true, query: location.search, hasCards: document.querySelectorAll('article, [class*="card"]').length > 0 }
  })()`, true)
  record('C2a 搜索无结果空态 + 清除筛选入口', c2empty?.empty && c2empty?.hasClear, `空态=${c2empty?.empty}`)
  record('C2b 清除筛选恢复列表', c2clear?.ok && !c2clear?.query.includes('keyword') && c2clear?.hasCards, `query=「${c2clear?.query}」`)

  // C3 关键词搜索命中
  await goto(`/?keyword=${RUN}`, 2600)
  const c3 = await evalJs(`(() => ({
    heading: document.querySelector('h1')?.textContent?.trim() ?? '',
    hasCard: /E2E/.test(document.body.innerText),
  }))()`)
  record('C3 关键词搜索命中', c3?.hasCard && /搜索/.test(c3?.heading), `h1=「${c3?.heading}」`)

  // C4 标签/分类筛选与 URL 同步 + 前进后退还原
  await goto(`/?tag=${encodeURIComponent(tag?.slug ?? seedTag)}`, 2400)
  const c4tag = await evalJs(`(() => ({ heading: document.querySelector('h1')?.textContent?.trim() ?? '', query: location.search }))()`)
  await goto(`/?category=${encodeURIComponent(cat?.slug ?? '')}`, 2400)
  const c4cat = await evalJs(`(() => ({ heading: document.querySelector('h1')?.textContent?.trim() ?? '', query: location.search }))()`)
  // history.back() 会让当前执行上下文失效，所以分两步：先触发后退，等页面稳定后再读
  await evalJs(`(() => { history.back(); return true })()`)
  await sleep(2600)
  const c4back = await evalJs(`(() => ({ query: location.search, heading: document.querySelector('h1')?.textContent?.trim() ?? '' }))()`)
  record(
    'C4a 标签筛选与 URL 同步',
    /标签/.test(c4tag?.heading ?? '') && (c4tag?.query ?? '').includes('tag='),
    `h1=「${c4tag?.heading}」`,
  )
  record(
    'C4b 分类筛选与 URL 同步',
    /分类/.test(c4cat?.heading ?? '') && (c4cat?.query ?? '').includes('category='),
    `h1=「${c4cat?.heading}」`,
  )
  record(
    'C4c 浏览器后退还原筛选状态',
    (c4back?.query ?? '').includes('tag=') && /标签/.test(c4back?.heading ?? ''),
    `回到 query=「${c4back?.query}」h1=「${c4back?.heading}」`,
  )

  // C5 五种排序
  const sorts = ['oldest', 'hottest', 'updated', 'title']
  let sortOk = 0
  for (const s of sorts) {
    await goto(`/?sort=${s}`, 2200)
    const got = await evalJs(`(() => ({ query: location.search, hasCards: document.querySelectorAll('article, [class*="card"]').length > 0 }))()`)
    if ((got?.query ?? '').includes(`sort=${s}`) && got?.hasCards) sortOk++
  }
  record('C5 五种排序均生效', sortOk === sorts.length, `${sortOk}/${sorts.length} 生效（含默认 latest）`)

  // ================================================================ D. 详情页交互（打在本轮临时文章上）
  const detail = await api(`/api/v1/articles/${articleId}`)
  const slug = detail.body?.slug ?? a2?.slug
  if (!slug) {
    record('D0 取得临时文章 slug', false, '拿不到 slug，D 组用例跳过')
    uncovered.push('D 组（详情页交互）— 临时文章 slug 获取失败')
  } else {
    await goto(`/article/${encodeURIComponent(slug)}`, 3000)

    // D1 点赞
    const d1 = await evalJs(`(async () => {
      const before = document.body.innerText
      const btn = [...document.querySelectorAll('button')].find(b => /^(点赞|已点赞)/.test(b.textContent.trim()))
      if (!btn) return { ok: false, reason: '点赞按钮未找到' }
      btn.click()
      await new Promise(r => setTimeout(r, 2200))
      const after = document.body.innerText
      return { ok: true, label: [...document.querySelectorAll('button')].find(b => /已点赞|点赞/.test(b.textContent))?.textContent?.trim() ?? '', thanked: /感谢支持/.test(after), changed: before !== after }
    })()`, true)
    record('D1 点赞按钮（文案 + toast）', d1?.ok && /已点赞/.test(d1?.label ?? '') && d1?.thanked, `按钮=「${d1?.label}」`)

    // D2 上下篇
    const d2 = await evalJs(`(() => {
      const links = [...document.querySelectorAll('a[href^="/article/"]')].map(a => a.getAttribute('href'))
      return { total: links.length }
    })()`)
    record('D2 上下篇导航链接', (d2?.total ?? 0) >= 0 && (d2?.total ?? 0) > 0, `文章内链接 ${d2?.total} 个`)

    // D3 相关文章
    const related = await api(`/api/v1/articles/${articleId}/related`)
    record('D3 相关文章接口与区块', Array.isArray(related.body), `related 返回 ${related.body?.length ?? 0} 条`)

    // D4 代码块复制
    const d4 = await evalJs(`(async () => {
      const btn = document.querySelector('[class*="code-block__copy"]')
      if (!btn) return { ok: false, reason: '复制按钮未找到' }
      btn.click()
      await new Promise(r => setTimeout(r, 900))
      const done = btn.dataset.state === 'done' || /已复制/.test(btn.textContent)
      const fallbackToast = /不允许自动复制|请手动选中/.test(document.body.innerText)
      await new Promise(r => setTimeout(r, 1800))
      const reverted = btn.dataset.state !== 'done' && !/已复制/.test(btn.textContent)
      return { ok: true, done, fallbackToast, reverted }
    })()`, true)
    record(
      'D4 代码块复制按钮',
      d4?.ok && (d4?.done || d4?.fallbackToast),
      d4?.done ? `已复制态出现，${d4?.reverted ? '并已自动还原' : '未还原'}` : '剪贴板不可用，走了降级提示',
    )

    // D5 目录点击跳转 + hash 深链
    const d5 = await evalJs(`(async () => {
      const link = document.querySelector('nav[aria-label="文章目录"] a[href^="#"]')
      if (!link) return { ok: false, reason: '目录链接未找到' }
      const id = link.getAttribute('href')
      link.click()
      await new Promise(r => setTimeout(r, 900))
      return { ok: true, hash: location.hash, scrolled: window.scrollY > 0, id }
    })()`, true)
    record('D5 目录点击跳转与 hash 变化', d5?.ok && d5?.hash === d5?.id, `hash=${d5?.hash} 已滚动=${d5?.scrolled}`)

    // D6 移动端浮动目录
    await send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true,
    })
    await sleep(1200)
    const d6 = await evalJs(`(async () => {
      const trigger = [...document.querySelectorAll('button')].find(b => /目录/.test(b.getAttribute('aria-label') ?? '') || /目录/.test(b.textContent))
      if (!trigger) return { ok: false, reason: '浮动目录触发按钮未找到' }
      trigger.click()
      await new Promise(r => setTimeout(r, 900))
      const dialog = document.querySelector('[role="dialog"]')
      const item = dialog?.querySelector('a[href^="#"]')
      item?.click()
      await new Promise(r => setTimeout(r, 900))
      return { ok: true, opened: Boolean(dialog), closedAfterPick: !document.querySelector('[role="dialog"]') }
    })()`, true)
    record('D6 移动端浮动目录（打开 → 选中即关闭）', d6?.ok && d6?.opened, `打开=${d6?.opened} 选中后关闭=${d6?.closedAfterPick}`)

    // D7 阅读进度条
    const d7 = await evalJs(`(async () => {
      window.scrollTo(0, document.body.scrollHeight)
      await new Promise(r => setTimeout(r, 1000))
      const bar = document.querySelector('[role="progressbar"]')
      return { ok: true, exists: Boolean(bar), value: Number(bar?.getAttribute('aria-valuenow') ?? 0) }
    })()`, true)
    record('D7 阅读进度条（滚到底后 value > 0）', d7?.exists && (d7?.value ?? 0) > 0, `aria-valuenow=${d7?.value}`)
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 1200,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await sleep(800)

    // D8 二级评论（站长发言 → 立即发布）
    const d8 = await evalJs(`(async () => {
      const box = document.querySelector('#comment-form')
      const ta = box?.querySelector('textarea')
      const submit = [...(box?.querySelectorAll('button') ?? [])].find(b => b.textContent.includes('发表评论'))
      if (!ta || !submit) return { ok: false, reason: '评论表单未找到' }
      ta.value = ${JSON.stringify(`E2E 评论 ${RUN}`)}
      ta.dispatchEvent(new Event('input', { bubbles: true }))
      await new Promise(r => setTimeout(r, 200))
      submit.click()
      await new Promise(r => setTimeout(r, 2600))
      const items = [...document.querySelectorAll('#comments li')]
      const mine = items.find(li => li.textContent.includes(${JSON.stringify(RUN)}))
      const replyBtn = [...(mine?.querySelectorAll('button') ?? [])].find(b => /回复/.test(b.textContent))
      replyBtn?.click()
      await new Promise(r => setTimeout(r, 600))
      const hint = /正在回复/.test(document.body.innerText)
      const ta2 = document.querySelector('#comment-form textarea')
      if (ta2) {
        ta2.value = ${JSON.stringify(`E2E 回复 ${RUN}`)}
        ta2.dispatchEvent(new Event('input', { bubbles: true }))
        await new Promise(r => setTimeout(r, 200))
        const submit2 = [...document.querySelectorAll('#comment-form button')].find(b => b.textContent.includes('发表评论'))
        submit2?.click()
        await new Promise(r => setTimeout(r, 2600))
      }
      const nested = document.querySelectorAll('#comments ul li').length
      return { ok: true, posted: Boolean(mine), hint, nested }
    })()`, true)
    // 失败详情要把三个条件都列出来：只写 hint 的话，`posted=false`
    //（评论压根没出现）看起来会和「评论出现了但回复按钮没生效」一模一样，
    // 而这两者的排查方向完全不同。
    record(
      'D8 二级评论：发表 → 回复 → 嵌套渲染',
      d8?.ok && d8?.posted && d8?.hint,
      `已发表=${d8?.posted} 回复提示=${d8?.hint} 嵌套项=${d8?.nested}` +
        `${d8?.reason ? ` 原因=${d8.reason}` : ''} `,
    )

    // 登记本轮评论，供 cleanup 回收
    const commentList = await api(`/api/v1/comments/article/${articleId}`)
    const collect = (list) => {
      for (const c of list ?? []) {
        if (c.content?.includes(RUN)) registry.comments.push(c.id)
        if (c.replies?.length) collect(c.replies)
      }
    }
    collect(commentList.body)
  }

  // ================================================================ B. 认证与主题
  // B2 主题三态循环 + 持久化
  await goto('/', 2400)
  const b2 = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => /主题|切换/.test(b.getAttribute('aria-label') ?? ''))
    if (!btn) return { ok: false, reason: '主题按钮未找到' }
    const read = () => ({ label: btn.getAttribute('aria-label') ?? '', cls: document.documentElement.className, stored: localStorage.getItem('blog-theme') })
    const seq = [read()]
    for (let i = 0; i < 3; i++) { btn.click(); await new Promise(r => setTimeout(r, 700)); seq.push(read()) }
    return { ok: true, seq, labels: seq.map(s => s.label), stored: seq.map(s => s.stored), dark: seq.map(s => s.cls.includes('dark')) }
  })()`, true)
  const labelsOk = new Set(b2?.labels ?? []).size >= 3
  record('B2 主题三态循环与持久化', b2?.ok && labelsOk, `三态=${(b2?.labels ?? []).join(' | ')}`)

  // B3 未登录访问后台被重定向
  // 「未登录」现在等价于「没有 refresh Cookie」：清掉它，应用启动时就换不出
  // access token，路由守卫必然把人弹到登录页。
  await clearCredentials()
  await goto('/admin/articles', 2600)
  const b3 = await evalJs(`(() => ({ path: location.pathname, search: location.search }))()`)
  record(
    'B3 未登录访问后台 → 重定向到 /login?redirect=',
    b3?.path === '/login' && (b3?.search ?? '').includes('redirect='),
    `${b3?.path}${b3?.search}`,
  )

  // B1 登出（重新登录后从后台登出）
  const b1Login = await loginAs(ADMIN.username, ADMIN.password)
  if (!b1Login.ok) uncovered.push(`B1 前置条件未满足：${b1Login.reason}`)
  await goto('/admin/articles', 2600)
  const b1 = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '退出')
    if (!btn) return { ok: false, reason: '退出按钮未找到' }
    btn.click()
    await new Promise(r => setTimeout(r, 2400))
    return { ok: true, path: location.pathname }
  })()`, true)
  // 怎么证明"真的登出了"——两个看似显然的信号其实都是**永真**的：
  //   1. localStorage 里那两个 key 没了：它们在新方案里根本不再被写入，必然是 null；
  //   2. 页面内 fetch /auth/me 返回 401：access token 只活在 axios 拦截器里，
  //      裸 fetch 从来带不上它，登没登出都回 401。
  // 唯一可信的信号是**应用自己的行为**：重新访问受保护的后台页，路由守卫必须
  // 把人弹回登录页。没弹回，就说明会话还在。
  await goto('/admin/articles', 2600)
  const b1guard = await evalJs(`(() => ({ path: location.pathname, search: location.search }))()`)
  // 第三个信号：服务端下发的删 Cookie 指令确实生效了。Cookie 还留着的话，
  // 每次刷新请求都会把一枚已作废的 token 送上去。
  const refreshGone = (await readRefreshCookie()) === null
  record(
    'B1 登出：会话失效（后台页被弹回登录页）+ Cookie 已删',
    b1?.ok &&
      b1?.path === '/login' &&
      b1guard?.path === '/login' &&
      (b1guard?.search ?? '').includes('redirect=') &&
      refreshGone,
    `退出后=${b1?.path} 再访问后台→${b1guard?.path}${b1guard?.search}` +
      ` refreshCookie=${refreshGone ? '已删除' : '仍存在'}${b1?.reason ? ` 原因=${b1.reason}` : ''}`,
  )

  // B1 刚做过登出：服务端已吊销令牌，这里必须换一枚新的，
  // 否则下面重置 role 的 PATCH 与收尾清理都会 401（表现为 B4 失败 + 满地残留）。
  const afterLogout = await relogin()
  if (!afterLogout.ok) {
    uncovered.push(`登出后重新登录失败（HTTP ${afterLogout.status}）：后续用例与清理可能受影响`)
  }

  // B4 非站长访问受限后台页被弹回首页
  // 前置条件显式化：把 role 与 is_active **一起**设成"作者且启用"。
  // 只设 role 会让这条用例依赖前序用例留下的状态（A8c 的停用/启用），
  // 一旦那个状态有偏差，失败信息会指向"权限守卫有问题"——误导排查方向。
  const roleReset = await api(`/api/v1/auth/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify({ role: 'author', is_active: true }),
  })
  // 这一步失败时如果继续跑，B4 的失败信息会是「没被弹回首页」，
  // 看起来像权限守卫坏了，实际是前置条件没设上——显式说出来，别误导排查方向。
  if (roleReset.status !== 200) {
    uncovered.push(
      `B4 前置条件未设上：重置临时账号为 author 失败（HTTP ${roleReset.status} ${JSON.stringify(roleReset.body).slice(0, 80)}）`,
    )
  }
  // 只能**在浏览器里**换身份：access token 不落 localStorage，页面内 fetch 拿不到
  // 它（Node 侧拿到的那枚也塞不进页面），唯一入口就是登录页。
  const authorLogin = await loginAs(userName, 'E2ePassw0rd!')
  if (authorLogin.ok) {
    await goto('/admin/users', 2600)
    const b4users = await evalJs(`(() => ({ path: location.pathname }))()`)
    await goto('/admin/settings', 2600)
    const b4settings = await evalJs(`(() => ({ path: location.pathname }))()`)
    record(
      'B4 非站长访问 /admin/users 与 /admin/settings 被弹回首页',
      b4users?.path === '/' && b4settings?.path === '/',
      `users→${b4users?.path} settings→${b4settings?.path}`,
    )
  } else {
    record(
      'B4 非站长访问受限后台页',
      false,
      `临时 author 登录失败：${authorLogin.reason}`,
    )
    uncovered.push(`B4 — 临时 author 登录失败（${authorLogin.reason}）`)
  }
  // 这里**不**再切回站长：E 段全是公开页（404 / RSS / sitemap / 标题），
  // 不需要登录态；省掉这次登录是因为 /auth/login 有 5 次 / 60 秒的限流，
  // 而 B 段刚连着登了三次，再叠一次就把后面的收尾清理挤成 429。

  // ================================================================ E. 站点与元信息
  await goto(`/nope-${RUN}`, 2400)
  const e1 = await evalJs(`(() => ({ text: document.body.innerText.slice(0, 120), title: document.title, path: location.pathname }))()`)
  record(
    'E1 404 页面与标题',
    /404/.test(e1?.text ?? '') && (e1?.title ?? '').includes('页面不存在'),
    `title=「${e1?.title}」`,
  )

  const e2 = await evalJs(`(async () => {
    const rss = await fetch('/feed.xml')
    const rssType = rss.headers.get('content-type') ?? ''
    const rssText = await rss.text()
    const map = await fetch('/sitemap.xml')
    const mapType = map.headers.get('content-type') ?? ''
    const mapText = await map.text()
    return { rssOk: rss.ok, rssType, hasRssTag: rssText.includes('<rss'), mapOk: map.ok, mapType, hasUrlset: mapText.includes('<urlset') }
  })()`, true)
  record(
    'E2 RSS / sitemap 可达且 Content-Type 正确',
    e2?.rssOk && /rss\+xml/.test(e2?.rssType ?? '') && e2?.hasRssTag && e2?.mapOk && e2?.hasUrlset,
    `rss=${e2?.rssType} sitemap=${e2?.mapType}`,
  )

  const titles = []
  for (const [path, expect] of [
    ['/', '首页'],
    ['/archive', '归档'],
    ['/tags', '标签'],
    ['/about', '关于'],
  ]) {
    await goto(path, 2000)
    const t = await evalJs(`(() => document.title)()`)
    titles.push(`${path}→${t}（期望含「${expect}」）`)
    if (!String(t).includes(expect)) titles.push('★不匹配')
  }
  record('E3 document.title 随路由变化', !titles.includes('★不匹配'), titles.join(' '))

  // E4 前台登录入口开关（site_profile.show_login_entry）。
  //
  // 为什么必须放在这里：E 段是**匿名**浏览器状态（B1 登出后 Cookie 已删），
  // 而「登录」入口只在匿名时渲染 —— 用已经登录的浏览器根本看不到它。
  // 开关用 API 切（token 是 B1 之后重新取的那枚），因此不额外消耗 /auth/login
  // 的 5 次/分钟配额（B 段刚登过三次，见上面的注释）。
  //
  // 为什么要关 HTTP 缓存：`/api/v1/site/profile` 是公开可缓存接口（max-age=60），
  // 第一次匿名加载会把它存进浏览器缓存 —— 不关的话第二次加载拿到的还是旧档案，
  // 表现为"开关明明关了前台还显示"，一条彻头彻尾的假失败。
  const loginEntryCount = () =>
    evalJs(`document.querySelectorAll('header a[href="/login"]').length`)

  const patchLoginEntry = async (visible) => {
    let resp = await api('/api/v1/site/profile', {
      method: 'PATCH',
      body: JSON.stringify({ show_login_entry: visible }),
    })
    if (resp.status === 401) {
      // 令牌在这之前被吊销了（例如又跑过一次登出）：换一枚再试，
      // 且**只在需要时**登录，避免常态下多消耗一次登录配额
      await relogin()
      resp = await api('/api/v1/site/profile', {
        method: 'PATCH',
        body: JSON.stringify({ show_login_entry: visible }),
      })
    }
    return resp
  }

  await send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {})
  // 必须先切回匿名：B4「非站长访问后台被弹回首页」把浏览器留在了**已登录**状态，
  // 而已登录时顶栏渲染的是「后台」—— 两个开关状态下都数不到「登录」入口，
  // 用例会以"关 0 个 / 开 0 个"的形状失败（本轮第一版正是如此）。
  // 后面只剩截图与收尾，清理走的是 API + relogin，不依赖浏览器会话。
  await send('Network.clearBrowserCookies').catch(() => {})
  const e4Before = (await api('/api/v1/site/profile')).body?.show_login_entry
  let e4Off
  let e4On
  let e4Hidden = -1
  let e4Shown = -1
  try {
    e4Off = await patchLoginEntry(false)
    await goto('/', 2000)
    e4Hidden = await loginEntryCount()

    e4On = await patchLoginEntry(true)
    await goto('/', 2000)
    e4Shown = await loginEntryCount()
  } finally {
    // 无论断言成败都要把开关还原：留在"关"的状态会让后面的用例与手工复核
    // 都看到与站点默认不一样的界面
    if (e4On?.body?.show_login_entry !== true) await patchLoginEntry(true)
    await send('Network.setCacheDisabled', { cacheDisabled: false }).catch(() => {})
  }
  record(
    'E4 后台关掉登录入口开关后前台真的不再显示（再打开即恢复）',
    e4Off?.status === 200 &&
      e4Off?.body?.show_login_entry === false &&
      e4Hidden === 0 &&
      e4On?.status === 200 &&
      e4On?.body?.show_login_entry === true &&
      e4Shown === 1,
    `关：HTTP ${e4Off?.status} 字段=${e4Off?.body?.show_login_entry} 渲染 ${e4Hidden} 个；` +
      `开：HTTP ${e4On?.status} 字段=${e4On?.body?.show_login_entry} 渲染 ${e4Shown} 个（初始 ${e4Before}）`,
  )

  // ================================================================ E5–E8 电梯栏 + 二维码 + 目录改造
  //
  // 计划 Phase 2 的明文验收（deliverables/article-detail-upgrade-plan-2026-09-27.md §五）：
  //   ① 配置为空 → 联系按钮不渲染、返回顶部仍可用；
  //   ② 配置齐全 → 桌面 hover/点击展开弹层，含二维码图与复制按钮；
  //   ③ 移动端电梯栏与 MobileToc 按钮不重叠（原文口径是"截图比对 ≥ 12px"，这里换成
  //      getBoundingClientRect 的几何量：截图既脆又读不出数字，几何量还能写进失败信息）；
  //   ④ 目录：默认折叠 depth≥3、折叠按钮可切换、点章节改 hash（replaceState 语义）、滚动高亮。
  //
  // 三个容易写出假绿的地方，这里都堵上了：
  //   - **不写"存在即通过"**：返回顶部在 scrollY=0 时必须是 0 个、滚过 300 之后才 1 个；
  //     目录默认折叠要同时证「折叠按钮 aria-expanded=false」与「收起分支的子项根本没渲染」——
  //     只查前者的话，一个永远渲染全部条目的实现照样绿。
  //   - **切配置必须关 HTTP 缓存**：`/api/v1/site/profile` 是 max-age=60 的公开接口，复用缓存
  //     会拿到改配置之前的档案（E4 第一版就是这么假失败的，见 docs/devlog/2026-09-28.md）。
  //   - **改配置必须自清理**：段内先读一次真实值，finally 里还原并**复核**（E6c），否则一次
  //     失败就会把测试用的二维码配置永久留在站点上。
  //
  // 为什么自建一篇长文、而不是挑一篇演示文章：种子文章的层级只到 h3（## → ###），而"默认折叠"
  // 的判据是「depth ≥ 3 **且有子节点**」——演示文章里没有任何这种节点，折叠按钮根本不会渲染，
  // 计划原文那句"挑一篇标题层级够多的演示文章"在真实种子数据上无法成立。所以这里自建
  // h2→h3→h4 三层长文（登记进 registry，收尾一并回收），层级与长度都可控。
  const tocArticleTitle = `E2E 目录长文 ${RUN}`
  const filler = (count, tag) =>
    Array.from(
      { length: count },
      (_, index) =>
        `第 ${index + 1} 段${tag}填充文本：用来撑开正文高度，让"返回顶部"阈值与滚动高亮有真实的滚动距离。`,
    ).join('\n\n')
  const tocArticleBody = [
    '## 第一节 概览',
    '',
    '### 1.1 子节甲',
    '',
    '#### 1.1.1 细节一',
    '',
    filler(6, 'A'),
    '',
    '#### 1.1.2 细节二',
    '',
    filler(6, 'B'),
    '',
    '### 1.2 子节乙',
    '',
    '#### 1.2.1 细节三',
    '',
    filler(6, 'C'),
    '',
    '## 第二节 实践',
    '',
    '### 2.1 子节丙',
    '',
    '#### 2.1.1 细节四',
    '',
    filler(6, 'D'),
    '',
    '### 2.2 子节丁',
    '',
    filler(20, 'E'),
    '',
    '## 第三节 结论',
    '',
    filler(40, 'F'),
  ].join('\n')
  const tocCreate = await api('/api/v1/articles', {
    method: 'POST',
    body: JSON.stringify({
      title: tocArticleTitle,
      content_md: tocArticleBody,
      status: 'published',
    }),
  })
  const tocArticleId = tocCreate.body?.id
  if (tocArticleId) registry.articles.push(tocArticleId)
  const tocSlug = tocArticleId
    ? ((await api(`/api/v1/articles/${tocArticleId}`)).body?.slug ?? '')
    : ''
  record(
    'E5-0 前置：自建三层目录长文（h2→h3→h4，供 E5–E8 使用）',
    tocCreate.status === 201 && Boolean(tocSlug),
    `HTTP ${tocCreate.status} id=${tocArticleId} slug=${tocSlug}`,
  )

  if (!tocSlug) {
    record('E5–E8 电梯栏与目录', false, '三层目录长文没建出来，整段无法进行')
    uncovered.push('E5–E8 电梯栏与目录 — 前置长文创建失败')
  } else {
    const ARTICLE = `/article/${encodeURIComponent(tocSlug)}`
    const CONTACT_BTN = 'button[aria-label="联系站长"]'
    const TOP_BTN = 'button[aria-label="返回顶部"]'
    const PANEL = '[role="dialog"][aria-label="联系方式"]'

    /** 改站点档案（401 时换一枚令牌再试，与 E4 的 patchLoginEntry 同款）。 */
    const patchProfile = async (payload) => {
      let resp = await api('/api/v1/site/profile', {
        method: 'PATCH',
        body: JSON.stringify(payload),
      })
      if (resp.status === 401) {
        await relogin()
        resp = await api('/api/v1/site/profile', {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return resp
    }
    const contactButtons = () => evalJs(`document.querySelectorAll('${CONTACT_BTN}').length`)
    const topButtons = () => evalJs(`document.querySelectorAll('${TOP_BTN}').length`)
    /**
     * 立刻跳转（显式 behavior:'instant' 绕开 base.css 的 `scroll-behavior: smooth`）。
     * 平滑滚动会让页面经过一串中间位置，IntersectionObserver 会对着中间态回调，
     * 断言就变成"碰运气命中最后一次回调"；一次跳到位的语义则完全确定。
     */
    const jumpScroll = async (y) => {
      await evalJs(
        `(() => { window.scrollTo({ top: ${y}, behavior: 'instant' }); return Math.round(window.scrollY) })()`,
      )
      await sleep(600)
      return evalJs(`Math.round(window.scrollY)`)
    }

    // ---- E5 配置为空 → 联系按钮不渲染、返回顶部仍可用 ------------------------------
    // 两侧都要断言：只写"滚过之后返回顶部出现了"的话，一个常驻渲染的实现也是绿的 ——
    // 阈值 300（决策 D-电梯-2）必须在 scrollY=0 时表现为"没有这颗按钮"。
    await send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {})
    const contactBefore = (await api('/api/v1/site/profile')).body?.contact_qrcodes ?? null
    const profileHitsBefore = profileResponses.length
    let e5Off = null
    let e5ContactCount = -1
    let e5TopAtZero = -1
    let e5TopAfterScroll = -1
    let e5BackTo = -1
    try {
      e5Off = await patchProfile({ contact_qrcodes: [] })
      await goto(ARTICLE, 2800)
      await waitFor(evalJs, `!!document.querySelector('.prose h2')`, 20000)
      await jumpScroll(0)
      e5TopAtZero = await topButtons()
      e5ContactCount = await contactButtons()
      await jumpScroll(420)
      await waitFor(evalJs, `document.querySelectorAll('${TOP_BTN}').length === 1`, 8000)
      e5TopAfterScroll = await topButtons()
      // 点一下要真的回顶：组件用的是 smooth 滚动，轮询等它走完再读
      await evalJs(`(() => { document.querySelector('${TOP_BTN}')?.click(); return true })()`)
      await waitFor(evalJs, `window.scrollY < 60`, 8000)
      e5BackTo = await evalJs(`Math.round(window.scrollY)`)
    } finally {
      await patchProfile({ contact_qrcodes: contactBefore })
      await send('Network.setCacheDisabled', { cacheDisabled: false }).catch(() => {})
    }
    const e5ProfileSeen = profileResponses.slice(profileHitsBefore)
    record(
      'E5 配置为空 → 联系按钮不渲染，返回顶部仍可用（阈值 300 双向）',
      e5Off?.status === 200 &&
        Array.isArray(e5Off?.body?.contact_qrcodes) &&
        e5Off.body.contact_qrcodes.length === 0 &&
        e5ProfileSeen.includes(200) &&
        e5ContactCount === 0 &&
        e5TopAtZero === 0 &&
        e5TopAfterScroll === 1 &&
        e5BackTo >= 0 &&
        e5BackTo < 20,
      `PATCH=${e5Off?.status} 字段=${JSON.stringify(e5Off?.body?.contact_qrcodes)}` +
        `（本轮该页档案响应 ${JSON.stringify(e5ProfileSeen)}，证明是"读到空配置"而不是"档案没加载"）；` +
        `联系按钮 ${e5ContactCount} 个；scrollY=0 时回顶按钮 ${e5TopAtZero} 个、滚过 300 后 ${e5TopAfterScroll} 个；` +
        `点击回顶后 scrollY=${e5BackTo}`,
    )

    // ---- E6 配置齐全 → 联系站长弹层（hover / 点击 / 二维码图 / 复制 / Esc 还原焦点） ----
    // 这条同时是 E5 的**正向对照**：档案链路真的能影响渲染时，按钮必须出现 1 个。
    // 少了它，E5 的"0 个"有可能只是"档案没加载"（失败模式同样是 0 个）——正是 E4 踩过的坑。
    const CONTACT_FIXTURE = [
      { kind: 'wechat', label: 'E2E 微信', image_url: '/favicon.svg', value: 'e2e-wechat' },
      { kind: 'qq', label: '', image_url: null, value: '123456789' },
    ]
    let e6On = null
    let e6 = null
    let e6copy = null
    let e6Restore = null
    try {
      // 必须**自己**关缓存，不能指望 E5 留下。
      //
      // `/api/v1/site/profile` 是 `public, max-age=60`，而 E5 的 finally 在收尾时把
      // `cacheDisabled` 设回了 false —— 于是本段 PATCH 完整配置后再导航，浏览器直接
      // 复用 E5 期间缓存的那份**空配置**，组件按设计不渲染联系按钮，断言就变成了
      // 「配置齐全但按钮没渲染」这种极具误导性的失败。
      // 实测过 A/B 对照：不关缓存 → 0 个按钮（页面 fetch 到 0 条）；
      // 关缓存 → 1 个按钮（fetch 到 2 条）。详见 docs/devlog/2026-09-29.md。
      await send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {})
      e6On = await patchProfile({ contact_qrcodes: CONTACT_FIXTURE })
      // 「桌面悬停展开」这条路径取决于组件读到 `(hover: none)` 的判定，而无头浏览器
      // 可能自报 hover: none（没有真实指针设备），于是悬停分支被组件按设计关掉 ——
      // 断言会退化成"条件分支"，验收明文里那句"桌面 hover 展开"就没人守着了。
      // 这里尽力把媒体特性覆盖成 hover: hover；CDP 若不支持会静默无效，
      // 断言自动退回条件分支（detail 里会写明读到的 hover 能力）。
      await send('Emulation.setEmulatedMedia', {
        features: [
          { name: 'hover', value: 'hover' },
          { name: 'pointer', value: 'fine' },
        ],
      }).catch(() => {})
      await goto(ARTICLE, 2800)
      await waitFor(evalJs, `document.querySelectorAll('${CONTACT_BTN}').length === 1`, 20000)
      e6 = await evalJs(`(async () => {
        const wait = (ms) => new Promise((r) => setTimeout(r, ms))
        const btn = document.querySelector('${CONTACT_BTN}')
        if (!btn) return { ok: false, reason: '配置齐全但联系按钮没渲染' }
        const root = btn.parentElement
        const dialog = () => document.querySelector('${PANEL}')
        const hoverCapable = !window.matchMedia('(hover: none)').matches
        const detail = {}
        const initial = {
          haspopup: btn.getAttribute('aria-haspopup'),
          expanded: btn.getAttribute('aria-expanded'),
        }

        if (hoverCapable) {
          // 桌面：悬停展开 → 移开收起。触屏设备这条路径按设计不存在
          // （tap 会合成 mouseenter，一视同仁会出现"点一下先开后关"）
          root.dispatchEvent(new MouseEvent('mouseenter'))
          await wait(250)
          detail.hoverOpen = Boolean(dialog()) && btn.getAttribute('aria-expanded') === 'true'
          root.dispatchEvent(new MouseEvent('mouseleave'))
          await wait(250)
          detail.hoverClose = !dialog()
        }

        // 点击 = 钉住：之后移开鼠标仍然开着（这正是 pinned 相对 hovering 的意义）
        root.dispatchEvent(new MouseEvent('mouseenter'))
        btn.click()
        await wait(250)
        root.dispatchEvent(new MouseEvent('mouseleave'))
        await wait(250)
        detail.pinnedOpen = Boolean(dialog()) && btn.getAttribute('aria-expanded') === 'true'

        const box = dialog()
        if (!box) return { ok: false, reason: '点击后弹层没有出现', hoverCapable, initial, ...detail }
        const cards = [...box.querySelectorAll('li')]
        const img = box.querySelector('img')
        const copyCount = [...box.querySelectorAll('button')].filter((b) => b.textContent.trim() === '复制').length
        // 二维码必须**真的加载出来**（naturalWidth > 0）：只查 <img> 存在的话，
        // src 写错、图片 404 也算通过 —— 那是"有二维码图"这句话的反面
        let loaded = false
        for (let i = 0; i < 25 && !loaded; i += 1) {
          const el = box.querySelector('img')
          if (el && el.naturalWidth > 0) { loaded = true; break }
          await wait(200)
        }
        const cardText = box.textContent.replace(/\\s+/g, ' ').trim()
        // Esc 关闭 + 焦点还给触发按钮（关闭路径也是 Phase 2 验收的一部分）
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
        await wait(300)
        return {
          ok: true,
          hoverCapable,
          initial,
          ...detail,
          cards: cards.length,
          cardText,
          imgSrc: img ? img.getAttribute('src') : '',
          imgAlt: img ? img.getAttribute('alt') : '',
          imgLoaded: loaded,
          copyCount,
          closedByEsc: !dialog(),
          focusBack: document.activeElement === btn,
        }
      })()`, true)

      // 复制按钮：真的点一下，看有没有明确结果（成功提示，或 D4 同款的降级提示）
      e6copy = await evalJs(`(async () => {
        const wait = (ms) => new Promise((r) => setTimeout(r, ms))
        if (!document.querySelector('${PANEL}')) {
          document.querySelector('${CONTACT_BTN}')?.click()
          await wait(300)
        }
        const box = document.querySelector('${PANEL}')
        const copy = [...(box ? box.querySelectorAll('button') : [])].find((b) => b.textContent.trim() === '复制')
        if (!copy) return { ok: false, reason: '弹层里没有「复制」按钮' }
        copy.click()
        await wait(700)
        const text = document.body.innerText
        return { ok: true, success: /已复制/.test(text), fallback: /不允许自动复制|请手动选中/.test(text) }
      })()`, true)
    } finally {
      e6Restore = await patchProfile({ contact_qrcodes: contactBefore })
      // 媒体特性覆盖也要还原：留着 hover: hover 会让后面的用例看到的不是本机真实能力
      await send('Emulation.setEmulatedMedia', { features: [] }).catch(() => {})
      await send('Network.setCacheDisabled', { cacheDisabled: false }).catch(() => {})
    }
    const e6Text = e6?.cardText ?? ''
    record(
      'E6a 配置齐全 → 弹层含二维码图与复制按钮（hover/点击展开、Esc 关闭并归还焦点）',
      e6On?.status === 200 &&
        e6On?.body?.contact_qrcodes?.length === 2 &&
        e6?.ok === true &&
        e6?.initial?.haspopup === 'true' &&
        e6?.initial?.expanded === 'false' &&
        e6?.pinnedOpen === true &&
        e6?.cards === 2 &&
        /favicon\.svg$/.test(e6?.imgSrc ?? '') &&
        e6?.imgAlt === 'E2E 微信二维码' &&
        e6?.imgLoaded === true &&
        e6?.copyCount === 2 &&
        /E2E 微信/.test(e6Text) &&
        /e2e-wechat/.test(e6Text) &&
        /QQ/.test(e6Text) &&
        /123456789/.test(e6Text) &&
        e6?.closedByEsc === true &&
        e6?.focusBack === true &&
        (e6?.hoverCapable === false || (e6?.hoverOpen === true && e6?.hoverClose === true)),
      e6?.ok !== true
        ? `原因=${e6?.reason ?? '弹层状态没读回来（页面内异常）'}（hover 能力=${e6?.hoverCapable}）`
        : `PATCH=${e6On?.status} 回读 ${e6On?.body?.contact_qrcodes?.length} 条；初始 aria-haspopup=${e6?.initial?.haspopup} expanded=${e6?.initial?.expanded}；` +
          `hover 能力=${e6?.hoverCapable} 悬停展开=${e6?.hoverOpen} 悬停收起=${e6?.hoverClose} 点击钉住=${e6?.pinnedOpen}；` +
          `卡片=${e6?.cards} img=${e6?.imgSrc}（已加载=${e6?.imgLoaded} alt=${e6?.imgAlt}）复制按钮=${e6?.copyCount}；` +
          `Esc 关闭=${e6?.closedByEsc} 焦点归还=${e6?.focusBack}；文案=「${e6Text}」`,
    )
    record(
      'E6b 点「复制」有明确结果（成功提示，或 headless 下的降级提示）',
      e6copy?.ok === true && (e6copy?.success === true || e6copy?.fallback === true),
      e6copy?.ok !== true
        ? (e6copy?.reason ?? '复制结果没读回来（页面内异常）')
        : `成功提示=${e6copy?.success} 降级提示=${e6copy?.fallback}` +
          (e6copy?.success ? '' : '（剪贴板不可用，走了与 D4 同口径的降级提示）'),
    )
    // 自清理复核：改配置的用例失败时最容易留下的就是"站点被改了"，所以复原本身也是一条断言
    record(
      'E6c 段内改过的二维码配置已复原（自清理）',
      e6Restore?.status === 200 &&
        JSON.stringify(e6Restore?.body?.contact_qrcodes ?? null) === JSON.stringify(contactBefore ?? null),
      `PATCH=${e6Restore?.status} 现为 ${JSON.stringify(e6Restore?.body?.contact_qrcodes ?? null)}` +
        `（本轮开始时是 ${JSON.stringify(contactBefore ?? null)}）`,
    )

    // ---- E7 目录改造（折叠 / hash / 高亮） ----------------------------------------
    await goto(ARTICLE, 2800)
    await waitFor(evalJs, `!!document.querySelector('nav[aria-label="文章目录"]')`, 20000)

    // E7f 侧栏是否"吸住"：读长文时目录必须**整体**可见。
    //
    // 这条守过两个真 bug：
    //   1. 侧栏原先用绝对定位 + 内层 sticky，而 sticky 的 top 只在**父元素高度范围内**
    //      生效 —— 父元素只有目录自己那么高，滑过几百像素目录就被带出视口。
    //   2. （本批）sticky 的包含块是那个两列 flex 行，实测到文档 8940px 结束，
    //      而文章本体到 9652px、文档到 9972px —— 最后约 1000px 里目录被父容器带走。
    //      同时长目录（实测 1990px）比视口还高，底部压根够不到。
    //
    // 判据刻意用**整体在视口内**（`fullyVisible`）而不是"碰到视口就算可见"：
    // 后者的下限太松 —— 目录高 1990px 而视口 900px 时 `bottom > 0 && top < innerHeight`
    // 依然成立，目录被裁掉一半也照样判通过。第 2 个 bug 就是这样溜过去的。
    // 采样点也必须覆盖 **95% 与 100%**：包含块失效只发生在文章尾部，
    // 只测 25/40/55 三个中段位置的话，正好绕开了出问题的那一段。
    const e7f = await evalJs(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      const nav = document.querySelector('nav[aria-label="文章目录"]')
      if (!nav) return { ok: false, reason: '目录未渲染' }
      const article = document.querySelector('article')
      const samples = []
      const scrollable = document.documentElement.scrollHeight - window.innerHeight
      for (const pct of [0.25, 0.4, 0.55, 0.95, 1]) {
        window.scrollTo({ top: Math.round(scrollable * pct), behavior: 'instant' })
        await wait(280)
        const r = nav.getBoundingClientRect()
        samples.push({
          pct,
          scrollY: Math.round(window.scrollY),
          top: Math.round(r.top),
          bottom: Math.round(r.bottom),
          // 整体在视口内：上边缘不越顶、下边缘不越底
          fullyVisible: r.top >= 0 && r.bottom <= window.innerHeight,
          // 目录是否自身可滚（内容超出可视高度时，底部只能靠滚它自己到达）
          selfScrollable: nav.scrollHeight > nav.clientHeight,
        })
      }
      return {
        ok: true,
        samples,
        allFullyVisible: samples.every((s) => s.fullyVisible),
        maxTop: Math.max(...samples.map((s) => s.top)),
        articleHeight: article ? Math.round(article.getBoundingClientRect().height) : null,
        viewportH: window.innerHeight,
      }
    })()`, true)
    record(
      'E7f 侧栏吸住：读长文时目录**整体**可见（含 95%/100%，sticky 的包含块必须撑满全文）',
      e7f?.ok === true && e7f.allFullyVisible === true && e7f.maxTop < e7f.viewportH / 2,
      e7f?.ok !== true
        ? (e7f?.reason ?? '目录几何读不回来')
        : `正文 ${e7f.articleHeight}px / 视口 ${e7f.viewportH}px；` +
          e7f.samples
            .map(
              (s) =>
                `${Math.round(s.pct * 100)}% top=${s.top}/bottom=${s.bottom}` +
                `${s.fullyVisible ? '' : ' ❌超出视口'}` +
                `${s.selfScrollable ? '(目录可滚)' : ''}`,
            )
            .join('，') +
          `；全部整体可见=${e7f.allFullyVisible}`,
    )

    // E7h 目录滚动与正文滚动互不干扰（用户报的问题本身，此前没有任何断言守它）。
    //
    // 四条判据，全部与「目录有多长」无关：
    //   1. **面板不漂移**：同一 scrollY 下两次采样几何必须一致。
    //      只比"两次是否相同"不够 —— 基线（sticky 被父容器带走）在深滚处也是稳定值，
    //      只是 top 变成 -2122，所以要再断言下面第 2 条。
    //   2. **钉住**：面板 top 在三个不同滚动位置必须**完全相同**。
    //      这是"吸住"的定义。注意不能拿 top 跟某个小常数比 —— 设计值就是
    //      `top: 6rem`（=96px），第一版写成 `|top| <= 4` 是把 `<=` 看成了别的，
    //      结果断言恒假（96px 永不 ≤ 4）。判据只能是"与滚动位置无关"。
    //   3. **页尾仍整体可见**：滚到页尾时 top/bottom 都要落在视口内。
    //      包含块失效时 top 会变成很大的负数（基线实测 -2122），这条会立刻红。
    //   4. **滚动链的闸已装**：`overscroll-behavior-y === 'contain'`。
    //      断言计算样式而不是"真的滑一下看页面动不动"：浏览器没有脚本接口能触发
    //      滚动链（赋 scrollTop 与合成 wheel 事件都不产生滚动链），可判定的只剩"闸装没装"。
    //
    // 刻意**不**在此处断言「目录必须可滚」：本段造数文章 `E2E-目录长文` 只有 11 个标题
    // （H2x3 + H3x3 + H4x5），展开到不动点实测约 233px，在 1200px 视口里永远不可能滚动 ——
    // 拿它断言"可滚"是前提错误（第一版就是这么写红的）。
    // 长目录的可滚动性由 E7f 承担：它用的长文目录实测 1163~1990px，
    // 一旦不能滚动，bottom 就会冲出视口，E7f 的"整体可见"会立刻红。
    const e7h = await evalJs(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      const nav = document.querySelector('nav[aria-label="文章目录"]')
      if (!nav) return { ok: false, reason: '目录未渲染' }
      const scrollable = document.documentElement.scrollHeight - window.innerHeight
      const overscroll = getComputedStyle(nav).overscrollBehaviorY
      const rect = () => nav.getBoundingClientRect()
      const at = async (frac) => {
        window.scrollTo({ top: Math.round(scrollable * frac), behavior: 'instant' })
        await wait(320)
        const r = rect()
        return { frac, top: Math.round(r.top), bottom: Math.round(r.bottom) }
      }
      const s20 = await at(0.2)
      const s90 = await at(0.9)
      const s20again = await at(0.2)
      const sEnd = await at(1)
      window.scrollTo({ top: 0, behavior: 'instant' })
      await wait(280)
      const tops = [s20.top, s90.top, s20again.top, sEnd.top]
      return {
        ok: true,
        overscroll,
        geomA: [s20.top, s20.bottom],
        geomB: [s20again.top, s20again.bottom],
        geomDeep: [s90.top, s90.bottom],
        drift: Math.max(
          Math.abs(s20.top - s20again.top),
          Math.abs(s20.bottom - s20again.bottom),
        ),
        tops,
        pinned: tops.every((t) => t === tops[0]),
        deepTop: sEnd.top,
        deepBottom: sEnd.bottom,
        deepFullyVisible: sEnd.top >= 0 && sEnd.bottom <= window.innerHeight,
        navDims: nav.scrollHeight + '/' + nav.clientHeight,
      }
    })()`, true)
    record(
      'E7h 目录滚动与正文滚动互不干扰（面板不漂移 + overscroll:contain 切断滚动链）',
      e7h?.ok === true &&
        e7h.overscroll === 'contain' &&
        e7h.drift === 0 &&
        e7h.pinned === true &&
        e7h.deepFullyVisible === true,
      e7h?.ok !== true
        ? (e7h?.reason ?? '目录几何读不回来')
        : `overscroll-behavior-y=${e7h.overscroll}` +
          `${e7h.overscroll === 'contain' ? '' : ' [BAD] 未切断滚动链，目录滚到底会带走正文'}；` +
          `同一 scrollY 两次采样 top/bottom=${JSON.stringify(e7h.geomA)}->${JSON.stringify(e7h.geomB)}` +
          `（深滚处 ${JSON.stringify(e7h.geomDeep)}），漂移=${e7h.drift}` +
          `${e7h.drift !== 0 ? ' [BAD] 面板随页面漂移' : ''}；` +
          `面板 top 在三个滚动位置为 ${JSON.stringify(e7h.tops)}（钉住=${e7h.pinned}）` +
          `${e7h.pinned ? '' : ' [BAD] 位置随滚动变化 → 没吸住'}；` +
          `滚到页尾时 top=${e7h.deepTop}/bottom=${e7h.deepBottom}` +
          `${e7h.deepFullyVisible ? '' : ' [BAD] 未整体留在视口内 → 被父容器带走了'}；` +
          `目录 ${e7h.navDims}`,
    )

    // E7g 正文列宽与行宽：**"感觉正文好窄"这类问题只有量出来才守得住**。
    //
    // 一行多少字不硬编码：用 span 实测「一个汉字多宽」，再用 列宽 / 字宽 算出来。
    // 字号或字体一变，这个比值就跟着变 —— 那正是我们要盯的东西。
    // 目标区间来自仓库自己的口径（tailwind.config.js 里 maxWidth.content 写着
    // 「中文正文每行 38~42 字是舒适区」）与参考站实测（873px / 18px / 45 字）的交集。
    const e7g = await evalJs(`(() => {
      const article = document.querySelector('article')
      const col = article?.querySelector('.article-column')
      const p = article?.querySelector('.prose p')
      const aside = article?.querySelector('aside')
      if (!col || !p) return { ok: false, reason: '找不到正文列或正文段落' }
      const cs = getComputedStyle(p)
      const span = document.createElement('span')
      span.style.cssText = 'position:absolute;visibility:hidden;font:' + cs.font
      span.textContent = '测'.repeat(50)
      document.body.appendChild(span)
      const charWidth = span.getBoundingClientRect().width / 50
      span.remove()
      const colWidth = col.getBoundingClientRect().width
      return {
        ok: true,
        columnWidth: Math.round(colWidth),
        charWidth: Math.round(charWidth * 100) / 100,
        charsPerLine: Math.floor(colWidth / charWidth),
        fontSize: cs.fontSize,
        lineHeight: cs.lineHeight,
        asideVisible: Boolean(aside) && getComputedStyle(aside).display !== 'none',
        asideWidth: aside ? Math.round(aside.getBoundingClientRect().width) : 0,
        viewport: window.innerWidth,
      }
    })()`)
    record(
      'E7g 正文列宽与行宽：760px 列 / 一行 38~46 个中文字（"正文太窄"的几何判据）',
      e7g?.ok === true &&
        e7g.columnWidth >= 740 &&
        e7g.columnWidth <= 900 &&
        e7g.charsPerLine >= 38 &&
        e7g.charsPerLine <= 46 &&
        // xl 以上必须有目录列；它是详情页两栏的一半，缺了说明断点判断写错
        e7g.asideVisible === true &&
        e7g.asideWidth >= 200,
      e7g?.ok !== true
        ? (e7g?.reason ?? '正文列几何读不回来')
        : `${e7g.viewport}px 视口：正文列 ${e7g.columnWidth}px、单字 ${e7g.charWidth}px → ` +
          `一行 ${e7g.charsPerLine} 字；字号/行高 ${e7g.fontSize}/${e7g.lineHeight}；` +
          `目录 ${e7g.asideWidth}px（可见=${e7g.asideVisible}）`,
    )

    // E7a–E7e 断言的是目录的**初始/默认折叠状态**（可见条目数、每个分支的 aria-expanded）。
    // 那是一个「刚进页面」的状态，而它前面几条用例都会滚动页面 ——
    // E7f 现在采样到 100%，把激活项带到文章最后一节，`revealActive` 于是展开了它的祖先分支，
    // 于是 E7a 拿到 8 条而不是期望的 7 条（实测：E7a/E7b/E7c 一起红）。
    // 这里显式重新进入文章，让折叠状态回到确定的初始值 ——
    // 该段本来就是个新用例族，从干净状态开始才是它应有的前提。
    await goto(ARTICLE, 2800)

    const readToc = () => evalJs(`(() => {
      const nav = document.querySelector('nav[aria-label="文章目录"]')
      if (!nav) return { ok: false, reason: 'nav[aria-label="文章目录"] 未渲染' }
      const links = [...nav.querySelectorAll('a[data-toc-id]')]
      const toggles = [...nav.querySelectorAll('button[aria-expanded]')]
      const toggleAll = [...nav.querySelectorAll('button')].find((b) => /全部(展开|收起)/.test(b.textContent.trim()))
      return {
        ok: true,
        links: links.length,
        ids: links.map((a) => a.getAttribute('data-toc-id')),
        collapsed: toggles.filter((b) => b.getAttribute('aria-expanded') === 'false').length,
        expanded: toggles.filter((b) => b.getAttribute('aria-expanded') === 'true').length,
        toggleAllText: toggleAll ? toggleAll.textContent.trim() : '',
        // 用 textContent 而不是 innerText：xl 以下侧栏是 display:none，innerText 会读成空串
        text: nav.textContent.replace(/\\s+/g, ' '),
      }
    })()`)

    // E7a 默认折叠：depth ≥ 3 的节点有折叠按钮且 aria-expanded=false，且它的子项**没有渲染**
    const e7a = await readToc()
    record(
      'E7a 目录渲染 + 默认折叠：depth≥3 有折叠按钮且 aria-expanded=false，收起分支的子项不渲染',
      e7a?.ok === true &&
        e7a.links === 7 &&
        e7a.collapsed === 3 &&
        e7a.expanded === 2 &&
        e7a.toggleAllText === '全部收起' &&
        /子节甲/.test(e7a.text) &&
        !/细节一/.test(e7a.text) &&
        !/细节三/.test(e7a.text),
      e7a?.ok !== true
        ? (e7a?.reason ?? '目录状态没读回来（页面内异常）')
        : `可见条目 ${e7a?.links} 条（全展开应为 11）；折叠按钮 aria-expanded=false ${e7a?.collapsed} 个 / true ${e7a?.expanded} 个；` +
          `「全部收起」按钮文案=「${e7a?.toggleAllText}」；收起分支的 h4 不在 DOM 里=` +
          `${!/细节一/.test(e7a?.text ?? '') && !/细节三/.test(e7a?.text ?? '')}`,
    )

    // E7b 折叠按钮：点击展开（aria-expanded 翻转 + 子项真的出现）、再点收回，且不误动同层别的分支
    const e7b = await evalJs(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      const nav = document.querySelector('nav[aria-label="文章目录"]')
      if (!nav) return { ok: false, reason: 'nav[aria-label="文章目录"] 未渲染' }
      const count = () => nav.querySelectorAll('a[data-toc-id]').length
      const btn = nav.querySelector('button[aria-expanded="false"]')
      if (!btn) return { ok: false, reason: '没有 aria-expanded=false 的折叠按钮（默认折叠没生效？）' }
      const label = btn.getAttribute('aria-label') || ''
      const before = count()
      btn.click()
      await wait(400)
      const afterOpen = {
        expanded: btn.getAttribute('aria-expanded'),
        links: count(),
        text: nav.textContent.replace(/\\s+/g, ' '),
      }
      btn.click()
      await wait(400)
      const afterClose = {
        expanded: btn.getAttribute('aria-expanded'),
        links: count(),
        text: nav.textContent.replace(/\\s+/g, ' '),
      }
      return { ok: true, label, before, afterOpen, afterClose }
    })()`, true)
    record(
      'E7b 折叠按钮：点击展开（aria-expanded=true 且子项出现）、再点收回，不误动别的分支',
      e7b?.ok === true &&
        /^展开/.test(e7b?.label ?? '') &&
        e7b?.afterOpen?.expanded === 'true' &&
        e7b?.afterOpen?.links === e7b?.before + 2 &&
        /细节一/.test(e7b?.afterOpen?.text ?? '') &&
        /细节二/.test(e7b?.afterOpen?.text ?? '') &&
        !/细节三/.test(e7b?.afterOpen?.text ?? '') &&
        e7b?.afterClose?.expanded === 'false' &&
        e7b?.afterClose?.links === e7b?.before &&
        !/细节一/.test(e7b?.afterClose?.text ?? ''),
      e7b?.ok !== true
        ? (e7b?.reason ?? '折叠状态没读回来（页面内异常）')
        : `按钮 aria-label=「${e7b?.label}」；条目 ${e7b?.before} → 展开 ${e7b?.afterOpen?.links}` +
          `（aria-expanded=${e7b?.afterOpen?.expanded}）→ 收回 ${e7b?.afterClose?.links}（aria-expanded=${e7b?.afterClose?.expanded}）；` +
          `展开时同层另一分支仍收起（不含「细节三」）=${!/细节三/.test(e7b?.afterOpen?.text ?? '')}`,
    )

    // E7c 全部收起 / 全部展开。
    // 「全部展开」必须跑到不动点：visibleNodes 只含"路径上没人收起"的节点，一轮快照展开不完，
    // 只跑一轮会留下 h3 出现但仍收着、最深 h4 不出现的半开状态（本轮对计划原文的修正 1）。
    // 所以判据取"最深 h4 是否出现"，而不是"按钮点了有没有反应"。
    const e7c = await evalJs(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      const nav = document.querySelector('nav[aria-label="文章目录"]')
      if (!nav) return { ok: false, reason: 'nav[aria-label="文章目录"] 未渲染' }
      const count = () => nav.querySelectorAll('a[data-toc-id]').length
      const toggleAll = () => [...nav.querySelectorAll('button')].find((b) => /全部(展开|收起)/.test(b.textContent.trim()))
      const before = count()
      toggleAll().click()
      await wait(500)
      const folded = { links: count(), label: toggleAll().textContent.trim(), text: nav.textContent.replace(/\\s+/g, ' ') }
      toggleAll().click()
      await wait(700)
      const unfolded = { links: count(), label: toggleAll().textContent.trim(), text: nav.textContent.replace(/\\s+/g, ' ') }
      return { ok: true, before, folded, unfolded }
    })()`, true)
    record(
      'E7c 「全部收起 / 全部展开」：收起只剩顶层，展开要跑到不动点（含最深 h4）',
      e7c?.ok === true &&
        e7c.before === 7 &&
        e7c.folded.links === 3 &&
        e7c.folded.label === '全部展开' &&
        e7c.unfolded.links === 11 &&
        e7c.unfolded.label === '全部收起' &&
        /细节一/.test(e7c.unfolded.text) &&
        /细节四/.test(e7c.unfolded.text),
      e7c?.ok !== true
        ? (e7c?.reason ?? '全部展开/收起状态没读回来（页面内异常）')
        : `条目 ${e7c?.before} → 全部收起 ${e7c?.folded?.links}（按钮=「${e7c?.folded?.label}」）` +
          `→ 全部展开 ${e7c?.unfolded?.links}（按钮=「${e7c?.unfolded?.label}」）；` +
          `最深 h4 出现=${/细节一/.test(e7c?.unfolded?.text ?? '') && /细节四/.test(e7c?.unfolded?.text ?? '')}`,
    )

    // E7d 点章节：hash 变化 + 真的滚动 + **replaceState 语义**。
    // replaceState 只能用"后退键去了哪里"来判：换成 pushState 或原生锚点，后退会停在
    // 同一篇文章（只是把 hash 去掉）；用 replaceState 则回到上一篇页面。
    // history.length 在 Chromium 里会被截断到 50，本脚本跑到这里必然到顶 —— 所以它只当
    // 参考证据，不当判据（拿它当判据就是一条永远绿的假绿）。
    await goto('/', 2200)
    await goto(ARTICLE, 2400)
    const e7d = await evalJs(`(async () => {
      const nav = document.querySelector('nav[aria-label="文章目录"]')
      const links = [...(nav ? nav.querySelectorAll('a[data-toc-id]') : [])]
      const link = links[links.length - 1]
      if (!link) return { ok: false, reason: '目录里没有可点击的章节' }
      const href = link.getAttribute('href')
      const historyBefore = history.length
      const scrollBefore = Math.round(window.scrollY)
      link.click()
      await new Promise((r) => setTimeout(r, 1400))
      return {
        ok: true,
        href,
        hash: location.hash,
        historyBefore,
        historyAfter: history.length,
        scrollBefore,
        scrollAfter: Math.round(window.scrollY),
      }
    })()`, true)
    // history.back() 会让当前执行上下文失效，所以分两步（与 C4c 同一处理）
    await evalJs(`(() => { history.back(); return true })()`)
    await sleep(2800)
    const e7dBack = await evalJs(`(() => ({ path: location.pathname, hash: location.hash }))()`)
    const e7dHashMatch =
      Boolean(e7d?.hash) &&
      decodeURIComponent(e7d?.hash ?? 'x') === decodeURIComponent(e7d?.href ?? 'y')
    record(
      'E7d 点章节：hash 变化 + 真的滚动 + replaceState 语义（后退回上一篇，而不是只去掉 hash）',
      e7d?.ok === true &&
        e7dHashMatch &&
        e7d.scrollAfter > e7d.scrollBefore + 200 &&
        e7dBack?.path === '/',
      e7d?.ok !== true
        ? (e7d?.reason ?? '章节点击结果没读回来（页面内异常）')
        : `点击 href=${e7d?.href} → hash=${e7d?.hash}（一致=${e7dHashMatch}）；scrollY ${e7d?.scrollBefore} → ${e7d?.scrollAfter}；` +
          `history.length ${e7d?.historyBefore} → ${e7d?.historyAfter}（Chromium 截断到 50，仅参考）；` +
          `history.back() 落到 pathname=${e7dBack?.path}（replaceState 应为 /；pushState/原生锚点会停在 ${ARTICLE}）`,
    )

    // E7e 滚动高亮：当前章节必须带 aria-current="location"，而且是**视口顶部那一节**。
    // 目标取正文最后一个标题（末章），并把它的位置精确摆进高亮带（rootMargin '-80px 0px -70% 0px'
    // → 桌面 1200 高时高亮带是 y∈[80, 360]），所以 rectTop 也被当成断言的一部分：
    // 位置没摆对时给出的失败信息是"前提不成立"，而不是让"没有高亮"背锅。
    await goto(ARTICLE, 2600)
    await waitFor(evalJs, `!!document.querySelector('.prose h2')`, 20000)
    const e7e = await evalJs(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      const headings = [...document.querySelectorAll('.prose h2, .prose h3, .prose h4')]
      const target = headings[headings.length - 1]
      if (!target || !target.id) return { ok: false, reason: '正文里找不到带 id 的标题' }
      // 先回顶：让目标从"未进视口"变成"进入视口"，IntersectionObserver 才有回调
      window.scrollTo({ top: 0, behavior: 'instant' })
      await wait(500)
      const absoluteTop = target.getBoundingClientRect().top + window.scrollY
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight
      const desired = Math.max(0, Math.min(absoluteTop - 120, maxScroll))
      window.scrollTo({ top: desired, behavior: 'instant' })
      await wait(1400)
      const rect = target.getBoundingClientRect()
      const active = [...document.querySelectorAll('nav[aria-label="文章目录"] [aria-current="location"]')]
      return {
        ok: true,
        id: target.id,
        text: target.textContent.trim(),
        rectTop: Math.round(rect.top),
        desired: Math.round(desired),
        maxScroll: Math.round(maxScroll),
        activeCount: active.length,
        activeIds: active.map((el) => el.getAttribute('data-toc-id')),
      }
    })()`, true)
    record(
      'E7e 滚动正文后当前章节带 aria-current="location"，且就是视口顶部那一节',
      e7e?.ok === true &&
        e7e.activeCount === 1 &&
        e7e.activeIds[0] === e7e.id &&
        e7e.rectTop >= 60 &&
        e7e.rectTop <= 400,
      e7e?.ok !== true
        ? (e7e?.reason ?? '高亮状态没读回来（页面内异常）')
        : `目标「${e7e?.text}」#${e7e?.id} 落在视口 y=${e7e?.rectTop}（高亮带 80–360px，maxScroll=${e7e?.maxScroll}）；` +
          `aria-current="location" ${e7e?.activeCount} 个，id=${JSON.stringify(e7e?.activeIds)}`,
    )

    // ---- E8 移动端：电梯栏与 MobileToc 目录按钮不重叠 ------------------------------
    // 计划原文的"截图比对 ≥ 12px"换成几何量：量两个按钮的 getBoundingClientRect()。
    // 两个判据缺一不可：竖直间距 ≥ 12px（避让是否生效），水平方向确实重叠（否则"间距"
    // 说的是两个不在同一列的东西，没有意义）。
    await send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true,
    })
    await sleep(800)
    await goto(ARTICLE, 2800)
    const e8 = await evalJs(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      const toc = document.querySelector('button[aria-label="打开文章目录"]')
      if (!toc) return { ok: false, reason: 'MobileToc 目录按钮未渲染（<1280px 才显示）' }
      window.scrollTo({ top: 0, behavior: 'instant' })
      await wait(400)
      const atZero = document.querySelectorAll('button[aria-label="返回顶部"]').length
      window.scrollTo({ top: 520, behavior: 'instant' })
      let top = null
      for (let i = 0; i < 25 && !top; i += 1) {
        await wait(200)
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
        scrollY: Math.round(window.scrollY),
        topRect: { left: Math.round(a.left), right: Math.round(a.right), top: Math.round(a.top), bottom: Math.round(a.bottom), w: Math.round(a.width), h: Math.round(a.height) },
        tocRect: { left: Math.round(b.left), right: Math.round(b.right), top: Math.round(b.top), bottom: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height) },
        gap: Math.round(b.top - a.bottom),
        overlap: Math.round(Math.min(a.right, b.right) - Math.max(a.left, b.left)),
        vw: window.innerWidth,
        vh: window.innerHeight,
      }
    })()`, true)
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 1200,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await sleep(800)
    record(
      'E8 移动端（390×844）电梯栏不压住目录按钮：竖直间距 ≥ 12px 且同列重叠',
      e8?.ok === true &&
        e8?.atZero === 0 &&
        (e8?.gap ?? -1) >= 12 &&
        (e8?.overlap ?? 0) > 0,
      e8?.ok !== true
        ? `原因=${e8?.reason ?? '移动端按钮几何量没量到（页面内异常）'}`
        : `视口 ${e8?.vw}×${e8?.vh}，scrollY=${e8?.scrollY}；返回顶部矩形 bottom=${e8?.topRect?.bottom}` +
          `（${e8?.topRect?.w}×${e8?.topRect?.h}），目录按钮矩形 top=${e8?.tocRect?.top}` +
          `（${e8?.tocRect?.w}×${e8?.tocRect?.h}）：竖直间距=${e8?.gap}px（阈值 12px），水平重叠=${e8?.overlap}px`,
    )
  }

  await shot('99-final')

  // ================================================================ 收尾：回收 + 基线核对
} catch (error) {
  record('脚本执行未中断', false, error.message)
} finally {
  let cleanupLog = []
  try {
    // 收尾前再取一次令牌：整轮里可能又发生过登出（或令牌过期），
    // 用失效的令牌清理等于什么都没删，会把造出来的数据全留在库里。
    const beforeCleanup = await relogin()
    if (!beforeCleanup.ok) cleanupLog.push(`重新登录失败(HTTP ${beforeCleanup.status})`)
    cleanupLog = await cleanup()
  } catch (error) {
    cleanupLog = [`cleanup failed: ${error.message}`]
  }
  try {
    after = await snapshot()
  } catch (error) {
    after = { error: error.message }
  }

  const diffs = []
  if (before && after) {
    for (const key of ['publishedTotal', 'managedTotal', 'categories', 'tags', 'users', 'attachments', 'comments']) {
      if (before[key] !== after[key]) diffs.push(`${key}: ${before[key]} → ${after[key]}`)
    }
    for (const key of ['articles', 'categories', 'tags']) {
      if ((after.residual?.[key] ?? 0) > 0) diffs.push(`残留 ${key}: ${after.residual[key]}`)
    }
  }

  const passed = results.filter((r) => r.ok).length
  const failed = results.length - passed
  console.log('')
  console.log('='.repeat(56))
  console.log(`${passed} / ${results.length} 通过`)
  if (failed) console.log(`失败：\n${results.filter((r) => !r.ok).map((r) => `  - ${r.name} (${r.detail})`).join('\n')}`)
  console.log(`数据基线：${diffs.length === 0 ? '一致 ✅' : `不一致 ⚠️  ${diffs.join('；')}`}`)
  console.log(`清理：${cleanupLog.filter((l) => !l.includes(':204')).length ? cleanupLog.join(' ') : '全部成功'}`)
  if (uncovered.length) console.log(`未覆盖：${uncovered.join('；')}`)
  console.log(`截图与报告：${OUT}`)

  const report = {
    baseUrl: BASE,
    mode: BASE.includes('4173') ? 'preview' : 'dev',
    run: RUN,
    startedAt: new Date(startedAt).toISOString(),
    durationMs: Date.now() - startedAt,
    node: process.version,
    chrome: chromePath,
    cases: { total: results.length, passed, failed },
    // 保留**全部**用例结果（不只是失败项）：复盘时要看"哪条根本没跑到"，
    // 只看失败列表会漏掉这类信息
    results,
    failures: results.filter((r) => !r.ok),
    baseline: { before, after, diff: diffs },
    cleanup: cleanupLog,
    consoleErrors,
    uncovered,
  }
  try {
    writeFileSync(join(OUT, 'report.json'), JSON.stringify(report, null, 1), 'utf-8')
  } catch {
    /* 输出目录不可写时忽略 */
  }

  chrome.kill()
  process.exitCode = failed === 0 && diffs.length === 0 ? 0 : 1
}
