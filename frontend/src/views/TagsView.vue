<script setup lang="ts">
/** 标签页：标签云。字号按使用频次分档。 */
import { computed, onMounted, ref } from 'vue'

import { tagApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import TagCloud from '@/components/TagCloud.vue'
import { toErrorMessage, useAsyncData } from '@/composables/useAsyncData'
import type { Tag } from '@/types'

const tags = useAsyncData<Tag[]>(() => tagApi.list(), [])

/** 只看有文章的标签。空标签出现在标签云里对读者毫无意义。 */
const meaningful = computed(() => tags.data.value.filter((tag) => tag.article_count > 0))

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
        class="btn-ghost ml-auto px-2.5 py-1.5 text-xs"
        @click="hideCount = !hideCount"
      >
        {{ hideCount ? '显示篇数' : '隐藏篇数' }}
      </button>
    </header>

    <div v-if="tags.loading.value && !tags.ready.value" class="flex flex-wrap gap-2">
      <div v-for="index in 12" :key="index" class="skeleton h-8 w-20 rounded-full"></div>
    </div>

    <p v-else-if="tags.error.value" class="card p-6 text-center text-sm text-ink-soft">
      {{ toErrorMessage(tags.error.value) }}
    </p>

    <EmptyState
      v-else-if="!meaningful.length"
      title="还没有标签"
      description="发布文章时随手打标签，这里就会自动长出来。"
    />

    <TagCloud v-else :tags="meaningful" :hide-count="hideCount" />
  </div>
</template>
