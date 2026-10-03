<script setup lang="ts">
/** 关于页：数据全部来自站点档案，站长在后台「站点设置」里改。 */
import { computed, onMounted } from 'vue'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { useHead } from '@/composables/useHead'
import { useSiteStore } from '@/stores/site'
import { safeExternalUrl } from '@/utils/format'
import { renderMarkdown } from '@/utils/markdown'

useHead({ title: '关于' })

const site = useSiteStore()

const aboutHtml = computed(() => renderMarkdown(site.profile.about_md ?? '').html)
const bioHtml = computed(() => renderMarkdown(site.profile.bio_md ?? '').html)

/** 只保留能安全放进 `href` 的社交链接（理由见 `SiteFooter.vue` 的同名 computed）。 */
const safeSocialLinks = computed(() =>
  site.socialLinks
    .map((link) => ({ ...link, href: safeExternalUrl(link.url) }))
    .filter((link): link is typeof link & { href: string } => link.href !== null),
)

onMounted(() => {
  void site.load()
})
</script>

<template>
  <div class="mx-auto max-w-content">
    <header class="flex flex-col items-start gap-6 sm:flex-row sm:items-center">
      <img
        v-if="site.profile.avatar_url"
        :src="site.profile.avatar_url"
        :alt="site.title"
        class="h-24 w-24 rounded-2xl object-cover ring-2 ring-border shadow-sm"
      />
      <div
        v-else
        class="flex h-24 w-24 items-center justify-center rounded-2xl text-3xl font-bold text-white shadow-sm"
        style="background-image: var(--gradient-brand)"
      >
        {{ site.title.slice(0, 1) }}
      </div>

      <div class="min-w-0">
        <h1 class="font-display text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">
          {{ site.title }}
        </h1>
        <p v-if="site.headline" class="mt-1.5 text-sm text-ink-soft">{{ site.headline }}</p>

        <div class="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span v-if="site.profile.location" class="inline-flex items-center gap-1.5 text-ink-faint">
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/>
              <circle cx="12" cy="10" r="3"/>
            </svg>
            {{ site.profile.location }}
          </span>
          <a
            v-if="site.profile.email"
            :href="`mailto:${site.profile.email}`"
            class="inline-flex items-center gap-1.5 text-link transition-colors hover:text-link-hover"
          >
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="4" width="20" height="16" rx="2"/>
              <path d="m22 7-10 6L2 7"/>
            </svg>
            {{ site.profile.email }}
          </a>
          <a
            v-for="link in safeSocialLinks"
            :key="link.url"
            :href="link.href"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1.5 text-link transition-colors hover:text-link-hover"
          >
            {{ link.label }}
          </a>
        </div>
      </div>
    </header>

    <div v-if="site.skills.length" class="mt-7 flex flex-wrap gap-2">
      <span v-for="skill in site.skills" :key="skill" class="chip">{{ skill }}</span>
    </div>

    <div v-if="site.profile.bio_md" class="mt-10 border-t border-border pt-8">
      <MarkdownRenderer :html="bioHtml" />
    </div>

    <div v-if="site.profile.about_md" class="mt-8 border-t border-border pt-8">
      <MarkdownRenderer :html="aboutHtml" />
    </div>

    <p v-if="!site.profile.about_md && !site.profile.bio_md" class="mt-10 text-sm text-ink-soft">
      站长还没有填写自我介绍。登录后台后在「站点设置」里补充即可。
    </p>
  </div>
</template>
