<script setup lang="ts">
/**
 * 全局「电梯栏」：右下角的悬浮按钮列（联系站长 + 返回顶部）。
 *
 * ## 为什么挂在 DefaultLayout 而不是文章详情页
 *
 * 站内任何一页滚长了都需要「回到顶部」，而「联系站长」是全局信息（页脚里也有，
 * 但要滚到底才看得见）。挂在公开布局上一次覆盖全部公开页 —— 计划 §3.1 决策
 * D-电梯-1；AdminLayout 不加：后台既没有联系站长的场景，也不该往编辑区上压浮层。
 *
 * ## 为什么「目录」不在这一列里
 *
 * 桌面端目录侧栏常驻、移动端已有 MobileToc 浮动按钮（§3.2）。再加一个入口只会让
 * 右下角的悬浮件互相打架，所以本组件连这颗按钮都不渲染。
 *
 * ## 滚动位置从哪来
 *
 * 复用 `useScrollY()` 的**共享** window scroll 监听（rAF 节流）。自己再挂一个
 * scroll 监听会让同一个滚动帧里跑两遍回调，而滚动是全站最高频的用户事件。
 */
import { computed } from 'vue'

import ContactQrcode from '@/components/ContactQrcode.vue'
import { useScrollY } from '@/composables/useScrollY'

/**
 * 返回顶部的出现阈值（px）。
 *
 * 约等于半屏正文：太早出现会一直占着右下角，太晚则用户想起来要回顶时已经找不到它
 * （§3.6 决策 D-电梯-2）。提成常量而不是内联字面量，是为了让「阈值」这个概念在
 * 组件里只有一个出处，改的时候不会漏改注释。
 */
const TOP_THRESHOLD = 300

const { y } = useScrollY()

const showTop = computed(() => y.value > TOP_THRESHOLD)

function scrollToTop(): void {
  // smooth 而不是瞬移：读者滚了很长一段之后，瞬间回到顶部会让人彻底失去位置感
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
</script>

<template>
  <!-- 移动端 bottom-24 = 96px = 24（底边距）+ 48（MobileToc 目录按钮）+ 12（间距）：
       电梯栏整体浮在目录按钮正上方。桌面端（xl 以上）没有那颗按钮，用 bottom-28。
       数值来自计划 §3.4 —— 两边是纯 CSS 常量约定，电梯栏读不到 MobileToc 的状态，
       也不该为了一件排布的事去跨组件通信。

       关于 z-40（对计划 §3.4 的一处有意修正）：**容器自己就是一个层叠上下文**，
       弹层那层的 z-50 被困在容器内部，压不过容器之外的同级元素 —— 所以「弹层 z-50 >
       目录按钮 z-40」在 CSS 上并不成立。真正的顺序由**容器之间**的 z 值决定：
       正文（无 z-index）< MobileToc 遮罩 z-30 < 本容器 z-40 < MobileToc 抽屉面板 z-50。
       即「悬浮按钮在遮罩之上、模态抽屉在一切之上」，完整层叠表见 MobileToc.vue 模板顶部注释。 -->
  <div class="fixed bottom-24 right-5 z-40 flex flex-col gap-2 xl:bottom-28">
    <!-- 联系站长：配置为空时 ContactQrcode 内部整块不渲染（连按钮一起） -->
    <ContactQrcode />

    <!-- 只淡入淡出、不做位移：位移会让按钮在动画途中从指针下方滑过，诱使误点
         （§3.3）。duration 走令牌，暗色模式无需 dark: 变体（tokens.css 双套变量）。 -->
    <Transition
      enter-active-class="transition-opacity duration-[var(--duration-fast)] ease-out"
      enter-from-class="opacity-0"
      leave-active-class="transition-opacity duration-[var(--duration-fast)] ease-in"
      leave-to-class="opacity-0"
    >
      <button
        v-if="showTop"
        type="button"
        class="btn--icon flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface text-ink-soft shadow-lg transition-colors hover:text-brand-600"
        aria-label="返回顶部"
        @click="scrollToTop"
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
          <path d="M12 19V5M6 11l6-6 6 6" />
        </svg>
      </button>
    </Transition>
  </div>
</template>
