<script setup lang="ts">
/**
 * 「联系站长」二维码弹层（电梯栏里的那一颗 chat 按钮）。
 *
 * ## 为什么按钮和弹层放在同一个组件里
 *
 * 「配置为空 → 整个入口不渲染」这条规则只有拿得到配置的组件才判得了。如果让
 * ElevatorBar 先判断、再渲染按钮、把弹层塞进来，hover / 钉住 / 触屏 / 焦点归还
 * 这套状态机就得拆到两个组件里靠 props 来回传 —— 拆开之后「弹层还开着但按钮没了」
 * 这类不一致状态才真会出现。所以这里自持：按钮 + 弹层 + 状态机。
 *
 * ## 为什么展开态是 JS 算出来的，而不是纯 CSS 的 group-hover
 *
 * 纯 CSS 的 hover 展开有两个问题：`aria-expanded` 无从跟随（读屏软件会一直认为
 * 它是收起的），而触屏浏览器在 tap 之后会**合成** mouseenter —— 一次点击先被 hover
 * 打开、再被 click 翻转，表现为「点了没反应」。所以展开态是一个计算值
 * `pinned || hovering || focused`，三条来源都受控、可断言，aria 也跟着它走。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { useToast } from '@/composables/useToast'
import { useSiteStore } from '@/stores/site'
import { copyToClipboard } from '@/utils/clipboard'
import type { ContactQrcode } from '@/types'

/** 归一化后的展示项：缺省值已填好、两端空白已裁掉。 */
interface QrcodeItem {
  /** `other` 是兜底分支：库里可能存着更早写进去的其它 kind。 */
  kind: ContactQrcode['kind'] | 'other'
  label: string
  imageUrl: string | null
  value: string | null
}

const site = useSiteStore()
const toast = useToast()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)

/**
 * 指针设备判定：`(hover: none)` 命中时只保留点击展开。
 *
 * 触屏浏览器在 tap 之后会合成长串 mouse 事件，不禁用 hover 分支就会出现
 * 「点一下先开、再被 click 关掉」。设备支不支持 hover 只有媒体查询知道，
 * 而它在一个会话里不会变，所以读一次即可；读不到（非浏览器环境）按桌面处理。
 */
const hoverCapable =
  typeof window.matchMedia !== 'function' || !window.matchMedia('(hover: none)').matches

const pinned = ref(false)
const hovering = ref(false)
const focused = ref(false)

/** 三条来源任一成立就展开：钉住（点击）、悬停（桌面）、焦点在内（键盘）。 */
const open = computed(() => pinned.value || hovering.value || focused.value)

/**
 * 关闭时把焦点还给按钮，紧接着的那次 focusin 不能被当成「用户在用键盘浏览弹层」
 * 而立刻重开。这个标记只在同步的 focus() 窗口内有效，下一帧复位（focus() 什么都没
 * 触发的情形，比如按钮已经是 activeElement）。
 */
let restoringFocus = false

/** 上一次交互是不是键盘：只有键盘 Tab 来的焦点才展开弹层（理由见 onFocusIn）。 */
let keyboardFocus = false

/**
 * `kind` 收窄到已知图标分支。
 *
 * 类型上它只有两个取值，但数据来自 JSON 列，而后端读模型刻意不加校验器
 * （旧数据必须能读出来）—— 历史脏值必须落到「通用图标 + 中性文案」，
 * 不能渲染成一个没有图标、也没有文案的空格子。
 */
function normalizeKind(kind: string): QrcodeItem['kind'] {
  return kind === 'wechat' || kind === 'qq' ? kind : 'other'
}

/** label 的缺省值按 kind 给；未知 kind 不冒充「微信」，退回中性文案。 */
function defaultLabel(kind: QrcodeItem['kind']): string {
  if (kind === 'wechat') return '微信'
  if (kind === 'qq') return 'QQ'
  return '联系方式'
}

/**
 * 只保留「至少有一项内容」的条目。
 *
 * 后台点一下「添加二维码」就会多出一张两项全空的卡（用户还没填完），把它渲染
 * 出来只是一张空卡片 —— 看着像二维码加载失败。过滤放在渲染前，前端也兜一层。
 */
const items = computed<QrcodeItem[]>(() =>
  site.contactQrcodes
    .map((raw) => {
      const kind = normalizeKind(raw.kind)
      return {
        kind,
        label: (raw.label ?? '').trim() || defaultLabel(kind),
        imageUrl: (raw.image_url ?? '').trim() || null,
        value: (raw.value ?? '').trim() || null,
      }
    })
    .filter((item) => item.imageUrl !== null || item.value !== null),
)

function close(restoreFocus = false): void {
  // 三个状态一起清：只清 pinned 是不够的 —— 指针还停在按钮上时 hover 会立刻把它
  // 重新打开，表现为「Esc / 点外部关不掉」。
  pinned.value = false
  hovering.value = false
  focused.value = false
  if (!restoreFocus) return

  restoringFocus = true
  trigger.value?.focus()
  void nextTick(() => {
    restoringFocus = false
  })
}

function onTriggerClick(): void {
  // 触屏与桌面共用这一条路径（触屏没有 hover，它就是唯一的开关）；
  // 桌面上的差别只是「钉住之后移开鼠标仍保持」，这正是 pinned 相对 hovering 的意义。
  pinned.value = !pinned.value
}

function onFocusIn(): void {
  if (restoringFocus) {
    restoringFocus = false
    return
  }
  // 只认**键盘**带来的焦点：触屏 tap 也会给按钮上焦点，若一视同仁，第二次 tap
  // 想收起时会被「焦点还在这儿」立刻顶回来，成了关不掉的弹层。
  focused.value = keyboardFocus
}

function onFocusOut(event: FocusEvent): void {
  const next = event.relatedTarget as Node | null
  // 焦点只是从按钮挪进弹层（比如点「复制」），整块仍是一个交互单元，不算离开。
  if (next && root.value?.contains(next)) return
  focused.value = false
}

function onMouseEnter(): void {
  if (!hoverCapable) return
  hovering.value = true
}

function onMouseLeave(): void {
  // 无条件清：万一设备能力判断失效，鼠标移开也不该留下一个开着的弹层。
  hovering.value = false
}

/**
 * 弹层开着时，点到外面就收起。
 *
 * 与 MobileToc 一样常驻挂在 document 上、靠 `open` 守卫 —— 比「开关时增删监听器」
 * 少一个需要同步的状态。用 mousedown 而不是 click：触屏 tap 同样会合成 mousedown，
 * 一条路径覆盖鼠标与手指；而 click 要等手指抬起，期间点到的东西已经先响应了。
 */
function onDocumentMousedown(event: MouseEvent): void {
  // 指针一动，接下来的 focusin 就不算「键盘来的」
  keyboardFocus = false
  if (!open.value) return
  const target = event.target as Node | null
  if (target && root.value?.contains(target)) return
  close(true)
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Tab') keyboardFocus = true
  if (event.key === 'Escape' && open.value) close(true)
}

async function copy(item: QrcodeItem): Promise<void> {
  if (!item.value) return
  const ok = await copyToClipboard(item.value)
  if (ok) {
    toast.success(`已复制${item.label}`)
    return
  }
  // 两条降级路径都失败（局域网 HTTP 下没有 navigator.clipboard）时必须说话，
  // 否则用户以为复制成功，粘出来还是上一次的内容。
  toast.error('浏览器不允许自动复制，请手动选中复制')
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentMousedown)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMousedown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <!-- 配置为空（含「所有条目两项皆空」）时整颗按钮都不存在：
       一颗点了没反应的按钮比没有按钮更糟 -->
  <div
    v-if="items.length"
    ref="root"
    class="relative"
    @mouseenter="onMouseEnter"
    @mouseleave="onMouseLeave"
    @focusin="onFocusIn"
    @focusout="onFocusOut"
  >
    <button
      ref="trigger"
      type="button"
      class="btn--icon flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface text-ink-soft shadow-lg transition-colors hover:text-brand-600"
      aria-haspopup="true"
      :aria-expanded="open"
      aria-label="联系站长"
      @click="onTriggerClick"
    >
      <svg
        class="h-5 w-5"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path
          d="M20.5 11.8c0 4-3.8 7.2-8.5 7.2-1 0-1.9-.14-2.8-.4L4.6 20.2l1.3-3.3c-1.4-1.3-2.4-3-2.4-5.1C3.5 7.8 7.3 4.6 12 4.6s8.5 3.2 8.5 7.2Z"
        />
      </svg>
    </button>

    <!-- 只淡入淡出、不做位移：弹层向左展开，任何位移动画都会让它从指针下方滑过，
         诱使用户在动画途中误点（计划 §3.3）。 -->
    <Transition
      enter-active-class="transition-opacity duration-[var(--duration-fast)] ease-out"
      enter-from-class="opacity-0"
      leave-active-class="transition-opacity duration-[var(--duration-fast)] ease-in"
      leave-to-class="opacity-0"
    >
      <div
        v-if="open"
        class="card absolute bottom-0 right-full z-50 mr-3 max-h-80 w-60 overflow-auto p-4 shadow-lg"
        role="dialog"
        aria-label="联系方式"
      >
        <ul class="space-y-3">
          <li
            v-for="(item, index) in items"
            :key="index"
            class="rounded-lg border border-border bg-surface-muted p-3"
          >
            <div class="flex items-center gap-2">
              <!-- 图标一律内联：不引图标库（零依赖），也不引外链图片
                   （局域网 / 断网时外链就是一张破图）。三个分支都写出来，
                   未知 kind 走通用图标，绝不会渲染成空格子。 -->
              <svg
                v-if="item.kind === 'wechat'"
                class="h-5 w-5 shrink-0 text-brand-600"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M9.6 4.6C6 4.6 3.2 7 3.2 9.9c0 1.6.9 3.1 2.3 4.1l-.7 2.3 2.5-1.3c.7.2 1.5.3 2.3.3" />
                <path d="M20.8 15.2c0-2.4-2.3-4.4-5.2-4.4s-5.2 2-5.2 4.4 2.3 4.4 5.2 4.4c.6 0 1.2-.1 1.8-.3l2.1 1.2-.6-2c1.2-.7 1.9-1.9 1.9-3.3Z" />
                <circle cx="7.2" cy="9.1" r=".9" fill="currentColor" stroke="none" />
                <circle cx="12" cy="9.1" r=".9" fill="currentColor" stroke="none" />
                <circle cx="13.6" cy="15" r=".8" fill="currentColor" stroke="none" />
                <circle cx="17.6" cy="15" r=".8" fill="currentColor" stroke="none" />
              </svg>
              <svg
                v-else-if="item.kind === 'qq'"
                class="h-5 w-5 shrink-0 text-brand-600"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <ellipse cx="12" cy="12.4" rx="5.8" ry="6.8" />
                <path d="M9.4 6.4 8.6 3.6M14.6 6.4l.8-2.8" />
                <path d="M6.4 16.2 3.8 18M17.6 16.2l2.6 1.8" />
                <circle cx="9.9" cy="11" r="1" fill="currentColor" stroke="none" />
                <circle cx="14.1" cy="11" r="1" fill="currentColor" stroke="none" />
              </svg>
              <svg
                v-else
                class="h-5 w-5 shrink-0 text-brand-600"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.7"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M20.5 11.8c0 4-3.8 7.2-8.5 7.2-1 0-1.9-.14-2.8-.4L4.6 20.2l1.3-3.3c-1.4-1.3-2.4-3-2.4-5.1C3.5 7.8 7.3 4.6 12 4.6s8.5 3.2 8.5 7.2Z" />
              </svg>
              <span class="text-sm font-medium text-ink">{{ item.label }}</span>
            </div>

            <img
              v-if="item.imageUrl"
              :src="item.imageUrl"
              :alt="`${item.label}二维码`"
              class="mt-2 w-full rounded-md border border-border bg-surface"
              loading="lazy"
            />

            <!-- 只有号没有图也照样能用：文本 + 一键复制 -->
            <div v-if="item.value" class="mt-2 flex items-center justify-between gap-2">
              <span class="truncate text-xs text-ink-soft">{{ item.value }}</span>
              <button type="button" class="chip shrink-0" @click="copy(item)">复制</button>
            </div>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
</template>
