<script setup lang="ts">
/** 标签云。字号按文章数分档，视觉上能一眼看出哪些是高频标签。 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { Tag } from '@/types'

const props = withDefaults(
  defineProps<{
    tags: Tag[]
    /** 是否隐藏计数（侧边栏紧凑模式用） */
    hideCount?: boolean
  }>(),
  { hideCount: false },
)

const max = computed(() => Math.max(1, ...props.tags.map((tag) => tag.article_count)))

/** 把文章数映射到 4 个字号档位。用 4 档而不是连续字号，视觉上更整齐。 */
function sizeClass(count: number): string {
  const ratio = count / max.value
  if (ratio > 0.75) return 'text-base font-medium'
  if (ratio > 0.5) return 'text-[15px]'
  if (ratio > 0.25) return 'text-sm'
  return 'text-xs'
}
</script>

<template>
  <div class="flex flex-wrap gap-2">
    <RouterLink
      v-for="tag in tags"
      :key="tag.id"
      :to="{ path: '/', query: { tag: tag.slug } }"
      class="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-ink-soft transition-colors hover:border-brand-300 hover:text-brand-600"
      :class="sizeClass(tag.article_count)"
    >
      <span>{{ tag.name }}</span>
      <span v-if="!hideCount" class="text-[11px] text-ink-faint">{{ tag.article_count }}</span>
    </RouterLink>
  </div>
</template>
