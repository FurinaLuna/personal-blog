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
  // 已登录用户刷新页面后恢复登录态，顶栏才能正确显示"后台/登录"。
  //
  // 这里**必须**用 restoreIfLikely 而不是 restore：本布局挂在**公开页面**上，
  // 匿名访客占绝大多数，而 access token 只存内存、刷新后必然为空 —— 用 restore
  // 会让每个匿名访客进站都先打一次注定 401 的 /auth/refresh。
  //
  // 踩过的坑（别再犯）：这条调用点曾经是 `void auth.restore()`，而当时只在
  // **路由守卫**里加了提示 Cookie 的门控 —— 于是门控被这里绕过，目的没达成，
  // 而单测因为在路由层断言，全绿。**测调用方，别只测守卫。**
  void auth.restoreIfLikely()
})
</script>

<template>
  <div class="flex min-h-screen flex-col">
    <!-- 跳到主内容。
         键盘用户每进一个页面都要 Tab 穿过 7 个导航项 + 主题按钮 + 搜索框才能
         摸到正文；读屏用户更要在每个页面听一遍同样的导航。
         这个是全站第一个可聚焦元素，聚焦前视觉上不可见（sr-only），
         聚焦后浮出——用 focus:not-sr-only 而不是 display:none，
         后者会让它无法被聚焦。 -->
    <a
      href="#main"
      class="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:text-ink focus:shadow-lg focus:ring-2 focus:ring-brand-500"
    >
      跳到主内容
    </a>

    <SiteHeader />

    <!-- tabindex="-1" 是必要的：<main> 默认不可聚焦，跳转后焦点不会转移过去，
         键盘用户按 Tab 会从页面开头重新开始 -->
    <main
      id="main"
      tabindex="-1"
      class="mx-auto w-full max-w-shell flex-1 px-4 py-8 focus:outline-none sm:px-6 sm:py-10"
    >
      <slot />
    </main>

    <SiteFooter />
    <ToastHost />
  </div>
</template>
