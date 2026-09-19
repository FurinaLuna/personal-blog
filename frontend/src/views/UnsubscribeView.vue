<script setup lang="ts">
/**
 * 退订页。
 *
 * 用户是从邮件里的链接点进来的，所以：**打开即提交**，不需要再点一次按钮——
 * 退订接口是幂等的（重复提交只是查到已有记录），邮件客户端预取链接也不会出问题。
 *
 * 链接形如 `/unsubscribe#token=xxx`，token 是后端签发的签名 JWT，
 * 里面就是收件邮箱。页面不解析 token（前端解析等于把校验责任放到客户端），
 * 只负责把它转交给后端换取结果。
 *
 * **token 在 fragment（`#` 之后）而不是 query，这是刻意的**：fragment 由浏览器
 * 保留在本地、不会随请求发给服务器，因此不会落进 nginx 访问日志或 Referer 头。
 * 而这是一个有效期 10 年、凭它就能为任意邮箱退订的凭证，不该进日志。
 * 代价是只能从 `location.hash` 读——Vue Router 的 `route.query` 看不到它。
 */
import { computed, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { notificationApi } from '@/api'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { Message } from '@/types'
import { extractUnsubscribeToken } from '@/utils/unsubscribe'

const route = useRoute()

// 取 token 的规则（fragment 优先、兼容 query、非法转义兜底）在 utils/unsubscribe 里，
// 单独成模块是为了能测——这段逻辑有正则、解码与回退，不是一行能覆盖的
const token = computed(() => extractUnsubscribeToken(route.hash, route.query.token))

const result = useAsyncData<Message>(() => notificationApi.unsubscribe(token.value), {
  detail: '',
})

const missingToken = computed(() => !token.value)
const succeeded = computed(() => result.ready.value && !result.error.value)

useHead({ title: '退订通知' })

onMounted(() => {
  // 没有 token 就没什么可提交的，直接给「链接不完整」的提示
  if (!missingToken.value) void result.run()
})
</script>

<template>
  <div class="mx-auto max-w-content py-20 text-center">
    <!-- 链接不完整：邮件被截断，或用户手敲了路径 -->
    <template v-if="missingToken">
      <div
        class="mx-auto flex h-14 w-14 items-center justify-center rounded-xl bg-surface-muted text-ink-faint"
      >
        <svg class="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M12 8v5M12 16.5h.01" stroke-linecap="round" />
          <circle cx="12" cy="12" r="9" />
        </svg>
      </div>
      <h1 class="mt-5 text-xl font-semibold text-ink">退订链接不完整</h1>
      <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-soft">
        请回到邮件里点击完整的「退订」链接。如果链接显示不全，复制时注意别漏掉结尾的部分。
      </p>
      <RouterLink to="/" class="btn--ghost mt-7">返回首页</RouterLink>
    </template>

    <!-- 提交中 -->
    <template v-else-if="result.loading.value && !result.ready.value">
      <div class="skeleton mx-auto h-14 w-14 rounded-xl"></div>
      <h1 class="mt-5 text-xl font-semibold text-ink">正在处理…</h1>
      <p class="mt-2 text-sm text-ink-soft">马上就好。</p>
    </template>

    <!-- 成功 -->
    <template v-else-if="succeeded">
      <div
        class="mx-auto flex h-14 w-14 items-center justify-center rounded-xl bg-surface-muted text-brand-600"
      >
        <svg class="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </div>
      <h1 class="mt-5 text-xl font-semibold text-ink">已退订</h1>
      <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-soft">
        之后有新回复不会再给你发邮件了。想重新订阅的话，再留一次评论即可。
      </p>
      <RouterLink to="/" class="btn--primary mt-7">返回首页</RouterLink>
    </template>

    <!-- 失败：token 过期 / 伪造 / 网络异常 -->
    <template v-else>
      <div
        class="mx-auto flex h-14 w-14 items-center justify-center rounded-xl bg-surface-muted text-ink-faint"
      >
        <svg class="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M15 9l-6 6M9 9l6 6" stroke-linecap="round" />
          <circle cx="12" cy="12" r="9" />
        </svg>
      </div>
      <h1 class="mt-5 text-xl font-semibold text-ink">退订没有成功</h1>
      <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-soft">
        {{ toErrorMessage(result.error.value, '链接可能已失效，请回到邮件里重新点击') }}
      </p>
      <RouterLink to="/" class="btn--ghost mt-7">返回首页</RouterLink>
    </template>
  </div>
</template>
