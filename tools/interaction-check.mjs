/**
 * 验证 useAction 重构后的真实交互行为。
 *
 * 重点验证「失败路径」——那是重构最容易改坏、而静态检查完全看不出来的地方：
 * 1. 登录密码错误 → 错误贴在表单里，且**不弹** toast（silent 语义）
 * 2. 评论审核 → toast「已通过」+ 列表刷新
 * 3. 文章状态切换 → toast「已转为草稿」+ 状态徽标变化
 */
import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { chromeArgs, findChrome } from './lib/chrome.mjs'

// 目标站点可用 INTERACTION_BASE 覆盖。
// 需要它是因为本脚本与另外两个脚本的入参语义不同：smoke / full-check 的
// argv[2] 是「要测哪个站点」，而这里的 argv[2] 是「截图放哪」。
// 与其改动已有人在用的参数含义，不如多一个环境变量：
//   INTERACTION_BASE=http://localhost:8080 node tools/interaction-check.mjs
// 指向 Docker 栈（nginx 托管的构建产物）时，这套交互用例同样成立。
const BASE = process.env.INTERACTION_BASE ?? 'http://127.0.0.1:5173'
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

const chromePath = findChrome()
const chrome = spawn(
  chromePath,
  chromeArgs({
    port: PORT,
    userDataDir: join(tmpdir(), `interaction-profile-${Date.now()}`),
    windowSize: '1440,1000',
  }),
  { stdio: 'ignore' },
)


/** 轮询等待某个表达式为真（替代固定 sleep：dev server 冷编译时长的差异很大）。 */
async function waitFor(evalJs, expression, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await evalJs(expression)) return true
    await sleep(250)
  }
  return false
}

const results = []
const record = (name, ok, detail = '') => {
  results.push(ok)
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ' — ' + detail : ''}`)
}

try {
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
  /**
   * 执行页面内表达式。
   *
   * 注意：**不要**把表达式包进单行的 `(() => { ... })()`——多行表达式里常有 `//` 注释，
   * 拼成一行会把注释后面的代码全部注释掉，报出莫名其妙的 `Invalid regular expression`。
   * CDP 自己会在 exceptionDetails 里回传页面异常，直接读它即可。
   */
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
    return (await import('node:fs')).writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'))
  }

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })

  // ---------- 0) 预热：等应用真正挂载 ----------
  await send('Page.navigate', { url: BASE })
  const warmed = await waitFor(
    evalJs,
    `(document.getElementById('app')?.childElementCount ?? 0) > 0`,
    30000,
  )
  record('前端应用挂载（预热）', warmed, warmed ? '' : '30s 内 #app 仍为空，可能是 dev server 仍在编译')

  // ---------- 1) 登录失败：内联错误 + 不弹 toast ----------
  await send('Page.navigate', { url: `${BASE}/login` })
  const loginReady = await waitFor(
    evalJs,
    `!!document.querySelector('#username') && !!document.querySelector('#password')`,
    20000,
  )
  if (!loginReady) {
    // 前置条件不满足就直接判定失败并说明现状，不要带着 null 往下跑（那样只会看到 TypeError）
    const diag = await evalJs(`(() => ({
      url: location.pathname,
      mounted: (document.getElementById('app')?.childElementCount ?? 0) > 0,
      text: document.body.innerText.slice(0, 120),
    }))()`)
    record('登录页加载', false, `等待 20s 仍未出现表单：${JSON.stringify(diag)}`)
  } else {
    await evalJs(`(() => {
      const u = document.querySelector('#username'), p = document.querySelector('#password')
      const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
      set(u, 'admin'); set(p, 'wrong-password-xxx')
      return true
    })()`)
    await sleep(200)
  }
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
  const bad = badLogin ?? {}
  record('登录失败：错误内联展示', Boolean(bad.inline && bad.stillOnLogin), bad.inline ? '' : '未捕获到内联错误')
  record('登录失败：未弹 toast（silent 语义）', bad.toastCount === 0, `toast 元素 ${bad.toastCount ?? '未知'} 个`)
  await shot('01-login-error')

  // ---------- 2) 匿名发表留言（未登录状态，正是这条路径要覆盖的形态）----------
  //
  // 为什么放这里而不是 full-check：full-check 全程以站长身份登录，而公开页在登录后
  // 显示的是「以 XX 的身份留言」（站长发的会直接过审），于是
  // 「匿名提交 → 进入待审队列 → 公开列表里看不到」这条路径在那里根本走不到。
  //
  // 这条用例会**留下一条待审留言**（匿名没有删除权限，清不掉）——
  // 由 full-check 的 cleanup 按 `E2E` 命名约定兜底扫掉（那边有注释说明）。
  await send('Page.navigate', { url: `${BASE}/guestbook` })
  await sleep(1400)
  const guestbookPost = await evalJs(`(async () => {
    const set = (label, value) => {
      const el = document.querySelector(\`[aria-label="\${label}"]\`)
      if (!el) return false
      el.value = value
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    }
    if (!set('昵称（必填）', 'E2E 访客')) return { ok: false, reason: '找不到昵称输入框' }
    if (!set('留言内容', 'E2E interaction-check 待审留言')) {
      return { ok: false, reason: '找不到留言输入框' }
    }
    const submit = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '发表留言')
    if (!submit) return { ok: false, reason: '找不到「发表留言」按钮' }
    submit.click()
    await new Promise(r => setTimeout(r, 1800))
    return {
      ok: true,
      toast: document.body.innerText.includes('已提交，等待站长审核'),
      notice: document.body.innerText.includes('站长审核通过后会显示在下方'),
      listed: document.body.innerText.includes('E2E interaction-check 待审留言'),
    }
  })()`, true)
  record(
    '匿名留言提交：提示待审',
    guestbookPost?.ok === true && guestbookPost?.toast === true,
    guestbookPost?.reason ?? '',
  )
  record(
    '匿名留言提交：说清它去哪了，且不在公开列表里',
    guestbookPost?.notice === true && guestbookPost?.listed === false,
    `提示=${guestbookPost?.notice}，列表里出现=${guestbookPost?.listed}`,
  )
  await shot('02-guestbook-pending')

  // 空值校验：一个请求都不该发出去（与评论区同一口径）
  const guestbookEmpty = await evalJs(`(async () => {
    const set = (label, value) => {
      const el = document.querySelector(\`[aria-label="\${label}"]\`)
      if (!el) return false
      el.value = value
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    }
    set('昵称（必填）', 'E2E 访客')
    set('留言内容', '   ')
    const submit = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '发表留言')
    submit?.click()
    await new Promise(r => setTimeout(r, 900))
    return { toast: document.body.innerText.includes('留言内容不能为空') }
  })()`, true)
  record('匿名留言空内容：前端拦截并给出提示', guestbookEmpty?.toast === true)
  await send('Page.navigate', { url: `${BASE}/login` })
  await sleep(600)

  // ---------- 3) 登录成功 → 评论审核 ----------
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
    const findBtn = () => [...document.querySelectorAll('button')]
      .find(b => /转草稿|发布/.test(b.textContent.trim()))
    const btn = findBtn()
    if (!btn) return { skipped: true }
    const label = btn.textContent.trim()
    btn.click()
    await new Promise(r => setTimeout(r, 1800))
    const toast = document.body.innerText.match(/已发布|已转为草稿/)?.[0] ?? ''
    // 还原：这个脚本会改数据，必须对称操作，否则多跑几轮就把所有文章切成草稿，
    // 前台列表变空，后续断言全错（这个坑踩过一次）
    findBtn()?.click()
    await new Promise(r => setTimeout(r, 1800))
    return { label, toast, restored: true }
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

  // ---------- 6) 草稿自动保存与恢复 ----------
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
  })
  await send('Page.navigate', { url: `${BASE}/admin/articles/new` })
  await sleep(3000)

  // 清掉可能残留的草稿，保证从干净状态开始
  await evalJs(`localStorage.removeItem('article:new')`)

  const typed = await evalJs(`(() => {
    const title = document.querySelector('input[placeholder="文章标题"]')
    const body = document.querySelector('textarea')
    if (!title || !body) return false
    const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
    set(title, '草稿自动保存验证标题')
    set(body, '这段内容是自动保存验证用的，刷新后应该能被恢复。')
    return true
  })()`)
  record('编辑器可输入', typed)

  // 防抖是 3 秒，多等一点
  await sleep(4200)
  const drafted = await evalJs(`(() => {
    const raw = localStorage.getItem('article:new')
    if (!raw) return { stored: false }
    const parsed = JSON.parse(raw)
    return { stored: true, title: parsed.payload?.title ?? '', hasTime: typeof parsed.savedAt === 'number' }
  })()`)
  record('本地草稿已落盘', drafted.stored && drafted.title.includes('草稿自动保存验证'), drafted.title)
  record('草稿带保存时间', Boolean(drafted.hasTime))

  // 刷新（模拟误关标签页后重新打开）
  await send('Page.reload')
  await sleep(3200)
  const banner = await evalJs(`(() => ({
    text: document.body.innerText,
    hasRestore: !![...document.querySelectorAll('button')].find(b => b.textContent.trim() === '恢复草稿'),
    hasDiscard: !![...document.querySelectorAll('button')].find(b => b.textContent.trim() === '放弃'),
    currentTitle: document.querySelector('input[placeholder="文章标题"]')?.value ?? '',
  }))()`)
  record('刷新后提示恢复草稿', banner.hasRestore && /本地未保存的草稿/.test(banner.text))
  record('未自动覆盖编辑器（等用户确认）', banner.currentTitle === '', `当前标题「${banner.currentTitle}」`)
  await shot('07-draft-banner')

  const restored = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '恢复草稿')
    btn?.click()
    await new Promise(r => setTimeout(r, 700))
    return {
      title: document.querySelector('input[placeholder="文章标题"]')?.value ?? '',
      bannerGone: ![...document.querySelectorAll('button')].some(b => b.textContent.trim() === '恢复草稿'),
    }
  })()`, true)
  record('点击恢复后内容回填', restored.title.includes('草稿自动保存验证'), restored.title)
  record('恢复后横幅消失', restored.bannerGone)

  // 放弃路径：再写一次草稿，刷新后点放弃，应清空本地存储
  await send('Page.reload')
  await sleep(3000)
  const discarded = await evalJs(`(async () => {
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '放弃')
    if (!btn) return { skipped: true }
    btn.click()
    await new Promise(r => setTimeout(r, 600))
    return { stillThere: localStorage.getItem('article:new') !== null }
  })()`, true)
  if (discarded.skipped) record('放弃草稿', false, '未找到放弃按钮')
  else record('放弃后清除本地草稿', !discarded.stillThere)
  await shot('08-draft-discarded')

  // ---------- 7) 访客评论全链路（含失败路径）----------
  // 评论是全站唯一的「任何访客都能写入」入口，迁移到统一抽象后必须真实走一遍
  const article = await (await fetch(`${BASE}/api/v1/articles?page_size=2`)).json()
  const target = article.items[1] ?? article.items[0]
  await send('Page.navigate', { url: `${BASE}/article/${target.slug}` })
  await sleep(3200)

  const commentUi = await evalJs(`(() => {
    const section = document.querySelector('#comments')
    return {
      hasSection: !!section,
      hasForm: !!document.querySelector('#comment-form'),
      hasNameInput: !!document.querySelector('input[placeholder*="昵称"], #comment-name, input[name="author_name"]'),
      listCount: document.querySelectorAll('#comments ul li, #comments ol li').length,
    }
  })()`)
  record('评论区渲染（迁移后）', commentUi.hasSection && commentUi.hasForm)

  // 空内容提交 → 前端校验拦住，不发请求
  const emptySubmit = await evalJs(`(async () => {
    const box = document.querySelector('#comment-form')
    const textarea = box?.querySelector('textarea')
    // #comment-form 是个 div，提交按钮是 type="button"，按文案找
    const submitBtn = [...(box?.querySelectorAll('button') ?? [])].find(b => b.textContent.includes('发表评论'))
    if (!textarea || !submitBtn) return { skipped: true }
    const before = document.querySelectorAll('#comments ul li, #comments ol li').length
    submitBtn.click()
    await new Promise(r => setTimeout(r, 900))
    return {
      toast: /不能为空|请填写昵称/.test(document.body.innerText),
      // 内容与昵称都为空时不应新增条目
      listCount: document.querySelectorAll('#comments ul li, #comments ol li').length,
      before,
    }
  })()`, true)
  if (emptySubmit.skipped) record('评论空值校验', false, '未找到评论输入框')
  else
    record(
      '评论空值校验（前端拦截，未发请求）',
      emptySubmit.toast && emptySubmit.listCount === emptySubmit.before,
      `条目 ${emptySubmit.before} → ${emptySubmit.listCount}`,
    )

  // 正常提交 → 成功提示 + 列表刷新
  const posted = await evalJs(`(async () => {
    const box = document.querySelector('#comment-form')
    if (!box) return { skipped: true }
    const set = (el, v) => { if (el) { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) } }
    set(box.querySelector('input[placeholder*="昵称"]'), '交互验证访客')
    set(box.querySelector('textarea'), '这条评论由 interaction-check 自动生成，用于验证评论链路。')
    const submitBtn = [...box.querySelectorAll('button')].find(b => b.textContent.includes('发表评论'))
    const before = document.querySelectorAll('#comments ul li, #comments ol li').length
    submitBtn?.click()
    await new Promise(r => setTimeout(r, 2600))
    const text = document.body.innerText
    return {
      before,
      after: document.querySelectorAll('#comments ul li, #comments ol li').length,
      success: /评论已发布|等待站长审核/.test(text),
      needApproval: /需要审核后才会公开显示/.test(text),
      errorToast: /评论提交失败|过于频繁/.test(text),
    }
  })()`, true)
  if (posted.skipped) record('评论提交', false, '未找到评论表单')
  else {
    record(
      '评论提交：成功提示',
      posted.success && !posted.errorToast,
      `评论数 ${posted.before} → ${posted.after}`,
    )

    // 清理：评论是「立即发布」时把它删掉，保持环境干净（审核模式下不会进公开列表，无需处理）
    if (!posted.needApproval) {
      // 注意：要清理的是「刚才发表评论的那一篇」，不是列表里第一篇
      const cleaned = await evalJs(`(async () => {
        const token = localStorage.getItem('blog-access-token')
        if (!token) return { cleaned: false, reason: '未登录，无法删除' }
        const list = await fetch('/api/v1/comments/article/${target.id}').then(r => r.json())
        const target = list.find(c => (c.content ?? '').includes('由 interaction-check 自动生成'))
        if (!target) return { cleaned: false, reason: '未找到测试评论' }
        const resp = await fetch('/api/v1/comments/' + target.id, {
          method: 'DELETE', headers: { Authorization: 'Bearer ' + token },
        })
        return { cleaned: resp.ok, status: resp.status }
      })()`, true)
      record('测试评论已清理', Boolean(cleaned?.cleaned), `删除接口 ${cleaned?.status ?? cleaned?.reason ?? '-'}`)
    }
    // 站点默认「评论需审核」，此时列表不增长是正确行为，不能算失败
    if (posted.needApproval) {
      record('待审评论不进公开列表（符合审核策略）', posted.after === posted.before)
    } else {
      record('评论提交后列表刷新', posted.after > posted.before)
    }
  }
  await shot('09-comment-posted')

  ws.close()
} finally {
  chrome.kill()
}
const failed = results.filter((r) => !r).length
console.log(`\n${results.length - failed}/${results.length} 通过`)
process.exit(failed ? 1 : 0)
