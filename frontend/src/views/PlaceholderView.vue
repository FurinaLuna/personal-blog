<script setup lang="ts">
/**
 * 占位页。
 *
 * 用途：导航里先留好入口（友链、留言板…），页面先给个明确说明，
 * 而不是放一个 404 让人以为链接坏了。文案通过路由 props 传入，
 * 这样加一个新占位页只需要在路由表里加一条，不用新建组件文件。
 */
withDefaults(defineProps<{ heading?: string; hint?: string }>(), {
  heading: '敬请期待',
  hint: '这个页面还在建设中。',
})
</script>

<template>
  <div class="mx-auto max-w-content py-16 text-center">
    <div
      class="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-muted text-ink-faint"
    >
      <svg class="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
        <path d="M12 3v18M3 12h18" stroke-linecap="round" />
      </svg>
    </div>

    <h1 class="mt-5 text-xl font-semibold text-ink">{{ heading }}</h1>
    <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-soft">{{ hint }}</p>

    <p class="mt-6 text-xs text-ink-faint">
      页面结构已经预留，内容补上后无需改动路由。
    </p>

    <RouterLink to="/" class="btn-ghost mt-6">返回首页</RouterLink>
  </div>
</template>
