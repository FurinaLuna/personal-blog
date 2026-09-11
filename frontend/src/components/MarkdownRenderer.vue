<script setup lang="ts">
/**
 * Markdown 正文渲染。
 *
 * 组件只接收**已经消毒过的 HTML**，渲染逻辑放在 utils/markdown.ts 里。
 * 这样做的原因：详情页需要同时拿到 HTML 和目录（一次渲染两处消费），
 * 如果渲染发生在组件内部，父组件就拿不到目录数据了。
 *
 * 用 v-html 是安全的——内容已经过 DOMPurify 白名单过滤（见 utils/markdown.ts），
 * 这是全站唯一允许使用 v-html 的地方。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { useToast } from '@/composables/useToast'

defineProps<{ html: string }>()

const toast = useToast()
const root = ref<HTMLElement | null>(null)

/** 复制成功后的反馈时长。太短会让人怀疑没生效，太长会干扰连续复制多段代码。 */
const DONE_MS = 1400

/**
 * 把代码写进剪贴板。
 *
 * `navigator.clipboard` 只在安全上下文（HTTPS / localhost）可用；
 * 局域网 IP 访问开发服务器时它会变成 undefined，所以要降级到隐藏 textarea 方案。
 */
async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒或焦点问题，继续走降级路径
    }
  }

  const helper = document.createElement('textarea')
  helper.value = text
  helper.setAttribute('readonly', '')
  helper.style.position = 'fixed'
  helper.style.opacity = '0'
  document.body.append(helper)
  helper.select()
  const ok = document.execCommand('copy')
  helper.remove()
  return ok
}

/** 事件委托：整块正文只挂一个监听器，几十个代码块也只有一个监听器开销。 */
async function handleClick(event: MouseEvent): Promise<void> {
  const target = event.target as HTMLElement | null
  const button = target?.closest<HTMLButtonElement>('.code-copy')
  if (!button || !root.value?.contains(button)) return

  // 从按钮往上找最近的代码块，取其中的 code 文本（高亮后的 textContent 就是源码本身）
  const block = button.closest<HTMLElement>('.code-block')?.querySelector('code')
  const source = block?.textContent ?? ''
  if (!source) {
    toast.error('没有可复制的内容')
    return
  }

  const ok = await copyText(source)
  if (!ok) {
    toast.error('浏览器不允许自动复制，请手动选中复制')
    return
  }

  // 按钮反馈：换文案 + 变色，1.4 秒后还原
  const original = button.textContent
  button.textContent = '已复制'
  button.dataset.state = 'done'
  window.setTimeout(() => {
    button.textContent = original
    delete button.dataset.state
  }, DONE_MS)
}

onMounted(() => root.value?.addEventListener('click', handleClick))
onBeforeUnmount(() => root.value?.removeEventListener('click', handleClick))
</script>

<template>
  <div ref="root">
    <!-- eslint-disable-next-line vue/no-v-html -- 内容已在 utils/markdown.ts 里经 DOMPurify 消毒 -->
    <div class="prose prose-slate max-w-none dark:prose-invert" v-html="html" />
  </div>
</template>
