/**
 * HTTP 层回归测试。
 *
 * 这块此前**完全没有测试**，而它恰恰是登录态最容易出事的地方。这里守护五条契约：
 *
 * 1. 401 时静默续期并重放原请求，且**并发只有一次 refresh**（单飞锁，
 *    页面启动续期与 401 续期共用同一把锁）；
 * 2. 续期后重放仍然 401（账号停用 / 凭证已死）时强制登出，
 *    否则会卡在「token 还在、守卫放行、但每个请求都 401、页面永远空白」的死局；
 * 3. 登录 / 刷新接口自身返回 401 是「凭证不对」，重试没有意义，不能去续期；
 *    完全匿名的 401 也不续期、不登出（用户只是没登录，不是"登录过期"）；
 * 4. 凭证**只存在内存**：localStorage 里不许出现任何令牌，refresh token
 *    更不允许出现在刷新请求体里（它只应该由浏览器从 httpOnly Cookie 带上）；
 * 5. 后端错误体（含字段级校验错误）必须归一化成 ApiError，而不是把原始 axios 错误抛给组件。
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

import type { Token } from '@/types'
import {
  ApiError,
  SESSION_HINT_COOKIE_NAME,
  api,
  http,
  onCredentialsCleared,
  refreshSession,
  setAuthRequiredProbe,
  tokenStore,
} from '@/api/http'

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

/** 一次 refresh 调用的入参（url / body / config）。 */
type RefreshCall = [string, unknown, { withCredentials?: boolean; timeout?: number }]

/** 取第 n 次 axios.post 调用的入参：axios 的重载让 mock.calls 的类型没法直接用。 */
function refreshCall(post: { mock: { calls: unknown[][] } }, index = 0): RefreshCall {
  return post.mock.calls[index] as unknown as RefreshCall
}

beforeEach(() => {
  localStorage.clear()
  // token 现在只活在内存里，localStorage.clear() 清不掉它 —— 必须显式清，
  // 否则上一个用例的登录态会串到下一个用例
  tokenStore.clear()
  // 提示 Cookie 同理：jsdom 的 document.cookie 会跨用例残留
  document.cookie = `${SESSION_HINT_COOKIE_NAME}=; path=/; max-age=0`
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

describe('凭证存储（只存内存）', () => {
  it('保存令牌后 localStorage 里什么都没有（防回归的核心保证）', () => {
    // 后端只在 REFRESH_TOKEN_IN_BODY=true 时才在响应体里带 refresh token；
    // 即使带了也必须忽略，不能落进任何 JS 可读的持久化位置
    const response: Token = {
      access_token: 'at',
      refresh_token: 'rt',
      token_type: 'bearer',
      expires_in: 7200,
    }
    tokenStore.save(response)

    expect(tokenStore.access).toBe('at')
    expect(localStorage.getItem('blog-access-token')).toBeNull()
    expect(localStorage.getItem('blog-refresh-token')).toBeNull()
    // 顺带确认没有别的键被偷偷写进去
    expect(localStorage.length).toBe(0)
  })

  it('clear 只清内存里的 access token', () => {
    tokenStore.save({ access_token: 'at' })
    tokenStore.clear()

    expect(tokenStore.access).toBeNull()
  })
})

describe('会话提示 Cookie 的读取（只用来省一次请求）', () => {
  it('常量与后端配置项的名字一致', () => {
    // 后端配置项是 `session_hint_cookie_name`（backend/src/app/config.py）。
    // 这条只是把"前端硬编码的名字"钉在测试里；真正的跨端机器比对在
    // backend/tests/test_session_hint_cookie.py，后端改名时那里会红。
    expect(SESSION_HINT_COOKIE_NAME).toBe('blog_session')
  })

  it('Cookie 存在时 hasSessionHint 为 true，缺失时为 false', () => {
    expect(tokenStore.hasSessionHint).toBe(false)

    document.cookie = `${SESSION_HINT_COOKIE_NAME}=1; path=/`
    expect(tokenStore.hasSessionHint).toBe(true)

    document.cookie = `${SESSION_HINT_COOKIE_NAME}=; path=/; max-age=0`
    expect(tokenStore.hasSessionHint).toBe(false)
  })

  it('只认这个名字，别的前缀相同 Cookie 不算数', () => {
    document.cookie = `${SESSION_HINT_COOKIE_NAME}_other=1; path=/`
    expect(tokenStore.hasSessionHint).toBe(false)
  })

  it('它只是提示位，不代表已登录（access token 仍是空的）', () => {
    document.cookie = `${SESSION_HINT_COOKIE_NAME}=1; path=/`
    expect(tokenStore.hasSessionHint).toBe(true)
    // 关键：hint 不能被当成授权依据
    expect(tokenStore.access).toBeNull()
  })
})

describe('请求拦截器', () => {
  it('自动附加内存里的 Access Token', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
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
  it('刷新请求不携带 refresh token：只靠 httpOnly Cookie', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '登录已过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }
    const post = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 7200 },
    })

    await expect(api.get('/articles')).resolves.toEqual({ replayed: true })

    const [url, body, config] = refreshCall(post)
    expect(url).toContain('/auth/refresh')
    // 空的 `{}`：后端把 RefreshRequest 声明为必填 body，一个字节都不发会 422；
    // 但里面绝不能有任何凭证字段 —— refresh token 只能由浏览器从 Cookie 带上
    expect(body).toEqual({})
    expect(JSON.stringify(body)).not.toContain('refresh_token')
    expect(config).toMatchObject({ withCredentials: true, timeout: 15000 })
  })

  it('续期成功后用新令牌重放原请求', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
    // 只有携带新令牌的请求才会成功，旧令牌一律 401——以此证明「确实重放了」
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '登录已过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }
    const post = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 7200 },
    })

    await expect(api.get('/articles')).resolves.toEqual({ replayed: true })
    expect(post).toHaveBeenCalledTimes(1)
    // 续期结果必须回到内存里，否则下次请求还是拿旧令牌
    expect(tokenStore.access).toBe(NEW_ACCESS)
    // 而且只能回到内存，localStorage 里不许留痕
    expect(localStorage.getItem('blog-access-token')).toBeNull()
  })

  it('并发 401 只发出一次 refresh（单飞锁）', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }
    const post = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 7200 },
    })

    const results = await Promise.all([api.get('/a'), api.get('/b'), api.get('/c')])
    expect(results).toEqual([{ replayed: true }, { replayed: true }, { replayed: true }])
    // 三个请求同时 401，但只能有一次 refresh：滚动刷新下多余的 refresh 必然失败，
    // 会把用户"莫名其妙登出"
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('登录接口自身 401 不触发续期', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
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
    tokenStore.save({ access_token: OLD_ACCESS })
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
    tokenStore.save({ access_token: OLD_ACCESS })
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
    tokenStore.save({ access_token: OLD_ACCESS })
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
    tokenStore.save({ access_token: OLD_ACCESS })
    setAuthRequiredProbe(() => true)
    const { replace } = replaceStub()
    // 无论令牌新旧都 401：模拟账号被停用
    handler = async (config) => {
      throw httpError(config, 401, { detail: '登录状态已失效', code: 'unauthorized' })
    }
    vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 7200 },
    })

    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'token_expired',
    })
    expect(tokenStore.access).toBeNull()
    expect(replace).toHaveBeenCalledTimes(1)
  })

  it('有 access token 就一定尝试 Cookie 续期（不再依赖本地可读的 refresh token）', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
    // 只有新令牌才放行：以此证明「确实续期并重放了」
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }
    const post = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 7200 },
    })

    // 改造前这里还要求「本地能读到 refresh token」，而它现在只在 httpOnly
    // Cookie 里 —— 照旧判定就会变成"既不续期也不清凭证"的 401 死局：
    // 守卫放行、请求全 401、页面永远空白
    await expect(api.get('/articles')).resolves.toEqual({ replayed: true })
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('页面启动续期与 401 续期共用同一把锁（只发一次 refresh）', async () => {
    tokenStore.save({ access_token: OLD_ACCESS })
    // 续期一直挂着：模拟"守卫刚发起静默续期，首个业务请求就 401 了"
    let release!: (value: unknown) => void
    const gate = new Promise((resolve) => {
      release = resolve
    })
    const post = vi
      .spyOn(axios, 'post')
      .mockImplementation(() => gate as unknown as Promise<AxiosResponse>)
    handler = async (config) => {
      if (authOf(config) !== `Bearer ${NEW_ACCESS}`) {
        throw httpError(config, 401, { detail: '过期', code: 'unauthorized' })
      }
      return ok(config, { replayed: true })
    }

    // 两条路径同时发起：restore() 的静默续期 + 拦截器里 401 触发的续期
    const started = [refreshSession(), refreshSession()]
    const intercepted = api.get('/articles')
    await Promise.resolve()

    // 滚动轮换下第二个 refresh 必然失败 —— 症状是"刚打开页面就被登出"
    expect(post).toHaveBeenCalledTimes(1)

    release({ data: { access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 7200 } })
    await Promise.all(started)
    await expect(intercepted).resolves.toEqual({ replayed: true })
  })

  it('完全匿名（内存里没有 access token）的 401：不续期、不登出', async () => {
    tokenStore.clear()
    const notified = vi.fn()
    onCredentialsCleared(notified)
    const post = vi.spyOn(axios, 'post')
    handler = async (config) => {
      throw httpError(config, 401, { detail: '需要登录', code: 'unauthorized' })
    }

    await expect(api.get('/articles')).rejects.toMatchObject({
      status: 401,
      code: 'unauthorized',
    })
    // 匿名不是"登录过期"：既不该白跑一次续期，也不该通知 store 清登录态
    expect(post).not.toHaveBeenCalled()
    expect(notified).not.toHaveBeenCalled()
  })
})
