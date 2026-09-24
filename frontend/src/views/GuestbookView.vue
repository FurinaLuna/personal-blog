<script setup lang="ts">
/**
 * 留言板。
 *
 * 安全说明与评论区同一条：留言内容一律用**文本插值**渲染（Vue 自动转义），
 * 不做 Markdown 解析。留言板与评论区是全站仅有的两个"任何访客都能写入"的入口，
 * 把它们渲染成 HTML 等于把 XSS 从"要不要防"变成"防得够不够"。
 *
 * 状态来源：页码在 URL 上（与首页/搜索/系列详情同一口径），刷新与前进后退都还原，
 * 也让"给第 3 页留个书签"成为可能。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { guestbookApi } from '@/api'
import Pagination from '@/components/Pagination.vue'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { GuestbookMessage, Page } from '@/types'
import { formatRelative, safeExternalUrl } from '@/utils/format'

/** 每页条数。留言比文章短，一页放 10 条读起来正好。 */
const PAGE_SIZE = 10
/** 与后端 `content` 的 max_length 对齐（两处不一致会出现"前端说可以、后端 422"） */
const CONTENT_MAX = 2000

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()
const action = useAction()

const page = computed(() => {
  const raw = Number(route.query.page ?? 1)
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1
})

const messages = useAsyncData<Page<GuestbookMessage>>(
  () => guestbookApi.list(page.value, PAGE_SIZE),
  { items: [], total: 0, page: 1, page_size: PAGE_SIZE, pages: 0 },
)

useHead(() => ({ title: '留言板' }))

const form = ref({ author_name: '', author_email: '', author_site: '', content: '' })
/**
 * 刚提交、还在等审核的留言。
 *
 * 必须显式告诉访客"它去哪了"：先审后发时这条留言**不在下面的列表里**，
 * 只弹一个 toast 的话，用户会以为提交失败了。
 */
const pendingNotice = ref(false)

const loadError = computed(() =>
  messages.error.value ? toErrorMessage(messages.error.value, '留言加载失败') : '',
)

const contentLength = computed(() => form.value.content.length)
// 只在请求进行中禁用。刻意**不**按「内容非空」来禁用：那样空内容时按钮是灰的，
// 而 submit() 里那句「留言内容不能为空」的提示就永远走不到 —— 用户看到的是
// 「点了没反应」，比一句明确的提示差得多。
const canSubmit = computed(() => !action.running.value)

onMounted(() => {
  void messages.run()
})

// 页码变化（前进/后退、直接改地址）都要重拉：只绑 onMounted 会让翻页按钮
// 改了 URL 却没有新数据 —— 这类"地址变了内容没变"的问题不报任何错。
watch(page, () => {
  void messages.run()
})

function goPage(next: number): void {
  void router.replace({
    query: next > 1 ? { ...route.query, page: String(next) } : { ...route.query, page: undefined },
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function submit(): Promise<void> {
  const content = form.value.content.trim()
  if (!content) {
    toast.error('留言内容不能为空')
    return
  }
  if (!auth.isAuthenticated && !form.value.author_name.trim()) {
    toast.error('请填写昵称')
    return
  }

  const created = await action.run(
    () =>
      guestbookApi.create({
        // 登录用户不必重复填昵称：留空由后端取账号昵称（与评论同一口径）
        author_name: form.value.author_name.trim() || null,
        author_email: form.value.author_email.trim() || null,
        author_site: form.value.author_site.trim() || null,
        content,
      }),
    { errorMessage: '留言提交失败' },
  )
  if (created === undefined) return

  form.value.content = ''
  if (created.is_approved) {
    // 站长自己发的直接过审，顺手刷新就能看到
    toast.success('留言已发布')
    pendingNotice.value = false
    await messages.run()
  } else {
    toast.success('已提交，等待站长审核')
    pendingNotice.value = true
  }
}
</script>

<template>
  <div class="mx-auto max-w-content space-y-8">
    <header class="space-y-2">
      <h1 class="text-2xl font-semibold text-ink">留言板</h1>
      <p class="text-sm leading-relaxed text-ink-soft">
        想说什么都可以，不限于文章内容。留言经站长审核后显示在这里。
      </p>
    </header>

    <!-- 发表留言 -->
    <section class="card p-4 sm:p-5">
      <div v-if="!auth.isAuthenticated" class="grid gap-3 sm:grid-cols-3">
        <input
          v-model="form.author_name"
          class="input"
          placeholder="昵称（必填）"
          aria-label="昵称（必填）"
          maxlength="50"
        />
        <input
          v-model="form.author_email"
          class="input"
          type="email"
          placeholder="邮箱（选填，不公开）"
          aria-label="邮箱（选填，不公开）"
        />
        <input
          v-model="form.author_site"
          class="input"
          placeholder="网站（选填）"
          aria-label="网站（选填）"
        />
      </div>
      <p v-else class="text-sm text-ink-soft">
        以 <span class="font-medium text-ink">{{ auth.displayName }}</span> 的身份留言。
      </p>

      <textarea
        v-model="form.content"
        class="input mt-3 min-h-28 resize-y"
        :maxlength="CONTENT_MAX"
        placeholder="写点什么…"
        aria-label="留言内容"
      ></textarea>

      <div class="mt-3 flex items-center gap-3">
        <button type="button" class="btn--primary" :disabled="!canSubmit" @click="submit">
          {{ action.running.value ? '提交中…' : '发表留言' }}
        </button>
        <span class="text-xs text-ink-faint">{{ contentLength }} / {{ CONTENT_MAX }}</span>
        <span v-if="!auth.isAuthenticated" class="ml-auto text-xs text-ink-faint">
          邮箱只用于接收回复通知，不会公开
        </span>
      </div>

      <p
        v-if="pendingNotice"
        class="mt-3 rounded-lg border border-border bg-surface-muted px-3 py-2 text-xs text-ink-soft"
      >
        你的留言已提交，站长审核通过后会显示在下方。
      </p>
    </section>

    <!-- 留言列表 -->
    <section class="space-y-4">
      <div v-if="messages.loading.value && !messages.ready.value" class="space-y-4">
        <div v-for="index in 3" :key="index" class="card p-4">
          <div class="skeleton h-3.5 w-24"></div>
          <div class="skeleton mt-3 h-3 w-full"></div>
          <div class="skeleton mt-2 h-3 w-2/3"></div>
        </div>
      </div>

      <p
        v-else-if="loadError"
        class="rounded-lg border border-border bg-surface-muted px-4 py-3 text-sm text-ink-soft"
        role="alert"
      >
        {{ loadError }}
        <button type="button" class="btn--ghost mt-2 px-2.5 py-1 text-xs" @click="messages.run()">
          重试
        </button>
      </p>

      <p
        v-else-if="!messages.data.value.items.length"
        class="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-ink-soft"
      >
        还没有留言，来做第一个吧。
      </p>

      <ul v-else class="space-y-4">
        <li v-for="message in messages.data.value.items" :key="message.id" class="card p-4 sm:p-5">
          <div class="flex items-center gap-2 text-sm">
            <span class="font-medium text-ink">{{ message.author_name }}</span>
            <!-- 地址必须过一遍 safeExternalUrl：Vue 不清洗动态 href，
                 而 author_site 完全由访客控制，`javascript:` 会被原样写进 DOM -->
            <a
              v-if="safeExternalUrl(message.author_site)"
              :href="safeExternalUrl(message.author_site)!"
              target="_blank"
              rel="noopener noreferrer nofollow"
              class="text-xs text-brand-600 hover:text-brand-700"
            >
              网站
            </a>
            <time class="ml-auto text-xs text-ink-faint" :datetime="message.created_at">
              {{ formatRelative(message.created_at) }}
            </time>
          </div>

          <!-- 纯文本渲染：插值自动转义，留言板因此天然不产生 XSS -->
          <p class="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
            {{ message.content }}
          </p>

          <!-- 站长回复：一条留言只有一个回复，不做楼中楼，所以是平铺的一个块 -->
          <div
            v-if="message.reply_content"
            class="mt-3 rounded-lg border border-border bg-surface-muted px-3 py-2.5"
          >
            <div class="flex items-center gap-2 text-xs">
              <span class="font-medium text-brand-700 dark:text-brand-200">站长回复</span>
              <time
                v-if="message.replied_at"
                class="ml-auto text-ink-faint"
                :datetime="message.replied_at"
              >
                {{ formatRelative(message.replied_at) }}
              </time>
            </div>
            <p class="mt-1.5 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
              {{ message.reply_content }}
            </p>
          </div>
        </li>
      </ul>

      <Pagination
        v-if="messages.data.value.total > PAGE_SIZE"
        :page="messages.data.value.page"
        :page-size="messages.data.value.page_size"
        :total="messages.data.value.total"
        @change="goPage"
      />
    </section>
  </div>
</template>
