<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { useSiteStore } from '@/stores/site'

const site = useSiteStore()

const year = new Date().getFullYear()
const links = computed(() => site.socialLinks)
</script>

<template>
  <footer class="mt-20 border-t border-border bg-surface">
    <div class="mx-auto max-w-shell px-4 py-10 sm:px-6">
      <div class="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
        <div class="max-w-md">
          <p class="text-sm font-medium text-ink">{{ site.title }}</p>
          <p class="mt-2 text-sm leading-relaxed text-ink-soft">
            {{ site.headline || '记录技术、生活，以及一切值得写下来的东西。' }}
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <RouterLink to="/archive" class="text-ink-soft transition-colors hover:text-brand-600">
            归档
          </RouterLink>
          <RouterLink to="/guestbook" class="text-ink-soft transition-colors hover:text-brand-600">
            留言板
          </RouterLink>
          <!-- RSS 是后端直出的真实文件，不走前端路由，所以用 <a> 而不是 RouterLink -->
          <a href="/feed.xml" target="_blank" rel="noopener noreferrer" class="text-ink-soft transition-colors hover:text-brand-600">
            RSS
          </a>
          <a
            v-for="link in links"
            :key="link.url"
            :href="link.url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-ink-soft transition-colors hover:text-brand-600"
          >
            {{ link.label }}
          </a>
        </div>
      </div>

      <div
        class="mt-8 flex flex-col gap-2 border-t border-border pt-5 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between"
      >
        <p>© {{ year }} {{ site.title }}. 保留所有权利。</p>
        <p v-if="site.profile.icp">{{ site.profile.icp }}</p>
      </div>
    </div>
  </footer>
</template>
