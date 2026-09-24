/**
 * AboutView 测试。
 *
 * 关于页的数据全部来自站点档案（`siteApi.profile`，由 site store 缓存），
 * 页面本身没有任何交互。守的契约：
 *
 * 1. **进页面拉一次档案**：靠 store 的 `loaded` 缓存，翻回关于页不该再打一次请求；
 * 2. **降级不白屏**：档案接口挂了要回落默认文案（"个人博客" + 引导站长去补资料），
 *    不能因为一个次要接口让整页空掉，也不能把异常抛到页面上；
 * 3. **渲染口径**：avatar 有图给图、没图给站名首字；邮箱给 mailto；外链一律
 *    `target="_blank"` + `rel="noopener noreferrer"`（否则新页面能通过 window.opener
 *    反向操作本页）；bio / about 两段 Markdown 走同一条消毒渲染管线；
 * 4. 两段介绍都为空时才给"还没填写"的提示（有内容时那一段不该出现）。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, siteApi } from '@/api'
import type { SiteProfile } from '@/types'

import AboutView from './AboutView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeProfile(overrides: Partial<SiteProfile> = {}): SiteProfile {
  return {
    owner_name: '小站',
    headline: '写点技术，也写点生活',
    avatar_url: null,
    bio_md: null,
    about_md: null,
    email: null,
    location: null,
    icp: null,
    social_links: null,
    skills: null,
    comment_need_approval: true,
    allow_guest_comment: true,
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }
let pinia: Pinia

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/about', component: Blank, meta: { title: '关于' } },
    ],
  })
}

async function mountAbout() {
  const router = makeRouter()
  await router.push('/about')
  await router.isReady()
  const wrapper = mount(AboutView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile())
})

/* ------------------------------------------------------------ 用例 */

describe('AboutView · 档案加载', () => {
  it('进页面拉一次站点档案', async () => {
    const profile = vi.spyOn(siteApi, 'profile')

    await mountAbout()

    expect(profile).toHaveBeenCalledTimes(1)
  })

  it('档案已有缓存时不再请求（store 的 loaded 标记，翻回本页不重复打接口）', async () => {
    const profile = vi.spyOn(siteApi, 'profile')

    const first = await mountAbout()
    first.wrapper.unmount()
    await mountAbout()

    expect(profile).toHaveBeenCalledTimes(1)
  })

  it('档案接口挂掉时回落默认文案，不白屏、不抛异常', async () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})
    vi.spyOn(siteApi, 'profile').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountAbout()

    // 站点档案属于"锦上添花"：取不到只影响文案，不该让关于页整页空掉
    expect(wrapper.get('h1').text()).toBe('个人博客')
    // 站长自己打开这一页时要能看出"是没填，不是坏了"
    expect(wrapper.text()).toContain('站长还没有填写自我介绍')
    expect(wrapper.text()).not.toContain('服务器开小差了')
    expect(debug).toHaveBeenCalled()
  })

  it('失败后再次进入（新的 store 实例）会重新尝试，不把失败永久缓存', async () => {
    vi.spyOn(console, 'debug').mockImplementation(() => {})
    const profile = vi
      .spyOn(siteApi, 'profile')
      .mockRejectedValueOnce(new ApiError('boom', 500, 'internal_error'))
      .mockResolvedValueOnce(makeProfile({ owner_name: '恢复过来的站名' }))

    const failed = await mountAbout()
    expect(failed.wrapper.get('h1').text()).toBe('个人博客')

    // 刷新页面 = 新的 pinia 实例
    pinia = createPinia()
    setActivePinia(pinia)
    const recovered = await mountAbout()

    expect(profile).toHaveBeenCalledTimes(2)
    expect(recovered.wrapper.get('h1').text()).toBe('恢复过来的站名')
  })
})

describe('AboutView · 头部与联系方式', () => {
  it('渲染站名、副标题、所在地与 mailto 邮箱', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ owner_name: '小站', headline: '副标题在这', location: '杭州', email: 'me@example.com' }),
    )

    const { wrapper } = await mountAbout()

    expect(wrapper.get('h1').text()).toBe('小站')
    expect(wrapper.text()).toContain('副标题在这')
    expect(wrapper.text()).toContain('杭州')
    expect(wrapper.get('a[href="mailto:me@example.com"]').text()).toBe('me@example.com')
  })

  it('没有头像时用站名首字占位（空着一块灰圈更难看）', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile({ owner_name: '小站', avatar_url: null }))

    const { wrapper } = await mountAbout()

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('小')
  })

  it('有头像时渲染 img，alt 用站点名（读屏用户听得到"这是谁")', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ owner_name: '小站', avatar_url: '/media/avatar.png' }),
    )

    const { wrapper } = await mountAbout()
    const avatar = wrapper.get('img')

    expect(avatar.attributes('src')).toBe('/media/avatar.png')
    expect(avatar.attributes('alt')).toBe('小站')
  })

  it('社交链接一律新开窗口并带 noopener（否则新页面能反向操作本页）', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({
        social_links: [
          { label: 'GitHub', url: 'https://github.com/example' },
          { label: 'RSS', url: 'https://example.com/feed.xml' },
        ],
      }),
    )

    const { wrapper } = await mountAbout()
    const link = wrapper.get('a[href="https://github.com/example"]')

    expect(link.text()).toBe('GitHub')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
    expect(wrapper.findAll('header a[target="_blank"]')).toHaveLength(2)
  })

  it('没有副标题 / 所在地 / 邮箱 / 社交链接时不渲染空标签', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ owner_name: '小站', headline: null, location: null, email: null, social_links: null }),
    )

    const { wrapper } = await mountAbout()

    expect(wrapper.find('header p').exists()).toBe(false)
    expect(wrapper.find('header a').exists()).toBe(false)
    expect(wrapper.get('header').text()).not.toContain('undefined')
    expect(wrapper.get('header').text()).not.toContain('null')
  })

  it('技能标签渲染成 chip；没有技能时整块不渲染', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile({ skills: ['Vue', 'Python'] }))
    const withSkills = await mountAbout()
    expect(withSkills.wrapper.findAll('span.chip').map((item) => item.text())).toEqual([
      'Vue',
      'Python',
    ])
    withSkills.wrapper.unmount()

    pinia = createPinia()
    setActivePinia(pinia)
    vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile({ skills: [] }))
    const without = await mountAbout()
    expect(without.wrapper.findAll('span.chip')).toHaveLength(0)
  })
})

describe('AboutView · 自我介绍（Markdown）', () => {
  it('bio 与 about 两段 Markdown 分别渲染在各自的区块里', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ bio_md: '## 我是谁\n\n一个写博客的人。', about_md: '## 关于本站\n\n用 Vue 写的。' }),
    )

    const { wrapper } = await mountAbout()
    const headings = wrapper.findAll('h2').map((item) => item.text())

    expect(headings).toEqual(['我是谁', '关于本站'])
    expect(wrapper.text()).toContain('一个写博客的人。')
    expect(wrapper.text()).toContain('用 Vue 写的。')
    // 两段都有内容时不该出现"还没填写"的引导
    expect(wrapper.text()).not.toContain('站长还没有填写自我介绍')
  })

  it('只有一段有内容时只渲染这一段', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ bio_md: '## 我是谁\n\n只有简介。', about_md: null }),
    )

    const { wrapper } = await mountAbout()

    expect(wrapper.findAll('h2').map((item) => item.text())).toEqual(['我是谁'])
    expect(wrapper.text()).not.toContain('关于本站')
  })

  it('两段都空（null 或空串）时给站长一句引导', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile({ bio_md: '', about_md: null }))

    const { wrapper } = await mountAbout()

    expect(wrapper.text()).toContain('站长还没有填写自我介绍')
    expect(wrapper.text()).toContain('站点设置')
    expect(wrapper.findAll('h2')).toHaveLength(0)
  })

  it('介绍里的脚本被消毒，不会真的进 DOM（与正文同一条渲染管线）', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ about_md: '正常段落\n\n<script>window.__aboutPwned = 1</script>' }),
    )

    const { wrapper } = await mountAbout()

    expect(wrapper.find('script').exists()).toBe(false)
    expect((window as unknown as Record<string, unknown>).__aboutPwned).toBeUndefined()
    expect(wrapper.text()).toContain('正常段落')
  })

  it('介绍里的站外链接补 target/rel（作者手写的裸链也要安全）', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({ about_md: '看这里 [外站](https://evil.example/x)' }),
    )

    const { wrapper } = await mountAbout()
    const link = wrapper.get('.prose a[href="https://evil.example/x"]')

    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
  })
})

describe('AboutView · head', () => {
  it('浏览器标题为「关于」', async () => {
    await mountAbout()
    expect(document.title).toBe('关于 · 个人博客')
  })
})
