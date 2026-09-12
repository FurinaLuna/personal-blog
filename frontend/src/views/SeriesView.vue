<script setup lang="ts">
/** 系列聚合页：列出所有系列及文章数，点进某个系列查看完整列表。 */
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { seriesApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import type { Series } from '@/types'

useHead({ title: '系列' })

const series = useAsyncData<Series[]>(() => seriesApi.list(true), [])

onMounted(() => {
  void series.run()
})
</script>

<template>
  <div class="space-y-8">
    <header>
      <h1 class="text-xl font-semibold text-ink sm:text-2xl">系列</h1>
      <p class="mt-2 text-sm text-ink-soft">技术连载与合集。把相关文章串成一条阅读路径。</p>
    </header>

    <div v-if="series.loading.value && !series.ready.value" class="grid gap-4 sm:grid-cols-2">
      <div v-for="index in 4" :key="index" class="skeleton h-28 rounded-xl"></div>
    </div>

    <p v-else-if="series.error.value" class="card p-6 text-center text-sm text-ink-soft">
      {{ toErrorMessage(series.error.value) }}
    </p>

    <EmptyState
      v-else-if="!series.data.value.length"
      title="还没有系列"
      description="等站长把连载文章整理成系列后，这里会按阅读顺序展示。"
    />

    <ul v-else class="grid gap-4 sm:grid-cols-2">
      <li v-for="item in series.data.value" :key="item.id">
        <RouterLink
          :to="`/series/${item.slug}`"
          class="card block h-full p-5 transition-colors hover:border-brand-300"
        >
          <div class="flex items-center gap-2">
            <h2 class="font-medium text-ink">{{ item.name }}</h2>
            <span class="ml-auto shrink-0 text-xs text-ink-faint">{{ item.article_count }} 篇</span>
          </div>
          <p v-if="item.description" class="mt-2 line-clamp-2 text-sm text-ink-soft">
            {{ item.description }}
          </p>
        </RouterLink>
      </li>
    </ul>
  </div>
</template>
