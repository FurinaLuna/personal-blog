<script setup lang="ts">
/**
 * 阅读进度条：固定在视口顶部的 2px 细条，随页面滚动前进。
 *
 * 用 JS 监听而不是 CSS `animation-timeline: scroll()`：
 * 后者在 Safari 上要到 26 才支持，降级路径反而比直接写 JS 更长。
 * 这里用 rAF 节流 + passive listener，滚动时不会在主线程上做多余的事。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

const progress = ref(0)

let ticking = false

function update(): void {
  ticking = false
  const doc = document.documentElement
  const total = doc.scrollHeight - window.innerHeight
  // 页面不够一屏时 total <= 0，进度恒为 0（条不显示，见模板里的 v-show）
  progress.value = total > 0 ? Math.min(1, window.scrollY / total) : 0
}

function onScroll(): void {
  if (ticking) return
  ticking = true
  requestAnimationFrame(update)
}

onMounted(() => {
  update()
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
})
</script>

<template>
  <div
    v-show="progress > 0"
    class="pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 bg-transparent"
    role="progressbar"
    :aria-valuenow="Math.round(progress * 100)"
    aria-valuemin="0"
    aria-valuemax="100"
  >
    <div
      class="h-full origin-left bg-brand-500 transition-transform duration-75 ease-linear"
      :style="{ transform: `scaleX(${progress})` }"
    />
  </div>
</template>
