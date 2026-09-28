/**
 * SiteFooter 测试。
 *
 * 页脚出现在**每一页**，所以它是「社交链接会渲染到哪儿」这个问题里影响面最大的一处。
 * 守的契约：
 *
 * 1. 社交链接一律新开窗口并带 `rel="noopener noreferrer"`（否则新页面能通过
 *    `window.opener` 反向操作本页）；
 * 2. **伪协议地址不渲染成链接** —— 这是后端补上 `app.utils.url` 校验之前
 *    唯一漏掉的字段，库里可能有存量脏数据；Vue 不清洗动态 `href`，
 *    一个 `javascript:` 地址在裸机部署（无 CSP）下被点一下就能偷走登录态；
 * 3. 站点信息取不到时回落默认文案，不白屏。
 *
 * 说明：与既有 spec 一致，不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { siteApi } from '@/api'
import { useSiteStore } from '@/stores/site'
import type { SiteProfile } from '@/types'

import SiteFooter from './SiteFooter.vue'

function makeProfile(overrides: Partial<SiteProfile> = {}): SiteProfile {
  return {
    owner_name: '小站',
    headline: null,
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
    show_login_entry: true,
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const Blank = { template: '<div />' }
let pinia: Pinia

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank, meta: { title: '首页' } },
      { path: '/archive', component: Blank, meta: { title: '归档' } },
      { path: '/guestbook', component: Blank, meta: { title: '留言板' } },
    ],
  })
}

async function mountFooter() {
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  // 页脚自己不发请求：真实链路里是 DefaultLayout.vue 的 onMounted 里那句
  // `void site.load()` 把档案灌进 store 的（SiteHeader 也读同一份缓存）。
  // 这里单独挂载组件，所以要先手动走一次，否则拿到的是 store 的空初值，
  // 测出来的是「档案没加载」而不是「页脚怎么渲染」。
  await useSiteStore().load()
  const wrapper = mount(SiteFooter, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile())
})

describe('SiteFooter · 社交链接', () => {
  it('正常链接新开窗口并带 noopener', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({
        social_links: [
          { label: 'GitHub', url: 'https://github.com/example' },
          { label: '邮箱', url: 'https://example.com/contact' },
        ],
      }),
    )

    const { wrapper } = await mountFooter()

    // 页脚固定有一条 RSS 外链，所以社交链接要按 href 断言而不是按下标
    const github = wrapper.get('a[href="https://github.com/example"]')
    const contact = wrapper.get('a[href="https://example.com/contact"]')

    expect(github.text()).toBe('GitHub')
    expect(github.attributes('target')).toBe('_blank')
    expect(github.attributes('rel')).toContain('noopener')
    expect(contact.text()).toBe('邮箱')
    // 2 条社交链接 + 页脚固定的 RSS = 3
    expect(wrapper.findAll('a[target="_blank"]')).toHaveLength(3)
  })

  it('伪协议地址不渲染成链接（存量脏数据兜底）', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(
      makeProfile({
        social_links: [
          { label: '正常', url: 'https://github.com/example' },
          { label: '伪协议', url: 'javascript:alert(1)' },
          { label: '大小写混写', url: 'JaVaScRiPt:alert(1)' },
          { label: '前置空白', url: '  javascript:alert(1)' },
        ],
      }),
    )

    const { wrapper } = await mountFooter()

    // 3 条里只有 https 那条活下来（外加页脚固定的一条 RSS）
    expect(wrapper.get('a[href="https://github.com/example"]').text()).toBe('正常')
    expect(wrapper.findAll('a[target="_blank"]')).toHaveLength(2)
    expect(wrapper.html()).not.toContain('javascript:')
    expect(wrapper.html()).not.toContain('JaVaScRiPt:')
    expect(wrapper.text()).not.toContain('伪协议')
  })

  it('没有社交链接时不渲染多余的外链', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile({ social_links: null }))

    const { wrapper } = await mountFooter()

    // 页脚本身有 RSS 这一条固定外链，不该被社交链接的渲染逻辑影响
    const external = wrapper.findAll('a[target="_blank"]')
    expect(external).toHaveLength(1)
    expect(external[0]?.text()).toBe('RSS')
  })
})
