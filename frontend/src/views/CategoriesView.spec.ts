/**
 * CategoriesView 测试。
 *
 * 分类页是「逛」的入口：一屏卡片，每张点进去回到首页并带上 `?category=`。
 * 守的契约：
 *
 * 1. **请求形态**：必须带 `with_counts`（要显示「N 篇」，不带就全是 0）；
 * 2. **失败与空态分得开**：接口挂了要给后端文案，不能报成「还没有创建分类」
 *    （站长会以为自己的分类被删光了）；加载中也不许先闪一下空态；
 * 3. **筛选出口的形态**：链接是 `/?category=slug`（首页读 query），不是分类自己的路径；
 * 4. **描述是纯文本**：用插值渲染，站点数据里的尖括号不该变成真的节点；
 * 5. 没有描述时不渲染那一段（空段落会在卡片里多留一道空白）。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, categoryApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import type { Category } from '@/types'

import CategoriesView from './CategoriesView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeCategory(overrides: Partial<Category> = {}): Category {
  return {
    id: 1,
    name: '技术',
    slug: 'tech',
    description: null,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    article_count: 3,
    ...overrides,
  }
}

const CATEGORIES: Category[] = [
  makeCategory({ id: 1, name: '技术', slug: 'tech', description: '技术相关', article_count: 3 }),
  makeCategory({ id: 2, name: '生活', slug: 'life', description: null, article_count: 0 }),
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
      { path: '/categories', component: Blank, meta: { title: '分类' } },
    ],
  })
}

async function mountCategories() {
  const router = makeRouter()
  await router.push('/categories')
  await router.isReady()
  const wrapper = mount(CategoriesView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

/** 分类卡片按名称取，避免断言落到隔壁那一张。 */
function card(wrapper: VueWrapper, name: string) {
  const found = wrapper.findAll('a.card').find((item) => item.get('h2').text() === name)
  if (!found) throw new Error(`找不到名称为「${name}」的分类卡片`)
  return found
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)
})

/* ------------------------------------------------------------ 用例 */

describe('CategoriesView · 加载 / 失败 / 空态', () => {
  it('挂载时带 with_counts 拉分类（否则「N 篇」全是 0）', async () => {
    const list = vi.spyOn(categoryApi, 'list').mockResolvedValue(CATEGORIES)

    await mountCategories()

    expect(list).toHaveBeenCalledWith(true)
  })

  it('加载中显示骨架屏，不显示「还没有创建分类」', async () => {
    const pending = deferred<Category[]>()
    vi.spyOn(categoryApi, 'list').mockReturnValue(pending.promise)

    const { wrapper } = await mountCategories()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    // 请求还没回来就说"还没有"，等于用"还没加载"冒充"没有数据"
    expect(wrapper.text()).not.toContain('还没有创建分类')
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)

    pending.resolve(CATEGORIES)
    await flushPromises()

    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(wrapper.text()).toContain('技术')
  })

  it('加载失败显示后端文案，且不会错报成「还没有创建分类」', async () => {
    vi.spyOn(categoryApi, 'list').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountCategories()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    // 两种状态的出口完全不同：空态会引导站长去后台"添加"，而这里的问题不是没有分类
    expect(wrapper.findComponent(EmptyState).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('还没有创建分类')
  })

  it('一个分类都没有时给空态与后台入口提示', async () => {
    vi.spyOn(categoryApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountCategories()
    const empty = wrapper.getComponent(EmptyState)

    expect(empty.props('title')).toBe('还没有创建分类')
    expect(empty.props('description')).toContain('分类标签')
  })
})

describe('CategoriesView · 卡片渲染与筛选出口', () => {
  it('渲染名称、篇数与描述；没有描述时不渲染那一段', async () => {
    const { wrapper } = await mountCategories()

    expect(wrapper.findAll('a.card')).toHaveLength(2)
    expect(card(wrapper, '技术').text()).toContain('3 篇')
    expect(card(wrapper, '技术').text()).toContain('技术相关')
    // 空描述不该在卡片里留一段空白
    expect(card(wrapper, '生活').text()).toContain('0 篇')
    expect(card(wrapper, '生活').find('p').exists()).toBe(false)
    expect(card(wrapper, '生活').text()).not.toContain('undefined')
  })

  it('卡片链接带 slug 回首页筛选（分类没有自己的文章列表页）', async () => {
    const { wrapper } = await mountCategories()

    expect(card(wrapper, '技术').attributes('href')).toBe('/?category=tech')
    expect(card(wrapper, '生活').attributes('href')).toBe('/?category=life')
  })

  it('描述走插值渲染：站点数据里的尖括号不会变成真节点', async () => {
    vi.spyOn(categoryApi, 'list').mockResolvedValue([
      makeCategory({ description: '<b>加粗</b> & <script>alert(1)</script>' }),
    ])

    const { wrapper } = await mountCategories()

    expect(card(wrapper, '技术').find('b').exists()).toBe(false)
    expect(card(wrapper, '技术').find('script').exists()).toBe(false)
    expect(card(wrapper, '技术').text()).toContain('<b>加粗</b>')
  })

  it('超长描述不截断（分类说明是站长精心写的，前台不该私自截）', async () => {
    const long = '很长的分类说明。'.repeat(40)
    vi.spyOn(categoryApi, 'list').mockResolvedValue([makeCategory({ description: long })])

    const { wrapper } = await mountCategories()

    expect(card(wrapper, '技术').text()).toContain(long)
  })

  it('列表顺序按接口返回原样（前端不重排：后端已按 sort_order 排好）', async () => {
    vi.spyOn(categoryApi, 'list').mockResolvedValue([
      makeCategory({ id: 9, name: '乙类' }),
      makeCategory({ id: 8, name: '甲类' }),
    ])

    const { wrapper } = await mountCategories()

    expect(wrapper.findAll('a.card').map((item) => item.get('h2').text())).toEqual(['乙类', '甲类'])
  })
})

describe('CategoriesView · head', () => {
  it('浏览器标题为「分类」', async () => {
    await mountCategories()
    expect(document.title).toBe('分类 · 个人博客')
  })
})
