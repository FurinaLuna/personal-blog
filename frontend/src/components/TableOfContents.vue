<script setup lang="ts">
/**
 * 文章目录。
 *
 * 三件事分层：
 * - **结构**：平铺的 `TocItem[]` 由 `useTocTree` 在消费侧派生成树并管折叠
 *   （`renderMarkdown()` 的输入输出契约保持不变，树不反向污染渲染管线）；
 * - **高亮**：用 IntersectionObserver 而不是监听 scroll —— 前者由浏览器在合成
 *   线程计算，不会因为滚动频繁触发 JS，长文章的流畅度差距很明显；
 * - **渲染**：本组件只管把 `visibleNodes` 画成 `<ul>`。
 *
 * 桌面侧栏与移动端抽屉复用同一个组件，所以两端的层级视觉、折叠行为、
 * 高亮规则天然一致，不靠人工同步。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useTocTree } from '@/composables/useTocTree'
import type { TocItem } from '@/utils/markdown'

const props = defineProps<{ items: TocItem[] }>()

const activeId = ref('')
const root = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null

/** 缩进层级：h1 不缩进，h4 最多缩进两级，避免长标题把目录挤没。 */
function indentClass(depth: number): string {
  if (depth <= 2) return 'pl-0'
  if (depth === 3) return 'pl-4'
  return 'pl-8'
}

const tree = useTocTree({ items: () => props.items })

/**
 * 显示门槛：**「这篇文章有没有可看的目录」，不是「当前展开了几行」**。
 *
 * 这里刻意不用 `tree.visibleNodes.value.length > 1`：折叠状态会改变可见行数，
 * 用户一收起分支目录就会整块消失（而且再也没有入口展开回来）。
 * 判据换成「不折叠时该有几行」——与改造前 `visible.length > 1` 的语义逐字一致。
 */
const hasToc = computed(() => {
  const items = props.items.filter((item) => item.depth >= 2 && item.depth <= 4)
  return items.length > 1
})

/**
 * 层级视觉：h2 最重、h3 常规、h4 最轻（现状问题 T1 —— 原先三级都是
 * `text-[13px]`、只有缩进不同，长文目录「一眼望不到结构」）。
 *
 * 颜色单独走 `linkClass`：active 态三种层级都用同一个品牌色 + 中粗，
 * 「当前读到哪」是一眼可见的强信号，不该被层级差稀释。
 */
function levelClass(depth: number): string {
  if (depth <= 2) return 'text-[13px] font-medium'
  if (depth === 3) return 'text-[13px]'
  return 'text-xs'
}

/** 非 active 态的文字颜色：h4 最淡，越深越退后。 */
function linkClass(depth: number): string {
  return depth >= 4 ? 'text-ink-faint' : 'text-ink-soft'
}

function isCollapsed(id: string): boolean {
  return tree.isCollapsed(id)
}

/** 只要还有任何一个可折叠节点是展开的，按钮就显示「全部收起」。 */
const allCollapsed = computed(
  () => !tree.visibleNodes.value.some((entry) => entry.node.children.length && !isCollapsed(entry.node.item.id)),
)

/**
 * 全部展开 / 全部收起。
 *
 * 不进 composable：它只在目录这一处用。
 *
 * 两个方向各自有个坑，这里都处理了：
 *
 * - **展开**必须多轮跑。`visibleNodes` 只包含「路径上没人收起」的节点，
 *   所以一轮快照展开不完：h2 展开后 h3 才出现，h3 展开后 h4 才出现。
 *   只跑一轮会留下「h3 出现但仍收着」的半开状态，而且此时 `allCollapsed`
 *   已经算成 false、按钮变成「全部收起」——用户再点一次就把 h2 又收回去，
 *   看起来像按钮坏了。层级上限是 3，循环最多跑两三轮就收敛。
 * - **收起**只能收可见的那些折叠点。若遍历整棵树去收，那些「本来就收着、
 *   于是没渲染」的深层节点会被顺手展开，用户下次点开父节点会看到它自己开了一半。
 *
 * 两处都先对快照取 `[...]`：`tree.visibleNodes` 会随 `toggle` 立刻重算，
 * 边遍历边变会漏掉后面那几项。
 */
function toggleAll(): void {
  if (allCollapsed.value) {
    for (let pass = 0; pass < 4; pass += 1) {
      const pending = tree.visibleNodes.value.filter(
        (entry) => entry.node.children.length && isCollapsed(entry.node.item.id),
      )
      if (pending.length === 0) break
      for (const entry of pending) tree.toggle(entry.node.item.id)
    }
    return
  }
  for (const entry of [...tree.visibleNodes.value]) {
    if (entry.node.children.length && !isCollapsed(entry.node.item.id)) tree.toggle(entry.node.item.id)
  }
}

function setupObserver(): void {
  observer?.disconnect()
  const headings = props.items
    .filter((item) => item.depth >= 2 && item.depth <= 4)
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
  // 与 base.css 的 scroll-padding-top 配合，标题不会被吸顶头部盖住
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
      tree.revealActive(hash)
    }
  })
})

/**
 * 文章换了一篇时重建观察器。
 *
 * 这是真实踩过的坑：`onMounted` 只会在组件**首次挂载**时跑一次，而从
 * 文章 A 点进文章 B 时（下一篇 / 系列 / 相关阅读）走的是同一个路由记录，
 * `App.vue` 的 `<component :is="Page" />` 没有 `:key`，Vue 会复用组件实例、
 * 不重新挂载。正文由 `v-html` 整体换掉，可观察器还盯着**已经被移除的旧标题**，
 * 结果是跳转之后目录高亮彻底不动了，必须整页刷新才恢复。
 *
 * `nextTick` 而不是 `requestAnimationFrame`：这里要的是「v-html 的新节点
 * 已经进 DOM」，nextTick 正是这个语义；rAF 只保证「下一帧」，
 * 在快速连续跳转时有可能早于 DOM 更新。
 *
 * 折叠状态一并重置：上一篇的折叠记忆带到下一篇，会让人以为目录缺了章节
 * （D-目录-3 默认「不跨篇记忆」）。
 */
watch(
  () => props.items,
  () => {
    activeId.value = ''
    tree.resetCollapse()
    void nextTick(() => setupObserver())
  },
)

/**
 * 高亮项自动滚入侧栏可视区（现状问题 T3）。
 *
 * `block: 'nearest'` 是关键：用 `'start'` 会把**整个页面**带着跳一下。
 * 只在侧栏自身确实可滚动时才做——移动端抽屉 / 短目录本来就能一眼看完，
 * 多做一次滚动只会制造无谓的抖动。
 */
watch(activeId, (id) => {
  // 空态（切文章时主动清空）不展开也不滚动：那时候 `visibleNodes` 还是新文章的，
  // 拿旧 id 去 reveal 只会白跑一次
  if (!id) return
  tree.revealActive(id)
  void nextTick(() => {
    const container = root.value
    if (!container || container.scrollHeight <= container.clientHeight) return
    const link = container.querySelector<HTMLElement>(`[data-toc-id="${CSS.escape(id)}"]`)
    link?.scrollIntoView({ block: 'nearest' })
  })
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <nav v-if="hasToc" ref="root" aria-label="文章目录">
    <div class="mb-3 flex items-center justify-between gap-2">
      <p class="text-xs font-medium text-ink-faint">目录</p>
      <button
        type="button"
        class="text-xs text-ink-faint transition-colors hover:text-ink"
        @click="toggleAll"
      >
        {{ allCollapsed ? '全部展开' : '全部收起' }}
      </button>
    </div>

    <ul class="space-y-1 border-l border-border">
      <li v-for="entry in tree.visibleNodes.value" :key="entry.node.item.id">
        <div class="flex items-stretch">
          <a
            :href="`#${encodeURIComponent(entry.node.item.id)}`"
            :data-toc-id="entry.node.item.id"
            :aria-current="activeId === entry.node.item.id ? 'location' : undefined"
            class="-ml-px block w-full border-l-2 py-1 pr-2 text-left leading-snug transition-colors"
            :class="[
              indentClass(entry.depth),
              levelClass(entry.depth),
              activeId === entry.node.item.id
                ? 'border-brand-500 text-brand-600'
                : `border-transparent hover:text-ink ${linkClass(entry.depth)}`,
            ]"
            @click="jumpTo($event, entry.node.item.id)"
          >
            {{ entry.node.item.text }}
          </a>
          <button
            v-if="entry.node.children.length"
            type="button"
            :aria-expanded="!isCollapsed(entry.node.item.id)"
            :aria-label="`${isCollapsed(entry.node.item.id) ? '展开' : '收起'}${entry.node.item.text}`"
            class="shrink-0 px-1 text-ink-faint transition-colors hover:text-ink"
            @click="tree.toggle(entry.node.item.id)"
          >
            <svg
              class="h-3 w-3 transition-transform"
              :class="{ 'rotate-90': !isCollapsed(entry.node.item.id) }"
              style="transition-duration: var(--duration-fast)"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="m9 18 6-6-6-6" />
            </svg>
          </button>
        </div>
      </li>
    </ul>
  </nav>
</template>
