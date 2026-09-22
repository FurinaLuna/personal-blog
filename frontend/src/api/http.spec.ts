/**
 * HTTP 层回归测试。
 *
 * 这块此前**完全没有测试**，而它恰恰是登录态最容易出事的地方。这里守护四条契约：
 *
 * 1. 401 时用 refresh 静默续期并重放原请求，且**并发只有一次 refresh**（单飞锁）；
 * 2. 续期后重放仍然 401（账号停用 / 凭证已死）时强制登出，
 *    否则会卡在「token 还在、守卫放行、但每个请求都 401、页面永远空白」的死局；
 * 3. 登录 / 刷新接口自身返回 401 是「凭证不对」，重试没有意义，不能去续期；
 * 4. 后端错误体（含字段级校验错误）必须归一化成 ApiError，而不是把原始 axios 错误抛给组件。
 *
 * 说明：这里用替换 `http.defaults.adapter` 的方式模拟响应，而不是引入额外的 mock 库——
 * 既没有新依赖，也能真实走完拦截器链条（这是本文件唯一有价值的被测对象）。
 */
import axios, {
  AxiosError,
  AxiosHeaders,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, http, onCredentialsCleared, setAuthRequiredProbe, tokenStore } from '@/api/http'

type Handler = (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>

const OLD_ACCESS = 'old-access-token'
const NEW_ACCESS = 'new-access-token'

let handler: Handler

function ok(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: new AxiosHeaders(),
    config,
  }
}

function httpError(config: InternalAxiosRequestConfig, status: number, data: unknown): AxiosError {
  return new AxiosError(
    `请求失败（${status}）`,
    'ERR_BAD_RESPONSE',
    config,
    {},
    { data, status, statusText: '', headers: new AxiosHeaders(), config },
  )
}

/** 取到请求实际携带的 Authorization（拦截器写的是普通对象或 AxiosHeaders 都可能）。 */
function authOf(config: InternalAxiosRequestConfig): string | undefined {
  const headers = config.headers as AxiosHeaders | Record<string, string> | undefined
  if (!headers) return undefined
  if (typeof (headers as AxiosHeaders).get === 'function') {
    const value = (headers as AxiosHeaders).get('Authorization')
    return typeof value === 'string' ? value : undefined
  }
  return (headers as Record<string, string>).Authorization
}

function replaceStub(): { replace: ReturnType<typeof vi.fn>; pathname: string } {
  const replace = vi.fn()
  vi.stubGlobal('location', { pathname: '/admin/articles', search: '', replace })
  return { replace, pathname: '/admin/articles' }
}

beforeEach(() => {
  localStorage.clear()
  // 默认按"公开页面"起步：只有需要登录的页面才允许整页跳登录页
  setAuthRequiredProbe(() => false)
  handler = async (config) => ok(config, { ok: true })
  http.defaults.adapter = ((config: InternalAxiosRequestConfig) =>
    handler(config)) as unknown as AxiosAdapter
})

afterEach(() => {
  setAuthRequiredProbe(() => false)
  vi.unstubAllGlobals()
})

describe('错误归一化（后端错误体 → ApiError）', () => {
  it('401 且无 detail 时使用兜底文案，并标记 isUnauthorized', async () => {
    handler = async (config) => {
      throw httpError(config, 401, { code: 'unauthorized' })
    }
    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'unauthorized',
      message: '登录状态已失效，请重新登录',
      isUnauthorized: true,
    })
  })

  it('字段级校验错误被整理成 fields，供表单标红', async () => {
    handler = async (config) => {
      throw httpError(config, 422, {
        detail: [
          { field: 'password', message: '密码至少 8 位', type: 'value_error' },
          { field: 'password', message: '重复项只取第一条' },
        ],
        code: 'validation_error',
      })
    }

    const error = await api.post('/auth/users', {}).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.status).toBe(422)
    expect(apiError.code).toBe('validation_error')
    expect(apiError.fields.password).toBe('密码至少 8 位')
  })

  it('无响应（网络异常）时 status 归零并标记 isNetworkError', async () => {
    handler = async (config) => {
      throw new AxiosError('Network Error', 'ERR_NETWORK', config)
    }
    const error = await api.get('/articles').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).isNetworkError).toBe(true)
    expect((error as ApiError).code).toBe('network_error')
  })

  it('500 使用兜底文案，不把后端原始报文直接抛给组件', async () => {
    handler = async (config) => {
      throw httpError(config, 500, { detail: 'internal boom' })
    }
    await expect(api.get('/articles')).rejects.toMatchObject({ status: 500, message: 'internal boom' })

    handler = async (config) => {
      throw httpError(config, 500, undefined)
    }
    await expect(api.get('/articles')).rejects.toMatchObject({
      message: '服务器开小差了，请稍后重试',
    })
  })
})

describe('请求拦截器', () => {
  it('自动附加本地保存的 Access Token', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    let seen: string | undefined
    handler = async (config) => {
      seen = authOf(config)
      return ok(config, { ok: true })
    }
    await api.get('/articles')
    expect(seen).toBe(`Bearer ${OLD_ACCESS}`)
  })

  it('查询参数清洗：去掉空值，但保留 0 与 false', async () => {
    let seen: unknown
    handler = async (config) => {
      seen = config.params
      return ok(config, [])
    }
    await api.get('/articles', {
      zero: 0,
      no: false,
      empty: '',
      nothing: null,
      missing: undefined,
      keep: 'yes',
    })
    expect(seen).toEqual({ zero: 0, no: false, keep: 'yes' })
  })
})

describe('401 静默续期', () => {
  it('续期成功后用新令牌重放原请求', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    // 只有携带新令牌的请求才会成功，旧令牌一律 401——以此证明「确实重放了」
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '登录已过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }
    const post = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, refresh_token: 'rt2', token_type: 'bearer', expires_in: 7200 },
    })

    await expect(api.get('/articles')).resolves.toEqual({ replayed: true })
    expect(post).toHaveBeenCalledTimes(1)
    // 续期结果必须回写本地，否则下次请求还是拿旧令牌
    expect(tokenStore.access).toBe(NEW_ACCESS)
  })

  it('并发 401 只发出一次 refresh（单飞锁）', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }
    const post = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, refresh_token: 'rt2', token_type: 'bearer', expires_in: 7200 },
    })

    const results = await Promise.all([api.get('/a'), api.get('/b'), api.get('/c')])
    expect(results).toEqual([{ replayed: true }, { replayed: true }, { replayed: true }])
    // 三个请求同时 401，但只能有一次 refresh：滚动刷新下多余的 refresh 必然失败，
    // 会把用户"莫名其妙登出"
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('登录接口自身 401 不触发续期', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    handler = async (config) => {
      throw httpError(config, 401, { detail: '用户名或密码错误', code: 'unauthorized' })
    }
    const post = vi.spyOn(axios, 'post')

    await expect(api.post('/auth/login', { username: 'a', password: 'b' })).rejects.toMatchObject({
      status: 401,
    })
    expect(post).not.toHaveBeenCalled()
  })

  it('续期失败时清除本地凭证；公开页面上不做整页跳转', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    const { replace } = replaceStub()
    handler = async (config) => {
      throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
    }
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh 挂了'))

    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'token_expired',
    })
    expect(tokenStore.access).toBeNull()
    // 公开页面（探针为 false）：访客正在读文章，某个后台请求 401 不该把他弹走
    expect(replace).not.toHaveBeenCalled()
  })

  it('需要登录的页面上续期失败才整页跳登录页（带回跳地址）', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    setAuthRequiredProbe(() => true)
    const { replace } = replaceStub()
    handler = async (config) => {
      throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
    }
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh 挂了'))

    await expect(api.get('/articles')).rejects.toMatchObject({ code: 'token_expired' })
    expect(replace).toHaveBeenCalledTimes(1)
    expect(String(replace.mock.calls[0][0])).toContain('/login?redirect=')
  })

  it('凭证被判死时通知订阅者（store 要把内存里的 user 一起清掉）', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    const notified = vi.fn()
    const unsubscribe = onCredentialsCleared(notified)
    handler = async (config) => {
      throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
    }
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh 挂了'))

    await expect(api.get('/articles')).rejects.toMatchObject({ code: 'token_expired' })
    expect(notified).toHaveBeenCalledTimes(1)

    // 退订之后不再通知（避免组件卸载后仍被调用）
    unsubscribe()
    await expect(api.get('/articles')).rejects.toMatchObject({ status: 401 })
  })

  it('续期成功但重放仍 401（凭证已死）时强制登出，不留死局', async () => {
    tokenStore.save({ access_token: OLD_ACCESS, refresh_token: 'rt' })
    setAuthRequiredProbe(() => true)
    const { replace } = replaceStub()
    // 无论令牌新旧都 401：模拟账号被停用
    handler = async (config) => {
      throw httpError(config, 401, { detail: '登录状态已失效', code: 'unauthorized' })
    }
    vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, refresh_token: 'rt2', token_type: 'bearer', expires_in: 7200 },
    })

    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'token_expired',
    })
    expect(tokenStore.access).toBeNull()
    expect(replace).toHaveBeenCalledTimes(1)
  })

  it('有 access 但没有 refresh 时同样按凭证已死处理（不留 401 死局）', async () => {
    localStorage.clear()
    localStorage.setItem('blog-access-token', OLD_ACCESS)
    handler = async (config) => {
      throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
    }
    const post = vi.spyOn(axios, 'post')

    // 旧实现里这条路径条件不成立，于是既不续期也不清凭证 ——
    // 表现为"守卫放行、请求全 401、页面永远空白"
    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'token_expired',
    })
    expect(post).not.toHaveBeenCalled()
    expect(tokenStore.access).toBeNull()
  })

  it('完全匿名（没有任何凭证）时的 401 仍按普通未授权处理', async () => {
    localStorage.clear()
    handler = async (config) => {
      throw httpError(config, 401, { detail: '需要登录', code: 'unauthorized' })
    }

    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'unauthorized',
    })
  })
})
