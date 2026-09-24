/**
 * TagsView（标签云）回归测试。
 *
 * 这一页此前的两个问题都由本轮的用例守住：
 *
 * 1. **拉全量再在浏览器里丢**：接口一次取回所有标签（含每条的篇数计数），
 *    然后用 `filter(count > 0)` 在客户端过滤 —— 没有文章的标签白白下载、
 *    也白白让后端做了一轮计数子查询。现在过滤交给服务端（`minCount=1`）。
 * 2. **没有上限**：标签上千时首屏要等一个带计数的长列表。现在带上 `limit`
 *    （后端上限 200），触顶时页面明确说明"只显示最常用的 N 个"。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 的 tagApi.list）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, tagApi } from '@/api'
import type { Tag } from '@/types'

import TagsView from './TagsView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeTag(overrides: Partial<Tag> = {}): Tag {
  return { id: 1, name: 'vue', slug: 'vue', article_count: 3, ...overrides }
}

const TAG_LIMIT = 200

/* ------------------------------------------------------------ 脚手架 */

const Blank = { template: '<div />' }

async function mountTags(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: Blank }],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(TagsView, { global: { plugins: [router, createPinia()] } })
  await flushPromises()
  return { wrapper, router }
}

function links(wrapper: VueWrapper) {
  return wrapper.findAll('a')
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

/* ------------------------------------------------------------ 用例 */

describe('TagsView · 请求参数', () => {
  it('服务端就过滤掉没有文章的标签，并带上上限（不再拉全量）', async () => {
    const list = vi.spyOn(tagApi, 'list').mockResolvedValue([makeTag()])

    await mountTags()

    // 以前是 tagApi.list() —— 拉全量、含计数，再在浏览器里 filter。
    // 没有文章的标签对读者毫无意义，没必要下载、也没必要让后端算它的计数。
    expect(list).toHaveBeenCalledWith({ minCount: 1, limit: TAG_LIMIT })
  })
})

describe('TagsView · 渲染', () => {
  it('渲染标签云，链接带上 tag 查询参数', async () => {
    vi.spyOn(tagApi, 'list').mockResolvedValue([
      makeTag({ id: 1, name: 'vue', slug: 'vue', article_count: 5 }),
      makeTag({ id: 2, name: 'fastapi', slug: 'fastapi', article_count: 2 }),
    ])

    const { wrapper } = await mountTags()

    const rendered = links(wrapper)
    // text() 会把子元素文本直接拼起来（'vue' + '5'），不补空格
    expect(rendered.map((link) => link.text())).toEqual(['vue5', 'fastapi2'])
    expect(rendered[0]?.attributes('href')).toContain('tag=vue')
  })

  it('隐藏篇数后不再显示计数', async () => {
    vi.spyOn(tagApi, 'list').mockResolvedValue([makeTag({ name: 'vue', article_count: 5 })])

    const { wrapper } = await mountTags()
    await wrapper.get('button').trigger('click')

    expect(links(wrapper)[0]?.text()).toBe('vue')
  })

  it('零篇的标签即便被接口返回也不渲染（防御性）', async () => {
    vi.spyOn(tagApi, 'list').mockResolvedValue([
      makeTag({ id: 1, name: 'vue', article_count: 3 }),
      makeTag({ id: 2, name: '没人用的', slug: 'unused', article_count: 0 }),
    ])

    const { wrapper } = await mountTags()

    expect(links(wrapper).map((link) => link.text())).toEqual(['vue3'])
  })

  it('接口返回空数组时显示空态而不是空白页', async () => {
    vi.spyOn(tagApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountTags()

    expect(wrapper.text()).toContain('还没有标签')
    expect(links(wrapper)).toHaveLength(0)
  })
})

describe('TagsView · 加载与失败', () => {
  it('加载中显示骨架屏，不显示空态', async () => {
    let resolve!: (value: Tag[]) => void
    vi.spyOn(tagApi, 'list').mockReturnValue(
      new Promise<Tag[]>((res) => {
        resolve = res
      }),
    )

    const { wrapper } = await mountTags()

    expect(wrapper.findAll('.skeleton').length).toBeGreaterThan(0)
    // "还没加载完"不该说成"还没有标签"
    expect(wrapper.text()).not.toContain('还没有标签')

    resolve([makeTag()])
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
  })

  it('失败时给出错误文案与重试入口，重试真的重新请求', async () => {
    const list = vi
      .spyOn(tagApi, 'list')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce([makeTag({ name: 'vue' })])

    const { wrapper } = await mountTags()

    expect(wrapper.text()).toContain('服务器开小差了')
    expect(wrapper.text()).not.toContain('还没有标签')

    // 按文案取：错误态下页头那个「隐藏篇数」按钮也是 btn--ghost，
    // 用 class 选择器会点到它（点了当然不会重新请求）
    const retry = wrapper.findAll('button').find((item) => item.text().trim() === '重试')
    expect(retry).toBeDefined()
    await retry?.trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(links(wrapper).map((link) => link.text())).toEqual(['vue3'])
  })
})

describe('TagsView · 上限', () => {
  it('触到上限时说明只显示了最常用的这么多', async () => {
    const many = Array.from({ length: TAG_LIMIT }, (_, index) =>
      makeTag({ id: index + 1, name: `tag-${index}`, slug: `tag-${index}` }),
    )
    vi.spyOn(tagApi, 'list').mockResolvedValue(many)

    const { wrapper } = await mountTags()

    expect(wrapper.text()).toContain(`只显示最常用的 ${TAG_LIMIT} 个`)
  })

  it('没有触到上限时不出现截断说明（别制造不存在的焦虑）', async () => {
    vi.spyOn(tagApi, 'list').mockResolvedValue([makeTag()])

    const { wrapper } = await mountTags()

    expect(wrapper.text()).not.toContain('只显示最常用的')
  })
})
