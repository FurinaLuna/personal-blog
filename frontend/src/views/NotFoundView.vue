<script setup lang="ts">
/** 404 页。 */
import { RouterLink } from 'vue-router'

import { useHead } from '@/composables/useHead'

useHead({ title: '页面不存在' })
</script>

<template>
  <div class="mx-auto max-w-content py-20 text-center">
    <p class="font-mono text-6xl font-semibold tracking-tight text-ink-faint">404</p>
    <h1 class="mt-4 text-xl font-semibold text-ink">这个页面不存在</h1>
    <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-soft">
      链接可能已经失效，或者文章被作者改名了。试试从首页或归档里找找。
    </p>

    <div class="mt-7 flex justify-center gap-3">
      <RouterLink to="/" class="btn-primary">返回首页</RouterLink>
      <RouterLink to="/archive" class="btn-ghost">查看归档</RouterLink>
    </div>
  </div>
</template>
