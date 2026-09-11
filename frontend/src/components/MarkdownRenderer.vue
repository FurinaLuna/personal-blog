<script setup lang="ts">
/**
 * Markdown 正文渲染。
 *
 * 组件只接收**已经消毒过的 HTML**，渲染逻辑放在 utils/markdown.ts 里。
 * 这样做的原因：详情页需要同时拿到 HTML 和目录（一次渲染两处消费），
 * 如果渲染发生在组件内部，父组件就拿不到目录数据了。
 *
 * 用 v-html 是安全的——内容已经过 DOMPurify 白名单过滤（见 utils/markdown.ts），
 * 这是全站唯一允许使用 v-html 的地方。
 */
defineProps<{ html: string }>()
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- 内容已在 utils/markdown.ts 里经 DOMPurify 消毒 -->
  <div class="prose prose-slate max-w-none dark:prose-invert" v-html="html" />
</template>
