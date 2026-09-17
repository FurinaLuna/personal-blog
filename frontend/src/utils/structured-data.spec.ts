/**
 * JSON-LD 结构化数据测试。
 *
 * 重点在「不输出无效字段」：schema.org 允许缺字段，但填 null / 空串
 * 会被判为无效数据，反而降低整体可信度。所以每条断言都在确认
 * 「不该出现的键确实没出现」。
 */
import { describe, expect, it } from 'vitest'

import type { ArticleDetail, SiteProfile } from '@/types'
import {
  absoluteUrl,
  articleCanonicalUrl,
  buildArticleJsonLd,
  buildSiteJsonLd,
} from '@/utils/structured-data'

function makeArticle(overrides: Partial<ArticleDetail> = {}): ArticleDetail {
  return {
    id: 1,
    title: '为什么我把博客的数据层重写了一遍',
    slug: 'why-rewrite',
    summary: '起因是老博客用了同步 ORM。',
    cover_image: '/media/cover.png',
    cover_variants: [],
    status: 'published',
    is_top: false,
    allow_comment: true,
    view_count: 128,
    like_count: 19,
    reading_time: 8,
    published_at: '2026-09-11T15:03:27Z',
    created_at: '2026-09-11T15:03:27Z',
    updated_at: '2026-09-14T06:42:47Z',
    author: { id: 1, username: 'admin', nickname: '站长', avatar_url: null },
    category: { id: 1, name: '技术笔记', slug: 'tech' },
    series: null,
    series_order: 0,
    tags: [
      { id: 1, name: 'Python', slug: 'python' },
      { id: 4, name: 'SQL', slug: 'sql' },
    ],
    comment_count: 3,
    content_md: '# 起因',
    prev: null,
    next: null,
    series_prev: null,
    series_next: null,
    ...overrides,
  }
}

function makeProfile(overrides: Partial<SiteProfile> = {}): SiteProfile {
  return {
    owner_name: 'Furina',
    headline: '记录技术与生活',
    avatar_url: '/media/avatar.png',
    bio_md: null,
    about_md: null,
    email: 'me@example.com',
    location: null,
    icp: null,
    social_links: [
      { label: 'GitHub', url: 'https://github.com/x', icon: 'github' },
      // 站内相对链接不能进 sameAs：搜索引擎需要绝对地址才能确认身份
      { label: 'RSS', url: '/feed.xml', icon: 'rss' },
    ],
    skills: null,
    comment_need_approval: true,
    allow_guest_comment: true,
    updated_at: '2026-09-14T00:00:00Z',
    ...overrides,
  }
}

describe('absoluteUrl', () => {
  it('相对路径补成绝对地址', () => {
    expect(absoluteUrl('/media/a.png')).toBe(`${window.location.origin}/media/a.png`)
  })

  it('已经是绝对地址时原样返回', () => {
    expect(absoluteUrl('https://cdn.example.com/a.png')).toBe('https://cdn.example.com/a.png')
  })
})

describe('articleCanonicalUrl', () => {
  it('用 slug 而不是 id 构造', () => {
    expect(articleCanonicalUrl('why-rewrite')).toBe(
      `${window.location.origin}/article/why-rewrite`,
    )
  })

  it('中文 slug 会被正确转义', () => {
    const url = articleCanonicalUrl('中文标题')
    expect(url).toContain(encodeURIComponent('中文标题'))
    // 转义后仍应是合法的绝对地址（能解析回来）
    expect(() => new URL(url)).not.toThrow()
  })
})

describe('buildArticleJsonLd', () => {
  it('核心字段齐全且类型正确', () => {
    const data = buildArticleJsonLd(makeArticle())

    expect(data['@context']).toBe('https://schema.org')
    expect(data['@type']).toBe('BlogPosting')
    expect(data.headline).toBe('为什么我把博客的数据层重写了一遍')
    expect(data.url).toBe(`${window.location.origin}/article/why-rewrite`)
    expect(data.datePublished).toBe('2026-09-11T15:03:27Z')
    expect(data.dateModified).toBe('2026-09-14T06:42:47Z')
    expect(data.inLanguage).toBe('zh-CN')
  })

  it('封面补成绝对地址（相对路径等于没给）', () => {
    const data = buildArticleJsonLd(makeArticle())
    expect(data.image).toEqual([`${window.location.origin}/media/cover.png`])
  })

  it('分类映射成 articleSection，标签映射成 keywords', () => {
    const data = buildArticleJsonLd(makeArticle())
    expect(data.articleSection).toBe('技术笔记')
    expect(data.keywords).toEqual(['Python', 'SQL'])
  })

  it('作者取昵称，没有昵称时回退用户名', () => {
    const withNickname = buildArticleJsonLd(makeArticle())
    expect((withNickname.author as Record<string, unknown>).name).toBe('站长')

    const withoutNickname = buildArticleJsonLd(
      makeArticle({ author: { id: 1, username: 'admin', nickname: null, avatar_url: null } }),
    )
    expect((withoutNickname.author as Record<string, unknown>).name).toBe('admin')
  })

  it('没有摘要时不输出 description，而不是填 null', () => {
    const data = buildArticleJsonLd(makeArticle({ summary: null }))
    expect(data).not.toHaveProperty('description')
  })

  it('没有封面时不输出 image', () => {
    const data = buildArticleJsonLd(makeArticle({ cover_image: null }))
    expect(data).not.toHaveProperty('image')
  })

  it('草稿（published_at 为 null）不输出 datePublished', () => {
    const data = buildArticleJsonLd(makeArticle({ published_at: null }))
    expect(data).not.toHaveProperty('datePublished')
  })

  it('没有标签时不输出空的 keywords 数组', () => {
    const data = buildArticleJsonLd(makeArticle({ tags: [] }))
    expect(data).not.toHaveProperty('keywords')
  })

  it('没有分类时不输出 articleSection', () => {
    const data = buildArticleJsonLd(makeArticle({ category: null }))
    expect(data).not.toHaveProperty('articleSection')
  })

  it('挂了系列时输出 isPartOf，指向系列详情页', () => {
    const data = buildArticleJsonLd(
      makeArticle({ series: { id: 2, name: 'SQLite 踩坑记', slug: 'sqlite' } }),
    )
    const partOf = data.isPartOf as Record<string, unknown>
    expect(partOf['@type']).toBe('CreativeWorkSeries')
    expect(partOf.name).toBe('SQLite 踩坑记')
    expect(partOf.url).toBe(`${window.location.origin}/series/sqlite`)
  })

  it('没挂系列时不输出 isPartOf', () => {
    expect(buildArticleJsonLd(makeArticle())).not.toHaveProperty('isPartOf')
  })

  it('传了站点名时输出 publisher', () => {
    const data = buildArticleJsonLd(makeArticle(), { siteName: '个人博客' })
    expect((data.publisher as Record<string, unknown>).name).toBe('个人博客')
  })

  it('结果是可序列化的（不能出现循环引用）', () => {
    expect(() => JSON.stringify(buildArticleJsonLd(makeArticle()))).not.toThrow()
  })
})

describe('buildSiteJsonLd', () => {
  it('用 @graph 组合 WebSite 与 Person', () => {
    const data = buildSiteJsonLd(makeProfile())
    const graph = data['@graph'] as Record<string, unknown>[]

    expect(data['@context']).toBe('https://schema.org')
    expect(graph).toHaveLength(2)
    expect(graph[0]['@type']).toBe('WebSite')
    expect(graph[1]['@type']).toBe('Person')
  })

  it('WebSite 通过 @id 引用 Person，两者是关联的而不是两份孤立数据', () => {
    const graph = buildSiteJsonLd(makeProfile())['@graph'] as Record<string, unknown>[]
    const personId = graph[1]['@id']
    expect((graph[0].author as Record<string, unknown>)['@id']).toBe(personId)
  })

  it('sameAs 只收绝对地址（相对链接无法用于身份确认）', () => {
    const graph = buildSiteJsonLd(makeProfile())['@graph'] as Record<string, unknown>[]
    expect(graph[1].sameAs).toEqual(['https://github.com/x'])
  })

  it('没有社交链接时不输出空的 sameAs', () => {
    const graph = buildSiteJsonLd(makeProfile({ social_links: [] }))['@graph'] as Record<
      string,
      unknown
    >[]
    expect(graph[1]).not.toHaveProperty('sameAs')
  })

  it('头像补成绝对地址', () => {
    const graph = buildSiteJsonLd(makeProfile())['@graph'] as Record<string, unknown>[]
    expect(graph[1].image).toBe(`${window.location.origin}/media/avatar.png`)
  })

  it('结果是可序列化的', () => {
    expect(() => JSON.stringify(buildSiteJsonLd(makeProfile()))).not.toThrow()
  })
})
