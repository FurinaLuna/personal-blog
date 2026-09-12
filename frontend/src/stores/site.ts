/** 站点配置 Store：关于页、页脚、SEO 标题都要用，缓存起来避免每个页面各请求一次。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { siteApi } from '@/api'
import type { SiteProfile, SiteProfilePayload, SiteStats } from '@/types'

const FALLBACK: SiteProfile = {
  owner_name: '个人博客',
  headline: null,
  avatar_url: null,
  bio_md: null,
  about_md: null,
  email: null,
  location: null,
  icp: null,
  social_links: null,
  skills: null,
  comment_need_approval: true,
  allow_guest_comment: true,
  updated_at: '',
}

export const useSiteStore = defineStore('site', () => {
  const profile = ref<SiteProfile>({ ...FALLBACK })
  const loaded = ref(false)
  const loading = ref(false)

  const title = computed(() => profile.value.owner_name || '个人博客')
  const headline = computed(() => profile.value.headline ?? '')
  const socialLinks = computed(() => profile.value.social_links ?? [])
  const skills = computed(() => profile.value.skills ?? [])

  /**
   * 拉取站点档案。
   *
   * 失败时不抛错、直接回落默认值：站点信息属于「锦上添花」，
   * 不该因为它挂掉就让整个页面白屏。
   */
  async function load(force = false): Promise<void> {
    if (loading.value) return
    if (loaded.value && !force) return

    loading.value = true
    try {
      profile.value = await siteApi.profile()
      loaded.value = true
    } catch {
      // 站点档案取不到是有意降级的：它只影响页脚与关于页的文案，
      // 不该因为一个次要接口就让整站白屏。留个 debug 便于排查「为什么显示默认文案」
      console.debug('[site] 档案加载失败，使用默认文案')
      profile.value = { ...FALLBACK }
    } finally {
      loading.value = false
    }
  }

  async function update(payload: SiteProfilePayload): Promise<void> {
    profile.value = await siteApi.updateProfile(payload)
    loaded.value = true
  }

  const stats = ref<SiteStats | null>(null)

  async function loadStats(): Promise<void> {
    stats.value = await siteApi.stats()
  }

  return {
    profile,
    loaded,
    loading,
    title,
    headline,
    socialLinks,
    skills,
    stats,
    load,
    update,
    loadStats,
  }
})
