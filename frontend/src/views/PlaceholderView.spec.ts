/**
 * PlaceholderView 测试。
 *
 * 这一页是"导航里先留好入口"占位用的：友链、留言板共用同一个组件，文案由路由表
 * 通过 props 传进来（`meta` 只管标题，`props` 管文案）。守的契约：
 *
 * 1. **props 覆盖默认文案**：传了就用传进来的，路由表里加一条新占位页不需要动组件；
 * 2. **默认值可用**：漏传 / 只传一个都不会渲染出空白或 undefined；
 * 3. 给出「返回首页」的出口（占位页也不能是死路）；
 * 4. 文案走插值渲染：路由 props 将来若掺进站点数据，尖括号也不该变成真节点。
 *
 * 说明：本组件不碰接口，所以没有网络出口要替换。
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import PlaceholderView from './PlaceholderView.vue'

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/links', component: Blank, meta: { title: '友情链接' } },
    ],
  })
}

async function mountPlaceholder(props: Record<string, string> = {}) {
  const router = makeRouter()
  await router.push('/links')
  await router.isReady()
  const wrapper = mount(PlaceholderView, { props, global: { plugins: [router] } })
  return { wrapper, router }
}

beforeEach(() => {
  document.title = ''
})

/* ------------------------------------------------------------ 用例 */

describe('PlaceholderView', () => {
  it('路由表传来的 heading / hint 被原样渲染', async () => {
    const { wrapper } = await mountPlaceholder({
      heading: '友情链接',
      hint: '这里会放一些我经常逛的站点。',
    })

    expect(wrapper.get('h1').text()).toBe('友情链接')
    expect(wrapper.text()).toContain('这里会放一些我经常逛的站点。')
  })

  it('一个 props 都不传时用默认文案（不会渲染成空白页）', async () => {
    const { wrapper } = await mountPlaceholder()

    expect(wrapper.get('h1').text()).toBe('敬请期待')
    expect(wrapper.text()).toContain('这个页面还在建设中。')
  })

  it('只传一个 props 时另一个仍走默认值', async () => {
    const { wrapper } = await mountPlaceholder({ heading: '留言板' })

    expect(wrapper.get('h1').text()).toBe('留言板')
    expect(wrapper.text()).toContain('这个页面还在建设中。')
  })

  it('给出「返回首页」的出口（占位页不是死路）', async () => {
    const { wrapper } = await mountPlaceholder({ heading: '留言板' })

    expect(wrapper.get('a[href="/"]').text()).toBe('返回首页')
  })

  it('文案走插值渲染：传入的尖括号不会变成真节点', async () => {
    const { wrapper } = await mountPlaceholder({ heading: '<b>加粗</b>', hint: '<img src=x>' })

    expect(wrapper.find('b').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('<b>加粗</b>')
  })
})
