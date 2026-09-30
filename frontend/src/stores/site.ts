/** 站点配置 Store：关于页、页脚、SEO 标题都要用，缓存起来避免每个页面各请求一次。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, siteApi } from '@/api'
import { toErrorMessage } from '@/composables/useAsyncData'
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
  // 没有配置二维码就**不显示**入口：默认空数组/空值，前台自然不渲染联系站长按钮。
  // 不在这里塞任何示例账号 —— 那会把「站长没配」变成一个错误展示的假联系方式。
  contact_qrcodes: null,
  comment_need_approval: true,
  allow_guest_comment: true,
  // 档案取不到时**默认显示**登录入口：降级路径要和"站点默认行为"一致，
  // 否则一次接口抖动就会让站长在前台找不到登录入口（见 store 的 load 注释）。
  show_login_entry: true,
  updated_at: '',
}

export const useSiteStore = defineStore('site', () => {
  const profile = ref<SiteProfile>({ ...FALLBACK })
  const loaded = ref(false)
  const loading = ref(false)
  /**
   * 最近一次加载的失败原因（成功时清空）。
   *
   * 为什么需要它：`load()` 刻意"失败就用默认值"，这对前台是对的（页脚与关于页
   * 不该因为一个次要接口白屏）。但**后台设置页不能这么降级** —— 它看到的是
   * 一个空表单，保存按钮又是可用的，一点就把真实站点信息整片覆盖成默认值。
   * 所以这里把失败也如实暴露出来，让后台能提示 + 重试 + 禁用保存。
   */
  const error = ref<string | null>(null)

  const title = computed(() => profile.value.owner_name || '个人博客')
  const headline = computed(() => profile.value.headline ?? '')
  const socialLinks = computed(() => profile.value.social_links ?? [])
  const skills = computed(() => profile.value.skills ?? [])
  /**
   * 电梯栏「联系站长」弹层的条目。
   *
   * 与 `socialLinks` / `skills` 同构：把 `null` 收敛成空数组，组件里就不用到处写
   * `?? []`，「没有配置」与「配置成空数组」两种等价状态在渲染层也自然合并成
   * 同一个分支（都不渲染按钮）。
   */
  const contactQrcodes = computed(() => profile.value.contact_qrcodes ?? [])
  /**
   * 前台顶栏要不要显示「登录」入口。
   *
   * 放在 store 而不是组件里：顶栏有**两个**渲染位（桌面链接 + 移动端抽屉），
   * 两处各写一遍判断迟早会漂成不一致。它只管入口，登录接口与 `/login` 路由
   * 始终可用 —— 那不是遗漏，而是「站长自己还得进得去」的必要条件。
   */
  const showLoginEntry = computed(() => profile.value.show_login_entry)

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
      error.value = null
    } catch (cause) {
      // 站点档案取不到是有意降级的：它只影响页脚与关于页的文案，
      // 不该因为一个次要接口就让整站白屏。留个 debug 便于排查「为什么显示默认文案」
      console.debug('[site] 档案加载失败，使用默认文案')
      profile.value = { ...FALLBACK }
      // 降级归降级，失败这件事要如实记下来：后台设置页靠它决定
      // 「能不能保存」（见 SettingsView 的 canSave）
      error.value = toErrorMessage(cause, '站点信息加载失败')
    } finally {
      loading.value = false
    }
  }

  async function update(payload: SiteProfilePayload): Promise<void> {
    profile.value = await siteApi.updateProfile(payload)
    loaded.value = true
  }

  const stats = ref<SiteStats | null>(null)
  /** 统计加载失败的提示。为 null 表示没出错。 */
  const statsError = ref<string | null>(null)
  const statsLoading = ref(false)

  async function loadStats(): Promise<void> {
    statsLoading.value = true
    statsError.value = null
    try {
      stats.value = await siteApi.stats()
    } catch (error) {
      // 不接管异常的话，仪表盘的 KPI 卡片会永远停在骨架屏上：
      // 模板的门槛是 `v-if="!site.stats"`，而失败后 stats 一直是 null，
      // 既没有错误提示也没有重试入口，promise 的 rejection 也无人处理。
      statsError.value = error instanceof ApiError ? error.message : '统计数据加载失败'
      stats.value = null
    } finally {
      statsLoading.value = false
    }
  }

  return {
    profile,
    error,
    loaded,
    loading,
    title,
    headline,
    socialLinks,
    skills,
    contactQrcodes,
    showLoginEntry,
    stats,
    statsError,
    statsLoading,
    load,
    update,
    loadStats,
  }
})
