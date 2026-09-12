<script setup lang="ts">
/** 站点设置（仅站长）。改完直接影响前台首页、页脚与关于页。 */
import { onMounted, ref, watch } from 'vue'

import { useAction } from '@/composables/useAction'
import { useToast } from '@/composables/useToast'
import { useSiteStore } from '@/stores/site'
import type { SocialLink } from '@/types'

const toast = useToast()
const site = useSiteStore()
const action = useAction()

const form = ref({
  owner_name: '',
  headline: '',
  avatar_url: '',
  bio_md: '',
  about_md: '',
  email: '',
  location: '',
  icp: '',
  skills: '',
  social_links: [] as SocialLink[],
  comment_need_approval: true,
  allow_guest_comment: true,
})

/** 用站点档案初始化表单。store 是异步加载的，所以这里用 watch 而不是 onMounted 一次赋值。 */
function fillFromStore(): void {
  const profile = site.profile
  form.value = {
    owner_name: profile.owner_name ?? '',
    headline: profile.headline ?? '',
    avatar_url: profile.avatar_url ?? '',
    bio_md: profile.bio_md ?? '',
    about_md: profile.about_md ?? '',
    email: profile.email ?? '',
    location: profile.location ?? '',
    icp: profile.icp ?? '',
    // 技能用逗号分隔的字符串编辑，比做一个标签编辑器轻量得多
    skills: (profile.skills ?? []).join(', '),
    social_links: (profile.social_links ?? []).map((item) => ({ ...item })),
    comment_need_approval: profile.comment_need_approval,
    allow_guest_comment: profile.allow_guest_comment,
  }
}

watch(() => site.loaded, (loaded) => { if (loaded) fillFromStore() }, { immediate: true })

function addSocialLink(): void {
  if (form.value.social_links.length >= 8) {
    toast.error('最多添加 8 个链接')
    return
  }
  form.value.social_links = [...form.value.social_links, { label: '', url: '' }]
}

function removeSocialLink(index: number): void {
  form.value.social_links = form.value.social_links.filter((_, i) => i !== index)
}

async function save(): Promise<void> {
  await action.run(
    () =>
      site.update({
        owner_name: form.value.owner_name.trim() || '个人博客',
        headline: form.value.headline.trim() || null,
        avatar_url: form.value.avatar_url.trim() || null,
        bio_md: form.value.bio_md || null,
        about_md: form.value.about_md || null,
        email: form.value.email.trim() || null,
        location: form.value.location.trim() || null,
        icp: form.value.icp.trim() || null,
        skills: form.value.skills
          .split(/[,，]/)
          .map((item) => item.trim())
          .filter(Boolean),
        // 丢掉 label 或 url 为空的半成品条目，否则前台页脚会出现空链接
        social_links: form.value.social_links
          .map((item) => ({ label: item.label.trim(), url: item.url.trim() }))
          .filter((item) => item.label && item.url),
        comment_need_approval: form.value.comment_need_approval,
        allow_guest_comment: form.value.allow_guest_comment,
      }),
    {
      success: '站点设置已保存',
      errorMessage: '保存失败',
      // 保存成功后用服务端返回值回填，避免本地草稿与服务端不一致
      onSuccess: fillFromStore,
    },
  )
}

onMounted(() => {
  void site.load()
})
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-6">
    <div class="flex items-center gap-3">
      <h2 class="text-sm text-ink-soft">站点信息会实时反映到前台首页、页脚与关于页</h2>
      <button type="button" class="btn-primary ml-auto" :disabled="action.running.value" @click="save">
        {{ action.running.value ? '保存中…' : '保存设置' }}
      </button>
    </div>

    <section class="card p-5">
      <h3 class="mb-4 text-sm font-medium text-ink">基本信息</h3>
      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block text-sm">
          <span class="text-ink-soft">站点名称</span>
          <input v-model="form.owner_name" class="input mt-1.5" maxlength="50" />
        </label>
        <label class="block text-sm">
          <span class="text-ink-soft">所在地</span>
          <input v-model="form.location" class="input mt-1.5" maxlength="100" />
        </label>
        <label class="block text-sm sm:col-span-2">
          <span class="text-ink-soft">一句话签名</span>
          <input v-model="form.headline" class="input mt-1.5" maxlength="200" />
        </label>
        <label class="block text-sm">
          <span class="text-ink-soft">联系邮箱</span>
          <input v-model="form.email" class="input mt-1.5" type="email" />
        </label>
        <label class="block text-sm">
          <span class="text-ink-soft">备案号 / 页脚附加信息</span>
          <input v-model="form.icp" class="input mt-1.5" maxlength="100" />
        </label>
        <label class="block text-sm sm:col-span-2">
          <span class="text-ink-soft">头像地址</span>
          <input v-model="form.avatar_url" class="input mt-1.5" placeholder="/media/avatars/xxx.png" />
          <span class="mt-1 block text-xs text-ink-faint">
            先在「媒体库」上传头像，再回到这里粘贴地址。
          </span>
        </label>
      </div>
    </section>

    <section class="card p-5">
      <h3 class="mb-1 text-sm font-medium text-ink">技能标签</h3>
      <p class="mb-3 text-xs text-ink-faint">用逗号分隔，会以标签形式展示在关于页。</p>
      <input v-model="form.skills" class="input" placeholder="Python, FastAPI, Vue, PostgreSQL" />
    </section>

    <section class="card p-5">
      <div class="mb-3 flex items-center gap-3">
        <h3 class="text-sm font-medium text-ink">社交链接</h3>
        <button type="button" class="btn-ghost ml-auto px-2.5 py-1 text-xs" @click="addSocialLink">
          添加
        </button>
      </div>

      <p v-if="!form.social_links.length" class="text-xs text-ink-faint">还没有添加链接。</p>

      <div v-else class="space-y-2">
        <div
          v-for="(link, index) in form.social_links"
          :key="index"
          class="flex flex-col gap-2 sm:flex-row"
        >
          <input
            v-model="link.label"
            class="input sm:w-32"
            placeholder="名称"
            aria-label="链接名称"
          />
          <input v-model="link.url" class="input flex-1" placeholder="https://" aria-label="链接地址" />
          <button
            type="button"
            class="btn-ghost shrink-0 px-2.5 py-1.5 text-xs"
            @click="removeSocialLink(index)"
          >
            移除
          </button>
        </div>
      </div>
    </section>

    <section class="card p-5">
      <h3 class="mb-3 text-sm font-medium text-ink">评论策略</h3>
      <label class="flex items-start gap-3 text-sm">
        <input v-model="form.comment_need_approval" type="checkbox" class="mt-1 rounded border-border" />
        <span>
          <span class="text-ink">评论需要审核后显示</span>
          <span class="mt-0.5 block text-xs text-ink-faint">
            开启后，访客的评论会先进入待审队列（站长与作者的回复不受影响，始终直接显示）。
          </span>
        </span>
      </label>
      <label class="mt-3 flex items-start gap-3 text-sm">
        <input v-model="form.allow_guest_comment" type="checkbox" class="mt-1 rounded border-border" />
        <span>
          <span class="text-ink">允许游客评论</span>
          <span class="mt-0.5 block text-xs text-ink-faint">
            关闭后只有登录用户才能发表评论。
          </span>
        </span>
      </label>
    </section>

    <section class="card p-5">
      <h3 class="mb-1 text-sm font-medium text-ink">关于我 / 关于本站</h3>
      <p class="mb-3 text-xs text-ink-faint">
        支持 Markdown。第一段展示在关于页顶部区域，第二段作为正文。
      </p>
      <label class="block text-sm">
        <span class="text-ink-soft">个人简介</span>
        <textarea
          v-model="form.bio_md"
          class="input mt-1.5 min-h-[88px] resize-y font-mono text-xs"
          placeholder="一个喜欢把事情做到底的开发者。"
        />
      </label>
      <label class="mt-4 block text-sm">
        <span class="text-ink-soft">关于本站</span>
        <textarea
          v-model="form.about_md"
          class="input mt-1.5 min-h-[220px] resize-y font-mono text-xs"
          placeholder="## 关于我&#10;&#10;自由发挥…"
        />
      </label>
    </section>

    <div class="flex justify-end">
      <button type="button" class="btn-primary" :disabled="action.running.value" @click="save">
        {{ action.running.value ? '保存中…' : '保存设置' }}
      </button>
    </div>
  </div>
</template>
