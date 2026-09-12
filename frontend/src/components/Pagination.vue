<script setup lang="ts">
/**
 * 分页组件。
 *
 * 只负责「算页码」和「派发事件」，不直接改路由——这样它既能被前台列表复用，
 * 也能被后台表格复用（两者更新 URL 的方式并不一样）。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    /** 当前页两侧各显示几个页码 */
    siblings?: number
  }>(),
  { siblings: 1 },
)

const emit = defineEmits<{ change: [page: number] }>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))

/**
 * 计算要显示的页码，超出部分用 '...' 占位。
 * 首尾页永远显示（用户最高频的跳转目标），中间围绕当前页展开。
 */
const pages = computed<(number | '...')[]>(() => {
  const last = totalPages.value
  const current = props.page
  const span = props.siblings * 2 + 1

  if (last <= span + 2) {
    return Array.from({ length: last }, (_, index) => index + 1)
  }

  const items: (number | '...')[] = [1]
  const start = Math.max(2, current - props.siblings)
  const end = Math.min(last - 1, current + props.siblings)

  if (start > 2) items.push('...')
  for (let index = start; index <= end; index += 1) items.push(index)
  if (end < last - 1) items.push('...')
  items.push(last)

  return items
})

function go(target: number): void {
  if (target < 1 || target > totalPages.value || target === props.page) return
  emit('change', target)
}

const rangeText = computed(() => {
  if (props.total === 0) return '共 0 条'
  const from = (props.page - 1) * props.pageSize + 1
  const to = Math.min(props.page * props.pageSize, props.total)
  return `${from}–${to} / 共 ${props.total} 条`
})
</script>

<template>
  <nav v-if="total > 0" class="flex flex-col items-center gap-3 sm:flex-row sm:justify-between">
    <p class="text-xs text-ink-faint">{{ rangeText }}</p>

    <div class="flex items-center gap-1">
      <button
        type="button"
        class="btn--ghost px-2.5 py-1.5"
        :disabled="page <= 1"
        aria-label="上一页"
        @click="go(page - 1)"
      >
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M15 19l-7-7 7-7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>

      <template v-for="(item, index) in pages" :key="`${item}-${index}`">
        <span v-if="item === '...'" class="px-1.5 text-sm text-ink-faint">…</span>
        <button
          v-else
          type="button"
          class="min-w-[34px] rounded-lg px-2.5 py-1.5 text-sm transition-colors"
          :class="
            item === page
              ? 'bg-brand-600 font-medium text-white'
              : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
          "
          :aria-current="item === page ? 'page' : undefined"
          @click="go(item)"
        >
          {{ item }}
        </button>
      </template>

      <button
        type="button"
        class="btn--ghost px-2.5 py-1.5"
        :disabled="page >= totalPages"
        aria-label="下一页"
        @click="go(page + 1)"
      >
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M9 5l7 7-7 7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
  </nav>
</template>
