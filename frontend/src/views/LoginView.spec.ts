/**
 * LoginView 测试。
 *
 * 登录页是全站唯一「访客输入什么就发什么」的入口，它一边拿用户输入换 token，
 * 一边按 `?redirect=` 把人送走。所以这里守三类契约：
 *
 * 1. **校验先于请求**：空用户名 / 空密码（含只输空格）就地拦下，一个请求都不发；
 *    用户名 trim 后提交，密码**原样**提交（密码里的空格是密码的一部分）；
 * 2. **redirect 只认站内地址**：这个参数完全由 URL 控制，是开放重定向的经典入口，
 *    外部绝对地址一律回落 /admin（`//` 开头的协议相对地址是**现状缺陷**，
 *    见「redirect 校验」一节，用例钉住现状待修）；
 * 3. **成败语义**：登录成功必须真的跳走（auth.login 的返回值曾被 `ok === undefined`
 *    误判成失败，接口 200、toast 已弹，用户却卡在登录页）；失败要把错误贴在表单里
 *    （下一个动作是改输入，弹 toast 会飘走）、不弹 toast、不跳转、按钮解禁可重试。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口
 * （spy `@/api` 上的方法，并用 tokenStore 摆放本地凭证）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, authApi, tokenStore } from '@/api'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { Token, User } from '@/types'

import LoginView from './LoginView.vue'

/* ------------------------------------------------------------ 测试数据 */

/** 登录响应：refresh token 由后端经 Set-Cookie 下发，响应体里默认没有它。 */
const TOKEN: Token = {
  access_token: 'at-1',
  token_type: 'bearer',
  expires_in: 7200,
}

const ADMIN: User = {
  id: 1,
  username: 'admin',
  nickname: '站长',
  avatar_url: null,
  email: 'admin@example.com',
  bio: null,
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

/** 手动控制 resolve / reject 时机的 promise：用来观察「请求还没回来」这段中间态。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }
let pinia: Pinia

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/login', component: Blank, meta: { title: '登录', layout: 'blank' } },
      { path: '/admin', component: Blank, meta: { title: '仪表盘' } },
      { path: '/admin/articles', component: Blank },
      { path: '/article/:slug', component: Blank },
    ],
  })
}

async function mountLogin(initial = '/login') {
  const router = makeRouter()
  await router.push(initial)
  await router.isReady()
  const wrapper = mount(LoginView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, auth: useAuthStore() }
}

/** 登录表单的字段（label 与 input 用 id 关联，这里直接按 id 取）。 */
function usernameInput(wrapper: VueWrapper) {
  return wrapper.get('input#username')
}

function passwordInput(wrapper: VueWrapper) {
  return wrapper.get('input#password')
}

function submitButton(wrapper: VueWrapper) {
  return wrapper.get('button[type="submit"]')
}

async function fillAndSubmit(wrapper: VueWrapper, username: string, password: string) {
  await usernameInput(wrapper).setValue(username)
  await passwordInput(wrapper).setValue(password)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

/** 表单内的错误提示：没有错误时返回空串，省得每条用例都判存在性。 */
function errorText(wrapper: VueWrapper): string {
  return wrapper.find('form p').exists() ? wrapper.get('form p').text() : ''
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().items.value = []
  // localStorage 在同一个文件里是跨用例共享的，不清掉会让"已登录"串到下一个用例
  tokenStore.clear()
  // 默认按"访客"起步：Cookie 里没有可用的 refresh token，静默续期直接 401。
  // 页面挂载时 LoginView 会先 restore() 一次，不 mock 就会发出真实请求。
  vi.spyOn(authApi, 'refresh').mockRejectedValue(new ApiError('登录已过期', 401, 'unauthorized'))
})

afterEach(() => {
  tokenStore.clear()
})

/* ------------------------------------------------------------ 用例 */

describe('LoginView · 表单校验', () => {
  it('用户名与密码都空时只提示，不发请求', async () => {
    const login = vi.spyOn(authApi, 'login')

    const { wrapper } = await mountLogin()
    await fillAndSubmit(wrapper, '', '')

    // 交给后端只会白跑一趟并拿到 422，前端拦下来更快也更清楚
    expect(login).not.toHaveBeenCalled()
    expect(errorText(wrapper)).toBe('请填写用户名和密码')
  })

  it('只填了用户名同样拦下（半张表单不该发出去）', async () => {
    const login = vi.spyOn(authApi, 'login')

    const { wrapper } = await mountLogin()
    await fillAndSubmit(wrapper, 'admin', '')

    expect(login).not.toHaveBeenCalled()
    expect(errorText(wrapper)).toBe('请填写用户名和密码')
  })

  it('只有空格的用户名不算填写（判空前先 trim）', async () => {
    const login = vi.spyOn(authApi, 'login')

    const { wrapper } = await mountLogin()
    await fillAndSubmit(wrapper, '   ', 'secret123')

    expect(login).not.toHaveBeenCalled()
    expect(errorText(wrapper)).toBe('请填写用户名和密码')
  })

  it('提交时用户名去掉首尾空格，密码原样（密码里的空格是有意义的字符）', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper } = await mountLogin()
    await fillAndSubmit(wrapper, '  admin  ', '  pass word  ')

    expect(authApi.login).toHaveBeenCalledWith({ username: 'admin', password: '  pass word  ' })
  })
})

describe('LoginView · 登录与跳转', () => {
  it('成功后跳到 redirect 指定页，欢迎语用登录后的昵称（不是「访客」）', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin('/login?redirect=/admin/articles')
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(router.currentRoute.value.fullPath).toBe('/admin/articles')
    // 文案取的是 login 完成后的 displayName：写成模板串会在调用前求值，
    // 得到的是"欢迎回来，访客"
    expect(useToast().items.value.at(-1)?.message).toBe('欢迎回来，站长')
  })

  it('没有 redirect 参数时默认进后台首页', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin('/login')
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(router.currentRoute.value.fullPath).toBe('/admin')
  })

  it('登录进行中：按钮禁用并显示「登录中…」（防重复提交）', async () => {
    const pending = deferred<Token>()
    vi.spyOn(authApi, 'login').mockReturnValue(pending.promise)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper } = await mountLogin()
    await usernameInput(wrapper).setValue('admin')
    await passwordInput(wrapper).setValue('secret123')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(submitButton(wrapper).text()).toBe('登录中…')
    expect(submitButton(wrapper).attributes('disabled')).toBeDefined()

    pending.resolve(TOKEN)
    await flushPromises()

    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})

describe('LoginView · redirect 校验（防开放重定向）', () => {
  it('站内路径原样跳过去（带 query 的深层地址也要完整保留）', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin('/login?redirect=/admin/articles?status=draft')
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(router.currentRoute.value.fullPath).toBe('/admin/articles?status=draft')
  })

  it('外部绝对地址被拒绝，回落到 /admin', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin('/login?redirect=https://evil.example/phish')
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    // 登录成功后把用户送去外站，等于给钓鱼站白送一个"刚从博客跳过来"的落点
    expect(router.currentRoute.value.fullPath).toBe('/admin')
  })

  it('javascript: 之类的伪协议同样被拒绝', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin('/login?redirect=javascript:alert(1)')
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(router.currentRoute.value.fullPath).toBe('/admin')
  })

  it('空 redirect 与重复参数都回落到 /admin（不跳空地址）', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const empty = await mountLogin('/login?redirect=')
    await fillAndSubmit(empty.wrapper, 'admin', 'secret123')
    expect(empty.router.currentRoute.value.fullPath).toBe('/admin')

    const array = await mountLogin('/login?redirect=/admin&redirect=/article/x')
    await fillAndSubmit(array.wrapper, 'admin', 'secret123')
    // 重复参数在 vue-router 里是数组，不是字符串 → 只能回落默认值
    expect(array.router.currentRoute.value.fullPath).toBe('/admin')
  })

  it('// 开头的协议相对地址被拒绝（不再"登录成功却停在登录页"）', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin('/login?redirect=//evil.example/phish')
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    // `//evil.example` 是协议相对地址，浏览器解析成 https://evil.example。
    // 以前它通过了 startsWith('/') 检查，于是 history 模式 pushState 到跨源地址
    // 触发 SecurityError：登录真的成功了，用户却停在登录页，控制台一条 rejection。
    expect(router.currentRoute.value.fullPath).toBe('/admin')
  })
})

describe('LoginView · 失败路径', () => {
  it('401 的错误贴在表单里、不弹 toast、不跳转，输入内容保留', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError('用户名或密码错误', 401, 'unauthorized'))

    const { wrapper, router } = await mountLogin()
    await fillAndSubmit(wrapper, 'admin', 'wrong-pass')

    expect(errorText(wrapper)).toBe('用户名或密码错误')
    // 登录失败的错误属于表单：弹 toast 会飘走，而用户的下一个动作是改输入
    expect(useToast().items.value).toHaveLength(0)
    expect(router.currentRoute.value.fullPath).toBe('/login')
    // 别把人辛苦敲的内容清掉
    expect((usernameInput(wrapper).element as HTMLInputElement).value).toBe('admin')
    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('网络异常（status 0）给出可读文案，而不是裸露的异常信息', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(
      new ApiError('网络异常，请检查网络连接', 0, 'network_error'),
    )

    const { wrapper, router } = await mountLogin()
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(errorText(wrapper)).toContain('网络异常')
    expect(router.currentRoute.value.fullPath).toBe('/login')
  })

  it('非 ApiError 的异常也失败得干净：按钮解禁、留在登录页、有提示', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new Error('token 解析失败'))

    const { wrapper, router } = await mountLogin()
    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(errorText(wrapper)).not.toBe('')
    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
    expect(router.currentRoute.value.fullPath).toBe('/login')
  })

  it('失败后重新提交能恢复：第二次成功照样跳转', async () => {
    vi.spyOn(authApi, 'login')
      .mockRejectedValueOnce(new ApiError('用户名或密码错误', 401, 'unauthorized'))
      .mockResolvedValueOnce(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { wrapper, router } = await mountLogin()
    await fillAndSubmit(wrapper, 'admin', 'wrong-pass')
    expect(errorText(wrapper)).toBe('用户名或密码错误')

    await fillAndSubmit(wrapper, 'admin', 'secret123')

    expect(errorText(wrapper)).toBe('')
    expect(router.currentRoute.value.fullPath).toBe('/admin')
  })
})

describe('LoginView · 已登录访客', () => {
  it('本地没有 token 时不请求 /auth/me（未登录访客不该白吃一个 401）', async () => {
    const me = vi.spyOn(authApi, 'me')

    const { router } = await mountLogin()

    expect(me).not.toHaveBeenCalled()
    expect(router.currentRoute.value.fullPath).toBe('/login')
  })

  it('带着有效登录态进登录页会被直接送走，不用再登一次', async () => {
    tokenStore.save(TOKEN)
    vi.spyOn(authApi, 'me').mockResolvedValue(ADMIN)

    const { router } = await mountLogin('/login?redirect=/admin/articles')

    expect(router.currentRoute.value.fullPath).toBe('/admin/articles')
  })

  it('本地 token 已失效（401）时留在登录页，表单可用', async () => {
    tokenStore.save(TOKEN)
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('登录状态已失效', 401, 'unauthorized'))

    const { wrapper, router } = await mountLogin()

    expect(router.currentRoute.value.fullPath).toBe('/login')
    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})

describe('LoginView · head 与出口', () => {
  it('浏览器标题为「登录」，并给出返回前台的出口', async () => {
    const { wrapper } = await mountLogin()

    expect(document.title).toBe('登录 · 个人博客')
    expect(wrapper.get('a[href="/"]').text()).toContain('返回前台')
  })
})
