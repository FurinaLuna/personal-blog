/**
 * NotFoundView 测试。
 *
 * 404 页是被三个地方复用的（前台兜底路由、后台 `:pathMatch(.*)*`、以及将来可能的
 * 其他兜底），它唯一的职责是**别让用户停在死路上**。守的契约：
 *
 * 1. 明确写出 404，并把"可能是什么原因"说清楚（链接失效 / 文章改名）；
 * 2. **两个出口都要在**：回首页（不知道去哪时最稳）与看归档（想找回某篇旧文时）；
 * 3. 浏览器标题为「页面不存在」——多开几个标签页时不该显示成站名或上一个页面的标题。
 *
 * 说明：本组件不碰接口，所以没有网络出口要替换。
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import NotFoundView from './NotFoundView.vue'

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/archive', component: Blank, meta: { title: '归档' } },
      // 兜底路由：不挂它的话 push 到不存在的路径会打印 vue-router 警告
      { path: '/:pathMatch(.*)*', component: Blank },
    ],
  })
}

async function mountNotFound() {
  const router = makeRouter()
  await router.push('/no-such-page')
  await router.isReady()
  const wrapper = mount(NotFoundView, { global: { plugins: [router] } })
  return { wrapper, router }
}

beforeEach(() => {
  document.title = ''
})

/* ------------------------------------------------------------ 用例 */

describe('NotFoundView', () => {
  it('明确显示 404 与原因，而不是一句干巴巴的"出错了"', async () => {
    const { wrapper } = await mountNotFound()

    expect(wrapper.text()).toContain('404')
    expect(wrapper.get('h1').text()).toBe('这个页面不存在')
    expect(wrapper.text()).toContain('链接可能已经失效')
  })

  it('同时给出「返回首页」与「查看归档」两个出口', async () => {
    const { wrapper } = await mountNotFound()

    // 想找回某篇旧文的人需要归档，只是迷路的人需要首页：两个都留着
    expect(wrapper.get('a[href="/"]').text()).toBe('返回首页')
    expect(wrapper.get('a[href="/archive"]').text()).toBe('查看归档')
  })

  it('浏览器标题为「页面不存在」（不是沿用上一个页面的标题）', async () => {
    await mountNotFound()

    expect(document.title).toBe('页面不存在 · 个人博客')
  })
})
