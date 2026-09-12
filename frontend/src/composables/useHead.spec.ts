/**
 * useHead 测试。
 *
 * 这条链路有一个历史 bug：初版在卸载时把标题重置为站点名，
 * 把路由 afterEach 刚写好的新页面标题冲掉（旧组件卸载晚于新路由钩子）。
 * 所以要专门锁住「以路由标题为底、业务标题为盖」这条契约。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { useHead } from '@/composables/useHead'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' }, meta: { title: '首页' } },
      { path: '/about', name: 'about', component: { template: '<div />' }, meta: { title: '关于' } },
      {
        path: '/article/:slug',
        name: 'article',
        component: { template: '<div />' },
        meta: { title: '文章' },
      },
    ],
  })
}

/** 造一个使用 useHead 的宿主组件，方便测挂载/卸载两侧的行为。 */
function makeHost(meta: () => { title?: string; description?: string; image?: string }) {
  return defineComponent({
    setup() {
      useHead(meta)
      return () => h('div', 'host')
    },
  })
}

function metaContent(selector: string): string | null {
  return document.head.querySelector<HTMLMetaElement>(selector)?.content ?? null
}

beforeEach(() => {
  // 每个用例前清掉上一轮留下的标签，避免相互污染
  for (const el of document.head.querySelectorAll('meta[property^="og:"], meta[name="twitter:card"]')) {
    el.remove()
  }
})

afterEach(() => {
  document.title = ''
})

describe('useHead 标题', () => {
  it('没有业务标题时回退到路由 meta.title', async () => {
    const router = makeRouter()
    await router.push('/about')
    await router.isReady()

    mount(makeHost(() => ({})), { global: { plugins: [router] } })
    expect(document.title).toBe('关于 · 个人博客')
  })

  it('业务标题覆盖路由标题（详情页用文章名）', async () => {
    const router = makeRouter()
    await router.push('/article/hello')
    await router.isReady()

    mount(makeHost(() => ({ title: '为什么重写数据层' })), { global: { plugins: [router] } })
    expect(document.title).toBe('为什么重写数据层 · 个人博客')
  })

  it('业务标题变化时同步更新（响应式）', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    // 生产中传的是 computed，这里是等价的响应式源
    const current = ref<{ title?: string }>({ title: '第一版' })
    mount(makeHost(() => current.value), { global: { plugins: [router] } })
    expect(document.title).toBe('第一版 · 个人博客')

    current.value = { title: '第二版' }
    await nextTick()
    expect(document.title).toBe('第二版 · 个人博客')
  })
})

describe('useHead meta 标签', () => {
  it('写入 og:title / og:description / twitter:card', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    mount(makeHost(() => ({ title: '标题', description: '摘要文字' })), {
      global: { plugins: [router] },
    })

    expect(metaContent('meta[property="og:title"]')).toBe('标题 · 个人博客')
    expect(metaContent('meta[property="og:description"]')).toBe('摘要文字')
    expect(metaContent('meta[name="twitter:card"]')).toBe('summary')
  })

  it('没有封面时不残留上一篇的 og:image', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    const current = ref<{ title?: string; image?: string }>({
      title: 'A',
      image: 'https://x/cover.png',
    })
    mount(makeHost(() => current.value), { global: { plugins: [router] } })
    expect(metaContent('meta[property="og:image"]')).toBe('https://x/cover.png')

    current.value = { title: 'B' }
    await nextTick()
    expect(document.head.querySelector('meta[property="og:image"]')).toBeNull()
  })
})
