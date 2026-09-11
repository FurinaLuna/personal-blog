<script setup lang="ts">
/** 空白布局：登录页这类「不需要导航」的页面用。 */
import ToastHost from '@/components/ToastHost.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
import { useSiteStore } from '@/stores/site'

const site = useSiteStore()
void site.load()
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-bg px-4 py-10">
    <div class="absolute right-4 top-4">
      <ThemeToggle />
    </div>
    <div class="w-full max-w-sm">
      <slot />
      <p class="mt-6 text-center text-xs text-ink-faint">{{ site.title }}</p>
    </div>
    <ToastHost />
  </div>
</template>
