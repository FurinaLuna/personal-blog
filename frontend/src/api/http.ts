/**
 * HTTP 客户端。
 *
 * 三件事在这里收口，业务代码只关心「拿到数据」或「抛出一个 ApiError」：
 * 1. 自动附加 Access Token（**只活在内存里**，刷新页面就没了）；
 * 2. 收到 401 时用 httpOnly Cookie 里的 Refresh Token 静默续期，并把原请求重放一次；
 * 3. 把后端各种错误体（领域异常 / 参数校验 / 网络异常）统一成 ApiError。
 *
 * 凭证怎么放（与后端 `app/api/cookies.py` 的约定一一对应）：
 * - access token：内存中的一个模块级变量，短命，丢了就用 Cookie 换一枚；
 * - refresh token：httpOnly Cookie，JS 读不到、也不该读到。
 * 这样即使页面里混进一段 XSS，也拿不到能换出新 access token 的长效凭证。
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

import type { Token, ValidationIssue } from '@/types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

/** 统一的接口异常。组件里只需要读 message / status / fields 三个属性。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  /** 表单场景下的字段级错误，key 是字段名 */
  readonly fields: Record<string, string>

  constructor(
    message: string,
    status = 0,
    code = 'unknown_error',
    fields: Record<string, string> = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = fields
  }

  get isNetworkError(): boolean {
    return this.status === 0
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isNotFound(): boolean {
    return this.status === 404
  }
}

/**
 * 会话提示 Cookie 的名字。
 *
 * 对应后端配置项 `session_hint_cookie_name`（`backend/src/app/config.py`，
 * `.env.example` 里是 `SESSION_HINT_COOKIE_NAME`，默认 `blog_session`）。
 * 前端读不到后端配置，只能硬编码这个名字——两边的一致性由
 * `backend/tests/test_session_hint_cookie.py` 做机器比对（与 `SITE_ARTICLE_PATH`
 * 对前端路由表那套一样）：后端改名时那条用例会红，而不是这里静默失效。
 *
 * 它**不含任何秘密**（值恒为 "1"），只表达"这台浏览器可能有会话"，
 * 用来让公开页面跳过那次多余的 `POST /auth/refresh`。
 */
export const SESSION_HINT_COOKIE_NAME = 'blog_session'

/**
 * 读「会话提示」Cookie 是否存在。
 *
 * 只判断"在不在"，不读值——值恒为 "1"，没有信息量。
 *
 * 为什么不能用别的方式判断有没有会话：refresh token 在 httpOnly Cookie 里，
 * JS 读不到；access token 只在内存里，刷新页面就没了。所以除了这个提示位，
 * 前端在页面加载时**没有任何**本地信号可用。
 */
function readSessionHint(): boolean {
  return document.cookie
    .split(';')
    .some((item) => item.trim().startsWith(`${SESSION_HINT_COOKIE_NAME}=`))
}

/**
 * Access Token 唯一的存放处：**内存**。
 *
 * 以前 access / refresh 两个令牌都写在 localStorage，XSS 一句话就能全带走。
 * 现在 refresh token 只存在于 httpOnly Cookie（JS 完全读不到），access token
 * 短命且只活在这个模块级变量里 —— 刷新页面就没了，所以应用启动时要先用
 * Cookie 静默续期一枚出来（见 `stores/auth.ts` 的 restore）。
 */
let accessToken: string | null = null

export const tokenStore = {
  get access(): string | null {
    return accessToken
  },
  /**
   * 这台浏览器是否**可能**有会话（读后端下发的非 httpOnly 提示 Cookie）。
   *
   * **只用来省一次请求，绝不用于授权 / 安全判断**：hint 与真实会话状态可能不一致
   * （用户手删了 refresh Cookie、后端已吊销会话但 Cookie 还在效期内……）。
   * 受保护路由仍然**无条件**尝试恢复登录态（见 `router/index.ts`），
   * 否则一旦 hint 缺失就会把已登录用户判成未登录，变成"偶发被登出"。
   *
   * 部署边界：前后端不同域且后端未设 COOKIE_DOMAIN 时，这个 Cookie 落在 API 域上，
   * 页面读不到 → 退化成"公开页不恢复登录态"（功能仍正确，只是顶栏慢一步）。
   */
  get hasSessionHint(): boolean {
    return readSessionHint()
  },
  /**
   * 只保存 access token。
   *
   * refresh token 由后端在 `Set-Cookie` 里下发（httpOnly）。即使响应体里带了它
   * （后端为非浏览器客户端保留了这条路）也**一律忽略**：只要 JS 能读到长效凭证，
   * 这次改造就等于白做，XSS 又能顺着轮换链无限续期。
   */
  save(tokens: Pick<Token, 'access_token'>): void {
    accessToken = tokens.access_token
  },
  clear(): void {
    accessToken = null
  },
}

/* ------------------------------------------------------------------ 错误归一化 */

function normalizeError(error: AxiosError): ApiError {
  if (!error.response) {
    const isTimeout = error.code === 'ECONNABORTED'
    return new ApiError(
      isTimeout ? '请求超时，请稍后重试' : '网络异常，请检查网络连接',
      0,
      isTimeout ? 'timeout' : 'network_error',
    )
  }

  const { status, data } = error.response

  // 后端统一错误体：{ detail, code } 或 { detail: ValidationIssue[], code: 'validation_error' }
  const body = (data ?? {}) as { detail?: unknown; code?: string }

  if (Array.isArray(body.detail)) {
    const issues = body.detail as ValidationIssue[]
    const fields: Record<string, string> = {}
    for (const issue of issues) {
      if (issue.field && !fields[issue.field]) fields[issue.field] = issue.message
    }
    const first = issues[0]
    return new ApiError(
      first ? `${first.field || '请求'}：${first.message}` : '提交的内容不合法',
      status,
      body.code ?? 'validation_error',
      fields,
    )
  }

  const detail = typeof body.detail === 'string' ? body.detail : ''
  return new ApiError(detail || defaultMessage(status), status, body.code ?? 'http_error')
}

function defaultMessage(status: number): string {
  switch (status) {
    case 400:
      return '请求参数有误'
    case 401:
      return '登录状态已失效，请重新登录'
    case 403:
      return '没有权限执行该操作'
    case 404:
      return '请求的内容不存在'
    case 413:
      return '文件过大'
    case 415:
      return '不支持的文件类型'
    case 429:
      return '操作过于频繁，请稍后再试'
    case 500:
      return '服务器开小差了，请稍后重试'
    default:
      return `请求失败（${status}）`
  }
}

/* ------------------------------------------------------------------ 实例 */

export const http: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 20000,
  headers: { Accept: 'application/json' },
  // 生产是前后端分离部署，refresh Cookie 只在跨站请求里才需要显式声明；
  // 同源时它无害。不写这一行，跨域场景下浏览器根本不会带上 Cookie。
  withCredentials: true,
})

http.interceptors.request.use((config) => {
  const token = tokenStore.access
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/**
 * 续期的单飞锁。
 *
 * 页面加载时经常几个请求同时 401，如果不加锁，就会并发发出 N 个 refresh，
 * 而滚动刷新（refresh 后旧 refresh 立即作废）会让其中 N-1 个失败，
 * 用户表现为"莫名其妙被登出"。这里保证同时只有一个 refresh 在飞。
 *
 * **所有调用方共用这一把锁**（页面启动的静默续期 + 拦截器里 401 触发的续期）：
 * 这两条路径天然会撞在一起（守卫发起 restore 的同时首个业务请求也 401 了），
 * 各用一把锁就会发出两个 refresh，而滚动轮换下后到的那个必失败 ——
 * 症状是"刚打开页面就被登出"。
 */
let refreshPromise: Promise<Token> | null = null

/**
 * 用 httpOnly Cookie 里的 refresh token 换一枚新的 access token。
 *
 * 请求体是**空的**（`{}`）：refresh token 由浏览器在 Cookie 里自动带上。
 * 走 body 那条路（后端为脚本客户端保留了它）等于把长效凭证又放回 JS 手里。
 * 空 `{}` 而不是完全不带 body：`RefreshRequest` 在后端是必填 body，
 * 一个字节都不发会被 FastAPI 判成 422（实测确认），刷新就永远发不出去。
 *
 * 用裸 axios 是为了不走进本实例的拦截器造成递归。
 */
async function refreshTokens(): Promise<Token> {
  try {
    const { data } = await axios.post<Token>(
      `${BASE_URL}/auth/refresh`,
      {},
      { timeout: 15000, withCredentials: true },
    )
    tokenStore.save(data)
    return data
  } catch (error) {
    // 必须归一化：store 靠 `ApiError.isUnauthorized` 区分「服务端说没登录」和
    // 「网络问不出来」。裸 AxiosError 会被一律当成后者，于是 restored 永远
    // 置不上，匿名访客每次导航都被重试一次刷新。
    throw normalizeError(error as AxiosError)
  }
}

/**
 * 对外唯一的续期入口：带单飞锁（理由见 `refreshPromise`）。
 *
 * 应用启动时由 `stores/auth.ts` 的 `restore()` 调用（内存里的 access token
 * 刷新页面后必然为空），401 时由下面的拦截器调用。
 */
export function refreshSession(): Promise<Token> {
  if (!refreshPromise) {
    refreshPromise = refreshTokens().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

function shouldSkipRefresh(config?: AxiosRequestConfig): boolean {
  const url = config?.url ?? ''
  // 登录/刷新接口自身返回 401 是"凭证不对"，重试没有意义
  return url.includes('/auth/login') || url.includes('/auth/token') || url.includes('/auth/refresh')
}

/**
 * 「当前页面是否需要登录」的探针，由 router 在创建后注入（见 router/index.ts）。
 *
 * 为什么不直接 import router：会形成
 * `router → stores/auth → api/auth → api/http → router` 的循环依赖，
 * 打包器能容忍、运行时却会在某个加载顺序下拿到未初始化的值。
 * 注入式还顺带让这个行为可以被测试直接驱动，不需要真的挂一个 router。
 */
let currentRouteRequiresAuth: () => boolean = () => false

export function setAuthRequiredProbe(probe: () => boolean): void {
  currentRouteRequiresAuth = probe
}

/**
 * 「凭证被判死」的订阅点：清完 token 后触发，让 auth store 把 `user` 也清掉。
 *
 * 没有它会出现审计里描述的那种"没有出口的状态"：token 没了、顶栏还显示着
 * 登录态、页面上的每个请求都 401。http 层不能直接 import store（循环依赖），
 * 所以用这个单向信号。
 */
const credentialsClearedHandlers = new Set<() => void>()

export function onCredentialsCleared(handler: () => void): () => void {
  credentialsClearedHandlers.add(handler)
  return () => {
    credentialsClearedHandlers.delete(handler)
  }
}

/**
 * 强制登出：清掉凭证，必要时回到登录页。
 *
 * 抽成函数是因为有两条路径需要它：续期失败，以及**续期成功但重放请求仍然 401**。
 * 后者以前走不到这里，会出现「token 还在、user 还在、路由守卫继续放行，
 * 但每个请求都 401、页面永远是空的」这种没有出口的状态。
 *
 * 幂等很重要：第二条路径会被走进两次——内层拦截器先判定凭证已死调用一次，
 * 异常冒泡到外层的 catch 又会调用一次。重复 `replace` 肉眼看不出差别，
 * 但同一副作用触发两遍终归不稳（且在测试/离线场景下会放大），所以用
 * 「凭证是不是已经清过」做闸门：没有凭证说明刚刚已经执行过，直接返回。
 *
 * **只有"当前页面本来就需要登录"时才整页跳转**：公开页面上某个后台请求 401
 * （比如顶栏没渲染出来的未读统计）不该把正在读文章的访客弹到登录页——
 * 那会让人以为"站点坏了"。公开页只清凭证 + 通知 store，页面自己按未登录渲染。
 */
function forceLogout(): void {
  // refresh token 已经不在 JS 里了，闸门只能看内存里的 access token。
  // 语义不变：没有凭证说明刚刚已经执行过（或本来就是匿名），直接返回。
  if (!tokenStore.access) return
  tokenStore.clear()
  for (const handler of credentialsClearedHandlers) {
    try {
      handler()
    } catch {
      // 单个订阅者出错不能影响登出本身
    }
  }

  if (!currentRouteRequiresAuth()) return
  // 用 replace 而不是 push：登出后不该还能按返回键回到需要登录的页面
  if (!window.location.pathname.startsWith('/login')) {
    const redirect = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.replace(`/login?redirect=${redirect}`)
  }
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined

    if (error.response?.status === 401 && original) {
      // 已经续期并重放过一次还是 401：说明凭证本身已经死了
      // （账号被停用 / 强制重新登录）。再刷新也没有意义，直接登出。
      if (original._retried) {
        forceLogout()
        return await Promise.reject(new ApiError('登录已过期，请重新登录', 401, 'token_expired'))
      }

      // 判据是「内存里有没有 access token」：
      //
      // - **有** = 这个页面此前登录过，值得用 Cookie 换一枚新的再试一次
      //   （以前这里判的是 `tokenStore.refresh`，而 refresh token 现在 JS 读不到，
      //   照旧写就永远是 false —— 于是"已登录用户 token 过期"会退化成
      //   「不续期、直接 401」，用户卡在每个请求都失败的死局里）；
      // - **没有** = 匿名访客，401 就是普通的未授权，**既不续期也不登出**，
      //   更不该说成"登录已过期"——那会让新访客看到莫名其妙的过期提示。
      //
      // 匿名与"登录过期"的区分全靠这一条：两边都是 401，但用户的处境完全不同。
      if (!shouldSkipRefresh(original) && tokenStore.access) {
        original._retried = true
        try {
          const tokens = await refreshSession()
          original.headers = {
            ...original.headers,
            Authorization: `Bearer ${tokens.access_token}`,
          }
          return await http.request(original)
        } catch {
          // 续期失败（含它自己 401 / 403 / 断网）：凭证这条路走不通了
          forceLogout()
          return await Promise.reject(
            new ApiError('登录已过期，请重新登录', 401, 'token_expired'),
          )
        }
      }
    }

    return await Promise.reject(normalizeError(error))
  },
)

/** 带泛型的便捷方法，省掉每个 api 模块都写一遍 `http.get<T>(...).then(r => r.data)`。 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const { data } = await http.request<T>(config)
  return data
}

/**
 * 查询参数清洗：去掉值为 `undefined` / `null` / 空串的键。
 *
 * 统一在 `api.get` 里做，业务模块直接传对象即可，不用各自维护一份
 * `clean()`（此前 articles/taxonomy/comments 三个模块三种写法）。
 * 注意 `0` / `false` 是合法值，不清洗。
 */
function cleanParams(params?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!params) return undefined
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    result[key] = value
  }
  return result
}

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) =>
    request<T>({ method: 'GET', url, params: cleanParams(params) }),
  post: <T>(url: string, data?: unknown) => request<T>({ method: 'POST', url, data }),
  patch: <T>(url: string, data?: unknown) => request<T>({ method: 'PATCH', url, data }),
  put: <T>(url: string, data?: unknown) => request<T>({ method: 'PUT', url, data }),
  delete: <T = void>(url: string) => request<T>({ method: 'DELETE', url }),
}
