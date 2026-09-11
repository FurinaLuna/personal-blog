<script setup lang="ts">
/** 关于页：数据全部来自站点档案，站长在后台「站点设置」里改。 */
import { computed, onMounted } from 'vue'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { useHead } from '@/composables/useHead'
import { useSiteStore } from '@/stores/site'
import { renderMarkdown } from '@/utils/markdown'

useHead({ title: '关于' })

const site = useSiteStore()

const aboutHtml = computed(() => renderMarkdown(site.profile.about_md ?? '').html)
const bioHtml = computed(() => renderMarkdown(site.profile.bio_md ?? '').html)

onMounted(() => {
  void site.load()
})
</script>

<template>
  <div class="mx-auto max-w-content">
    <header class="flex flex-col items-start gap-5 sm:flex-row sm:items-center">
      <img
        v-if="site.profile.avatar_url"
        :src="site.profile.avatar_url"
        :alt="site.title"
        class="h-20 w-20 rounded-full object-cover ring-2 ring-border"
      />
      <div
        v-else
        class="flex h-20 w-20 items-center justify-center rounded-full bg-brand-600 text-2xl font-bold text-white"
      >
        {{ site.title.slice(0, 1) }}
      </div>

      <div class="min-w-0">
        <h1 class="text-2xl font-semibold text-ink">{{ site.title }}</h1>
        <p v-if="site.headline" class="mt-1.5 text-sm text-ink-soft">{{ site.headline }}</p>

        <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span v-if="site.profile.location" class="text-ink-faint">
            {{ site.profile.location }}
          </span>
          <a
            v-if="site.profile.email"
            :href="`mailto:${site.profile.email}`"
            class="text-brand-600 hover:text-brand-700"
          >
            {{ site.profile.email }}
          </a>
          <a
            v-for="link in site.socialLinks"
            :key="link.url"
            :href="link.url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-brand-600 hover:text-brand-700"
          >
            {{ link.label }}
          </a>
        </div>
      </div>
    </header>

    <div v-if="site.skills.length" class="mt-6 flex flex-wrap gap-2">
      <span v-for="skill in site.skills" :key="skill" class="chip">{{ skill }}</span>
    </div>

    <div v-if="site.profile.bio_md" class="mt-8 border-t border-border pt-8">
      <MarkdownRenderer :html="bioHtml" />
    </div>

    <div v-if="site.profile.about_md" class="mt-8 border-t border-border pt-8">
      <MarkdownRenderer :html="aboutHtml" />
    </div>

    <p v-if="!site.profile.about_md && !site.profile.bio_md" class="mt-8 text-sm text-ink-soft">
      站长还没有填写自我介绍。登录后台后在「站点设置」里补充即可。
    </p>
  </div>
</template>
