<script setup lang="ts">
/** 分类页：列出全部分类及其文章数。点进去回到首页并带上 category 筛选。 */
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { categoryApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import type { Category } from '@/types'

const categories = useAsyncData<Category[]>(() => categoryApi.list(true), [])

onMounted(() => {
  void categories.run()
})
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <header class="mb-6">
      <h1 class="text-xl font-semibold text-ink">分类</h1>
      <p class="mt-1.5 text-sm text-ink-soft">
        分类是文章的骨架，一篇文章只属于一个分类。
      </p>
    </header>

    <div v-if="categories.loading.value && !categories.ready.value" class="space-y-3">
      <div v-for="index in 3" :key="index" class="card p-5">
        <div class="skeleton h-5 w-32"></div>
        <div class="skeleton mt-3 h-3.5 w-2/3"></div>
      </div>
    </div>

    <p
      v-else-if="categories.error.value"
      class="card p-6 text-center text-sm text-ink-soft"
    >
      {{ toErrorMessage(categories.error.value) }}
    </p>

    <EmptyState
      v-else-if="!categories.data.value.length"
      title="还没有创建分类"
      description="登录后台后可以在「分类标签」里添加。"
    />

    <div v-else class="grid gap-4 sm:grid-cols-2">
      <RouterLink
        v-for="item in categories.data.value"
        :key="item.id"
        :to="{ path: '/', query: { category: item.slug } }"
        class="card card-hover p-5"
      >
        <div class="flex items-center justify-between gap-3">
          <h2 class="font-medium text-ink">{{ item.name }}</h2>
          <span class="shrink-0 text-xs text-ink-faint">{{ item.article_count }} 篇</span>
        </div>
        <p v-if="item.description" class="mt-2 text-sm leading-relaxed text-ink-soft">
          {{ item.description }}
        </p>
      </RouterLink>
    </div>
  </div>
</template>
