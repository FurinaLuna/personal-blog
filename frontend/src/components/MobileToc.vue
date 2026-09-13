<script setup lang="ts">
/**
 * 移动端浮动目录。
 *
 * 背景：桌面端的目录挂在正文右侧（xl 断点以上才显示），移动端完全没有入口，
 * 长文章在手机上无法跳转章节 —— 这个组件补的就是这个缺口。
 *
 * 复用同一个 `TableOfContents`，所以目录数据结构、高亮逻辑、缩进规则
 * 与桌面端完全一致，不会出现「两端长得不一样」的维护负担。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import TableOfContents from '@/components/TableOfContents.vue'
import type { TocItem } from '@/utils/markdown'

const props = defineProps<{ items: TocItem[] }>()

const open = ref(false)
const button = ref<HTMLButtonElement | null>(null)

const hasToc = computed(() => props.items.length > 1)

function toggle(): void {
  open.value = !open.value
}

function close(): void {
  open.value = false
  // 关掉后把焦点还给触发按钮，键盘/读屏用户才不会「迷路」
  button.value?.focus()
}

/** 点了目录项就收起抽屉 —— 跳转已经发生，抽屉挡着正文只会碍事 */
function onNavClick(): void {
  if (open.value) close()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) close()
}

function lockScroll(locked: boolean): void {
  document.body.style.overflow = locked ? 'hidden' : ''
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  lockScroll(false)
})

// 文章切换时目录会整体重建，直接收起，避免出现「上一篇文章的抽屉还开着」
watch(
  () => props.items,
  () => {
    if (open.value) close()
  },
)
</script>

<template>
  <!-- xl 断点以下才显示：桌面端有常驻侧栏目录，两套并存只会互相干扰 -->
  <div v-if="hasToc" class="xl:hidden">
    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0"
        leave-active-class="transition duration-100 ease-in"
        leave-to-class="opacity-0"
      >
        <div
          v-if="open"
          class="fixed inset-0 z-40 bg-ink/40 backdrop-blur-sm"
          @click="close"
        />
      </Transition>

      <Transition
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="translate-y-4 opacity-0"
        leave-active-class="transition duration-150 ease-in"
        leave-to-class="translate-y-4 opacity-0"
      >
        <div
          v-if="open"
          class="card fixed inset-x-4 bottom-24 z-50 max-h-[60vh] overflow-auto p-4 shadow-xl"
          role="dialog"
          aria-modal="true"
          aria-label="文章目录"
        >
          <div class="mb-2 flex items-center justify-between">
            <p class="text-xs font-medium uppercase tracking-wide text-ink-faint">目录</p>
            <button
              type="button"
              class="text-xs text-ink-faint hover:text-ink"
              @click="close"
            >
              关闭
            </button>
          </div>
          <!-- 点击任意目录项后收起抽屉 -->
          <div @click="onNavClick">
            <TableOfContents :items="items" />
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 浮动触发按钮 -->
    <button
      ref="button"
      type="button"
      class="fixed bottom-6 right-5 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-brand-600 text-white shadow-lg transition-transform active:scale-95"
      :aria-expanded="open"
      aria-label="打开文章目录"
      @click="toggle"
    >
      <svg
        class="h-5 w-5"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
      >
        <path d="M4 6h16M4 12h10M4 18h7" />
      </svg>
    </button>
  </div>
</template>
