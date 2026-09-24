/**
 * DashboardView 测试（后台仪表盘）。
 *
 * 这一页有两个彼此独立的数据源：站点统计（走 site store 的 `loadStats`）与
 * 近 30 天趋势（本页自己的 `useAsyncData` + statsApi.dailyViews）。两者失败的
 * 影响面完全不同，所以守的契约分成三组：
 *
 * 1. **加载态 ≠ 零值**：首次加载给骨架卡，统计失败给错误条 + 重试入口，
 *    绝不能停在骨架屏上，也绝不能把「不知道」画成 0（那是两回事）；
 * 2. **KPI 口径**：已发布带「共 N 篇」、总阅读量按 k/w 缩写、最近发布时间为空
 *    显示「—」；待审评论 > 0 才标黄并给「去审核」入口，为 0 时退回「共 N 条」；
 * 3. **趋势失败不阻塞其余数字**：趋势请求挂了只影响趋势卡片，KPI 照常渲染；
 *    边界上要分清「没有数据」（空态文案）与「有日期但全是 0」（照常画图，合计 0）。
 *
 * 说明：不 mock 业务模块（site store 也是真的），只替换网络出口。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, siteApi, statsApi } from '@/api'
import { useSiteStore } from '@/stores/site'
import type { DailyViewStats, SiteStats } from '@/types'

import DashboardView from './DashboardView.vue'

/* ------------------------------------------------------------ 测试数据 */

const STATS: SiteStats = {
  article_total: 15,
  published_total: 12,
  draft_total: 3,
  category_total: 4,
  tag_total: 9,
  comment_total: 30,
  pending_comment_total: 5,
  total_views: 12345,
  latest_published_at: '2026-01-01T08:00:00Z',
}

function makeDay(date: string, views: number, uniqueVisitors = Math.round(views / 2)): DailyViewStats {
  return { date, views, unique_visitors: uniqueVisitors }
}

const TREND: DailyViewStats[] = [makeDay('2026-01-01', 800), makeDay('2026-01-02', 700)]

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
let pinia: Pinia

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin', component: Blank },
      { path: '/admin/articles', component: Blank },
      { path: '/admin/comments', component: Blank },
      { path: '/admin/articles/new', component: Blank },
      { path: '/admin/taxonomy', component: Blank },
      { path: '/admin/media', component: Blank },
    ],
  })
}

async function mountDashboard() {
  const router = makeRouter()
  await router.push('/admin')
  await router.isReady()
  const wrapper = mount(DashboardView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, site: useSiteStore() }
}

/**
 * 按标签定位 KPI 卡片：卡片结构是「小字标签 + 大数字（+ 可选补充行）」，
 * 直接断言整页文本会因为数字太短而误命中（例如 4 出现在文章数里）。
 */
function statCard(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = wrapper
    .findAll('.card')
    .find((item) => item.find('p').exists() && item.findAll('p')[0]?.text().trim() === label)
  if (!found) throw new Error(`找不到标签为「${label}」的统计卡片`)
  return found
}

/** 统计卡片里的大数字（标签是第一个 p，数字是第二个）。 */
function statValue(wrapper: VueWrapper, label: string): string {
  return statCard(wrapper, label).findAll('p')[1]?.text().trim() ?? ''
}

function statValueClasses(wrapper: VueWrapper, label: string): string[] {
  return statCard(wrapper, label).findAll('p')[1]?.classes() ?? []
}

function trendCard(wrapper: VueWrapper): DOMWrapper<Element> {
  const found = wrapper
    .findAll('.card')
    .find((item) => item.find('h2').exists() && item.find('h2').text().includes('访问趋势'))
  if (!found) throw new Error('找不到「访问趋势」卡片')
  return found
}

function trendChart(wrapper: VueWrapper): DOMWrapper<Element> {
  return wrapper.find('svg[aria-label="访问趋势图"]')
}

function button(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

function quickLink(wrapper: VueWrapper, label: string): DOMWrapper<Element> | undefined {
  return wrapper.findAll('a').find((item) => item.text().trim() === label)
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(siteApi, 'stats').mockResolvedValue({ ...STATS })
  vi.spyOn(statsApi, 'dailyViews').mockResolvedValue(TREND)
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* ------------------------------------------------------------ 用例 */

describe('DashboardView · 加载与 KPI', () => {
  it('挂载时同时拉站点统计与近 30 天趋势（两次请求，不是一次拿全）', async () => {
    const stats = vi.spyOn(siteApi, 'stats').mockResolvedValue({ ...STATS })
    const daily = vi.spyOn(statsApi, 'dailyViews').mockResolvedValue(TREND)

    await mountDashboard()

    expect(stats).toHaveBeenCalledTimes(1)
    expect(daily).toHaveBeenCalledWith(30)
  })

  it('首屏加载中显示 4 张骨架卡，而不是先报 0', async () => {
    const pending = deferred<SiteStats>()
    vi.spyOn(siteApi, 'stats').mockReturnValue(pending.promise)

    const { wrapper } = await mountDashboard()

    // 4 张卡片 × 2 条骨架
    expect(wrapper.findAll('.skeleton')).toHaveLength(8)
    expect(wrapper.text()).not.toContain('已发布')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)

    pending.resolve({ ...STATS })
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.text()).toContain('已发布')
  })

  it('统计就绪后渲染 4 张 KPI 卡：已发布带总数、总阅读量按 w 缩写、底部三个计数', async () => {
    const { wrapper } = await mountDashboard()

    expect(statValue(wrapper, '已发布')).toBe('12')
    expect(statCard(wrapper, '已发布').text()).toContain('共 15 篇')
    expect(statValue(wrapper, '草稿')).toBe('3')
    // 12345 → 1.2w：长数字不能直接铺在卡片里
    expect(statValue(wrapper, '总阅读量')).toBe('1.2w')
    expect(statValue(wrapper, '待审评论')).toBe('5')

    expect(statValue(wrapper, '分类')).toBe('4')
    expect(statValue(wrapper, '标签')).toBe('9')
    expect(statValue(wrapper, '评论总数')).toBe('30')
  })

  it('「草稿」卡带「去处理 →」入口，直达文章列表', async () => {
    const { wrapper } = await mountDashboard()

    expect(statCard(wrapper, '草稿').get('a').attributes('href')).toBe('/admin/articles')
  })

  it('最近发布时间为空时显示「—」（不能是空白，也不能是 Invalid Date）', async () => {
    vi.spyOn(siteApi, 'stats').mockResolvedValue({ ...STATS, latest_published_at: null })

    const { wrapper } = await mountDashboard()

    expect(statCard(wrapper, '总阅读量').text()).toContain('最近发布 —')
    expect(statCard(wrapper, '总阅读量').text()).not.toContain('Invalid')
  })

  it('有最近发布时间时展示格式化后的时间（不是原始 ISO 串）', async () => {
    const { wrapper } = await mountDashboard()

    const text = statCard(wrapper, '总阅读量').text()
    expect(text).toContain('2026')
    expect(text).not.toContain('2026-01-01T08:00:00Z')
  })

  it('待审评论 > 0：数字标黄，并给出「去审核」入口与快捷操作（带上条数）', async () => {
    const { wrapper } = await mountDashboard()

    expect(statValueClasses(wrapper, '待审评论')).toContain('text-amber-600')
    expect(statCard(wrapper, '待审评论').get('a').attributes('href')).toBe(
      '/admin/comments?approved=false',
    )
    expect(quickLink(wrapper, '审核评论（5）')?.attributes('href')).toBe(
      '/admin/comments?approved=false',
    )
  })

  it('待审评论为 0：不显示「去审核」，改显示「共 N 条」且数字不标黄', async () => {
    vi.spyOn(siteApi, 'stats').mockResolvedValue({ ...STATS, pending_comment_total: 0 })

    const { wrapper } = await mountDashboard()

    // 没有待处理的东西就不该给一个会把用户带到空队列的入口
    expect(statCard(wrapper, '待审评论').find('a').exists()).toBe(false)
    expect(statCard(wrapper, '待审评论').text()).toContain('共 30 条')
    expect(statValueClasses(wrapper, '待审评论')).toContain('text-ink')
    expect(quickLink(wrapper, '审核评论（0）')).toBeUndefined()
  })

  it('快捷操作区固定给「写文章 / 管理分类标签 / 媒体库」三个入口', async () => {
    const { wrapper } = await mountDashboard()

    expect(quickLink(wrapper, '写新文章')?.attributes('href')).toBe('/admin/articles/new')
    expect(quickLink(wrapper, '管理分类标签')?.attributes('href')).toBe('/admin/taxonomy')
    expect(quickLink(wrapper, '媒体库')?.attributes('href')).toBe('/admin/media')
  })
})

describe('DashboardView · 统计失败', () => {
  it('失败时给错误条与重试入口，而不是永远停在骨架屏上，也不把数字伪造成 0', async () => {
    vi.spyOn(siteApi, 'stats').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountDashboard()

    expect(wrapper.get('[role="alert"]').text()).toContain('服务器开小差了，请稍后重试')
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    // 「不知道」不能画成 0：KPI 卡片整块不渲染
    expect(wrapper.text()).not.toContain('已发布')
    expect(wrapper.text()).not.toContain('总阅读量')
  })

  it('非 ApiError 的异常归一成兜底文案，不会渲染成一条空白错误条', async () => {
    vi.spyOn(siteApi, 'stats').mockRejectedValue(new TypeError('boom'))

    const { wrapper } = await mountDashboard()

    expect(wrapper.get('[role="alert"]').text()).toContain('统计数据加载失败')
  })

  it('点「重试」能恢复：错误条消失、KPI 重新渲染（不是只画了个按钮）', async () => {
    const stats = vi
      .spyOn(siteApi, 'stats')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce({ ...STATS })

    const { wrapper } = await mountDashboard()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)

    await button(wrapper, '重试').trigger('click')
    await flushPromises()

    expect(stats).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(statValue(wrapper, '已发布')).toBe('12')
  })

  it('重试仍然失败时错误条留着（可以继续点，不会变成空白页）', async () => {
    vi.spyOn(siteApi, 'stats').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountDashboard()
    await button(wrapper, '重试').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('服务器开小差了，请稍后重试')
    expect(button(wrapper, '重试').attributes('disabled')).toBeUndefined()
  })
})

describe('DashboardView · 访问趋势', () => {
  it('趋势按天画点，合计取各天 views 之和并按缩写展示', async () => {
    const daily = vi.spyOn(statsApi, 'dailyViews').mockResolvedValue(TREND)

    const { wrapper } = await mountDashboard()

    expect(daily).toHaveBeenCalledWith(30)
    // 800 + 700 = 1500 → 1.5k
    expect(trendCard(wrapper).text()).toContain('合计 1.5k 次阅读')
    expect(trendCard(wrapper).text()).toContain('虚线为去重访客')
    expect(trendChart(wrapper).exists()).toBe(true)
    // 每个数据点两个圆：可见点 + 放大的透明命中区（含原生 title 提示）
    expect(trendChart(wrapper).findAll('circle')).toHaveLength(TREND.length * 2)
    expect(trendChart(wrapper).findAll('title').map((item) => item.text())).toEqual([
      '2026-01-01 · 阅读 800 · 访客 400',
      '2026-01-02 · 阅读 700 · 访客 350',
    ])
  })

  it('边界：趋势完全没有数据（空数组）→ 空态文案 + 合计 0，而不是画一张空图', async () => {
    vi.spyOn(statsApi, 'dailyViews').mockResolvedValue([])

    const { wrapper } = await mountDashboard()

    expect(trendCard(wrapper).text()).toContain('还没有访问数据，发布文章后这里会出现趋势曲线。')
    expect(trendCard(wrapper).text()).toContain('合计 0 次阅读')
    expect(trendChart(wrapper).exists()).toBe(false)
    // KPI 不受影响
    expect(statValue(wrapper, '已发布')).toBe('12')
  })

  it('边界：有日期但访问量全是 0 → 照常画图，合计 0（「没数据」与「数据是 0」不是一回事）', async () => {
    vi.spyOn(statsApi, 'dailyViews').mockResolvedValue([
      makeDay('2026-01-01', 0, 0),
      makeDay('2026-01-02', 0, 0),
      makeDay('2026-01-03', 0, 0),
    ])

    const { wrapper } = await mountDashboard()

    expect(trendCard(wrapper).text()).not.toContain('还没有访问数据')
    expect(trendChart(wrapper).exists()).toBe(true)
    expect(trendCard(wrapper).text()).toContain('合计 0 次阅读')
    expect(trendChart(wrapper).findAll('circle')).toHaveLength(6)
  })

  it('趋势加载失败：错误就地显示在趋势卡片里，KPI 其余数字照常显示', async () => {
    vi.spyOn(statsApi, 'dailyViews').mockRejectedValue(
      new ApiError('趋势数据加载失败', 500, 'internal_error'),
    )

    const { wrapper } = await mountDashboard()

    expect(trendCard(wrapper).text()).toContain('趋势数据加载失败')
    expect(trendChart(wrapper).exists()).toBe(false)
    // 这是这一页最重要的降级契约：趋势挂了不该把整页 KPI 一起吞掉
    expect(statValue(wrapper, '已发布')).toBe('12')
    expect(statValue(wrapper, '总阅读量')).toBe('1.2w')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('趋势还在路上时只显示趋势骨架，KPI 已经可以先看', async () => {
    const pending = deferred<DailyViewStats[]>()
    vi.spyOn(statsApi, 'dailyViews').mockReturnValue(pending.promise)

    const { wrapper } = await mountDashboard()

    expect(trendCard(wrapper).findAll('.skeleton')).toHaveLength(1)
    expect(statValue(wrapper, '已发布')).toBe('12')

    pending.resolve(TREND)
    await flushPromises()
    expect(trendCard(wrapper).findAll('.skeleton')).toHaveLength(0)
    expect(trendChart(wrapper).exists()).toBe(true)
  })
})
