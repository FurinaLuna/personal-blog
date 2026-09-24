/**
 * UnsubscribeView 测试。
 *
 * 这一页的入口只有一个：邮件里的链接（`/unsubscribe#token=xxx`），收件人多数不是
 * 本站用户。它的契约比看起来多：
 *
 * 1. **打开即提交**：页面上没有第二个按钮，用户点进来就该已经退订完成；
 *    且只提交一次（真实接口是幂等的，但重复打请求没有意义）；
 * 2. **token 的取法**：fragment 优先（当前格式，不会被服务器日志记下）、
 *    query 兜底（改成 fragment 之前发出的邮件有 10 年有效期）；
 *    解码失败 / 缺 token 一律按「链接不完整」处理，**不发请求**；
 * 3. **三态不许串**：处理中不能显示「已退订」，失败不能显示「已退订」，
 *    成功也不能还挂着错误文案 —— 用户凭这一句话决定要不要再点一次邮件里的链接；
 * 4. **失败有兜底文案**：token 过期 / 伪造 / 断网，都要给一句人能看懂的话。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口
 * （spy `@/api` 上的方法）。token 解析本身的用例在 utils/unsubscribe.spec.ts，
 * 这里只关心「取到的 token 有没有正确交给接口、页面停在哪个状态」。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, notificationApi } from '@/api'
import type { Message } from '@/types'

import UnsubscribeView from './UnsubscribeView.vue'

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/unsubscribe', component: Blank, meta: { title: '退订通知' } },
    ],
  })
}

async function mountUnsubscribe(target = '/unsubscribe') {
  const router = makeRouter()
  await router.push(target)
  await router.isReady()
  const wrapper = mount(UnsubscribeView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
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

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(notificationApi, 'unsubscribe').mockResolvedValue({ detail: '已退订' })
})

/* ------------------------------------------------------------ 用例 */

describe('UnsubscribeView · token 的取法', () => {
  it('fragment 里的 token 被原样交给接口（不在前端解析）', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=signed.jwt.value')

    expect(unsubscribe).toHaveBeenCalledWith('signed.jwt.value')
    expect(wrapper.text()).toContain('已退订')
  })

  it('兼容 query 里的 token（改成 fragment 之前发出的邮件仍能退订）', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    await mountUnsubscribe('/unsubscribe?token=legacy-token')

    expect(unsubscribe).toHaveBeenCalledWith('legacy-token')
  })

  it('两个来源同时存在时以 fragment 为准（它是当前格式，且不进服务端日志）', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    await mountUnsubscribe('/unsubscribe?token=old#token=new')

    expect(unsubscribe).toHaveBeenCalledWith('new')
  })

  it('百分号转义被解码后再交给接口', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    await mountUnsubscribe('/unsubscribe#token=a%2Bb%3Dc')

    expect(unsubscribe).toHaveBeenCalledWith('a+b=c')
  })

  it('没有 token：给「链接不完整」的提示，一个请求都不发', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    const { wrapper } = await mountUnsubscribe()

    expect(unsubscribe).not.toHaveBeenCalled()
    expect(wrapper.get('h1').text()).toBe('退订链接不完整')
    // 邮件被截断时用户需要知道"不是退订失败，是链接不全"，并有个出口离开
    expect(wrapper.text()).toContain('回到邮件里点击完整的「退订」链接')
    expect(wrapper.find('a[href="/"]').exists()).toBe(true)
  })

  it('token 是坏的百分号转义（手改坏的链接）：当作没有 token，不发请求', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=%E4%B8')

    expect(unsubscribe).not.toHaveBeenCalled()
    expect(wrapper.get('h1').text()).toBe('退订链接不完整')
  })

  it('token 为空串（`#token=`）同样按链接不完整处理', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=')

    expect(unsubscribe).not.toHaveBeenCalled()
    expect(wrapper.get('h1').text()).toBe('退订链接不完整')
  })

  it('打开即提交且只提交一次（页面上没有第二个按钮要点）', async () => {
    const unsubscribe = vi.spyOn(notificationApi, 'unsubscribe')

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(unsubscribe).toHaveBeenCalledTimes(1)
    // 唯一的交互是"返回首页"，不该再有一个"确认退订"的按钮
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})

describe('UnsubscribeView · 三态互斥', () => {
  it('处理中显示「正在处理…」，不提前报成功或失败', async () => {
    const pending = deferred<Message>()
    vi.spyOn(notificationApi, 'unsubscribe').mockReturnValue(pending.promise)

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(wrapper.get('h1').text()).toBe('正在处理…')
    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    // 请求还没回来就说"已退订"，而它其实可能失败 —— 用户不会再点第二次
    expect(wrapper.text()).not.toContain('已退订')
    expect(wrapper.text()).not.toContain('退订没有成功')

    pending.resolve({ detail: '已退订' })
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('已退订')
  })

  it('成功：只给成功态与回首页的出口', async () => {
    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(wrapper.get('h1').text()).toBe('已退订')
    expect(wrapper.text()).toContain('之后有新回复不会再给你发邮件了')
    expect(wrapper.get('a[href="/"]').classes()).toContain('btn--primary')
    expect(wrapper.text()).not.toContain('退订没有成功')
  })

  it('失败（token 过期 / 伪造）：显示后端文案，绝不显示「已退订」', async () => {
    vi.spyOn(notificationApi, 'unsubscribe').mockRejectedValue(
      new ApiError('退订链接已失效', 400, 'bad_request'),
    )

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(wrapper.get('h1').text()).toBe('退订没有成功')
    expect(wrapper.text()).toContain('退订链接已失效')
    expect(wrapper.text()).not.toContain('已退订')
    expect(wrapper.find('a[href="/"]').exists()).toBe(true)
  })

  it('失败但没有可读消息时用兜底文案，而不是一片空白', async () => {
    vi.spyOn(notificationApi, 'unsubscribe').mockRejectedValue(new ApiError('', 0, 'network_error'))

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(wrapper.get('h1').text()).toBe('退订没有成功')
    expect(wrapper.text()).toContain('链接可能已失效，请回到邮件里重新点击')
  })

  it('非 ApiError 的异常也停在失败态（不会永远卡在「正在处理…」）', async () => {
    vi.spyOn(notificationApi, 'unsubscribe').mockRejectedValue(new TypeError('boom'))

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(wrapper.get('h1').text()).toBe('退订没有成功')
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('失败态不给「重试」按钮（退订是幂等的，重试只会重复打同一个坏 token）', async () => {
    vi.spyOn(notificationApi, 'unsubscribe').mockRejectedValue(
      new ApiError('退订链接已失效', 400, 'bad_request'),
    )

    const { wrapper } = await mountUnsubscribe('/unsubscribe#token=abc')

    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})

describe('UnsubscribeView · head', () => {
  it('浏览器标题为「退订通知」（从邮件点进来的人一眼知道这是什么页）', async () => {
    await mountUnsubscribe('/unsubscribe#token=abc')

    expect(document.title).toBe('退订通知 · 个人博客')
  })
})
