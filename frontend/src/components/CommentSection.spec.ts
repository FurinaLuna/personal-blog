/**
 * CommentSection 测试。
 *
 * 评论区是全站唯一「任何访客都能写入」的入口，因此这里守的不是排版，而是四类
 * 真正会出事的行为：
 *
 * 1. **前端拦截要拦在发请求之前**——内容为空、只有空白、游客没填昵称，
 *    都不该产生一次网络请求（后端当然也会拒，但那要等一个来回，还要吃限流配额）；
 * 2. **提交的数据是清洗过的**——首尾空白要 trim、空字符串要转成 null、
 *    回复要带上 parent_id；
 * 3. **XSS 防线**：访客填的 author_site 完全不可信，`javascript:` 之类的
 *    地址必须被 safeExternalUrl 拦掉，一个 `<a>` 都不许渲染；
 * 4. **状态渲染**：待审核/站长徽标、加载骨架、加载失败文案、关闭游客评论后的提示。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { commentApi } from '@/api'
import CommentSection from '@/components/CommentSection.vue'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import type { Comment } from '@/types'

function makeComment(overrides: Partial<Comment> = {}): Comment {
  return {
    id: 1,
    article_id: 7,
    parent_id: null,
    author_name: '访客A',
    author_site: null,
    content: '写得真好',
    is_admin_reply: false,
    is_approved: true,
    created_at: '2026-09-01T10:00:00Z',
    replies: [],
    ...overrides,
  }
}

/** jsdom 不实现 scrollIntoView，点「回复」会踩到它 */
function stubScrollIntoView(): void {
  Element.prototype.scrollIntoView = vi.fn()
}

async function mountSection(comments: Comment[] = []) {
  const listSpy = vi.spyOn(commentApi, 'listForArticle').mockResolvedValue(comments)
  const wrapper = mount(CommentSection, { props: { articleId: 7 } })
  await flushPromises()
  return { wrapper, listSpy }
}

beforeEach(() => {
  setActivePinia(createPinia())
  stubScrollIntoView()
})

describe('CommentSection · 提交前拦截', () => {
  it('内容为空时不发请求', async () => {
    const { wrapper } = await mountSection()
    const create = vi.spyOn(commentApi, 'create')

    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
  })

  it('只有空白字符的内容同样被拦下（必须 trim 后判断）', async () => {
    const { wrapper } = await mountSection()
    const create = vi.spyOn(commentApi, 'create')

    await wrapper.get('textarea').setValue('   \n\t  ')
    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
  })

  it('游客未填昵称时不发请求', async () => {
    const { wrapper } = await mountSection()
    const create = vi.spyOn(commentApi, 'create')

    await wrapper.get('textarea').setValue('内容没问题')
    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
  })
})

describe('CommentSection · 提交数据清洗', () => {
  it('首尾空白被 trim，选填的空值转成 null', async () => {
    const { wrapper } = await mountSection()
    const create = vi.spyOn(commentApi, 'create').mockResolvedValue(makeComment())

    await wrapper.get('input[aria-label="昵称（必填）"]').setValue('  张三  ')
    await wrapper.get('input[aria-label="邮箱（选填，不公开）"]').setValue('   ')
    await wrapper.get('textarea').setValue('  正文内容  ')
    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith(
      7,
      expect.objectContaining({
        content: '正文内容',
        author_name: '张三',
        author_email: null,
        author_site: null,
        parent_id: null,
      }),
    )
    // 提交成功后输入框要清空，否则用户以为没发成功会再点一次
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('回复带上 parent_id，并在成功后复位', async () => {
    const root = makeComment()
    const { wrapper } = await mountSection([root])
    const create = vi.spyOn(commentApi, 'create').mockResolvedValue(makeComment({ id: 2 }))

    await wrapper.get('button.text-ink-faint').trigger('click') // 回复
    await flushPromises()
    expect(wrapper.text()).toContain('正在回复 @访客A')

    // 游客身份，昵称仍是必填（回不回复都一样）
    await wrapper.get('input[aria-label="昵称（必填）"]').setValue('路人')
    await wrapper.get('textarea').setValue('回你一句')
    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith(7, expect.objectContaining({ parent_id: 1 }))
    expect(wrapper.text()).not.toContain('正在回复 @')
  })
})

describe('CommentSection · XSS 与渲染', () => {
  it('javascript: 伪协议的网站地址不渲染成链接', async () => {
    const { wrapper } = await mountSection([
      makeComment({ author_site: 'javascript:alert(1)' }),
      makeComment({ id: 2, author_site: 'https://example.com' }),
    ])

    const links = wrapper.findAll('a[rel*="noopener"]')
    // 只有合法的那条被渲染；javascript: 那条一个 <a> 都不该出现
    expect(links).toHaveLength(1)
    // jsdom 会把 URL 规范化（补上尾斜杠），这里只断言协议与域名
    expect(links[0]?.attributes('href')).toMatch(/^https:\/\/example\.com\/?$/)
  })

  it('待审核与站长回复各带一枚徽标', async () => {
    const { wrapper } = await mountSection([
      makeComment({ is_approved: false }),
      makeComment({ id: 2, is_admin_reply: true }),
    ])
    expect(wrapper.text()).toContain('待审核')
    expect(wrapper.text()).toContain('站长')
  })

  it('评论正文按纯文本渲染（内容里的标签被转义而非解析）', async () => {
    const { wrapper } = await mountSection([makeComment({ content: '<img src=x onerror=alert(1)>' })])
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('没有评论时显示空态文案', async () => {
    const { wrapper } = await mountSection([])
    expect(wrapper.text()).toContain('还没有评论')
  })

  it('加载失败时给出可读文案而不是空白', async () => {
    vi.spyOn(commentApi, 'listForArticle').mockRejectedValue(new Error('boom'))
    const wrapper = mount(CommentSection, { props: { articleId: 7 } })
    await flushPromises()
    // 关键是「有可读文案」而不是空白/骨架屏卡住；具体措辞由 useAsyncData 统一口径
    expect(wrapper.text()).toMatch(/加载失败|请稍后重试/)
    expect(wrapper.text()).not.toContain('还没有评论')
  })
})

describe('CommentSection · 权限与提示', () => {
  it('关闭游客评论且未登录时，不渲染发表框并提示登录', async () => {
    const site = useSiteStore()
    site.profile.allow_guest_comment = false

    const { wrapper } = await mountSection()
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.text()).toContain('本站已关闭游客评论')
  })

  it('已登录用户不再要求填昵称，直接以当前身份发表', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'admin',
      nickname: '站长本人',
      avatar_url: null,
      email: 'admin@example.com',
      bio: null,
      role: 'admin',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
    }

    const { wrapper } = await mountSection()
    expect(wrapper.find('input[aria-label="昵称（必填）"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('以')
    expect(wrapper.text()).toContain('站长本人')

    const create = vi.spyOn(commentApi, 'create').mockResolvedValue(makeComment())
    await wrapper.get('textarea').setValue('一条评论')
    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()
    // 已登录时不应再带 author_name，身份以后端令牌为准
    expect(create).toHaveBeenCalledWith(7, expect.objectContaining({ author_name: null }))
  })

  it('开启审核时提示「需要审核」，站长/作者自己不受影响', async () => {
    const site = useSiteStore()
    site.profile.comment_need_approval = true
    const { wrapper } = await mountSection()
    expect(wrapper.text()).toContain('评论需要审核后才会公开显示')

    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'admin',
      nickname: null,
      avatar_url: null,
      email: 'admin@example.com',
      bio: null,
      role: 'admin',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
    }
    await flushPromises()
    expect(wrapper.text()).toContain('评论会立即显示')
  })
})
