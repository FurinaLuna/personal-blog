<script setup lang="ts">
/**
 * 后台布局：左侧导航 + 顶栏。
 *
 * 导航项按角色过滤（用户管理/站点设置只对站长可见）。注意这只是**体验优化**——
 * 真正的权限判定在后端，前端隐藏菜单不能替代服务端鉴权。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import ThemeToggle from '@/components/ThemeToggle.vue'
import ToastHost from '@/components/ToastHost.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const sidebarOpen = ref(false)

const NAV = [
  { to: '/admin', label: '仪表盘', exact: true, icon: 'grid' },
  { to: '/admin/articles', label: '文章', icon: 'doc' },
  { to: '/admin/comments', label: '评论', icon: 'chat' },
  { to: '/admin/taxonomy', label: '分类标签', icon: 'tag' },
  { to: '/admin/media', label: '媒体库', icon: 'image' },
  { to: '/admin/users', label: '用户', icon: 'users', adminOnly: true },
  { to: '/admin/settings', label: '站点设置', icon: 'cog', adminOnly: true },
]

const visibleNav = computed(() => NAV.filter((item) => !item.adminOnly || auth.isAdmin))

function isActive(item: (typeof NAV)[number]): boolean {
  return item.exact ? route.path === item.to : route.path.startsWith(item.to)
}

async function logout(): Promise<void> {
  await auth.logout()
  toast.success('已退出登录')
  await router.push('/login')
}

onMounted(() => {
  void auth.restore()
})
</script>

<template>
  <div class="flex min-h-screen bg-bg">
    <!-- 侧边栏 -->
    <aside
      class="fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-border bg-surface transition-transform lg:static lg:translate-x-0"
      :class="sidebarOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="flex h-16 items-center gap-2 border-b border-border px-4">
        <RouterLink to="/" class="flex items-center gap-2 text-sm font-semibold text-ink">
          <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-xs font-bold text-white">
            后
          </span>
          博客后台
        </RouterLink>
      </div>

      <nav class="space-y-1 p-3">
        <RouterLink
          v-for="item in visibleNav"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors"
          :class="
            isActive(item)
              ? 'bg-brand-50 font-medium text-brand-700 dark:bg-brand-900/30 dark:text-brand-200'
              : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
          "
          @click="sidebarOpen = false"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="absolute inset-x-0 bottom-0 border-t border-border p-3">
        <RouterLink
          to="/"
          class="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-ink-soft hover:bg-surface-muted hover:text-ink"
        >
          ← 回到前台
        </RouterLink>
      </div>
    </aside>

    <!-- 移动端遮罩 -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-30 bg-slate-900/40 lg:hidden"
      @click="sidebarOpen = false"
    />

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-surface px-4 sm:px-6">
        <button
          type="button"
          class="btn-icon inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-soft hover:bg-surface-muted lg:hidden"
          aria-label="切换侧边栏"
          @click="sidebarOpen = !sidebarOpen"
        >
          <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>

        <h1 class="truncate text-sm font-medium text-ink">{{ route.meta.title ?? '后台' }}</h1>

        <div class="ml-auto flex items-center gap-2">
          <ThemeToggle />
          <span class="hidden text-sm text-ink-soft sm:inline">{{ auth.displayName }}</span>
          <span
            v-if="auth.isAdmin"
            class="hidden rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] text-brand-700 sm:inline dark:bg-brand-900/40 dark:text-brand-200"
          >
            站长
          </span>
          <button type="button" class="btn-ghost px-2.5 py-1.5 text-xs" @click="logout">退出</button>
        </div>
      </header>

      <main class="flex-1 p-4 sm:p-6">
        <slot />
      </main>
    </div>

    <ToastHost />
  </div>
</template>
