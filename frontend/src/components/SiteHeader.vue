<script setup lang="ts">
/** 站点顶部导航。移动端折叠成抽屉。 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import ThemeToggle from '@/components/ThemeToggle.vue'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'

const route = useRoute()
const auth = useAuthStore()
const site = useSiteStore()

const NAV = [
  { name: 'home', label: '首页', to: '/' },
  { name: 'categories', label: '分类', to: '/categories' },
  { name: 'tags', label: '标签', to: '/tags' },
  { name: 'archive', label: '归档', to: '/archive' },
  { name: 'links', label: '友链', to: '/links' },
  { name: 'about', label: '关于', to: '/about' },
]

const mobileOpen = ref(false)
const scrolled = ref(false)

/** 顶栏加阴影的阈值。用 passive 监听，滚动时不阻塞主线程。 */
function onScroll(): void {
  scrolled.value = window.scrollY > 8
}

onMounted(() => {
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
})

// 路由变化后收起抽屉，否则移动端点完菜单还得手动关
watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false
  },
)

const isActive = (name: string): boolean =>
  name === 'home' ? route.name === 'home' : route.path.startsWith(`/${name}`)

const brandName = computed(() => site.title)
</script>

<template>
  <header
    class="sticky top-0 z-40 border-b border-border bg-bg/85 backdrop-blur transition-shadow"
    :class="scrolled ? 'shadow-sm' : ''"
  >
    <div class="mx-auto flex h-16 max-w-shell items-center gap-4 px-4 sm:px-6">
      <RouterLink
        to="/"
        class="flex shrink-0 items-center gap-2 text-base font-semibold tracking-tight text-ink"
      >
        <span
          class="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white"
        >
          {{ brandName.slice(0, 1) }}
        </span>
        <span class="hidden sm:inline">{{ brandName }}</span>
      </RouterLink>

      <nav class="hidden flex-1 items-center gap-1 md:flex" aria-label="主导航">
        <RouterLink
          v-for="item in NAV"
          :key="item.name"
          :to="item.to"
          class="rounded-lg px-3 py-2 text-sm transition-colors"
          :class="
            isActive(item.name)
              ? 'bg-surface-muted font-medium text-brand-600'
              : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
          "
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="ml-auto flex items-center gap-1 md:ml-0">
        <ThemeToggle />

        <RouterLink
          v-if="auth.isAuthenticated"
          to="/admin"
          class="hidden rounded-lg px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink sm:block"
        >
          后台
        </RouterLink>
        <RouterLink
          v-else
          to="/login"
          class="hidden rounded-lg px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink sm:block"
        >
          登录
        </RouterLink>

        <button
          type="button"
          class="btn--icon inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-soft hover:bg-surface-muted hover:text-ink md:hidden"
          :aria-expanded="mobileOpen"
          aria-label="切换导航菜单"
          @click="mobileOpen = !mobileOpen"
        >
          <svg
            class="h-5 w-5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
          >
            <path v-if="!mobileOpen" d="M4 7h16M4 12h16M4 17h16" />
            <path v-else d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
    </div>

    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="-translate-y-1 opacity-0"
      leave-active-class="transition duration-100 ease-in"
      leave-to-class="-translate-y-1 opacity-0"
    >
      <nav
        v-if="mobileOpen"
        class="border-t border-border bg-surface px-4 py-2 md:hidden"
        aria-label="移动端导航"
      >
        <RouterLink
          v-for="item in NAV"
          :key="item.name"
          :to="item.to"
          class="block rounded-lg px-3 py-2.5 text-sm"
          :class="isActive(item.name) ? 'bg-surface-muted font-medium text-brand-600' : 'text-ink-soft'"
        >
          {{ item.label }}
        </RouterLink>
        <RouterLink
          :to="auth.isAuthenticated ? '/admin' : '/login'"
          class="block rounded-lg px-3 py-2.5 text-sm text-ink-soft"
        >
          {{ auth.isAuthenticated ? '进入后台' : '登录' }}
        </RouterLink>
      </nav>
    </Transition>
  </header>
</template>
