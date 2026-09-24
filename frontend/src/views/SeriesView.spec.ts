/**
 * SeriesView 测试。
 *
 * 系列聚合页与分类页长得像，但契约不同：系列是**有顺序的连载**，所以守的是
 *
 * 1. **请求形态**：必须带 `with_counts`（要显示「N 篇」）；
 * 2. **失败与空态分得开**：接口挂了要给后端文案，不能报成「还没有系列」；
 *    加载中也不许先闪一下空态；
 * 3. **顺序不乱**：列表按接口返回原样渲染（后端已按更新时间排好，前端重排只会打架）；
 * 4. **出口是系列自己的路径** `/series/{slug}`（不是首页 query，与分类页相反）；
 * 5. 没有描述时不渲染那一段；描述是纯文本，不出节点。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, seriesApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import type { Series } from '@/types'

import SeriesView from './SeriesView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeSeries(overrides: Partial<Series> = {}): Series {
  return {
    id: 1,
    name: 'Vue 源码',
    slug: 'vue-internals',
    description: null,
    article_count: 5,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const SERIES: Series[] = [
  makeSeries({ id: 1, name: 'Vue 源码', slug: 'vue-internals', description: '逐行读响应式', article_count: 5 }),
  makeSeries({ id: 2, name: '空系列', slug: 'empty-series', description: null, article_count: 0 }),
]

/** 手动控制 resolve 时机的 promise：用来观察「请求还没回来」这段中间态。 */
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

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/series', component: Blank, meta: { title: '系列' } },
      { path: '/series/:slug', component: Blank },
    ],
  })
}

async function mountSeries() {
  const router = makeRouter()
  await router.push('/series')
  await router.isReady()
  const wrapper = mount(SeriesView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

/** 系列条目按名称取，避免断言落到隔壁那一项。 */
function item(wrapper: VueWrapper, name: string) {
  const found = wrapper.findAll('li a').find((link) => link.get('h2').text() === name)
  if (!found) throw new Error(`找不到名称为「${name}」的系列条目`)
  return found
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)
})

/* ------------------------------------------------------------ 用例 */

describe('SeriesView · 加载 / 失败 / 空态', () => {
  it('挂载时带 with_counts 拉系列（否则「N 篇」全是 0）', async () => {
    const list = vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    await mountSeries()

    expect(list).toHaveBeenCalledWith(true)
  })

  it('加载中显示骨架屏，不显示「还没有系列」', async () => {
    const pending = deferred<Series[]>()
    vi.spyOn(seriesApi, 'list').mockReturnValue(pending.promise)

    const { wrapper } = await mountSeries()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    expect(wrapper.text()).not.toContain('还没有系列')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)

    pending.resolve(SERIES)
    await flushPromises()

    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.text()).toContain('Vue 源码')
  })

  it('加载失败显示后端文案，且不会错报成「还没有系列」', async () => {
    vi.spyOn(seriesApi, 'list').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountSeries()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('还没有系列')
  })

  it('网络异常（status 0）也有可读文案，而不是裸露的异常信息', async () => {
    vi.spyOn(seriesApi, 'list').mockRejectedValue(
      new ApiError('网络异常，请检查网络连接', 0, 'network_error'),
    )

    const { wrapper } = await mountSeries()

    expect(wrapper.text()).toContain('网络异常')
  })

  it('一个系列都没有时给空态与说明', async () => {
    vi.spyOn(seriesApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountSeries()
    const empty = wrapper.getComponent(EmptyState)

    expect(empty.props('title')).toBe('还没有系列')
    expect(empty.props('description')).toContain('阅读顺序')
  })
})

describe('SeriesView · 条目渲染与出口', () => {
  it('渲染名称与篇数，链接指向系列自己的详情页', async () => {
    const { wrapper } = await mountSeries()

    expect(wrapper.findAll('li a')).toHaveLength(2)
    expect(item(wrapper, 'Vue 源码').text()).toContain('5 篇')
    expect(item(wrapper, 'Vue 源码').attributes('href')).toBe('/series/vue-internals')
    expect(item(wrapper, '空系列').attributes('href')).toBe('/series/empty-series')
  })

  it('有描述才渲染描述段；没有描述时不留空白也不出现 undefined', async () => {
    const { wrapper } = await mountSeries()

    expect(item(wrapper, 'Vue 源码').text()).toContain('逐行读响应式')
    // 空描述不占位（否则卡片里会莫名多出一段行距）
    expect(item(wrapper, '空系列').find('p').exists()).toBe(false)
    expect(item(wrapper, '空系列').text()).not.toContain('undefined')
  })

  it('article_count 为 0 的系列照常显示（与标签页不同：系列是站长建的结构）', async () => {
    const { wrapper } = await mountSeries()

    expect(item(wrapper, '空系列').text()).toContain('0 篇')
  })

  it('描述走插值渲染：站点数据里的尖括号不会变成真节点', async () => {
    vi.spyOn(seriesApi, 'list').mockResolvedValue([
      makeSeries({ description: '<img src=x onerror=alert(1)> 说明' }),
    ])

    const { wrapper } = await mountSeries()

    expect(item(wrapper, 'Vue 源码').find('img').exists()).toBe(false)
    expect(item(wrapper, 'Vue 源码').text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('顺序按接口返回原样（不重排：后端已经排好了）', async () => {
    vi.spyOn(seriesApi, 'list').mockResolvedValue([
      makeSeries({ id: 9, name: '乙系列' }),
      makeSeries({ id: 8, name: '甲系列' }),
    ])

    const { wrapper } = await mountSeries()

    expect(wrapper.findAll('li h2').map((node) => node.text())).toEqual(['乙系列', '甲系列'])
  })
})

describe('SeriesView · head', () => {
  it('浏览器标题为「系列」', async () => {
    await mountSeries()
    expect(document.title).toBe('系列 · 个人博客')
  })
})
