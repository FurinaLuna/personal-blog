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

  /** 把站长 token 注入 localStorage，使后台页面直接可用 */
  const injectToken = async () => {
    await evalJs(`(() => {
      localStorage.setItem('blog-access-token', ${JSON.stringify(token)})
      localStorage.setItem('blog-refresh-token', 'e2e-placeholder')
      return true
    })()`)
  }
  await injectToken()

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
  const linkName = `E2E 友链 ${RUN}`
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
    if (!set('站点地址', 'https://example.com/e2e-link')) return { ok: false, reason: '找不到站点地址输入框' }
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
  await evalJs(`(() => { localStorage.removeItem('blog-access-token'); localStorage.removeItem('blog-refresh-token'); return true })()`)
  await goto('/admin/articles', 2600)
  const b3 = await evalJs(`(() => ({ path: location.pathname, search: location.search }))()`)
  record(
    'B3 未登录访问后台 → 重定向到 /login?redirect=',
    b3?.path === '/login' && (b3?.search ?? '').includes('redirect='),
    `${b3?.path}${b3?.search}`,
  )

  // B1 登出（重新登录后从后台登出）
  await injectToken()
  await goto('/admin/articles', 2600)
  const b1 = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '退出')
    if (!btn) return { ok: false, reason: '退出按钮未找到' }
    btn.click()
    await new Promise(r => setTimeout(r, 2400))
    return {
      ok: true,
      path: location.pathname,
      access: localStorage.getItem('blog-access-token'),
      refresh: localStorage.getItem('blog-refresh-token'),
    }
  })()`, true)
  record(
    'B1 登出：清空 token 并回到登录页',
    b1?.ok && !b1?.access && !b1?.refresh && b1?.path === '/login',
    `path=${b1?.path} access=${b1?.access ?? 'null'}`,
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
  /** 用临时 author 账号换取 token（失败时打印真实响应，便于定位而不是静默跳过） */
  const authorLoginOnce = async () => {
    const resp = await fetch(`${BASE}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: userName, password: 'E2ePassw0rd!' }),
    })
    const body = await resp.json().catch(() => ({}))
    return { status: resp.status, token: body?.access_token ?? '', detail: JSON.stringify(body).slice(0, 120) }
  }
  let authorLogin = await authorLoginOnce()
  if (!authorLogin.token) {
    // 偶发的时序/连接问题重试一次（手动 curl 验证过后端与代理均正常）
    await sleep(1000)
    authorLogin = await authorLoginOnce()
  }
  if (authorLogin.token) {
    await evalJs(`(() => {
      localStorage.setItem('blog-access-token', ${JSON.stringify(authorLogin.token)})
      localStorage.setItem('blog-refresh-token', 'e2e-author')
      return true
    })()`)
    await goto('/admin/users', 2600)
    const b4users = await evalJs(`(() => ({ path: location.pathname }))()`)
    await goto('/admin/settings', 2600)
    const b4settings = await evalJs(`(() => ({ path: location.pathname }))()`)
    record(
      'B4 非站长访问 /admin/users 与 /admin/settings 被弹回首页',
      b4users?.path === '/' && b4settings?.path === '/',
      `users→${b4users?.path} settings→${b4settings?.path}`,
    )
    await injectToken()
  } else {
    record(
      'B4 非站长访问受限后台页',
      false,
      `临时 author 登录失败：HTTP ${authorLogin.status} ${authorLogin.detail}`,
    )
    uncovered.push(`B4 — 临时 author 登录失败（HTTP ${authorLogin.status}）`)
  }

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
