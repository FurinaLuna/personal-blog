<script setup lang="ts">
/**
 * 友情链接（前台）。
 *
 * 页面只做三件事：拉公开列表、渲染成卡片、把加载/失败/空三态分清楚。
 * 「只显示已启用」的过滤在后端完成（`is_active`），前端不做二次筛选 ——
 * 否则同一个规则会有两份实现，迟早对不上。
 */
import { onMounted } from 'vue'

import { linkApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { FriendLink } from '@/types'

useHead({ title: '友情链接' })

const links = useAsyncData<FriendLink[]>(() => linkApi.list(), [])

/** 没有头像时用站点名首字占位，比一张破图好看，也省一次请求。 */
function initial(link: FriendLink): string {
  return link.name.trim().charAt(0).toUpperCase() || '·'
}

onMounted(() => {
  void links.run()
})
</script>

<template>
  <div class="mx-auto max-w-content">
    <header class="mb-6">
      <h1 class="text-xl font-semibold text-ink">友情链接</h1>
      <p class="mt-1.5 text-sm text-ink-soft">
        这里是一些我常逛的站点。想交换链接的话，可以在
        <RouterLink to="/guestbook" class="text-brand-600 hover:text-brand-700">留言板</RouterLink>
        里说一声。
      </p>
    </header>

    <div v-if="links.loading.value && !links.ready.value" class="grid gap-4 sm:grid-cols-2">
      <div v-for="index in 4" :key="index" class="skeleton h-20 rounded-xl"></div>
    </div>

    <div
      v-else-if="links.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(links.error.value) }}</span>
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="links.run()">重试</button>
    </div>

    <EmptyState
      v-else-if="!links.data.value.length"
      title="还没有友链"
      description="站长在后台添加之后，这里就会出现。"
    />

    <ul v-else class="grid gap-4 sm:grid-cols-2">
      <li v-for="link in links.data.value" :key="link.id">
        <a
          :href="link.url"
          target="_blank"
          rel="noopener noreferrer"
          class="card flex h-full items-start gap-3 p-4 transition-colors hover:border-brand-300"
        >
          <img
            v-if="link.avatar_url"
            :src="link.avatar_url"
            :alt="`${link.name} 的头像`"
            class="h-10 w-10 shrink-0 rounded-lg object-cover"
            loading="lazy"
            decoding="async"
          />
          <span
            v-else
            aria-hidden="true"
            class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface-muted text-sm font-medium text-ink-soft"
          >
            {{ initial(link) }}
          </span>

          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-ink">{{ link.name }}</span>
            <span v-if="link.description" class="mt-0.5 block text-xs leading-relaxed text-ink-soft">
              {{ link.description }}
            </span>
            <span class="mt-1 block truncate font-mono text-[11px] text-ink-faint">
              {{ link.url }}
            </span>
          </span>
        </a>
      </li>
    </ul>
  </div>
</template>
