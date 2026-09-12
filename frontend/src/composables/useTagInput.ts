/**
 * 标签输入交互的通用封装。
 *
 * 「回车/逗号确认、去重、上限拦截、Backspace 删最后一个」是一套完整的
 * 键盘交互语义，原先内联在文章编辑器里；抽出后任何需要自由打标签的
 * 表单（如未来的作品集、书单）都能复用。
 *
 * 设计：标签列表由调用方持有（传入 ref，通常是表单字段的桥接 computed），
 * 这样标签仍然属于表单状态——本地草稿快照、提交 payload 都不需要改结构。
 */
import { ref, type Ref } from 'vue'

import { useToast } from '@/composables/useToast'

export interface TagInput {
  /** 输入框文本（v-model） */
  input: Ref<string>
  /** 添加一个标签：trim、去重、超限拦截 */
  add: (name: string) => void
  /** 移除一个标签 */
  remove: (name: string) => void
  /** 键盘交互：Enter / 逗号 添加，Backspace 在输入框为空时删掉最后一个 */
  onKeydown: (event: KeyboardEvent) => void
}

export function useTagInput(tags: Ref<string[]>, options: { max?: number } = {}): TagInput {
  const toast = useToast()
  const max = options.max ?? 10
  const input = ref('')

  function add(name: string): void {
    const value = name.trim()
    if (!value) return
    // 重复标签静默忽略：用户按两次回车不应该看到报错
    if (tags.value.includes(value)) {
      input.value = ''
      return
    }
    if (tags.value.length >= max) {
      toast.error(`最多 ${max} 个标签`)
      return
    }
    tags.value = [...tags.value, value]
    input.value = ''
  }

  function remove(name: string): void {
    tags.value = tags.value.filter((item) => item !== name)
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      add(input.value)
    } else if (event.key === 'Backspace' && !input.value && tags.value.length) {
      tags.value = tags.value.slice(0, -1)
    }
  }

  return { input, add, remove, onKeydown }
}
