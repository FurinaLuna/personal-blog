/**
 * useTagInput 测试。
 *
 * 核心契约：add 的 trim/去重/上限三段语义、键盘交互（Enter / 逗号 /
 * Backspace 删末尾）、标签列表由调用方持有（桥接 computed 写回生效）。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { computed, ref } from 'vue'

import { useTagInput } from '@/composables/useTagInput'
import { useToast } from '@/composables/useToast'

const toast = useToast()

beforeEach(() => {
  toast.items.value = []
})

describe('useTagInput.add 语义', () => {
  it('添加时 trim 空白并清空输入框', () => {
    const tags = ref<string[]>([])
    const { input, add } = useTagInput(tags)
    input.value = '  Vue  '
    add(input.value)
    expect(tags.value).toEqual(['Vue'])
    expect(input.value).toBe('')
  })

  it('空白输入是空操作', () => {
    const tags = ref<string[]>([])
    const { add } = useTagInput(tags)
    add('   ')
    expect(tags.value).toEqual([])
  })

  it('重复标签静默忽略，不报错', () => {
    const tags = ref(['Vue'])
    const { add } = useTagInput(tags)
    add('Vue')
    expect(tags.value).toEqual(['Vue'])
    expect(toast.items.value).toHaveLength(0)
  })

  it('超过上限时拒绝并提示', () => {
    const tags = ref(['a', 'b'])
    const { add } = useTagInput(tags, { max: 2 })
    add('c')
    expect(tags.value).toEqual(['a', 'b'])
    expect(toast.items.value.at(-1)?.message).toBe('最多 2 个标签')
  })

  it('remove 按名称移除', () => {
    const tags = ref(['a', 'b', 'c'])
    const { remove } = useTagInput(tags)
    remove('b')
    expect(tags.value).toEqual(['a', 'c'])
  })
})

describe('useTagInput 键盘交互', () => {
  it('Enter 与逗号触发添加并阻止默认行为', () => {
    const tags = ref<string[]>([])
    const { input, onKeydown } = useTagInput(tags)
    const enter = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true })
    input.value = 'Vue'
    onKeydown(enter)
    expect(tags.value).toEqual(['Vue'])
    expect(enter.defaultPrevented).toBe(true)

    const comma = new KeyboardEvent('keydown', { key: ',', cancelable: true })
    input.value = 'Rust'
    onKeydown(comma)
    expect(tags.value).toEqual(['Vue', 'Rust'])
    expect(comma.defaultPrevented).toBe(true)
  })

  it('Backspace 在输入框为空时删掉最后一个标签', () => {
    const tags = ref(['a', 'b'])
    const { input, onKeydown } = useTagInput(tags)
    input.value = ''
    onKeydown(new KeyboardEvent('keydown', { key: 'Backspace' }))
    expect(tags.value).toEqual(['a'])
  })

  it('Backspace 在输入框非空时不删标签（正常编辑文本）', () => {
    const tags = ref(['a', 'b'])
    const { input, onKeydown } = useTagInput(tags)
    input.value = 'c'
    onKeydown(new KeyboardEvent('keydown', { key: 'Backspace' }))
    expect(tags.value).toEqual(['a', 'b'])
  })
})

describe('useTagInput 与表单字段桥接', () => {
  it('通过 computed 桥接时，写入反映到宿主表单对象', () => {
    const form = ref({ title: '', tags: [] as string[] })
    const tagsField = computed({
      get: () => form.value.tags,
      set: (next: string[]) => {
        form.value.tags = next
      },
    })
    const { add, remove } = useTagInput(tagsField)
    add('Vue')
    add('Rust')
    remove('Vue')
    expect(form.value.tags).toEqual(['Rust'])
  })
})
