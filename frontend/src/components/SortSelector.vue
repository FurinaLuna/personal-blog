<script setup lang="ts">
/** 排序选择器。选项与后端 ArticleSort 枚举一一对应。 */
import type { ArticleSort } from '@/types'

defineProps<{ modelValue: ArticleSort }>()
const emit = defineEmits<{ 'update:modelValue': [value: ArticleSort] }>()

const OPTIONS: { value: ArticleSort; label: string }[] = [
  { value: 'latest', label: '最新发布' },
  { value: 'oldest', label: '最早发布' },
  { value: 'hottest', label: '最多阅读' },
  { value: 'updated', label: '最近更新' },
  { value: 'title', label: '标题排序' },
]
</script>

<template>
  <label class="inline-flex items-center gap-2 text-sm text-ink-soft">
    <span class="hidden sm:inline">排序</span>
    <select
      class="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/25"
      :value="modelValue"
      @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value as ArticleSort)"
    >
      <option v-for="option in OPTIONS" :key="option.value" :value="option.value">
        {{ option.label }}
      </option>
    </select>
  </label>
</template>
