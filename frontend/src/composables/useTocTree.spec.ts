/**
 * useTocTree 测试。
 *
 * 重点覆盖三类最容易写错的地方：
 * 1. 挂载规则——depth 跳级（h2 → h4）、depth 回退、首个节点不是 h2 时的顶层处理；
 * 2. 折叠状态的增删语义——尤其是「收起的节点自己仍在 visibleNodes 里」
 *    和 `revealActive` 只展开不收起；
 * 3. 换文章后的响应式重建与 `resetCollapse` 的必要性。
 */
import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import {
  buildTocTree,
  useTocTree,
  type TocNode,
  type UseTocTreeReturn,
} from '@/composables/useTocTree'
import type { TocItem } from '@/utils/markdown'

/** depth 由 id 的第一段给出（'2-1' → h2），这样用例读起来和目录结构能直接对上。 */
function toc(...ids: string[]): TocItem[] {
  return ids.map((id) => ({ id, text: id, depth: Number(id.split('-')[0]) }))
}

/** 把树压成 `['2-1', ['3-1', ['4-1']]]` 这样的嵌套数组：断言结构比逐个比节点对象好读。 */
function outline(nodes: TocNode[]): unknown[] {
  return nodes.map((node) =>
    node.children.length > 0 ? [node.item.id, outline(node.children)] : node.item.id,
  )
}

/** visibleNodes 里的 id 列表（多数断言只关心这个）。 */
function visibleIds(tree: UseTocTreeReturn): string[] {
  return tree.visibleNodes.value.map((entry) => entry.node.item.id)
}

/** visibleNodes 里的 depth 列表，用来钉住「depth 是标题自身层级」的语义。 */
function visibleDepths(tree: UseTocTreeReturn): number[] {
  return tree.visibleNodes.value.map((entry) => entry.depth)
}

describe('buildTocTree 挂载规则', () => {
  it('h2/h3/h4 按层级嵌套，同级后来者挂在最近的父节点下', () => {
    expect(outline(buildTocTree(toc('2-1', '3-1', '4-1', '3-2')))).toEqual([
      ['2-1', [['3-1', ['4-1']], '3-2']],
    ])
  })

  it('同 depth 连续多项互为兄弟，不互相嵌套', () => {
    expect(outline(buildTocTree(toc('2-1', '2-2', '3-1')))).toEqual(['2-1', ['2-2', ['3-1']]])
  })

  it('depth 跳级：h2 直接接 h4，h4 挂到 h2 下（不要求中间层级存在）', () => {
    expect(outline(buildTocTree(toc('2-1', '4-1')))).toEqual([['2-1', ['4-1']]])
  })

  it('跳级后再出现 h3：回退到最近的更小 depth（h2），而不是成为 h4 的子节点', () => {
    expect(outline(buildTocTree(toc('2-1', '4-1', '3-1')))).toEqual([['2-1', ['4-1', '3-1']]])
  })

  it('首个节点 depth > 2（文章直接从 h3 起）时按顶层处理，不被吞掉', () => {
    expect(outline(buildTocTree(toc('3-1', '4-1', '3-2')))).toEqual([['3-1', ['4-1']], '3-2'])
  })

  it('首个节点是 h4 时，后面的 h2 仍然回到顶层', () => {
    expect(outline(buildTocTree(toc('4-1', '2-1')))).toEqual(['4-1', '2-1'])
  })

  it('depth 回退：h4 → h2 之后出现的 h3 挂到新的 h2 下，不挂到旧分支', () => {
    const nodes = buildTocTree(toc('2-1', '3-1', '4-1', '2-2', '3-2'))
    expect(outline(nodes)).toEqual([['2-1', [['3-1', ['4-1']]]], ['2-2', ['3-2']]])
    expect(nodes[1].children[0].item.id).toBe('3-2')
  })

  it('空数组返回空树', () => {
    expect(buildTocTree([])).toEqual([])
  })

  it('纯函数不做层级过滤：h1 也会参与成树（过滤由 useTocTree 负责）', () => {
    expect(outline(buildTocTree(toc('1-1', '2-1')))).toEqual([['1-1', ['2-1']]])
  })

  it('节点直接引用原 TocItem，不复制对象', () => {
    const items = toc('2-1')
    expect(buildTocTree(items)[0].item).toBe(items[0])
  })
})

describe('useTocTree 默认折叠与可见视图', () => {
  it('默认只有「depth >= 3 且有子节点」的节点收起，h2 展开、叶子不受影响', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2') })
    expect(tree.isCollapsed('3-1')).toBe(true)
    expect(tree.isCollapsed('2-1')).toBe(false)
    expect(tree.isCollapsed('4-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '2-2'])
  })

  it('收起的节点自己仍在 visibleNodes 里（否则它的展开按钮会一起消失）', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1') })
    const entry = tree.visibleNodes.value.find((item) => item.node.item.id === '3-1')
    expect(entry?.node.item.text).toBe('3-1')
    expect(entry?.node.children.map((child) => child.item.id)).toEqual(['4-1'])
  })

  it('自定义策略对叶子返回 true 时也不入折叠集合，叶子照常显示', () => {
    const tree = useTocTree({
      items: () => toc('2-1', '3-1', '2-2'),
      defaultCollapsed: () => true,
    })
    expect(tree.isCollapsed('2-1')).toBe(true)
    expect(tree.isCollapsed('2-2')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '2-2'])
  })

  it('只渲染 h2–h4：h1 与 h5 都不进目录，且 h1 不会把后面的章节吞成子节点', () => {
    const tree = useTocTree({
      items: () => [
        { id: '1-1', text: 'h1', depth: 1 },
        ...toc('2-1', '3-1'),
        { id: '5-1', text: 'h5', depth: 5 },
      ],
    })
    expect(visibleIds(tree)).toEqual(['2-1', '3-1'])
    expect(visibleDepths(tree)).toEqual([2, 3])
  })

  it('visibleNodes 的 depth 是标题自身层级，跳级时如实给出 2 和 4', () => {
    const tree = useTocTree({ items: () => toc('2-1', '4-1') })
    expect(visibleDepths(tree)).toEqual([2, 4])
  })

  it('空文章：visibleNodes 为空数组', () => {
    const tree = useTocTree({ items: () => [] })
    expect(tree.visibleNodes.value).toEqual([])
  })

  it('单标题文章：visibleNodes 长度为 1（是否显示目录由组件的门槛决定）', () => {
    const tree = useTocTree({ items: () => toc('2-1') })
    expect(visibleIds(tree)).toHaveLength(1)
  })

  it('切换文章后必须 resetCollapse：否则新树直接沿用旧文章的折叠 id', () => {
    const items = ref<TocItem[]>(toc('2-1', '3-1', '4-1'))
    const tree = useTocTree({ items: () => items.value })
    expect(visibleIds(tree)).toEqual(['2-1', '3-1'])

    items.value = toc('2-9', '3-9', '4-9')
    // 新文章的 3-9 还没被折叠（默认策略只在初始化和 resetCollapse 时套用）
    expect(visibleIds(tree)).toEqual(['2-9', '3-9', '4-9'])
  })
})

describe('useTocTree 折叠交互', () => {
  it('toggle 收起父节点：子树从 visibleNodes 消失，父节点自己还在', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2') })
    tree.toggle('2-1')
    expect(tree.isCollapsed('2-1')).toBe(true)
    expect(visibleIds(tree)).toEqual(['2-1', '2-2'])
  })

  it('toggle 再点一次展开，子树回来', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1') })
    tree.toggle('2-1')
    expect(visibleIds(tree)).toEqual(['2-1'])
    tree.toggle('2-1')
    expect(tree.isCollapsed('2-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1'])
  })

  it('toggle 展开默认收起的节点', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1') })
    tree.toggle('3-1')
    expect(tree.isCollapsed('3-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '4-1'])
  })

  it('toggle 对没有子节点的项是无害空操作（不会让它自己被标记为收起）', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1') })
    const before = visibleIds(tree)
    tree.toggle('3-1')
    expect(tree.isCollapsed('3-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(before)
  })
})

describe('useTocTree revealActive', () => {
  it('展开 active 的全部祖先链，包括用户手动收起的祖先（计划 §2.1 的例外条款）', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2') })
    tree.toggle('2-1') // 用户手动把整个 h2 分支收起来
    expect(visibleIds(tree)).toEqual(['2-1', '2-2'])

    tree.revealActive('4-1')
    expect(tree.isCollapsed('2-1')).toBe(false)
    expect(tree.isCollapsed('3-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '4-1', '2-2'])
  })

  it('只展开不收起：不影响祖先链之外的分支，也不做「全部展开」', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2', '3-2', '4-2') })
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '2-2', '3-2'])

    tree.revealActive('4-1')
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '4-1', '2-2', '3-2'])
    expect(tree.isCollapsed('3-2')).toBe(true)
  })

  it('从不往折叠集合里加 id：用户已展开的分支不会被收起', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2') })
    tree.toggle('3-1') // 展开默认收起的 3-1
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '4-1', '2-2'])

    tree.revealActive('2-2')
    expect(tree.isCollapsed('3-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '4-1', '2-2'])
  })

  it('active 自身在折叠集合里时也会被展开', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1') })
    tree.revealActive('3-1') // 3-1 默认收起
    expect(tree.isCollapsed('3-1')).toBe(false)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '4-1'])

    tree.toggle('2-1')
    expect(tree.isCollapsed('2-1')).toBe(true)
    tree.revealActive('2-1')
    expect(tree.isCollapsed('2-1')).toBe(false)
  })

  it('id 不在树里时是 no-op：不抛错、不动折叠状态', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2') })
    tree.toggle('2-1')
    const before = visibleIds(tree)

    expect(() => tree.revealActive('not-in-toc')).not.toThrow()
    expect(tree.isCollapsed('2-1')).toBe(true)
    expect(visibleIds(tree)).toEqual(before)
  })

  it('toggle 一个不存在的 id 也是 no-op', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1') })
    expect(() => tree.toggle('not-in-toc')).not.toThrow()
    expect(visibleIds(tree)).toEqual(['2-1', '3-1'])
  })
})

describe('useTocTree resetCollapse 与响应式', () => {
  it('resetCollapse 把手动改过的折叠状态还原为默认策略', () => {
    const tree = useTocTree({ items: () => toc('2-1', '3-1', '4-1', '2-2') })
    tree.toggle('3-1') // 展开默认收起的
    tree.toggle('2-1') // 收起默认展开的
    expect(tree.isCollapsed('2-1')).toBe(true)
    expect(tree.isCollapsed('3-1')).toBe(false)

    tree.resetCollapse()
    expect(tree.isCollapsed('2-1')).toBe(false)
    expect(tree.isCollapsed('3-1')).toBe(true)
    expect(visibleIds(tree)).toEqual(['2-1', '3-1', '2-2'])
  })

  it('items 变了 visibleNodes 立即跟着变（切文章）', () => {
    const items = ref<TocItem[]>(toc('2-1', '3-1', '4-1'))
    const tree = useTocTree({ items: () => items.value })
    expect(visibleIds(tree)).toEqual(['2-1', '3-1'])

    items.value = toc('2-9', '3-9', '4-9', '2-8')
    // 旧文章的折叠 id 对新树不生效，新树在 resetCollapse 之前是「全展开」的
    expect(visibleIds(tree)).toEqual(['2-9', '3-9', '4-9', '2-8'])
  })

  it('切文章后 resetCollapse 清掉旧 id 并套用新文章的默认策略', () => {
    const items = ref<TocItem[]>(toc('2-1', '3-1', '4-1'))
    const tree = useTocTree({ items: () => items.value })
    expect(visibleIds(tree)).toEqual(['2-1', '3-1'])

    items.value = toc('2-9', '3-9', '4-9')
    tree.resetCollapse()
    expect(tree.isCollapsed('3-1')).toBe(false) // 旧文章的 id 已被清掉
    expect(tree.isCollapsed('3-9')).toBe(true) // 新文章重新套用默认策略
    expect(visibleIds(tree)).toEqual(['2-9', '3-9'])
  })
})
