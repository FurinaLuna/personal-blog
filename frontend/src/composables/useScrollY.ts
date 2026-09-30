/**
 * 共享滚动位置的 composable（rAF 节流）。
 *
 * ## 为什么需要它
 *
 * 「返回顶部」按钮要按滚动位置显隐、「阅读进度条」要按滚动位置算比例 ——
 * 两个独立的 `scroll` 监听器会在同一个滚动帧里各跑一次回调。滚动是最高频的
 * 用户事件，能合并就合并：这里维护**唯一的** window scroll 监听 + rAF 节流，
 * 多个消费者各取所需。
 *
 * 它**不是** `ReadingProgress` 的重写，这是刻意的：进度条还需要 `scrollHeight` /
 * `innerHeight`（布局尺寸），而布局尺寸在滚动过程中不会变——每次滚动都读它
 * 反而会触发强制重排。所以进度条继续自己算比例（滚动管道因此仍有两处订阅，
 * 合并它属于独立改动）。
 *
 * ## 只暴露一个只读的 `y`（这里做过一次减法）
 *
 * 第一版还导出了 `onScroll(listener)` 命令式订阅，但它**一次都没被用过**：
 * 唯一消费者 `ElevatorBar` 是 `computed(() => y.value > 阈值)`——值本身是响应式的，
 * `computed` 天然只在依赖变化时重算，「只在值变化时回调」是白送的。
 * 一个没人用的 API 要付的代价是实打实的：得维护订阅集合、得逐订阅者记住
 * 「上次收到多少」（否则新订阅者拿不到初值）、还得多写一组用例。
 * 所以删掉它，消费者一律用 `watch(y, …)` 或 `computed`。
 *
 * ## 去重（这里踩过一个坑）
 *
 * `y.value` 只在**真的变化**时才写：`scroll` 事件会在同一像素上连续刷好几帧
 * （惯性滚动、回弹），而 `window.scrollY` 取整后常常一模一样。第一版每帧都写，
 * 于是每个消费者每帧都被触发一次依赖检查——`computed` 能挡住重复渲染，
 * 挡不住每帧一次的触发开销，而这是全局路径。`resize` 也接在同一条管道上：
 * 窗口尺寸变化但滚动位置没变时同样不该唤醒任何东西。
 *
 * 第一版还额外用过「全局上次通知位置」去重，但既然现在只有 `ref` 一种消费方式，
 * `ref` 自身「同值不触发」就够——少一层记账少一处出错点。
 *
 * ## 生命周期
 *
 * 不用调用方手动摘监听：组件卸载由引用计数处理，**最后一个消费者卸载时才真正摘掉**，
 * 并顺带取消在途的那一帧（否则「最后一个消费者卸载」之后它还会跑一次，
 * 把已经不该更新的值写回去）。
 */
import { onBeforeUnmount, onMounted, readonly, ref, type Ref } from 'vue'

const y = ref(0)

let ticking = false
/** 在途的 rAF 句柄：`detach()` 要能取消它，见模块 docstring 的生命周期一节。 */
let frame = 0
/** 还活着的消费者数量：归零才摘监听器。 */
let consumers = 0

/** 读一次当前位置写进 `y`。`ref` 自己保证「同值不触发依赖」。 */
function syncPosition(): void {
  y.value = window.scrollY
}

function flush(): void {
  ticking = false
  frame = 0
  syncPosition()
}

function requestUpdate(): void {
  if (ticking) return
  ticking = true
  frame = requestAnimationFrame(flush)
}

function attach(): void {
  window.addEventListener('scroll', requestUpdate, { passive: true })
  window.addEventListener('resize', requestUpdate, { passive: true })
  // 第一个消费者创建时先同步一次：否则它拿到的是「未滚动」的初值
  syncPosition()
}

function detach(): void {
  window.removeEventListener('scroll', requestUpdate)
  window.removeEventListener('resize', requestUpdate)
  // 取消在途的那一帧：否则「最后一个消费者卸载」之后它还会跑一次并把值写回去
  // （也会污染测试之间的状态）。
  if (frame !== 0) {
    cancelAnimationFrame(frame)
    frame = 0
  }
  ticking = false
}

export interface ScrollY {
  /**
   * 当前滚动位置（只读）。页面上所有消费者共享同一个值。
   *
   * 消费方式：`computed(() => y.value > 300)` 或 `watch(y, …)`。
   */
  y: Readonly<Ref<number>>
}

export function useScrollY(): ScrollY {
  consumers += 1
  if (consumers === 1) attach()

  onMounted(() => {
    // 页面可能在本组件挂载之前就已经滚过一段：再同步一次，
    // 否则它要等用户动一下才拿到真实值。
    syncPosition()
  })

  onBeforeUnmount(() => {
    consumers -= 1
    if (consumers === 0) detach()
  })

  return { y: readonly(y) as Readonly<Ref<number>> }
}

/** 仅供测试重置内部状态（模块级单例，测试之间要隔离）。 */
export function __resetScrollYForTest(): void {
  consumers = 0
  y.value = 0
  // detach 会顺带取消在途的 rAF 并把 ticking 复位
  detach()
}
