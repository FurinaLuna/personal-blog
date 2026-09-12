<script setup lang="ts">
/**
 * 文章目录。
 *
 * 用 IntersectionObserver 而不是监听 scroll 事件：前者由浏览器在合成线程计算，
 * 不会因为滚动而频繁触发 JS，长文章的滚动流畅度差距很明显。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { TocItem } from '@/utils/markdown'

const props = defineProps<{ items: TocItem[] }>()

const activeId = ref('')
let observer: IntersectionObserver | null = null

/** 缩进层级：h1 不缩进，h4 最多缩进两级，避免长标题把目录挤没 */
const visible = computed(() => props.items.filter((item) => item.depth >= 2 && item.depth <= 4))

function indentClass(depth: number): string {
  if (depth <= 2) return 'pl-0'
  if (depth === 3) return 'pl-4'
  return 'pl-8'
}

function setupObserver(): void {
  observer?.disconnect()
  const headings = visible.value
    .map((item) => document.getElementById(item.id))
    .filter((element): element is HTMLElement => element !== null)
  if (!headings.length) return

  observer = new IntersectionObserver(
    (entries) => {
      // 取「当前可见的最靠上的标题」，比简单地取最后一条进入视口的更符合直觉
      const visibleEntries = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visibleEntries[0]?.target.id) {
        activeId.value = visibleEntries[0].target.id
      }
    },
    // 顶部留出固定头部的空间，底部收窄：这样"当前阅读位置"才落在视口上半部分
    { rootMargin: '-80px 0px -70% 0px', threshold: 0 },
  )

  headings.forEach((heading) => observer?.observe(heading))
}

/**
 * 跳到某个标题。
 *
 * 用 `<a href="#id">` 而不是 `<button>`：章节是有 URL 语义的，
 * 用户应该能右键复制链接、Cmd+点击、或者直接把 #section 发给别人。
 * 默认行为（原生跳转）会丢掉平滑滚动，所以这里拦下来手动画，
 * 再用 replaceState 把 hash 写回地址栏——不进历史栈，
 * 否则用户要按十几次返回键才能离开这篇文章。
 */
function jumpTo(event: MouseEvent, id: string): void {
  // 按住修饰键是「在新标签/窗口打开」的意图，交还给浏览器默认行为
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return

  const target = document.getElementById(id)
  if (!target) return
  event.preventDefault()
  // 与 style.css 的 scroll-padding-top 配合，标题不会被吸顶头部盖住
  target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  window.history.replaceState(null, '', `#${encodeURIComponent(id)}`)
  activeId.value = id
}

onMounted(() => {
  // 等一帧，确保 markdown 渲染出的标题已经进入 DOM 再建立观察
  requestAnimationFrame(() => {
    setupObserver()
    // 带 #章节 直接打开时补一次定位：SPA 渲染完 DOM 时原生锚点跳转已经错过了
    const hash = decodeURIComponent(window.location.hash.slice(1))
    if (hash && document.getElementById(hash)) {
      document.getElementById(hash)?.scrollIntoView({ block: 'start' })
      activeId.value = hash
    }
  })
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <nav v-if="visible.length > 1" aria-label="文章目录">
    <p class="mb-3 text-xs font-medium uppercase tracking-wide text-ink-faint">目录</p>
    <ul class="space-y-1 border-l border-border">
      <li v-for="item in visible" :key="item.id">
        <a
          :href="`#${encodeURIComponent(item.id)}`"
          class="-ml-px block w-full border-l-2 py-1 pr-2 text-left text-[13px] leading-snug transition-colors"
          :class="[
            indentClass(item.depth),
            activeId === item.id
              ? 'border-brand-500 font-medium text-brand-600'
              : 'border-transparent text-ink-soft hover:text-ink',
          ]"
          @click="jumpTo($event, item.id)"
        >
          {{ item.text }}
        </a>
      </li>
    </ul>
  </nav>
</template>
