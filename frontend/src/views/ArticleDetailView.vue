<script setup lang="ts">
/** 文章详情页：正文 + 目录 + 上下篇 + 相关文章 + 评论。 */
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { articleApi } from '@/api'
import CommentSection from '@/components/CommentSection.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import MobileToc from '@/components/MobileToc.vue'
import ReadingProgress from '@/components/ReadingProgress.vue'
import TableOfContents from '@/components/TableOfContents.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import { useAuthStore } from '@/stores/auth'
import type { ArticleDetail, ArticleSummary } from '@/types'
import { formatCount, formatDate, formatReadingTime } from '@/utils/format'
import { renderMarkdown } from '@/utils/markdown'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const liked = ref(false)
// 点赞与删除分开计数：点赞是高频轻操作，不该让删除按钮的状态跟着它变
const likeAction = useAction()
const action = useAction()

const article = useAsyncData<ArticleDetail | null>(
  () => articleApi.detail(String(route.params.slug)),
  null,
)

/** 相关文章独立拉取：它依赖 article.id，且失败不该拖垮正文渲染。 */
const related = ref<ArticleSummary[]>([])

/** 正文与目录只渲染一次，两处消费（这里用 computed 天然缓存）。 */
const rendered = computed(() => {
  const source = article.data.value?.content_md ?? ''
  return source ? renderMarkdown(source) : { html: '', toc: [] }
})

// 标题 / OG meta 跟随文章变化（摘要取后端字段，没有就退化为站点默认文案）
useHead(
  computed(() => {
    const item = article.data.value
    return {
      title: item?.title,
      description: item?.summary ?? undefined,
      image: item?.cover_image ?? undefined,
      type: 'article' as const,
    }
  }),
)

const canEdit = computed(
  () =>
    auth.isAuthenticated &&
    article.data.value !== null &&
    (auth.isAdmin || article.data.value.author?.id === auth.user?.id),
)

const publishedLabel = computed(() => {
  const item = article.data.value
  return item ? formatDate(item.published_at ?? item.created_at) : ''
})

async function like(): Promise<void> {
  const item = article.data.value
  if (!item || likeAction.running.value) return
  const result = await likeAction.run(() => articleApi.like(item.id), {
    success: '感谢支持！',
    errorMessage: '点赞失败',
  })
  if (result === undefined) return
  // 后端返回权威计数，本地不要自己 +1 ——否则并发下会和真实值越差越远
  article.data.value = { ...item, like_count: result.like_count }
  liked.value = true
}

async function removeArticle(): Promise<void> {
  const item = article.data.value
  if (!item) return
  if (!window.confirm(`确定要删除《${item.title}》吗？该操作不可撤销。`)) return
  const done = await action.run(() => articleApi.remove(item.id), {
    success: '文章已删除',
    errorMessage: '删除失败',
  })
  if (done === undefined) return
  await router.push('/')
}

watch(
  () => route.params.slug,
  () => {
    // 切换文章时重置页面级状态，否则上一篇的"已点赞"会串到这一篇
    liked.value = false
    related.value = []
    void article.run().then(() => {
      const item = article.data.value
      if (!item) return
      // 相关文章是「锦上添花」：失败只记录不打扰，正文阅读不受影响
      articleApi
        .related(item.id)
        .then((list) => {
          related.value = list
        })
        .catch(() => {
          // 有意忽略：相关文章是锦上添花，失败时区块自然隐藏，
          // 绝不能因为推荐接口挂了就在正文下面弹一个错误
          console.debug('[article] 相关文章加载失败，区块已隐藏')
        })
    })
    window.scrollTo({ top: 0 })
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <!-- 加载中 -->
    <div v-if="article.loading.value && !article.ready.value" class="mx-auto max-w-content">
      <div class="skeleton h-8 w-3/4"></div>
      <div class="skeleton mt-4 h-4 w-40"></div>
      <div class="mt-8 space-y-3">
        <div v-for="index in 6" :key="index" class="skeleton h-4 w-full"></div>
      </div>
    </div>

    <!-- 错误 -->
    <div v-else-if="article.error.value" class="mx-auto max-w-content py-16 text-center">
      <p class="text-5xl font-semibold text-ink-faint">
        {{ article.error.value.isNotFound ? '404' : '出错了' }}
      </p>
      <p class="mt-4 text-sm text-ink-soft">{{ toErrorMessage(article.error.value) }}</p>
      <div class="mt-6 flex justify-center gap-3">
        <RouterLink to="/" class="btn-primary">返回首页</RouterLink>
        <button type="button" class="btn-ghost" @click="article.run()">重试</button>
      </div>
    </div>

    <article v-else-if="article.data.value" class="mx-auto max-w-content">
      <!-- 标题区 -->
      <header class="border-b border-border pb-6">
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <RouterLink
            v-if="article.data.value.category"
            :to="{ path: '/', query: { category: article.data.value.category.slug } }"
            class="rounded-md bg-brand-50 px-2 py-1 font-medium text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
          >
            {{ article.data.value.category.name }}
          </RouterLink>
          <span
            v-if="article.data.value.status !== 'published'"
            class="rounded-md bg-amber-50 px-2 py-1 font-medium text-amber-700 dark:bg-amber-900/40 dark:text-amber-200"
          >
            {{ article.data.value.status === 'draft' ? '草稿（仅你可见）' : '已归档' }}
          </span>
        </div>

        <h1 class="mt-3 text-2xl font-semibold leading-snug text-ink sm:text-[28px]">
          {{ article.data.value.title }}
        </h1>

        <div class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint">
          <time :datetime="article.data.value.published_at ?? article.data.value.created_at">
            {{ publishedLabel }}
          </time>
          <span>{{ formatReadingTime(article.data.value.reading_time) }}</span>
          <span>{{ formatCount(article.data.value.view_count) }} 次阅读</span>
          <span>{{ article.data.value.comment_count }} 条评论</span>

          <div v-if="canEdit" class="ml-auto flex items-center gap-2">
            <RouterLink
              :to="`/admin/articles/${article.data.value.id}/edit`"
              class="text-brand-600 hover:text-brand-700"
            >
              编辑
            </RouterLink>
            <button type="button" class="text-red-500 hover:text-red-600" @click="removeArticle">
              删除
            </button>
          </div>
        </div>

        <div v-if="article.data.value.tags.length" class="mt-4 flex flex-wrap gap-2">
          <RouterLink
            v-for="tag in article.data.value.tags"
            :key="tag.id"
            :to="{ path: '/', query: { tag: tag.slug } }"
            class="chip"
          >
            # {{ tag.name }}
          </RouterLink>
        </div>
      </header>

      <!-- 正文 + 目录 -->
      <div class="relative">
        <div class="pt-8">
          <MarkdownRenderer :html="rendered.html" />
        </div>

        <aside class="absolute -right-64 top-10 hidden w-56 xl:block">
          <div class="sticky top-24 max-h-[calc(100vh-8rem)] overflow-auto">
            <TableOfContents :items="rendered.toc" />
          </div>
        </aside>
      </div>

      <!-- 移动端浮动目录：xl 以下没有常驻侧栏，用抽屉补上跳转能力 -->
      <MobileToc :items="rendered.toc" />
      <ReadingProgress />

      <!-- 点赞 -->
      <div class="mt-10 flex justify-center">
        <button
          type="button"
          class="inline-flex items-center gap-2 rounded-full border px-5 py-2.5 text-sm transition-colors"
          :class="
            liked
              ? 'border-brand-300 bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-200'
              : 'border-border text-ink-soft hover:border-brand-300 hover:text-brand-600'
          "
          :disabled="likeAction.running.value"
          @click="like"
        >
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M7 22V11l5-9a2 2 0 0 1 2 2v6h5a2 2 0 0 1 2 2.2l-1.3 7A2 2 0 0 1 17.7 22H7Z" />
            <path d="M7 11H4v11h3" />
          </svg>
          {{ liked ? '已点赞' : '点赞' }}
          <span class="text-xs text-ink-faint">{{ article.data.value.like_count }}</span>
        </button>
      </div>

      <!-- 上下篇 -->
      <nav
        v-if="article.data.value.prev || article.data.value.next"
        class="mt-10 grid gap-3 border-t border-border pt-6 sm:grid-cols-2"
      >
        <RouterLink
          v-if="article.data.value.prev"
          :to="`/article/${article.data.value.prev.slug}`"
          class="card card-hover p-4"
        >
          <p class="text-xs text-ink-faint">← 上一篇</p>
          <p class="mt-1.5 line-clamp-2 text-sm font-medium text-ink">
            {{ article.data.value.prev.title }}
          </p>
        </RouterLink>
        <span v-else class="hidden sm:block"></span>

        <RouterLink
          v-if="article.data.value.next"
          :to="`/article/${article.data.value.next.slug}`"
          class="card card-hover p-4 sm:text-right"
        >
          <p class="text-xs text-ink-faint">下一篇 →</p>
          <p class="mt-1.5 line-clamp-2 text-sm font-medium text-ink">
            {{ article.data.value.next.title }}
          </p>
        </RouterLink>
      </nav>

      <!-- 相关文章 -->
      <section v-if="related.length" class="mt-10 border-t border-border pt-6">
        <h2 class="text-sm font-semibold text-ink">相关阅读</h2>
        <ul class="mt-4 grid gap-3 sm:grid-cols-2">
          <li v-for="item in related" :key="item.id">
            <RouterLink :to="`/article/${item.slug}`" class="card card-hover flex gap-3 p-3">
              <div
                v-if="item.cover_image"
                class="h-14 w-20 shrink-0 overflow-hidden rounded-md bg-surface-muted"
              >
                <img :src="item.cover_image" :alt="item.title" loading="lazy" class="h-full w-full object-cover" />
              </div>
              <div class="min-w-0">
                <p class="line-clamp-2 text-sm font-medium leading-snug text-ink">{{ item.title }}</p>
                <p class="mt-1 text-xs text-ink-faint">
                  {{ formatDate(item.published_at ?? item.created_at) }} · {{ formatCount(item.view_count) }} 阅读
                </p>
              </div>
            </RouterLink>
          </li>
        </ul>
      </section>

      <CommentSection
        v-if="article.data.value.allow_comment"
        :article-id="article.data.value.id"
        :initial-count="article.data.value.comment_count"
      />
      <p v-else class="mt-12 border-t border-border pt-8 text-sm text-ink-faint">
        本文已关闭评论。
      </p>
    </article>
  </div>
</template>
