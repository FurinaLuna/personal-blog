/**
 * 部署产物验证：真的构建镜像、真的用 compose 起一套完整栈，逐条断言"部署出来的东西能用"。
 *
 * ── 为什么必须有这一层 ──────────────────────────────────────────────
 * 这个仓库此前所有自动化（pytest / vitest / smoke / interaction / full-check / e2e_live）
 * 跑的都是**源码**：uvicorn 直接跑 app/、Vite dev server 直接跑 src/。
 * 而线上跑的是两个镜像 + nginx + PostgreSQL，这一层里有一堆"源码模式永远不会经过"的路径：
 *
 *   - Dockerfile 里 COPY 的文件到底存不存在（本仓库真实翻过车：
 *     `COPY deploy/nginx.conf` 在上下文之外，构建直接失败，而没人发现，因为镜像从没被构建过）；
 *   - compose 的变量插值（`${VAR:?}` 缺失是**启动前**失败，本地 .env 齐全时永远看不见）；
 *   - 容器入口的 `alembic upgrade head` 是否真的把库升到了 head；
 *   - nginx 的 location 与安全响应头（CSP 只在**文档响应**上才有意义，
 *     而此处一度因为 add_header 不继承，首页恰恰没有 CSP）；
 *   - APP_ENV 是否真的按生产跑（`/docs` 该关、DEBUG 该 false）；
 *   - 定时备份服务到底有没有产出可恢复的 dump；
 *   - 自管证书那套（HTTP→HTTPS 跳转、ACME 续期挑战不被跳转吃掉）。
 *
 * 这些都属于"只有真正部署一次才会暴露"的类别，所以这个脚本做的就是部署一次。
 *
 * ── 用法 ────────────────────────────────────────────────────────────
 *     node tools/deploy-check.mjs
 *
 * 需要 Docker（含 compose v2）。脚本自成一体：自己建临时目录、自己起栈、自己清理，
 * 不碰开发者的 .env、不碰已有的 compose 项目、不碰 ./backups 与 ./deploy/certs。
 * 全流程约 3~5 分钟，其中大头是两个镜像的首次构建（有 BuildKit 缓存后快很多）。
 *
 * 环境变量（都有默认值，通常不用设）：
 *     APP_PORT=18080          宿主机发布端口。默认**不是** 8080：
 *                             开发者本地那套栈常常正占着 8080，用 18080 才能并存。
 *     TLS_PORT=18443          HTTPS 阶段的宿主机端口（同样避开 443）。
 *     COMPOSE_PROJECT=blog-deploycheck
 *                             独立项目名 ⇒ 独立的容器与数据卷，不会动到 personal-blog 那套。
 *     SKIP_TLS=1              跳过 TLS 阶段（迭代 nginx 配置时省时间）
 *     KEEP=1                  失败时保留现场（容器与临时目录都不删），便于手查
 *
 * 退出码：全部通过 0，有失败 1（可直接进 CI）。
 */

import { spawnSync } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const PROJECT = process.env.COMPOSE_PROJECT ?? 'blog-deploycheck'
const APP_PORT = process.env.APP_PORT ?? '18080'
const TLS_PORT = process.env.TLS_PORT ?? '18443'
const SKIP_TLS = process.env.SKIP_TLS === '1'
const KEEP = process.env.KEEP === '1'
const BASE_URL = `http://127.0.0.1:${APP_PORT}`
const TLS_URL = `https://127.0.0.1:${TLS_PORT}`

// 临时目录：备份产物、证书、ACME webroot 全落在这里，跑完即删。
// 刻意不复用仓库里的 ./backups 与 ./deploy/certs —— 验证脚本往里写东西，
// 轻则污染真实备份，重则覆盖真证书（那是能把站点搞挂的操作）。
const TMP = mkdtempSync(join(tmpdir(), 'blog-deploy-'))
const BACKUP_DIR = join(TMP, 'backups')
const CERTS_DIR = join(TMP, 'certs')
const WEBROOT_DIR = join(TMP, 'webroot')
const REPORT_DIR = join(ROOT, 'shots', 'deploy')
const CERT_DOMAIN = 'example.com' // 与 deploy/nginx.https.conf 里的路径一致

const results = []
const commandLog = []
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ────────────────────────────────────────────────────────────── 基础设施

// 自带一次性密钥，**不继承**开发者 .env 里的那套。
// 两个原因，第二个是实测撞出来的：
//   1) 干净性：拿别人的口令去起一套临时栈，等于把"能不能起来"绑定在他本地的配置上，
//      而 .env 是 gitignore 的，换个机器结论就可能不同；
//   2) 应用有一条 `_enforce_production_safety()` 门禁：生产模式下仍用仓库里的
//      开发默认口令（.env.example 里那份）会**拒绝启动**。第一次跑这个脚本时
//      它就照着仓库根目录的 .env 起来了，于是后端一路 restart 循环，
//      日志里明明白白写着"检测到生产环境使用不安全的默认配置，已拒绝启动"。
//      门禁是对的，错的是验证脚本不该把别人的开发口令当成生产口令。
const SECRETS = {
  POSTGRES_USER: 'blog',
  POSTGRES_DB: 'blog',
  POSTGRES_PASSWORD: `deploy-check-pg-${randomBytes(9).toString('hex')}`,
  JWT_SECRET_KEY: randomBytes(32).toString('hex'),
  ADMIN_PASSWORD: `deploy-check-admin-${randomBytes(6).toString('hex')}`,
}

// 库连接的三个参数：以 argv 直接传给 psql / pg_restore / createdb，**不走 `sh -c`**。
// 这不是风格偏好，是实测踩出来的：把带引号的 SQL 塞进 `sh -c` 的字符串里，
// 要穿过 Node 的参数引号 → docker.exe 的解析 → Docker API → 容器内的 sh，
// 任何一层吃掉一个引号，报错就是容器里那句
// 「sh: syntax error: unterminated quoted string」—— 而宿主侧完全看不出异常，
// 表现为"恢复演练断言失败，但库明明是恢复好的"。
// argv 直传没有这个面：Docker 收到的是参数数组，容器里不经过 shell。
const PG = { user: SECRETS.POSTGRES_USER, db: SECRETS.POSTGRES_DB }
const PSQL = ['psql', '-U', PG.user, '-d', PG.db]

const composeEnv = {
  ...process.env,
  ...SECRETS,
  APP_PORT,
  TLS_PORT,
  BACKUP_HOST_DIR: BACKUP_DIR,
  CERTBOT_WEBROOT: WEBROOT_DIR,
  TLS_CERTS_DIR: CERTS_DIR,
  // 显式钉死"按生产跑"：宿主 .env 里可能是 APP_ENV=development（本地试玩配置），
  // 而这一层要验证的恰恰是生产形态（/docs 关闭、DEBUG=false、不用 create_all 建表）。
  // shell 环境变量的优先级高于 .env，所以这里设了就一定生效。
  APP_ENV: 'production',
  DEBUG: 'false',
  DB_AUTO_CREATE: 'false',
  SEED_DEMO_DATA: 'false',
}

/** 跑一条命令，返回 {code, out, err}。不用 shell，避免 Windows 上的路径与引号转换。 */
function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    ...options,
  })
  return {
    code: result.status ?? 1,
    out: result.stdout ?? '',
    err: result.stderr ?? '',
    spawnError: result.error,
  }
}

function docker(args, options = {}) {
  return run('docker', args, options)
}

/** 跑 compose；tls=true 时叠上覆盖文件（与 README 里给用户的命令完全一致）。 */
function compose(args, { tls = false, ...options } = {}) {
  const files = tls
    ? ['-f', 'docker-compose.yml', '-f', 'docker-compose.tls.yml']
    : ['-f', 'docker-compose.yml']
  const argv = ['compose', '-p', PROJECT, ...files, ...args]
  commandLog.push(`docker ${argv.join(' ')}`)
  return run('docker', argv, { env: composeEnv, ...options })
}

/**
 * 极简 HTTP 客户端（不跟随跳转、不主动要 gzip）。
 *
 * 用 Node 自带的 http/https 而不是 curl：Windows 与 Linux 行为一致，
 * 且能精确拿到响应头（跳转与缓存头的断言全靠它）。
 * `insecure` 只给自签证书的 HTTPS 阶段用。
 * 刻意不发 Accept-Encoding：这样 nginx 不会压缩，body 就是原始字节，
 * 内联脚本的哈希才能与源文件对得上。
 */
function httpGet(url, { headers = {}, insecure = false, timeoutMs = 15000 } = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    const send = new URL(url).protocol === 'https:' ? httpsRequest : httpRequest
    const req = send(url, { method: 'GET', headers, rejectUnauthorized: !insecure }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () =>
        resolvePromise({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString('utf8'),
        }),
      )
    })
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`请求超时：${url}`)))
    req.on('error', rejectPromise)
    req.end()
  })
}

/**
 * spawnSync 在 `encoding: null`（要往子进程 stdin 灌二进制的那条路径，
 * 也就是恢复演练）下把 stdout/stderr 返回成 Buffer 而不是字符串。
 * 断言里直接 `.trim()` 会抛 "xxx.trim is not a function"，而这条断言本来
 * 是**最该给出准确信息**的那条 —— 统一从这里过一道。
 */
function text(value) {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : String(value)
}

function record(name, ok, detail = '') {
  results.push({ name, ok: Boolean(ok), detail })
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? `  ${detail}` : ''}`)
  return Boolean(ok)
}

function log(message) {
  console.log(`\n── ${message} ${'─'.repeat(Math.max(0, 56 - message.length))}`)
}

/** 容器 id（服务没起时返回空串）。 */
function containerId(service, { tls = false } = {}) {
  const r = compose(['ps', '-q', service], { tls })
  return r.out.trim().split('\n')[0] ?? ''
}

function containerHealth(id) {
  if (!id) return ''
  const r = docker(['inspect', '-f', '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', id])
  return r.out.trim()
}

function containerState(id) {
  if (!id) return ''
  const r = docker(['inspect', '-f', '{{.State.Status}}', id])
  return r.out.trim()
}

/** 等某个 HTTP 地址可用（容器刚起来时后端还在跑迁移）。 */
async function waitForHttp(url, { timeoutMs = 120000, insecure = false } = {}) {
  const deadline = Date.now() + timeoutMs
  let lastError = ''
  while (Date.now() < deadline) {
    try {
      const res = await httpGet(url, { insecure })
      if (res.status < 500) return res
      lastError = `HTTP ${res.status}`
    } catch (error) {
      lastError = error.message
    }
    await sleep(1000)
  }
  throw new Error(`${url} 在 ${timeoutMs / 1000}s 内没就绪（最后一次：${lastError}）`)
}

function headerOf(res, name) {
  return res.headers[name.toLowerCase()] ?? ''
}

/** 抓失败现场：容器日志往往比断言信息有用得多。 */
function collectDiagnostics() {
  const sections = []
  for (const service of ['db', 'backup', 'backend', 'frontend']) {
    const r = compose(['logs', '--no-color', '--tail', '40', service])
    sections.push(`### ${service}\n\n\`\`\`\n${(r.out + r.err).trim() || '（无日志）'}\n\`\`\``)
  }
  return sections.join('\n\n')
}

function cleanup() {
  for (const tls of [true, false]) {
    compose(['down', '-v', '--remove-orphans'], { tls })
  }
}

// ────────────────────────────────────────────────────────────── 阶段 1：静态校验

function staticChecks() {
  log('阶段 1/6：compose 与配置的静态校验')

  const base = compose(['config', '-q'])
  record('compose 配置可解析（基础版）', base.code === 0, base.code === 0 ? '' : base.err.trim().slice(0, 200))

  const tls = compose(['config', '-q'], { tls: true })
  record('compose 配置可解析（叠加 TLS 覆盖文件）', tls.code === 0, tls.code === 0 ? '' : tls.err.trim().slice(0, 200))

  // 负向验证：`.env` 齐全时，那几条 `${VAR:?}` 的"缺失即失败"保护永远不会被触发，
  // 所以这里用一个空 env-file 把它们逼出来 —— 否则那只是几行看起来很像保护的注释。
  const emptyEnvFile = join(TMP, 'empty.env')
  writeFileSync(emptyEnvFile, '')
  for (const variable of ['POSTGRES_PASSWORD', 'JWT_SECRET_KEY', 'ADMIN_PASSWORD']) {
    const r = run(
      'docker',
      ['compose', '-p', PROJECT, '-f', 'docker-compose.yml', '--env-file', emptyEnvFile, 'config', '-q'],
      { env: { ...process.env, [variable]: '' } },
    )
    const mentioned = `${r.err}${r.out}`.includes(variable)
    record(`缺少 ${variable} 时 compose 拒绝启动（fail fast）`, r.code !== 0 && mentioned)
  }

  // 反向确认上面那条不是因为"空 env-file 本身就会失败"
  const withSecrets = run(
    'docker',
    ['compose', '-p', PROJECT, '-f', 'docker-compose.yml', '--env-file', emptyEnvFile, 'config', '-q'],
    {
      env: {
        ...process.env,
        POSTGRES_PASSWORD: 'deploy-check-pw',
        JWT_SECRET_KEY: 'deploy-check-secret-key-not-for-production',
        ADMIN_PASSWORD: 'deploy-check-admin-pw',
      },
    },
  )
  record('变量补齐后同一命令通过（证明上一条测的是变量缺失）', withSecrets.code === 0,
    withSecrets.err.trim().slice(0, 160))
}

// ────────────────────────────────────────────────────────────── 阶段 2：构建与启动

function buildImages() {
  log('阶段 2/6：构建镜像（首次构建较慢，之后走 BuildKit 缓存）')
  console.log('  构建输出在结束时汇总（首次构建可能要几分钟；有 BuildKit 缓存后是秒级）')
  const started = Date.now()
  const r = compose(['build'])
  const seconds = ((Date.now() - started) / 1000).toFixed(0)
  const ok = record('docker compose build（backend + frontend）', r.code === 0, `耗时 ${seconds}s`)
  if (!ok) {
    console.log((r.out + r.err).split('\n').slice(-25).join('\n'))
  }
  return ok
}

async function startStack() {
  log('阶段 3/6：起栈（HTTP 版）')

  // 先清一遍自己的项目：上一次跑崩留下的数据卷会让"迁移把库升到 head"这条断言失去意义。
  cleanup()

  const up = compose(['up', '-d', '--build'])
  record('docker compose up -d --build', up.code === 0, up.code === 0 ? '' : up.err.trim().slice(0, 300))
  if (up.code !== 0) {
    console.log((up.out + up.err).split('\n').slice(-25).join('\n'))
    return false
  }

  try {
    await waitForHttp(`${BASE_URL}/health`)
  } catch (error) {
    record('栈在 120s 内就绪', false, error.message)
    // 就绪失败十有八九是容器在重启循环里，原因只在容器日志里 ——
    // 直接打出来，省掉"跑完再去翻报告"的一步。
    for (const service of ['db', 'backend', 'frontend']) {
      const logs = compose(['logs', '--no-color', '--tail', '25', service])
      console.log(`
----- ${service} 最近 25 行 -----
${(logs.out + logs.err).trim()}`)
    }
    return false
  }
  record('栈在 120s 内就绪', true, BASE_URL)

  for (const service of ['db', 'backend', 'frontend']) {
    const id = containerId(service)
    record(`${service} 容器健康`, containerHealth(id) === 'healthy' || containerState(id) === 'running',
      `${containerState(id)}/${containerHealth(id)}`)
  }
  return true
}

// ────────────────────────────────────────────────────────────── 阶段 4：HTTP 行为

/** 取出 HTML 里所有**内联**脚本，算 CSP 需要的 sha256（src= 的外链脚本不参与）。 */
function inlineScriptHashes(html) {
  const hashes = []
  const pattern = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi
  let match
  while ((match = pattern.exec(html))) {
    if (/\bsrc\s*=/i.test(match[1])) continue
    hashes.push(createHash('sha256').update(match[2], 'utf8').digest('base64'))
  }
  return hashes
}

async function httpChecks() {
  log('阶段 4/6：HTTP 行为（经 nginx，与线上同一条路径）')

  // ---------- 健康检查 ----------
  const health = await httpGet(`${BASE_URL}/health`)
  record('/health 经 nginx 返回 200', health.status === 200, `HTTP ${health.status}`)

  // ---------- 首页文档 ----------
  const home = await httpGet(`${BASE_URL}/`)
  record('首页返回 HTML 且含 SPA 挂载点', home.status === 200 && home.body.includes('id="app"'),
    `HTTP ${home.status} ${home.body.length}B`)

  const csp = headerOf(home, 'content-security-policy')
  // 这条是**回归网**：拆分前 `location = /index.html` 自己写了 add_header，
  // 导致 nginx 的 add_header 不再继承，首页文档一个安全头都没有（CSP 只在文档上有意义，
  // 挂在 /assets/ 上的 CSP 等于没挂）。
  record('首页文档带 CSP', csp.includes("script-src 'self'"), csp ? '已发出' : '缺少 Content-Security-Policy')

  // CSP 必须放行 index.html 里那段内联主题脚本（否则暗色模式白闪回归 + 控制台 CSP 违规）。
  // 这里独立算哈希，失败时直接把该填的值打出来。
  const hashes = inlineScriptHashes(home.body)
  const missing = hashes.filter((hash) => !csp.includes(`'sha256-${hash}'`))
  record(
    `内联脚本被 CSP 放行（共 ${hashes.length} 段）`,
    missing.length === 0,
    missing.length ? `需要在 deploy/security-headers.inc 里加：'sha256-${missing[0]}'` : '',
  )
  record("CSP 未使用 'unsafe-inline'（否则这道防线等于没有）", !/script-src[^;]*'unsafe-inline'/.test(csp))

  // 首页不能被长缓存，否则前端发版后用户一直加载旧 JS
  record('首页禁止缓存', /no-cache/.test(headerOf(home, 'cache-control')), headerOf(home, 'cache-control'))

  // ---------- HSTS 的条件式行为 ----------
  record('纯 HTTP 下不发 HSTS（避免把自己锁死）', !headerOf(home, 'strict-transport-security'))
  const forwarded = await httpGet(`${BASE_URL}/`, { headers: { 'X-Forwarded-Proto': 'https' } })
  record('外层声明 https 时发出 HSTS', Boolean(headerOf(forwarded, 'strict-transport-security')),
    headerOf(forwarded, 'strict-transport-security'))

  // ---------- 静态资源缓存 ----------
  const assetMatch = home.body.match(/\/assets\/[A-Za-z0-9._-]+\.js/)
  if (assetMatch) {
    const asset = await httpGet(`${BASE_URL}${assetMatch[0]}`)
    const cacheControl = headerOf(asset, 'cache-control')
    record('带指纹的静态资源长缓存', asset.status === 200 && /immutable/.test(cacheControl) &&
      /max-age=31536000/.test(cacheControl), `HTTP ${asset.status} ${cacheControl}`)
  } else {
    record('带指纹的静态资源长缓存', false, '首页里找不到 /assets/*.js 引用')
  }
  const missingAsset = await httpGet(`${BASE_URL}/assets/deploy-check-missing.js`)
  record('缺失的静态资源返回 404（不回退成 HTML）', missingAsset.status === 404, `HTTP ${missingAsset.status}`)

  // ---------- SPA history 回退 ----------
  const deepLink = await httpGet(`${BASE_URL}/article/deploy-check-missing`)
  record('深链回退到 index.html（刷新不 404）', deepLink.status === 200 && deepLink.body.includes('id="app"'),
    `HTTP ${deepLink.status}`)

  // ---------- API 反代 ----------
  const articles = await httpGet(`${BASE_URL}/api/v1/articles`)
  let envelope = null
  try {
    envelope = JSON.parse(articles.body)
  } catch {
    /* 下面按解析失败处理 */
  }
  record('/api/v1/articles 反代正常且是 JSON 信封', articles.status === 200 && Array.isArray(envelope?.items),
    `HTTP ${articles.status}`)

  // 匿名读接口的公开缓存：这条同时是「Vary: Authorization」那个 bug 的回归网
  // （匿名与登录态共用 URL，共享缓存不区分凭证会弄坏后台的读己之写）
  record('匿名读接口带公开缓存头与 Vary: Authorization',
    /public/.test(headerOf(articles, 'cache-control')) && /authorization/i.test(headerOf(articles, 'vary')),
    `${headerOf(articles, 'cache-control')} | vary: ${headerOf(articles, 'vary')}`)

  // 路径取的是真实存在的后台接口（与公开读共用 /api/v1/articles 前缀，
  // 靠路径里的 /manage 被缓存中间件排除 —— 这是那条约定的回归网）
  const manage = await httpGet(`${BASE_URL}/api/v1/articles/manage/list`)
  record('后台路径不被公开缓存且要求登录',
    manage.status === 401 && !/public/.test(headerOf(manage, 'cache-control')),
    `HTTP ${manage.status} ${headerOf(manage, 'cache-control') || '无缓存头'}`)

  const notFound = await httpGet(`${BASE_URL}/api/v1/articles/deploy-check-missing`)
  record('API 的 404 不被 SPA 回退吃掉', notFound.status === 404, `HTTP ${notFound.status}`)

  // ---------- RSS / sitemap（这次重构把两个 location 合成了一个正则，必须实测） ----------
  const feed = await httpGet(`${BASE_URL}/feed.xml`)
  record('/feed.xml 可用且是 XML', feed.status === 200 && feed.body.includes('<rss'), `HTTP ${feed.status}`)
  const sitemap = await httpGet(`${BASE_URL}/sitemap.xml`)
  record('/sitemap.xml 可用且含 urlset', sitemap.status === 200 && sitemap.body.includes('<urlset'),
    `HTTP ${sitemap.status}`)

  // ---------- 媒体 ----------
  const media = await httpGet(`${BASE_URL}/media/deploy-check-missing.png`)
  record('媒体路径走反代且带 nosniff（空卷下应为 404）',
    media.status === 404 && headerOf(media, 'x-content-type-options') === 'nosniff',
    `HTTP ${media.status} nosniff=${headerOf(media, 'x-content-type-options') || '无'}`)
}

// ────────────────────────────────────────────────────────────── 阶段 5：容器内部

async function containerChecks() {
  log('阶段 5/6：容器内部（生产形态、迁移、备份）')

  const backendId = containerId('backend')

  // ---------- 生产形态 ----------
  const envProbe = compose(['exec', '-T', 'backend', 'printenv', 'APP_ENV', 'DEBUG', 'DB_AUTO_CREATE'])
  const [appEnv, debug, autoCreate] = envProbe.out.trim().split(/\s+/)
  record('容器按生产模式运行（不只是"看起来像"）',
    appEnv === 'production' && debug === 'false' && autoCreate === 'false',
    `APP_ENV=${appEnv} DEBUG=${debug} DB_AUTO_CREATE=${autoCreate}`)

  const docs = compose(['exec', '-T', 'backend', 'sh', '-c',
    'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/docs'])
  record('生产模式下接口文档已关闭', docs.out.trim() === '404', `容器内 /docs → ${docs.out.trim()}`)

  const uid = docker(['exec', backendId, 'id', '-u'])
  record('后端以非 root 用户运行', uid.out.trim() === '1000', `uid=${uid.out.trim()}`)

  const storage = compose(['exec', '-T', 'backend', 'sh', '-c',
    'grep -q /app/storage /proc/mounts && test -w /app/storage && echo ok'])
  record('/app/storage 是挂载卷且可写（媒体不会随容器重建丢失）', storage.out.includes('ok'))

  // ---------- 迁移 ----------
  const heads = compose(['exec', '-T', 'backend', 'alembic', 'heads'])
  const headRevision = (heads.out.match(/^([0-9a-f]{6,})/m) ?? [])[1] ?? ''
  const current = compose(['exec', '-T', 'db', ...PSQL, '-tAc', 'select version_num from alembic_version'])
  const currentRevision = current.out.trim()
  record('容器入口自动迁移把库升到了 head', Boolean(headRevision) && headRevision === currentRevision,
    `head=${headRevision || '?'} 库=${currentRevision || '?'}`)

  // 光看 alembic_version 不够：万一 head 恰好是别的迁移，这里断言"最新那批表"确实存在。
  const tables = compose(['exec', '-T', 'db', ...PSQL, '-tAc',
    "select to_regclass('public.friend_links') is not null and to_regclass('public.refresh_sessions') is not null"])
  record('最新迁移建出的两张表都在（证明升到的是 head 而不是中间版本）',
    tables.out.trim() === 't', tables.out.trim())

  // ---------- 备份服务 ----------
  const backupId = containerId('backup')
  record('备份 sidecar 在运行', containerState(backupId) === 'running', containerState(backupId))

  const dumps = existsSync(BACKUP_DIR)
    ? readdirSync(BACKUP_DIR).filter((name) => name.startsWith('blog-') && name.endsWith('.dump')).sort()
    : []
  const newest = dumps.at(-1)
  record('启动后自动产出了备份文件', Boolean(newest), newest ?? `目录内 ${dumps.length} 份`)

  if (!newest) return

  const dumpPath = join(BACKUP_DIR, newest)
  const size = statSync(dumpPath).size
  record('备份文件大小合理（不是空归档）', size > 10 * 1024, `${(size / 1024).toFixed(1)} KB`)

  // ---------- 恢复演练 ----------
  // 「备份没验证过等于没有备份」：这里真的恢复到一个临时库，再核对结构与迁移版本。
  // 只验 pg_dump 退出码是不够的 —— 一个截断的归档退出码也可能是 0。
  const restoreDb = 'deploy_check_restore'
  try {
    compose(['exec', '-T', 'db', 'dropdb', '-U', PG.user, '--if-exists', restoreDb])
    const created = compose(['exec', '-T', 'db', 'createdb', '-U', PG.user, restoreDb])
    const restored = compose(['exec', '-T', 'db', 'pg_restore', '--exit-on-error', '-U', PG.user, '-d', restoreDb], {
      input: readFileSync(dumpPath),
      encoding: null,
      maxBuffer: 64 * 1024 * 1024,
    })
    const restoredTables = compose(['exec', '-T', 'db', 'psql', '-U', PG.user, '-d', restoreDb, '-tAc',
      "select count(*) from information_schema.tables where table_schema = 'public'"])
    const restoredRevision = compose(['exec', '-T', 'db', 'psql', '-U', PG.user, '-d', restoreDb, '-tAc',
      'select version_num from alembic_version'])
    const tableCount = Number(text(restoredTables.out).trim())
    // 这条断言的失败信息要能直接指出是哪一步坏了 —— 恢复演练是这里最复杂的一段
    // （建库 / 归档 / psql 三段都可能单独失败），只报个"表 0 张"等于让人再跑一遍。
    const lastLines = (value, count) => text(value).trim().split('\n').slice(-count).join(' / ')
    const detailParts = [
      `建库 rc=${created.code}`,
      `恢复 rc=${restored.code}`,
      `表 ${text(restoredTables.out).trim() || '（空）'}`,
      `alembic=${text(restoredRevision.out).trim() || '（空）'}`,
    ]
    if (text(restored.err).trim()) detailParts.push(`pg_restore: ${lastLines(restored.err, 2)}`)
    if (text(restoredTables.err).trim()) detailParts.push(`psql: ${lastLines(restoredTables.err, 1)}`)
    const detail = detailParts.join('，')
    record('把备份恢复到空库能成功（真的恢复演练）',
      created.code === 0 && restored.code === 0 && tableCount >= 14 &&
      text(restoredRevision.out).trim() === headRevision,
      detail)
  } catch (error) {
    record('把备份恢复到空库能成功（真的恢复演练）', false, error.message)
  } finally {
    compose(['exec', '-T', 'db', 'dropdb', '-U', PG.user, '--if-exists', restoreDb])
  }

  // ---------- 轮转 ----------
  // 两次手动备份 + BACKUP_KEEP=2：跑完目录里应当只剩 2 份。
  // 没有轮转的备份会把磁盘塞满，而"满盘后的那次备份"恰好是你唯一需要的备份。
  compose(['run', '--rm', '-e', 'BACKUP_ONCE=1', '-e', 'BACKUP_KEEP=2', 'backup'])
  // 两次手动备份要落在不同的"秒"上，否则文件名相同、第二次会覆盖第一次，
  // 这条断言就变成恒真（测不到轮转）。
  await sleep(1100)
  compose(['run', '--rm', '-e', 'BACKUP_ONCE=1', '-e', 'BACKUP_KEEP=2', 'backup'])
  const after = existsSync(BACKUP_DIR)
    ? readdirSync(BACKUP_DIR).filter((name) => /^blog-.*\.(dump|db)$/.test(name)).length
    : 0
  record('轮转只保留 BACKUP_KEEP 份', after === 2, `目录内 ${after} 份（期望 2）`)
}

// ────────────────────────────────────────────────────────────── 阶段 6：TLS

function makeSelfSignedCert() {
  mkdirSync(join(CERTS_DIR, 'live', CERT_DOMAIN), { recursive: true })
  // 用已经要构建的 python 基础镜像里的 openssl 生成，不额外拉镜像、也不依赖宿主装了 openssl。
  const r = docker([
    'run', '--rm',
    '-v', `${CERTS_DIR}:/certs`,
    '--entrypoint', 'openssl',
    'python:3.12-slim',
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
    '-subj', '/CN=localhost',
    '-keyout', `/certs/live/${CERT_DOMAIN}/privkey.pem`,
    '-out', `/certs/live/${CERT_DOMAIN}/fullchain.pem`,
  ])
  const cert = join(CERTS_DIR, 'live', CERT_DOMAIN, 'fullchain.pem')
  const key = join(CERTS_DIR, 'live', CERT_DOMAIN, 'privkey.pem')
  return record('生成自签证书（验证 TLS 配置用）',
    r.code === 0 && existsSync(cert) && existsSync(key) && statSync(cert).size > 0,
    r.code === 0 ? '' : r.err.trim().slice(0, 200))
}

async function tlsChecks() {
  log('阶段 6/6：自管证书的 HTTPS（覆盖文件 nginx.https.conf + compose.tls.yml）')

  if (!makeSelfSignedCert()) return

  const up = compose(['up', '-d', '--build'], { tls: true })
  record('叠加 docker-compose.tls.yml 后重建前端成功', up.code === 0, up.code === 0 ? '' : up.err.trim().slice(0, 300))
  if (up.code !== 0) return

  try {
    await waitForHttp(`${TLS_URL}/health`, { insecure: true, timeoutMs: 90000 })
    record('HTTPS 站点可用（443 上的 nginx + 自管证书）', true, TLS_URL)
  } catch (error) {
    record('HTTPS 站点可用（443 上的 nginx + 自管证书）', false, error.message)
    return
  }

  const home = await httpGet(`${TLS_URL}/`, { insecure: true })
  record('HTTPS 首页带 CSP', headerOf(home, 'content-security-policy').includes("script-src 'self'"),
    `HTTP ${home.status}`)
  // HTTPS 版是 TLS 终点，HSTS 必须无条件发出（与纯 HTTP 版的条件式行为相对）
  record('HTTPS 版无条件发出 HSTS',
    /max-age=31536000/.test(headerOf(home, 'strict-transport-security')),
    headerOf(home, 'strict-transport-security') || '缺少 Strict-Transport-Security')

  const api = await httpGet(`${TLS_URL}/api/v1/articles`, { insecure: true })
  record('HTTPS 下 API 反代同样正常', api.status === 200, `HTTP ${api.status}`)

  // ---------- 80：跳转与 ACME ----------
  const redirect = await httpGet(`${BASE_URL}/`)
  const location = headerOf(redirect, 'location')
  record('80 端口跳转到 HTTPS', redirect.status === 301 && location.startsWith('https://'),
    `HTTP ${redirect.status} → ${location || '（无 Location）'}`)

  // 续期挑战必须留在 80 且不能被跳转吃掉。这是最容易被写错的一处：
  // server 级的 `return 301` 属于 rewrite 阶段，会在 location 匹配**之前**执行，
  // 于是挑战请求也被跳走，而 certbot 只会说"校验失败"。
  const token = 'deploy-check-token'
  const tokenBody = `deploy-check-${Date.now()}`
  // 注意路径：certbot 把 `-w` 当**文档根**，所以它会自己建
  // `.well-known/acme-challenge/` 子目录；而 nginx 的 `root` 指令同样会拼上完整 URI，
  // 两边正好对上。把文件直接放在 webroot 根目录是取不到的（第一次跑就踩了这个：404）。
  const challengeDir = join(WEBROOT_DIR, '.well-known', 'acme-challenge')
  mkdirSync(challengeDir, { recursive: true })
  writeFileSync(join(challengeDir, token), tokenBody)
  const challenge = await httpGet(`${BASE_URL}/.well-known/acme-challenge/${token}`)
  record('ACME 续期挑战不被跳转吃掉（能取到明文文件）',
    challenge.status === 200 && challenge.body.trim() === tokenBody,
    `HTTP ${challenge.status} body=${challenge.body.trim().slice(0, 40) || '（空）'}`)
}

// ────────────────────────────────────────────────────────────── 主流程

function writeReport(failed) {
  const lines = [
    '# 部署产物验证报告',
    '',
    `- 生成时间：${new Date().toISOString()}`,
    `- 项目名：\`${PROJECT}\`（HTTP ${APP_PORT} / HTTPS ${SKIP_TLS ? '跳过' : TLS_PORT}）`,
    `- 结果：**通过 ${results.length - failed.length} / ${results.length}**`,
    '',
    '## 断言明细',
    '',
    '| # | 断言 | 结果 | 说明 |',
    '|---|---|---|---|',
    ...results.map((item, index) => `| ${index + 1} | ${item.name} | ${item.ok ? '✅' : '❌'} | ${item.detail.replace(/\|/g, '\\|')} |`),
    '',
    '## 执行的命令',
    '',
    '```bash',
    ...[...new Set(commandLog)].map((line) => line.replace(`-p ${PROJECT} `, `-p ${PROJECT} `)),
    '```',
  ]
  if (failed.length) {
    lines.push('', '## 失败现场（容器日志）', '', collectDiagnostics())
  }
  mkdirSync(REPORT_DIR, { recursive: true })
  writeFileSync(join(REPORT_DIR, 'deploy-report.md'), `${lines.join('\n')}\n`)
}

async function main() {
  const version = docker(['version', '--format', '{{.Server.Version}}'])
  if (version.code !== 0) {
    console.error('需要可用的 Docker（含 compose v2）：', version.err.trim() || version.spawnError?.message)
    process.exitCode = 1
    return
  }

  console.log(`部署产物验证 · 项目 ${PROJECT} · Docker ${version.out.trim()}`)
  console.log(`临时目录 ${TMP}（备份 ${BACKUP_DIR}，证书 ${CERTS_DIR}）`)

  let crashed = null
  try {
    staticChecks()
    if (buildImages() && (await startStack())) {
      await httpChecks()
      await containerChecks()
      if (!SKIP_TLS) await tlsChecks()
    }
  } catch (error) {
    crashed = error
    record('验证流程未抛异常', false, error.message)
  } finally {
    const failed = results.filter((item) => !item.ok)
    writeReport(failed)
    if (!KEEP) {
      cleanup()
      rmSync(TMP, { recursive: true, force: true })
    } else {
      console.log(`\n保留了现场（KEEP=1）：${TMP}`)
      console.log(`查看日志：docker compose -p ${PROJECT} -f docker-compose.yml logs --tail 50`)
    }
  }

  const failed = results.filter((item) => !item.ok)
  console.log(`\n${'='.repeat(56)}`)
  console.log(`通过 ${results.length - failed.length} / ${results.length}`)
  console.log(`报告：${join(REPORT_DIR, 'deploy-report.md')}`)
  if (failed.length) {
    console.log('\n失败项:')
    for (const item of failed) console.log(`  - ${item.name} ${item.detail}`)
    process.exitCode = 1
  }
  if (crashed) console.error('\n异常:', crashed.message)
}

await main()
