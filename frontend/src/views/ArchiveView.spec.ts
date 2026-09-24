/**
 * ArchiveView 测试。
 *
 * 归档页是「按时间找人」的兜底入口（详情页链接失效、搜索结果没有时，用户会来
 * 这里翻），它的核心契约是**分组与折叠**：
 *
 * 1. **默认展开规则**：当年展开、往年收起 —— 全部展开会让往年的几百条把首屏
 *    撑爆；全部收起则等于让用户自己猜哪个月有东西；
 * 2. **手动状态优先且持久**：点过某个月之后就以手动状态为准，不会因为重新渲染
 *    被默认规则盖回去；
 * 3. **失败与空态分得开**：接口挂了要给后端文案，不能报成「还没有可归档的文章」；
 *    加载中也不能先闪一下空态（`ready` 未置位时不许断言没有数据）；
 * 4. **计数自洽**：头部的「共 N 篇」是各分组的 count 之和，且与「分布在 M 个月份」
 *    用的是同一份数据。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, articleApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import type { ArchiveGroup } from '@/types'

import ArchiveView from './ArchiveView.vue'

/** 当年 / 往年都按运行时动态取，免得用例到了明年就因为写死的年份失效。 */
const CURRENT_YEAR = String(new Date().getFullYear())
const LAST_YEAR = String(new Date().getFullYear() - 1)

/* ------------------------------------------------------------ 测试数据 */

/**
 * 时间刻意写成**不带时区的本地时间**：带 `Z` 的话，西半球的时区会把
 * 03-05 显示成 03-04，用例就变成了「跑在哪个时区」的抽奖。
 */
function makeGroup(
  yearMonth: string,
  items: { id: number; title: string; slug: string; published_at?: string }[],
  count = items.length,
): ArchiveGroup {
  return {
    year_month: yearMonth,
    count,
    items: items.map((item) => ({
      published_at: `${yearMonth}-05T12:00:00`,
      ...item,
    })),
  }
}

const GROUPS: ArchiveGroup[] = [
  makeGroup(`${CURRENT_YEAR}-03`, [
    { id: 1, title: '今年的三月一', slug: 'this-mar-1' },
    { id: 2, title: '今年的三月二', slug: 'this-mar-2' },
  ]),
  makeGroup(`${CURRENT_YEAR}-01`, [{ id: 3, title: '今年的一月', slug: 'this-jan' }]),
  makeGroup(`${LAST_YEAR}-12`, [{ id: 4, title: '去年的十二月', slug: 'last-dec' }]),
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
      { path: '/archive', component: Blank, meta: { title: '归档' } },
      { path: '/article/:slug', component: Blank },
    ],
  })
}

async function mountArchive() {
  const router = makeRouter()
  await router.push('/archive')
  await router.isReady()
  const wrapper = mount(ArchiveView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

/** 月份分组：按标题取，避免断言落到隔壁那个月。 */
function group(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = wrapper.findAll('section').find((item) => item.get('h2').text().includes(label))
  if (!found) throw new Error(`找不到标题含「${label}」的分组`)
  return found
}

/**
 * 分组头（可点击的折叠按钮），标题、篇数与箭头都在里面。
 *
 * 返回类型跟着 `get()` 走：VTU 的 `get` 去掉 `exists`（取不到就抛，
 * 所以那个断言没有意义），写死成 DOMWrapper 会过不了类型检查。
 */
function groupToggle(section: DOMWrapper<Element>): Omit<DOMWrapper<Element>, 'exists'> {
  return section.get('button')
}

function groupItems(section: DOMWrapper<Element>): DOMWrapper<Element>[] {
  return section.findAll('li')
}

/**
 * 分组是否展开。
 *
 * 刻意不用 `isVisible()`：VTU 默认把组件挂在**游离**容器里，而 jsdom 对游离元素的
 * `getComputedStyle` 只算第一次、之后不再失效 —— 展开后再收起，它仍报 display: block
 * （同一文件里先读一次再切换就能复现）。`v-show` 的真身就在内联样式上，
 * 直接读 `style.display` 既稳定，也不会被这条 jsdom 的坑带偏。
 */
function isMonthShown(section: DOMWrapper<Element>): boolean {
  return (section.get('ul').element as HTMLElement).style.display !== 'none'
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(articleApi, 'archive').mockResolvedValue(GROUPS)
})

/* ------------------------------------------------------------ 用例 */

describe('ArchiveView · 加载 / 失败 / 空态', () => {
  it('加载中显示骨架屏，不显示「还没有可归档的文章」，也不提前报总数', async () => {
    const pending = deferred<ArchiveGroup[]>()
    vi.spyOn(articleApi, 'archive').mockReturnValue(pending.promise)

    const { wrapper } = await mountArchive()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    // 请求还没回来就说"还没有"是在用"还没加载"冒充"没有数据"
    expect(wrapper.text()).not.toContain('还没有可归档的文章')
    // 「共 N 篇」在加载期间显示的是初始值的 0，会让人以为文章都没了
    expect(wrapper.text()).not.toContain('共 0 篇')

    pending.resolve(GROUPS)
    await flushPromises()

    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.text()).toContain('共 4 篇')
  })

  it('加载失败显示后端文案，且不会错报成「还没有可归档的文章」', async () => {
    vi.spyOn(articleApi, 'archive').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountArchive()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('还没有可归档的文章')
  })

  it('一篇都没有时给空态与「共 0 篇」的说明', async () => {
    vi.spyOn(articleApi, 'archive').mockResolvedValue([])

    const { wrapper } = await mountArchive()

    expect(wrapper.getComponent(EmptyState).props('title')).toBe('还没有可归档的文章')
    expect(wrapper.text()).toContain('共 0 篇')
    expect(wrapper.text()).toContain('分布在 0 个月份')
  })

  it('头部计数是各分组 count 之和（不是 items 长度，后端可能只回一段）', async () => {
    vi.spyOn(articleApi, 'archive').mockResolvedValue([
      makeGroup(`${CURRENT_YEAR}-03`, [{ id: 1, title: '甲', slug: 'a' }], 12),
      makeGroup(`${CURRENT_YEAR}-01`, [{ id: 2, title: '乙', slug: 'b' }], 8),
    ])

    const { wrapper } = await mountArchive()

    expect(wrapper.text()).toContain('共 20 篇')
    expect(wrapper.text()).toContain('分布在 2 个月份')
  })
})

describe('ArchiveView · 分组渲染', () => {
  it('按年月分组渲染标题、篇数与文章链接', async () => {
    const { wrapper } = await mountArchive()

    expect(wrapper.findAll('section')).toHaveLength(3)
    expect(group(wrapper, `${CURRENT_YEAR} 年 3 月`).text()).toContain('2 篇')
    expect(wrapper.get('a[href="/article/this-mar-1"]').text()).toBe('今年的三月一')
    expect(wrapper.find('a[href="/article/last-dec"]').exists()).toBe(true)
  })

  it('每条只显示月-日（年份已经在分组标题上，重复显示是噪声）', async () => {
    const { wrapper } = await mountArchive()
    const section = group(wrapper, `${CURRENT_YEAR} 年 3 月`)

    expect(section.get('time').text()).toBe('03/05')
  })

  it('分组为空（后端给了 0 篇的月份）时不渲染任何条目，也不会崩', async () => {
    vi.spyOn(articleApi, 'archive').mockResolvedValue([makeGroup(`${CURRENT_YEAR}-02`, [], 0)])

    const { wrapper } = await mountArchive()

    expect(groupItems(group(wrapper, `${CURRENT_YEAR} 年 2 月`))).toHaveLength(0)
    expect(wrapper.text()).toContain('0 篇')
  })
})

describe('ArchiveView · 折叠规则', () => {
  it('当年默认展开、往年默认收起', async () => {
    const { wrapper } = await mountArchive()
    const thisYear = group(wrapper, `${CURRENT_YEAR} 年 3 月`)
    const lastYear = group(wrapper, `${LAST_YEAR} 年 12 月`)

    expect(groupToggle(thisYear).attributes('aria-expanded')).toBe('true')
    expect(isMonthShown(thisYear)).toBe(true)
    // 往年全部展开会让首屏被几百条旧文撑爆
    expect(groupToggle(lastYear).attributes('aria-expanded')).toBe('false')
    expect(isMonthShown(lastYear)).toBe(false)
  })

  it('点分组头展开往年、再点收起，aria-expanded 与可见性同步', async () => {
    const { wrapper } = await mountArchive()
    const lastYear = group(wrapper, `${LAST_YEAR} 年 12 月`)

    await groupToggle(lastYear).trigger('click')
    expect(groupToggle(lastYear).attributes('aria-expanded')).toBe('true')
    expect(isMonthShown(lastYear)).toBe(true)
    expect(lastYear.text()).toContain('去年的十二月')

    await groupToggle(lastYear).trigger('click')
    expect(groupToggle(lastYear).attributes('aria-expanded')).toBe('false')
    expect(isMonthShown(lastYear)).toBe(false)
  })

  it('手动收起当年的分组后，默认规则不许把它重新展开', async () => {
    const { wrapper } = await mountArchive()
    const thisYear = group(wrapper, `${CURRENT_YEAR} 年 3 月`)

    await groupToggle(thisYear).trigger('click')

    // 手动状态存在 overrides 里，任何一次重渲染都要以它为准
    expect(groupToggle(thisYear).attributes('aria-expanded')).toBe('false')
    expect(isMonthShown(thisYear)).toBe(false)
    // 同一年的另一个月份只被点过它自己，仍然是展开的
    expect(groupToggle(group(wrapper, `${CURRENT_YEAR} 年 1 月`)).attributes('aria-expanded')).toBe(
      'true',
    )
  })

  it('展开状态按月份各自独立（点开 12 月不会连带打开 11 月）', async () => {
    vi.spyOn(articleApi, 'archive').mockResolvedValue([
      makeGroup(`${LAST_YEAR}-12`, [{ id: 1, title: '十二月', slug: 'dec' }]),
      makeGroup(`${LAST_YEAR}-11`, [{ id: 2, title: '十一月', slug: 'nov' }]),
    ])

    const { wrapper } = await mountArchive()
    await groupToggle(group(wrapper, `${LAST_YEAR} 年 12 月`)).trigger('click')

    expect(isMonthShown(group(wrapper, `${LAST_YEAR} 年 12 月`))).toBe(true)
    expect(isMonthShown(group(wrapper, `${LAST_YEAR} 年 11 月`))).toBe(false)
  })
})

describe('ArchiveView · head', () => {
  it('浏览器标题为「归档」', async () => {
    await mountArchive()
    expect(document.title).toBe('归档 · 个人博客')
  })
})
