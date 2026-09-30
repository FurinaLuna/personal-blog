<script setup lang="ts">
/** 站点设置（仅站长）。改完直接影响前台首页、页脚与关于页。 */
import { computed, onMounted, ref, watch } from 'vue'

import { useAction } from '@/composables/useAction'
import { useToast } from '@/composables/useToast'
import { useSiteStore } from '@/stores/site'
import type { ContactQrcode, ContactQrcodeKind, SocialLink } from '@/types'

/** 联系二维码的 kind 选项与默认展示名。与后端 `Literal["wechat","qq"]` 一一对应。 */
const QRCODE_KINDS: Array<{ value: ContactQrcodeKind; label: string }> = [
  { value: 'wechat', label: '微信' },
  { value: 'qq', label: 'QQ' },
]

/** 联系二维码的表单行。
 *
 * 为什么和 `ContactQrcode` 不一样：`<input>` 的 `v-model` 在用户清空时会给出
 * **空串**而不是 `null`，所以表单侧要用「字符串」的宽松类型；
 * 提交前由 `qrcodesPayload()` 收敛成后端契约（空串 → null）。 */
interface QrcodeFormRow {
  kind: ContactQrcodeKind
  label: string
  image_url: string | null
  value: string | null
}

/**
 * 后台能配几条二维码。
 *
 * 上限 2 是 D-电梯-5 的默认取值：真实场景就是微信 + QQ 两个，
 * 无上限的表单迟早变成「配置垃圾场」，而前台弹层只有 `max-h-80` 那么高。
 */
const QRCODE_MAX = 2

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
  contact_qrcodes: [] as QrcodeFormRow[],
  comment_need_approval: true,
  allow_guest_comment: true,
  show_login_entry: true,
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
    // 深拷贝一层：二维码条目是对象数组，直接引用 store 会让「取消编辑」失效
    contact_qrcodes: (profile.contact_qrcodes ?? []).map((item) => ({ ...item })),
    comment_need_approval: profile.comment_need_approval,
    allow_guest_comment: profile.allow_guest_comment,
    show_login_entry: profile.show_login_entry,
  }
}

watch(() => site.loaded, (loaded) => { if (loaded) fillFromStore() }, { immediate: true })

/**
 * 能不能保存。
 *
 * **只有确实读到过服务端档案才允许保存**。否则会出现一条静默数据丢失：
 * 档案接口失败 → store 回落到默认值 → 表单是空的 → 站长随手一点「保存」，
 * 就把站点名、签名、关于页、邮箱、备案号、评论策略整片覆盖成默认值，
 * 而且界面会提示"保存成功"。
 */
const canSave = computed(() => site.loaded && !action.running.value)

/** 档案加载失败时的重试。 */
function reloadProfile(): void {
  void site.load(true)
}

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

function addQrcode(): void {
  if (form.value.contact_qrcodes.length >= QRCODE_MAX) {
    toast.error(`最多添加 ${QRCODE_MAX} 个联系二维码`)
    return
  }
  form.value.contact_qrcodes = [
    ...form.value.contact_qrcodes,
    // 默认给微信：绝大多数站长第一个要放的就是微信
    { kind: 'wechat', label: '', image_url: null, value: null },
  ]
}

function removeQrcode(index: number): void {
  form.value.contact_qrcodes = form.value.contact_qrcodes.filter((_, i) => i !== index)
}

/**
 * 表单里的二维码是「半成品也可能存在」的（用户先选了 kind 还没填值），
 * 提交前要收敛成后端契约的形状。
 *
 * 三条规则，缺一不可：
 * 1. `image_url` / `value` 的空串必须变成 `null` —— 后端写模型会对空串抛
 *    ValueError（它复用了 `_normalize_site_url`，那里「非空但空白」是错误）；
 * 2. `label` 原样提交（后端允许空串，前台缺省按 kind 显示「微信」/「QQ」）；
 * 3. 两项都空的条目直接丢掉 —— 否则前台会渲染出一张什么都没有的空卡片。
 */
function qrcodesPayload(): ContactQrcode[] {  return form.value.contact_qrcodes
    .map((item) => ({
      kind: item.kind,
      label: (item.label ?? '').trim(),
      image_url: (item.image_url ?? '').trim() || null,
      value: (item.value ?? '').trim() || null,
    }))
    .filter((item) => item.image_url !== null || item.value !== null)
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
        contact_qrcodes: qrcodesPayload(),
        comment_need_approval: form.value.comment_need_approval,
        allow_guest_comment: form.value.allow_guest_comment,
        show_login_entry: form.value.show_login_entry,
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
      <button type="button" class="btn--primary ml-auto" :disabled="!canSave" @click="save">
        {{ action.running.value ? '保存中…' : '保存设置' }}
      </button>
    </div>

    <!--
      档案没读到时必须说清楚：否则表单看起来只是"空的"，
      而保存按钮一旦可用就会把真实数据覆盖成默认值（静默数据丢失）。
    -->
    <div
      v-if="site.error"
      class="card flex items-center justify-between gap-3 border-red-200 p-4 text-sm"
      role="alert"
    >
      <span class="text-ink-soft">{{ site.error }}（当前不可保存，避免用默认值覆盖真实配置）</span>
      <button type="button" class="btn--ghost px-2.5 py-1 text-xs" @click="reloadProfile">重试</button>
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
        <button type="button" class="btn--ghost ml-auto px-2.5 py-1 text-xs" @click="addSocialLink">
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
            class="btn--ghost shrink-0 px-2.5 py-1.5 text-xs"
            @click="removeSocialLink(index)"
          >
            移除
          </button>
        </div>
      </div>
    </section>

    <section class="card p-5">
      <div class="mb-1 flex items-center gap-3">
        <h3 class="text-sm font-medium text-ink">联系二维码</h3>
        <button type="button" class="btn--ghost ml-auto px-2.5 py-1 text-xs" @click="addQrcode">
          添加
        </button>
      </div>
      <p class="mb-3 text-xs text-ink-faint">
        显示在前台右下角的「电梯栏」里，读者点开就能加你。最多 {{ QRCODE_MAX }} 条；
        二维码图片与账号至少填一个，两项都空的行不会显示。
      </p>

      <p v-if="!form.contact_qrcodes.length" class="text-xs text-ink-faint">还没有添加二维码。</p>

      <div v-else class="space-y-4">
        <div
          v-for="(item, index) in form.contact_qrcodes"
          :key="index"
          class="rounded-lg border border-border p-3"
        >
          <div class="flex flex-col gap-2 sm:flex-row">
            <label class="block text-xs sm:w-28">
              <span class="text-ink-soft">类型</span>
              <select v-model="item.kind" class="input mt-1.5" aria-label="二维码类型">
                <option v-for="kind in QRCODE_KINDS" :key="kind.value" :value="kind.value">
                  {{ kind.label }}
                </option>
              </select>
            </label>
            <label class="block flex-1 text-xs">
              <span class="text-ink-soft">展示名（留空按类型显示）</span>
              <input
                v-model="item.label"
                class="input mt-1.5"
                maxlength="20"
                :placeholder="item.kind === 'qq' ? 'QQ' : '微信'"
                aria-label="展示名"
              />
            </label>
            <button
              type="button"
              class="btn--ghost shrink-0 self-end px-2.5 py-1.5 text-xs sm:self-auto"
              @click="removeQrcode(index)"
            >
              移除
            </button>
          </div>

          <div class="mt-3 grid gap-3 sm:grid-cols-2">
            <label class="block text-xs">
              <span class="text-ink-soft">二维码图片地址</span>
              <input
                v-model="item.image_url"
                class="input mt-1.5"
                maxlength="500"
                placeholder="/media/qrcodes/wechat.png"
                aria-label="二维码图片地址"
              />
            </label>
            <label class="block text-xs">
              <span class="text-ink-soft">账号（微信号 / QQ 号，可一键复制）</span>
              <input
                v-model="item.value"
                class="input mt-1.5"
                maxlength="50"
                aria-label="联系账号"
              />
            </label>
          </div>

          <!-- 缩略图预览：地址填错在这里就能看出来，不用等前台 -->
          <img
            v-if="item.image_url"
            :src="item.image_url"
            alt="二维码预览"
            class="mt-3 h-24 w-24 rounded-lg border border-border object-contain"
          />
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
      <h3 class="mb-3 text-sm font-medium text-ink">前台入口</h3>
      <label class="flex items-start gap-3 text-sm">
        <input v-model="form.show_login_entry" type="checkbox" class="mt-1 rounded border-border" />
        <span>
          <span class="text-ink">前台顶栏显示「登录」入口</span>
          <span class="mt-0.5 block text-xs text-ink-faint">
            关闭后访客在前台看不到登录入口（桌面顶栏与移动端菜单都不再显示）。
            这只是入口开关，不是禁用登录：登录页仍可直接访问 /login 进入，站长自己
            要用，建议收藏该地址。已登录时顶栏的「后台」入口始终保留。
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
      <button type="button" class="btn--primary" :disabled="!canSave" @click="save">
        {{ action.running.value ? '保存中…' : '保存设置' }}
      </button>
    </div>
  </div>
</template>
