<script setup lang="ts">
/** 列表骨架屏。加载中用它而不是转圈，能减少布局跳动带来的「闪烁感」。 */
withDefaults(defineProps<{ rows?: number }>(), { rows: 3 })
</script>

<template>
  <div class="space-y-6">
    <!-- 结构与 ArticleCard 对齐（含右侧封面占位），加载完成时高度不跳 -->
    <article v-for="index in rows" :key="index" class="card p-6">
      <div class="flex flex-col-reverse gap-4 sm:flex-row">
        <div class="min-w-0 flex-1">
          <div class="skeleton h-4 w-20"></div>
          <div class="skeleton mt-3 h-6 w-3/4"></div>
          <div class="mt-3 space-y-2">
            <div class="skeleton h-3.5 w-full"></div>
            <div class="skeleton h-3.5 w-5/6"></div>
          </div>
          <div class="mt-4 flex gap-3">
            <div class="skeleton h-3.5 w-16"></div>
            <div class="skeleton h-3.5 w-16"></div>
          </div>
        </div>
        <div class="skeleton hidden aspect-video shrink-0 sm:block sm:aspect-[10/7] sm:w-40"></div>
      </div>
    </article>
  </div>
</template>
