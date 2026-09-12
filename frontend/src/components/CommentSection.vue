<script setup lang="ts">
/**
 * 评论区。
 *
 * 安全说明：评论内容一律用**文本插值**渲染（Vue 会自动转义），不做 Markdown 解析。
 * 评论是全站唯一的「任何访客都能写入」的入口，把它渲染成 HTML 等于把 XSS 风险
 * 从"要不要防"变成"防得够不够"。纯文本 + CSS 保留换行已经能覆盖 99% 的需求。
 */
import { computed, ref, watch } from 'vue'

import { commentApi } from '@/api'
import { useAction } from '@/composables/useAction'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import type { Comment } from '@/types'
import { formatRelative } from '@/utils/format'

const props = defineProps<{
  articleId: number
  /** 首屏由详情接口带回来的评论数，避免重复请求 */
  initialCount?: number
}>()

const auth = useAuthStore()
const site = useSiteStore()
const toast = useToast()

// 评论列表与提交走全站统一抽象：加载态/竞态/错误归一/toast 口径都不再各写一套
const comments = useAsyncData<Comment[]>(() => commentApi.listForArticle(props.articleId), [])
const action = useAction()

const form = ref({ author_name: '', author_email: '', author_site: '', content: '' })
const replyTo = ref<Comment | null>(null)
/** 记录「本次提交后是否需要等审核」，用于给出更准确的提示文案 */
const needApproval = computed(() => site.profile.comment_need_approval && !auth.isAuthor)

/** 加载失败时的可读消息（模板里直接用）。 */
const loadError = computed(() =>
  comments.error.value ? toErrorMessage(comments.error.value, '评论加载失败') : '',
)

function startReply(comment: Comment): void {
  replyTo.value = comment
  document.getElementById('comment-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function cancelReply(): void {
  replyTo.value = null
}

async function submit(): Promise<void> {
  const content = form.value.content.trim()
  if (!content) {
    toast.error('评论内容不能为空')
    return
  }
  if (!auth.isAuthenticated && !form.value.author_name.trim()) {
    toast.error('请填写昵称')
    return
  }

  const created = await action.run(
    () =>
      commentApi.create(props.articleId, {
        content,
        author_name: form.value.author_name.trim() || null,
        author_email: form.value.author_email.trim() || null,
        author_site: form.value.author_site.trim() || null,
        parent_id: replyTo.value?.id ?? null,
      }),
    {
      success: needApproval.value ? '评论已提交，等待站长审核后显示' : '评论已发布',
      errorMessage: '评论提交失败，请稍后重试',
    },
  )
  if (created === undefined) return

  form.value.content = ''
  replyTo.value = null
  await comments.run()
}

watch(() => props.articleId, () => void comments.run(), { immediate: true })

/** 回复与被回复者之间用「你」和名字区分，避免分不清谁在跟谁说话 */
const replyHint = computed(() =>
  replyTo.value ? `正在回复 @${replyTo.value.author_name}` : '',
)

const canComment = computed(() => site.profile.allow_guest_comment || auth.isAuthenticated)
</script>

<template>
  <section id="comments" class="mt-12 border-t border-border pt-8">
    <div class="mb-5 flex items-baseline gap-2">
      <h2 class="text-lg font-semibold text-ink">评论</h2>
      <span class="text-sm text-ink-faint">{{ comments.data.value.length || initialCount || 0 }}</span>
    </div>

    <!-- 评论列表 -->
    <div v-if="comments.loading.value && !comments.ready.value" class="space-y-4">
      <div v-for="index in 2" :key="index" class="card p-4">
        <div class="skeleton h-3.5 w-24"></div>
        <div class="skeleton mt-3 h-3 w-full"></div>
        <div class="skeleton mt-2 h-3 w-2/3"></div>
      </div>
    </div>

    <p v-else-if="loadError" class="rounded-lg border border-border bg-surface-muted px-4 py-3 text-sm text-ink-soft">
      {{ loadError }}
    </p>

    <p
      v-else-if="!comments.data.value.length"
      class="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-ink-soft"
    >
      还没有评论，来说点什么吧。
    </p>

    <ul v-else class="space-y-4">
      <li v-for="comment in comments.data.value" :key="comment.id" class="card p-4 sm:p-5">
        <div class="flex items-center gap-2 text-sm">
          <span class="font-medium text-ink">{{ comment.author_name }}</span>
          <span
            v-if="comment.is_admin_reply"
            class="rounded bg-brand-50 px-1.5 py-0.5 text-[11px] text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
          >
            站长
          </span>
          <span v-if="!comment.is_approved" class="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-200">
            待审核
          </span>
          <time class="ml-auto text-xs text-ink-faint" :datetime="comment.created_at">
            {{ formatRelative(comment.created_at) }}
          </time>
        </div>

        <!-- 纯文本渲染：Vue 的插值会自动转义，评论区因此天然不产生 XSS -->
        <p class="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
          {{ comment.content }}
        </p>

        <div class="mt-2 flex items-center gap-3 text-xs">
          <a
            v-if="comment.author_site"
            :href="comment.author_site"
            target="_blank"
            rel="noopener noreferrer nofollow"
            class="text-brand-600 hover:text-brand-700"
          >
            网站
          </a>
          <button type="button" class="text-ink-faint transition-colors hover:text-brand-600" @click="startReply(comment)">
            回复
          </button>
        </div>

        <!-- 二级回复 -->
        <ul v-if="comment.replies.length" class="mt-4 space-y-3 border-l-2 border-border pl-4">
          <li v-for="reply in comment.replies" :key="reply.id">
            <div class="flex items-center gap-2 text-sm">
              <span class="font-medium text-ink">{{ reply.author_name }}</span>
              <span
                v-if="reply.is_admin_reply"
                class="rounded bg-brand-50 px-1.5 py-0.5 text-[11px] text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
              >
                站长
              </span>
              <time class="ml-auto text-xs text-ink-faint" :datetime="reply.created_at">
                {{ formatRelative(reply.created_at) }}
              </time>
            </div>
            <p class="mt-1.5 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
              {{ reply.content }}
            </p>
          </li>
        </ul>
      </li>
    </ul>

    <!-- 发表框 -->
    <div id="comment-form" class="card mt-6 p-4 sm:p-5">
      <p v-if="replyHint" class="mb-3 flex items-center gap-2 text-sm text-brand-600">
        {{ replyHint }}
        <button type="button" class="text-xs text-ink-faint hover:text-ink" @click="cancelReply">
          取消
        </button>
      </p>

      <p v-if="!canComment" class="text-sm text-ink-soft">本站已关闭游客评论，登录后即可参与讨论。</p>

      <template v-else>
        <div v-if="!auth.isAuthenticated" class="mb-3 grid gap-3 sm:grid-cols-3">
          <input v-model="form.author_name" class="input" placeholder="昵称（必填）" maxlength="50" />
          <input v-model="form.author_email" class="input" type="email" placeholder="邮箱（选填，不公开）" />
          <input v-model="form.author_site" class="input" placeholder="网站（选填）" />
        </div>
        <p v-else class="mb-3 text-sm text-ink-soft">
          以 <span class="font-medium text-ink">{{ auth.displayName }}</span> 的身份发表
        </p>

        <textarea
          v-model="form.content"
          class="input min-h-[104px] resize-y"
          placeholder="说点什么…"
          maxlength="2000"
        />

        <div class="mt-3 flex items-center justify-between gap-3">
          <p class="text-xs text-ink-faint">
            <span v-if="needApproval">评论需要审核后才会公开显示</span>
            <span v-else>评论会立即显示</span>
            · {{ form.content.length }}/2000
          </p>
          <button type="button" class="btn-primary" :disabled="action.running.value" @click="submit">
            {{ action.running.value ? '提交中…' : '发表评论' }}
          </button>
        </div>
      </template>
    </div>
  </section>
</template>
