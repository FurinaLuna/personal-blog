<script setup lang="ts">
/**
 * 访问趋势折线图（纯 SVG，零图表库依赖）。
 *
 * 两条线共用一个 y 轴刻度：UV 恒 ≤ PV（去重不会变多），分开刻度反而
 * 误导。悬停提示用原生 `<title>`——框架 tooltip 要引依赖、手写定位，
 * 对「看看这天多少量」这个诉求是过度设计。
 */
import { computed } from 'vue'

import type { DailyViewStats } from '@/types'

const props = defineProps<{
  data: DailyViewStats[]
}>()

/** 视图盒尺寸：720×200，横向铺满容器（preserveAspectRatio + w-full）。 */
const WIDTH = 720
const HEIGHT = 200
const TOP = 14
const BOTTOM = 182 // 底线；以下留给 x 轴日期

/** 折线点坐标（两条线共享 x）。 */
const points = computed(() => {
  const count = props.data.length
  if (count === 0) return []
  const max = Math.max(...props.data.map((item) => item.views), 1)
  return props.data.map((item, index) => ({
    x: count === 1 ? WIDTH / 2 : (index / (count - 1)) * WIDTH,
    y: BOTTOM - (item.views / max) * (BOTTOM - TOP),
    uvY: BOTTOM - (item.unique_visitors / max) * (BOTTOM - TOP),
    date: item.date,
    views: item.views,
    uniqueVisitors: item.unique_visitors,
  }))
})

/** 把点列拼成 SVG path（M + L…）。单点时 path 为空串，只画点。 */
function toPath(key: 'y' | 'uvY'): string {
  return points.value
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(1)} ${point[key].toFixed(1)}`)
    .join(' ')
}

const viewPath = computed(() => toPath('y'))
const uvPath = computed(() => toPath('uvY'))

/** x 轴 5 个刻度（首、1/4、中、3/4、尾），日期格式 M/D。 */
const ticks = computed(() => {
  const count = points.value.length
  if (count === 0) return []
  const indices = [0, Math.round((count - 1) / 4), Math.round((count - 1) / 2), Math.round((3 * (count - 1)) / 4), count - 1]
  const seen = new Set<number>()
  return indices
    .filter((index) => {
      // 极短序列（如 1 天）多个刻度会落到同一点，去重
      if (seen.has(index) || !points.value[index]) return false
      seen.add(index)
      return true
    })
    .map((index) => {
      const [, month, day] = points.value[index].date.split('-')
      return { x: points.value[index].x, label: `${Number(month)}/${Number(day)}` }
    })
})

function tooltip(point: (typeof points.value)[number]): string {
  return `${point.date} · 阅读 ${point.views} · 访客 ${point.uniqueVisitors}`
}
</script>

<template>
  <p v-if="!data.length" class="py-10 text-center text-sm text-ink-faint">
    还没有访问数据，发布文章后这里会出现趋势曲线。
  </p>

  <svg
    v-else
    :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
    class="w-full"
    role="img"
    aria-label="访问趋势图"
  >
    <!-- 底线：视觉锚点，也让「全零贴底」时曲线不会悬空 -->
    <line x1="0" :y1="BOTTOM" :x2="WIDTH" :y2="BOTTOM" class="stroke-border" stroke-width="1" />

    <!-- UV：虚线、弱化色，永远不抢 PV 的视觉权重 -->
    <path :d="uvPath" fill="none" class="stroke-ink-faint/40" stroke-width="1.5" stroke-dasharray="4 3" />

    <!-- PV：品牌色实线 -->
    <path :d="viewPath" fill="none" class="stroke-brand-500" stroke-width="2" stroke-linejoin="round" />

    <!-- 数据点：可见小圆点 + 放大的透明命中区（好点中）+ 原生 title 提示 -->
    <g v-for="point in points" :key="point.date">
      <circle :cx="point.x" :cy="point.y" r="3" class="fill-brand-500" />
      <circle :cx="point.x" :cy="point.y" r="10" fill="transparent">
        <title>{{ tooltip(point) }}</title>
      </circle>
    </g>

    <!-- x 轴刻度 -->
    <g v-for="tick in ticks" :key="tick.x" class="fill-ink-faint">
      <text :x="tick.x" :y="HEIGHT - 6" font-size="11" text-anchor="middle">{{ tick.label }}</text>
    </g>
  </svg>
</template>
