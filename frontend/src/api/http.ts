/**
 * HTTP 客户端。
 *
 * 三件事在这里收口，业务代码只关心「拿到数据」或「抛出一个 ApiError」：
 * 1. 自动附加 Access Token；
 * 2. 收到 401 时用 Refresh Token 静默续期，并把原请求重放一次；
 * 3. 把后端各种错误体（领域异常 / 参数校验 / 网络异常）统一成 ApiError。
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

import type { Token, ValidationIssue } from '@/types'

export const ACCESS_TOKEN_KEY = 'blog-access-token'
export const REFRESH_TOKEN_KEY = 'blog-refresh-token'

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

export const tokenStore = {
  get access(): string | null {
    return localStorage.getItem(ACCESS_TOKEN_KEY)
  },
  get refresh(): string | null {
    return localStorage.getItem(REFRESH_TOKEN_KEY)
  },
  save(tokens: Pick<Token, 'access_token' | 'refresh_token'>): void {
    localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
    localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
  },
  clear(): void {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
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
 */
let refreshPromise: Promise<Token> | null = null

async function refreshTokens(): Promise<Token> {
  const refresh = tokenStore.refresh
  if (!refresh) throw new ApiError('没有可用的刷新令牌', 401, 'no_refresh_token')

  // 用裸 axios，避免走进本实例的拦截器造成递归
  const { data } = await axios.post<Token>(
    `${BASE_URL}/auth/refresh`,
    { refresh_token: refresh },
    { timeout: 15000 },
  )
  tokenStore.save(data)
  return data
}

function shouldSkipRefresh(config?: AxiosRequestConfig): boolean {
  const url = config?.url ?? ''
  // 登录/刷新接口自身返回 401 是"凭证不对"，重试没有意义
  return url.includes('/auth/login') || url.includes('/auth/token') || url.includes('/auth/refresh')
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined

    if (
      error.response?.status === 401 &&
      original &&
      !original._retried &&
      !shouldSkipRefresh(original) &&
      tokenStore.refresh
    ) {
      original._retried = true
      try {
        refreshPromise = refreshPromise ?? refreshTokens()
        const tokens = await refreshPromise
        original.headers = { ...original.headers, Authorization: `Bearer ${tokens.access_token}` }
        return await http.request(original)
      } catch {
        tokenStore.clear()
        // 用 replace 而不是 push：登出后不该还能按返回键回到需要登录的页面
        if (!window.location.pathname.startsWith('/login')) {
          const redirect = encodeURIComponent(window.location.pathname + window.location.search)
          window.location.replace(`/login?redirect=${redirect}`)
        }
        return await Promise.reject(new ApiError('登录已过期，请重新登录', 401, 'token_expired'))
      } finally {
        refreshPromise = null
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

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) => request<T>({ method: 'GET', url, params }),
  post: <T>(url: string, data?: unknown) => request<T>({ method: 'POST', url, data }),
  patch: <T>(url: string, data?: unknown) => request<T>({ method: 'PATCH', url, data }),
  put: <T>(url: string, data?: unknown) => request<T>({ method: 'PUT', url, data }),
  delete: <T = void>(url: string) => request<T>({ method: 'DELETE', url }),
}
