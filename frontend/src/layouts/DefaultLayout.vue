<script setup lang="ts">
/** 前台布局：顶部导航 + 内容 + 页脚。 */
import { onMounted } from 'vue'

import SiteFooter from '@/components/SiteFooter.vue'
import SiteHeader from '@/components/SiteHeader.vue'
import ToastHost from '@/components/ToastHost.vue'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'

const site = useSiteStore()
const auth = useAuthStore()

onMounted(() => {
  // 站点档案（站名、页脚、关于页）全站共用，进站时拉一次即可
  void site.load()
  // 已登录用户刷新页面后恢复登录态，顶栏才能正确显示"后台/登录"
  void auth.restore()
})
</script>

<template>
  <div class="flex min-h-screen flex-col">
    <SiteHeader />

    <main class="mx-auto w-full max-w-shell flex-1 px-4 py-8 sm:px-6 sm:py-10">
      <slot />
    </main>

    <SiteFooter />
    <ToastHost />
  </div>
</template>
