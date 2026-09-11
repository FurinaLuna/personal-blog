<script setup lang="ts">
/** 归档页：按年月分组，可折叠。 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { articleApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import type { ArchiveGroup } from '@/types'
import { formatDate, formatYearMonth } from '@/utils/format'

const groups = useAsyncData<ArchiveGroup[]>(() => articleApi.archive(), [])

/** 用户手动展开/收起过的月份。记录的是「是否展开」，未记录的用默认规则。 */
const overrides = ref<Record<string, boolean>>({})

const currentYear = String(new Date().getFullYear())

const total = computed(() => groups.data.value.reduce((sum, item) => sum + item.count, 0))

/** 默认规则：当年展开，往年收起。用户手动点过之后以手动状态为准。 */
function isExpanded(month: string): boolean {
  return overrides.value[month] ?? month.startsWith(currentYear)
}

function toggle(month: string): void {
  overrides.value = { ...overrides.value, [month]: !isExpanded(month) }
}

onMounted(() => {
  void groups.run()
})
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <header class="mb-6">
      <h1 class="text-xl font-semibold text-ink">归档</h1>
      <p v-if="!groups.loading.value" class="mt-1.5 text-sm text-ink-soft">
        共 {{ total }} 篇，分布在 {{ groups.data.value.length }} 个月份。
      </p>
    </header>

    <div v-if="groups.loading.value && !groups.ready.value" class="space-y-4">
      <div v-for="index in 3" :key="index" class="space-y-2">
        <div class="skeleton h-5 w-28"></div>
        <div class="skeleton h-4 w-2/3"></div>
        <div class="skeleton h-4 w-1/2"></div>
      </div>
    </div>

    <p v-else-if="groups.error.value" class="card p-6 text-center text-sm text-ink-soft">
      {{ toErrorMessage(groups.error.value) }}
    </p>

    <EmptyState v-else-if="!groups.data.value.length" title="还没有可归档的文章" />

    <div v-else class="space-y-6">
      <section v-for="group in groups.data.value" :key="group.year_month">
        <button
          type="button"
          class="flex w-full items-center gap-3 text-left"
          :aria-expanded="isExpanded(group.year_month)"
          @click="toggle(group.year_month)"
        >
          <h2 class="text-sm font-semibold text-ink">
            {{ formatYearMonth(group.year_month) }}
          </h2>
          <span class="text-xs text-ink-faint">{{ group.count }} 篇</span>
          <span class="h-px flex-1 bg-border" />
          <svg
            class="h-4 w-4 text-ink-faint transition-transform"
            :class="isExpanded(group.year_month) ? '' : '-rotate-90'"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>

        <ul v-show="isExpanded(group.year_month)" class="mt-3 space-y-1.5 pl-1">
          <li v-for="item in group.items" :key="item.id" class="flex items-baseline gap-3">
            <time class="shrink-0 font-mono text-xs text-ink-faint">
              {{ formatDate(item.published_at).slice(5) }}
            </time>
            <RouterLink
              :to="`/article/${item.slug}`"
              class="text-sm text-ink-soft transition-colors hover:text-brand-600"
            >
              {{ item.title }}
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>
