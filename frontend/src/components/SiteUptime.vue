<script setup lang="ts">
/**
 * 页脚那行「自从搭建已经历 N 天 HH 小时 MM 分 SS 秒」。
 *
 * 对应旧站页脚加载的 `/js/timeDate.js`（`#timeDate` + `#times` 两个 span）。
 * 注意它**不是时钟**：起点写死在脚本里（2025-11-14 13:14:52，Asia/Shanghai），
 * 文案里那句「FurinaLuna-Laboratory 自从搭建」说明了它的语义 —— 建站计时。
 * 计算与格式化都在 `@/utils/uptime`（纯函数，单测覆盖边界），这里只管"每秒刷新"。
 *
 * 每 1 秒重算一次而不是"给计数器 +1"：前者从固定起点算，页面挂起（后台标签页
 * 被节流）后回到前台会**立刻跳到正确值**；后者会越走越慢。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { formatUptime, SITE_EPOCH_MS, splitUptime } from '@/utils/uptime'

const elapsedMs = ref(Date.now() - SITE_EPOCH_MS)
const uptime = computed(() => formatUptime(splitUptime(elapsedMs.value)))

let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  elapsedMs.value = Date.now() - SITE_EPOCH_MS
  timer = setInterval(() => {
    elapsedMs.value = Date.now() - SITE_EPOCH_MS
  }, 1000)
})

// 不清理的话组件卸载后定时器会一直跑 —— 页脚在每个页面都有，
// 路由切换频繁时就是稳定的定时器泄漏
onBeforeUnmount(() => {
  if (timer !== undefined) clearInterval(timer)
})
</script>

<template>
  <p class="text-xs text-ink-faint">本站自从搭建已经历 {{ uptime }}</p>
</template>
