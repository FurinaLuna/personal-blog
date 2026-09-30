/**
 * 目录树：把 `renderMarkdown()` 产出的平铺 `TocItem[]` 派生成「树 → 折叠状态 → 扁平可见视图」。
 *
 * 为什么在消费侧派生而不是改数据源头：`TocItem` 和渲染管线（正文锚点 id、
 * 移动端抽屉、将来的锚点分享）是公共契约，而「嵌套层级 + 折叠」只是目录这一个
 * 消费者自己的展示需要。把树结构塞进 `TocItem` 会逼着所有消费者跟着改结构，
 * 所以源数据保持扁平，这里只做只读派生。
 *
 * 消费方（`TableOfContents.vue`）必须注意两件事：
 * 1. **显示门槛仍用未折叠的过滤结果判断**。「单标题文章不显示目录」看的是
 *    「这篇文章有没有可看的目录」，不是「当前展开了几行」；若拿 `visibleNodes.length > 1`
 *    当门槛，用户一收起分支目录就会整块消失。这里的过滤规则与组件改造前的 `visible`
 *    逐字一致（h1 不入目录、h5/h6 同样不入），不折叠时条目数与它相等。
 * 2. **切换文章后必须调用 `resetCollapse()`**（D-目录-3：折叠状态不跨篇记忆）。
 *    折叠状态按标题 id 记忆，而「总结」「参考」这类标题在两篇文章里会生成同一个 id，
 *    不重置就会串状态——看起来像「新文章一打开就有章节是收起的」这种怪 bug。
 */
import { computed, ref, type ComputedRef } from 'vue'

import type { TocItem } from '@/utils/markdown'

export interface TocNode {
  item: TocItem
  children: TocNode[]
}

/**
 * 平铺 `TocItem[]` → 树。规则：depth 大的项挂到前面最近的更小 depth 节点下；
 * 首个节点若 depth > 2（文章直接从 h3 起），按顶层处理，防止整棵树被吞掉。
 *
 * 纯函数，**刻意不做层级过滤**：过滤属于「这个标题要不要出现在目录里」的消费侧
 * 策略，统一由 `useTocTree` 负责（见 `TOC_MIN_DEPTH` / `TOC_MAX_DEPTH`）。
 * 这样这个函数不依赖任何配置、不需要 Vue 环境就能直接单测。
 *
 * 用栈实现，O(n)：每个节点最多入栈一次、出栈一次，不做二次扫描。
 */
export function buildTocTree(items: TocItem[]): TocNode[] {
  const roots: TocNode[] = []
  /** 当前可能的祖先链，自底向上 depth 严格递增（栈顶最深）。 */
  const stack: TocNode[] = []

  for (const item of items) {
    const node: TocNode = { item, children: [] }

    // 回退到「最近的、depth 严格更小的节点」：depth 大于等于当前项的全部弹出。
    // h2 → h4 这种跳级天然成立（h4 直接挂到 h2 下，不需要中间层级存在）；
    // depth 回退（h4 → h2）时旧分支的节点全部出栈，后面的 h3 只会挂到新 h2 下。
    while (stack.length > 0 && stack[stack.length - 1].item.depth >= item.depth) {
      stack.pop()
    }

    // 栈空说明前面没有更浅的标题：当顶层。
    // 首个节点永远走这一支（哪怕它是 h3/h4）——这正是计划里
    // 「首个节点 depth > 2 按顶层处理，防止整棵树被吞掉」的落点。
    const parent = stack[stack.length - 1]
    if (parent) parent.children.push(node)
    else roots.push(node)

    stack.push(node)
  }

  return roots
}

/** 目录显示的最小层级：h1 是文章标题，不进目录（与改造前的组件过滤一致）。 */
const TOC_MIN_DEPTH = 2
/** 目录显示的最大层级：h5/h6 太细，进目录只会把侧栏挤爆。 */
const TOC_MAX_DEPTH = 4
/** 默认折叠的起始层级（D-目录-1 的默认取值：depth ≥ 3 的节点默认收起）。 */
const DEFAULT_COLLAPSE_DEPTH = 3

export interface UseTocTreeOptions {
  items: () => TocItem[]
  /** 初始折叠策略：depth >= 3 的节点默认收起（D-目录-1 的默认取值） */
  defaultCollapsed?: (node: TocNode) => boolean
}

export interface UseTocTreeReturn {
  /**
   * 渲染用扁平视图：按「收起状态」过滤后的可见节点，带层级信息。
   *
   * `depth` 是**标题自身的层级**（2 / 3 / 4，即 `item.depth` 的原值），
   * 不是「相对根节点的层级」——组件直接拿它映射 `pl-0 / pl-4 / pl-8`。
   * 之所以不换算成相对值：跳级（h2 → h4）时相对层级会随所在分支变化，
   * 同一个 h4 在不同分支里缩进不同，读起来反而乱；标题自己是几级就缩进几级。
   */
  visibleNodes: ComputedRef<Array<{ node: TocNode; depth: number }>>
  isCollapsed: (id: string) => boolean
  toggle: (id: string) => void
  /** 滚动高亮变化时调用：展开 active 项的全部祖先链 */
  revealActive: (id: string) => void
  /** 文章切换时重置折叠状态为默认策略（D-目录-3 默认「不跨篇记忆」） */
  resetCollapse: () => void
}

export function useTocTree(options: UseTocTreeOptions): UseTocTreeReturn {
  /** 默认策略要求 depth ≥ 3 **且有子节点**：叶子收起没有意义，见 `resetCollapse` 的二次兜底。 */
  const isDefaultCollapsed =
    options.defaultCollapsed ??
    ((node: TocNode) => node.children.length > 0 && node.item.depth >= DEFAULT_COLLAPSE_DEPTH)

  /**
   * 折叠集合存的是标题 id，不是节点引用：换文章重建树之后旧引用会失效，
   * 而 id 至少能和 `resetCollapse()` 配合着按文章清干净。
   */
  const collapsedIds = ref<Set<string>>(new Set())

  // 过滤放在这里而不是 buildTocTree 里：buildTocTree 保持纯粹的「扁平 → 树」语义。
  // 谓词与 TableOfContents.vue 原有的 `visible` 一致，保证 visibleNodes 的条目集合
  // 与组件的显示门槛判断的是同一批标题。
  const tree = computed(() =>
    buildTocTree(
      options.items().filter((item) => item.depth >= TOC_MIN_DEPTH && item.depth <= TOC_MAX_DEPTH),
    ),
  )

  /** id → 节点、id → 父节点。展开祖先链只需要向上走，一次遍历同时建两张表。 */
  const lookup = computed(() => {
    const nodes = new Map<string, TocNode>()
    const parents = new Map<string, TocNode>()
    const walk = (list: TocNode[]): void => {
      for (const node of list) {
        nodes.set(node.item.id, node)
        for (const child of node.children) parents.set(child.item.id, node)
        walk(node.children)
      }
    }
    walk(tree.value)
    return { nodes, parents }
  })

  const visibleNodes = computed<Array<{ node: TocNode; depth: number }>>(() => {
    const result: Array<{ node: TocNode; depth: number }> = []

    const walk = (list: TocNode[]): void => {
      for (const node of list) {
        // 收起的节点**自己照常输出**，只砍子树：它那一行还得留着渲染折叠按钮，
        // 否则「收起了 → 整行和按钮一起消失 → 再也打不开」。
        result.push({ node, depth: node.item.depth })
        if (collapsedIds.value.has(node.item.id)) continue
        walk(node.children)
      }
    }

    walk(tree.value)
    return result
  })

  function isCollapsed(id: string): boolean {
    return collapsedIds.value.has(id)
  }

  function toggle(id: string): void {
    const node = lookup.value.nodes.get(id)
    // 叶子没有「折叠」语义：收起一个没有下级的标题，它本身还是照常显示，
    // 用户点了按钮却什么都没发生，反而像 bug。组件侧已经用
    // `v-if="node.children.length"` 不渲染按钮，这里再兜一层，防止调用方误用。
    if (!node || node.children.length === 0) return

    const next = new Set(collapsedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    // 必须整体换新 Set：Set 的增删不是响应式的，原地改不会让 computed 重算
    collapsedIds.value = next
  }

  /**
   * 滚动高亮变化时调用：把 active 的**全部祖先链**（以及它自己）从折叠集合里删掉。
   *
   * 「只展开不收起」是刻意的：这个函数只做减法，**从不往折叠集合里加 id**。
   * 目录的形状只由用户点击（`toggle`）决定，滚动经过不该顺手收起别的分支
   * ——那也是「用户手动收起的分支，滚动时不替他重新打开」的反面：我们既不加，
   * 也不碰 active 祖先链之外的分支。
   *
   * 但祖先链必须打开：折叠节点的子树不会出现在 `visibleNodes` 里，
   * 若 active 落在某个折叠分支下，它连一行都渲染不出来，高亮自然不可见。
   * 同理 active 自身若在折叠集合里也要移除（计划 §2.1 点名的例外条款）。
   */
  function revealActive(id: string): void {
    const { nodes, parents } = lookup.value
    // id 不在当前目录里（例如正文已换、观察器还没重建）：什么都不做，不抛错
    if (!nodes.has(id)) return

    const next = new Set(collapsedIds.value)
    let changed = next.delete(id)
    let current = parents.get(id)
    while (current) {
      if (next.delete(current.item.id)) changed = true
      current = parents.get(current.item.id)
    }

    // 没有实际变化就不换引用：避免每次高亮变化都让 visibleNodes 白重算一遍
    if (changed) collapsedIds.value = next
  }

  /** 文章切换时重置为默认策略（D-目录-3：折叠状态不跨篇记忆）。 */
  function resetCollapse(): void {
    const next = new Set<string>()
    const walk = (list: TocNode[]): void => {
      for (const node of list) {
        // 只有「有子节点」的节点才可能被收起。即使自定义策略对叶子返回 true 也忽略：
        // 把叶子放进折叠集合会给出「它被收起了」的假信息，而它其实一直显示着。
        if (node.children.length > 0 && isDefaultCollapsed(node)) next.add(node.item.id)
        walk(node.children)
      }
    }
    walk(tree.value)
    collapsedIds.value = next
  }

  // 初始状态就是默认策略（D-目录-1）；之后每次换文章由组件显式调用 resetCollapse。
  resetCollapse()

  return { visibleNodes, isCollapsed, toggle, revealActive, resetCollapse }
}
