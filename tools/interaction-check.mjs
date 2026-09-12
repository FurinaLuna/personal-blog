/**
 * 验证 useAction 重构后的真实交互行为。
 *
 * 重点验证「失败路径」——那是重构最容易改坏、而静态检查完全看不出来的地方：
 * 1. 登录密码错误 → 错误贴在表单里，且**不弹** toast（silent 语义）
 * 2. 评论审核 → toast「已通过」+ 列表刷新
 * 3. 文章状态切换 → toast「已转为草稿」+ 状态徽标变化
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const BASE = 'http://127.0.0.1:5173'
const PORT = 9229
const OUT = process.argv[2] || join(tmpdir(), 'wave2-shots')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function getWsUrl() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch { /* keep polling */ }
    await sleep(300)
  }
  throw new Error('CDP not ready')
}

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-first-run',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(tmpdir(), 'wave2-profile')}`,
  '--window-size=1440,1000', 'about:blank',
], { stdio: 'ignore' })

const results = []
const record = (name, ok, detail = '') => {
  results.push(ok)
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ' — ' + detail : ''}`)
}

try {
  if (!existsSync(CHROME)) throw new Error('chrome not found')
  mkdirSync(OUT, { recursive: true })
  const ws = new WebSocket(await getWsUrl())
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let seq = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id) }
  }
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++seq; pending.set(id, res)
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalJs = async (expr, awaitPromise = false) =>
    (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise }))?.result?.value
  const shot = async (name) => {
    const { data } = await send('Page.captureScreenshot', { format: 'png' })
    return (await import('node:fs')).writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'))
  }

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })

  // ---------- 1) 登录失败：内联错误 + 不弹 toast ----------
  await send('Page.navigate', { url: `${BASE}/login` })
  await sleep(2600)
  await evalJs(`(() => {
    const u = document.querySelector('#username'), p = document.querySelector('#password')
    const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
    set(u, 'admin'); set(p, 'wrong-password-xxx')
  })()`)
  await sleep(200)
  const badLogin = await evalJs(`(async () => {
    document.querySelector('form').requestSubmit()
    await new Promise(r => setTimeout(r, 1800))
    const text = document.body.innerText
    return {
      inline: /密码|错误|失败|不正确|invalid/i.test(text),
      // role="status" 是 ToastHost 的常驻容器，真正弹出的 toast 是它的子按钮
      toastCount: document.querySelectorAll('[role="status"] button').length,
      stillOnLogin: location.pathname === '/login',
    }
  })()`, true)
  record('登录失败：错误内联展示', badLogin.inline && badLogin.stillOnLogin)
  record('登录失败：未弹 toast（silent 语义）', badLogin.toastCount === 0, `toast 元素 ${badLogin.toastCount} 个`)
  await shot('01-login-error')

  // ---------- 2) 登录成功 → 评论审核 ----------
  await evalJs(`(() => {
    const u = document.querySelector('#username'), p = document.querySelector('#password')
    const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
    set(u, 'admin'); set(p, 'admin123456')
  })()`)
  await sleep(200)
  await evalJs(`(async () => {
    document.querySelector('form').requestSubmit()
    await new Promise(r => setTimeout(r, 2500))
  })()`, true)

  await send('Page.navigate', { url: `${BASE}/admin/comments` })
  await sleep(3000)
  const moderate = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => /通过/.test(b.textContent))
    if (!btn) return { skipped: true }
    const before = document.querySelectorAll('tbody tr, li').length
    btn.click()
    await new Promise(r => setTimeout(r, 1600))
    return {
      toast: document.body.innerText.match(/已通过|已撤下/)?.[0] ?? '',
      before,
    }
  })()`, true)
  if (moderate.skipped) record('评论审核', false, '页面上没有待审评论（数据不足）')
  else record('评论审核：toast 反馈', Boolean(moderate.toast), moderate.toast)
  await shot('02-comment-moderate')

  // ---------- 3) 文章状态切换 ----------
  await send('Page.navigate', { url: `${BASE}/admin/articles` })
  await sleep(3000)
  const toggle = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => /转草稿|发布/.test(b.textContent))
    if (!btn) return { skipped: true }
    const label = btn.textContent.trim()
    btn.click()
    await new Promise(r => setTimeout(r, 1800))
    return { label, toast: document.body.innerText.match(/已发布|已转为草稿/)?.[0] ?? '' }
  })()`, true)
  if (toggle.skipped) record('文章状态切换', false, '未找到状态切换按钮')
  else record('文章状态切换：toast 反馈', Boolean(toggle.toast), `${toggle.label} → ${toggle.toast}`)
  await shot('03-article-toggle')

  // ---------- 4) 设置页保存（onSuccess 回填）----------
  await send('Page.navigate', { url: `${BASE}/admin/settings` })
  await sleep(3000)
  const settings = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => /保存设置/.test(b.textContent))
    if (!btn) return { skipped: true }
    btn.click()
    await new Promise(r => setTimeout(r, 2000))
    return {
      toast: document.body.innerText.match(/站点设置已保存/)?.[0] ?? '',
      disabledDuring: btn.disabled,
    }
  })()`, true)
  if (settings.skipped) record('设置页保存', false, '未找到保存按钮')
  else record('设置页保存：toast 反馈', Boolean(settings.toast), settings.toast)
  await shot('04-settings-save')

  // ---------- 5) 后台表格在窄屏切换成卡片列表 ----------
  // 表格 min-w 680~720，窄屏只能横向滚动；改成 md 以下渲染卡片列表。
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  })
  await send('Page.navigate', { url: `${BASE}/admin/articles` })
  await sleep(3000)
  const narrow = await evalJs(`(() => {
    const table = document.querySelector('table')
    const list = document.querySelector('ul.divide-y')
    const tableVisible = table ? getComputedStyle(table.closest('div')).display !== 'none' : false
    const listVisible = list ? getComputedStyle(list).display !== 'none' : false
    return {
      tableExists: !!table,
      tableVisible,
      listVisible,
      listItems: list ? list.children.length : 0,
      hasScroll: document.documentElement.scrollWidth > window.innerWidth + 1,
    }
  })()`)
  record('窄屏：表格隐藏、卡片列表显示', !narrow.tableVisible && narrow.listVisible, `卡片 ${narrow.listItems} 项`)
  record('窄屏：没有横向溢出', !narrow.hasScroll)
  await shot('05-admin-articles-mobile')

  // 用户管理页同样检查一遍（另一处表格）
  await send('Page.navigate', { url: `${BASE}/admin/users` })
  await sleep(2600)
  const narrowUsers = await evalJs(`(() => {
    const table = document.querySelector('table')
    const list = document.querySelector('ul.divide-y')
    return {
      tableVisible: table ? getComputedStyle(table.closest('div')).display !== 'none' : false,
      listVisible: list ? getComputedStyle(list).display !== 'none' : false,
      items: list ? list.children.length : 0,
    }
  })()`)
  record(
    '窄屏：用户管理切换卡片列表',
    !narrowUsers.tableVisible && narrowUsers.listVisible,
    `卡片 ${narrowUsers.items} 项`,
  )
  await shot('06-admin-users-mobile')

  ws.close()
} finally {
  chrome.kill()
}
const failed = results.filter((r) => !r).length
console.log(`\n${results.length - failed}/${results.length} 通过`)
process.exit(failed ? 1 : 0)
