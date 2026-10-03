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

/** 侧边栏导航项的类型。adminOnly 可选（默认对所有人可见），exact 可选（默认前缀匹配）。 */
interface NavItem {
  to: string
  label: string
  icon: string
  exact?: boolean
  adminOnly?: boolean
}

const NAV: NavItem[] = [
  { to: '/admin', label: '仪表盘', exact: true, icon: 'grid' },
  { to: '/admin/articles', label: '文章', icon: 'doc' },
  { to: '/admin/comments', label: '评论', icon: 'chat' },
  { to: '/admin/taxonomy', label: '分类标签', icon: 'tag' },
  { to: '/admin/series', label: '系列', icon: 'list' },
  { to: '/admin/media', label: '媒体库', icon: 'image' },
  { to: '/admin/links', label: '友链', icon: 'link' },
  { to: '/admin/guestbook', label: '留言板', icon: 'chat' },
  { to: '/admin/users', label: '用户', icon: 'users', adminOnly: true },
  { to: '/admin/settings', label: '站点设置', icon: 'cog', adminOnly: true },
]

/**
 * 侧边栏图标：内联 SVG，不引图标库。
 *
 * 为什么不引 lucide / heroicons：本项目只有后台这一处需要图标，
 * 引一个完整图标库只为 10 个图标，打包体积不划算。
 * 内联 SVG 零依赖、可随主题色（currentColor）变色，暗色模式自动适配。
 *
 * 图标风格统一：1.8 描边、round 端点、24×24 viewBox——与前台卡片里的
 * 元信息图标保持同一套视觉语言。
 */
const ICONS: Record<string, string> = {
  grid: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
  doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h6"/>',
  chat: '<path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.4-4.2A8 8 0 1 1 21 12Z"/>',
  tag: '<path d="M20.6 13.4 13 21l-9-9V3h9z"/><circle cx="7.5" cy="7.5" r="1.2" fill="currentColor" stroke="none"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
}

const visibleNav = computed(() => NAV.filter((item) => !item.adminOnly || auth.isAdmin))

function isActive(item: NavItem): boolean {
  return item.exact ? route.path === item.to : route.path.startsWith(item.to)
}

async function logout(): Promise<void> {
  await auth.logout()
  toast.success('已退出登录')
  await router.push('/login')
}

onMounted(() => {
  // 这里用**无条件**的 restore()，与 DefaultLayout 的 restoreIfLikely() 不同 ——
  // 不是笔误：本布局只挂在受保护路由上，而守卫的恢复带 6 秒超时（网络半死时会
  // 放行让人先看到页面）。那种情况下 `restored` 仍是 false，这里这一问才是真正的
  // 补救，不能因为"提示位缺失"就短路掉。
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
          <span
            class="flex h-7 w-7 items-center justify-center rounded-lg text-xs font-bold text-white shadow-sm"
            style="background-image: var(--gradient-brand)"
          >
            后
          </span>
          博客后台
        </RouterLink>
      </div>

      <nav class="space-y-0.5 p-3">
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
          <!-- eslint-disable vue/no-v-html — ICONS 是代码内定义的静态 SVG 路径字符串，不涉及用户输入，无 XSS 风险 -->
          <svg
            class="h-[18px] w-[18px] shrink-0"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
            v-html="ICONS[item.icon]"
          />
          <!-- eslint-enable vue/no-v-html -->
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="absolute inset-x-0 bottom-0 border-t border-border p-3">
        <RouterLink
          to="/"
          class="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <svg class="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          回到前台
        </RouterLink>
      </div>
    </aside>

    <!-- 移动端遮罩 -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-30 bg-ink/40 lg:hidden"
      @click="sidebarOpen = false"
    />

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-surface px-4 sm:px-6">
        <button
          type="button"
          class="btn--icon inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-soft hover:bg-surface-muted lg:hidden"
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
          <button type="button" class="btn--ghost px-2.5 py-1.5 text-xs" @click="logout">退出</button>
        </div>
      </header>

      <main class="flex-1 p-4 sm:p-6">
        <slot />
      </main>
    </div>

    <ToastHost />
  </div>
</template>
