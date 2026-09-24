<script setup lang="ts">
/** 标签页：标签云。字号按使用频次分档。 */
import { computed, onMounted, ref } from 'vue'

import { tagApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import TagCloud from '@/components/TagCloud.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'

useHead({ title: '标签' })
import type { Tag } from '@/types'

/**
 * 标签页一次最多取多少个。
 *
 * 200 是后端的 `limit` 上限。加这个上限是因为：标签云是"一眼看全"的界面，
 * 没有合适的分页切法，但**不能不设上限** —— 标签上千时首屏要等一个
 * 带计数的长列表（每个计数都是一次子查询）。
 * 触顶时页面会明确说明"只显示了最常用的 N 个"，而不是让人以为标签就这么多。
 */
const TAG_LIMIT = 200

// 服务端就把没有文章的标签过滤掉：这一页只展示有文章的标签（下面那个 filter
// 是防御性保留），以前是把全量标签（含计数）都拉下来再在浏览器里丢掉。
const tags = useAsyncData<Tag[]>(
  () => tagApi.list({ minCount: 1, limit: TAG_LIMIT }),
  [],
)

/** 防御性过滤：接口契约若返回 0 篇的标签，也不该出现在标签云里。 */
const meaningful = computed(() => tags.data.value.filter((tag) => tag.article_count > 0))

/** 是否因为上限被截断（用于给出"还有更多"的说明）。 */
const truncated = computed(() => tags.data.value.length >= TAG_LIMIT)

const hideCount = ref(false)

onMounted(() => {
  void tags.run()
})
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <header class="mb-6 flex flex-wrap items-end gap-3">
      <div>
        <h1 class="text-xl font-semibold text-ink">标签</h1>
        <p class="mt-1.5 text-sm text-ink-soft">
          标签是文章之间的网络，一篇文章可以有多个。字号越大表示用得越多。
        </p>
      </div>
      <button
        type="button"
        class="btn--ghost ml-auto px-2.5 py-1.5 text-xs"
        @click="hideCount = !hideCount"
      >
        {{ hideCount ? '显示篇数' : '隐藏篇数' }}
      </button>
    </header>

    <div v-if="tags.loading.value && !tags.ready.value" class="flex flex-wrap gap-2">
      <div v-for="index in 12" :key="index" class="skeleton h-8 w-20 rounded-full"></div>
    </div>

    <div
      v-else-if="tags.error.value"
      class="card flex items-center justify-between gap-3 p-6 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ toErrorMessage(tags.error.value) }}</span>
      <!-- 与其它列表一致：失败必须给出下一步，而不是只留一行文案 -->
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="tags.run()">重试</button>
    </div>

    <EmptyState
      v-else-if="!meaningful.length"
      title="还没有标签"
      description="发布文章时随手打标签，这里就会自动长出来。"
    />

    <template v-else>
      <TagCloud :tags="meaningful" :hide-count="hideCount" />
      <p v-if="truncated" class="mt-4 text-center text-xs text-ink-faint">
        标签较多，这里只显示最常用的 {{ TAG_LIMIT }} 个。
      </p>
    </template>
  </div>
</template>
