/**
 * localStorage 安全访问。
 *
 * 为什么需要统一入口：**访问 localStorage 本身就可能抛异常**，不是只有读写值才可能。
 * Safari「阻止所有 Cookie」、企业策略加固的 WebView、无痕模式配额受限等环境下，
 * `window.localStorage` 这个属性访问就会抛 SecurityError。
 *
 * 这不是理论风险：主题 store 在 main.ts 里被**立即实例化**，一旦它在模块求值阶段
 * 抛错，`router.isReady().then(() => app.mount())` 就永远不会执行 ——
 * 用户看到的是一片空白页，而不是「主题没记住」这种小毛病。
 *
 * 所以凡是碰 localStorage 的地方都走这里，拿不到就退化成「不留存」，
 * 功能降级但绝不崩。
 */

let cached: Storage | null | undefined

/** 取一个可用的 localStorage；不可用时返回 null（**不抛异常**）。 */
export function safeLocalStorage(): Storage | null {
  if (cached !== undefined) return cached
  try {
    const probe = '__blog_probe__'
    window.localStorage.setItem(probe, '1')
    window.localStorage.removeItem(probe)
    cached = window.localStorage
  } catch {
    cached = null
  }
  return cached
}

/** 读一个字符串值；不可用或不存在时返回 null。 */
export function readStored(key: string): string | null {
  try {
    return safeLocalStorage()?.getItem(key) ?? null
  } catch {
    return null
  }
}

/**
 * 写一个字符串值。
 *
 * 返回是否写入成功：配额写满（QuotaExceededError）时返回 false，
 * 调用方据此决定要不要提示用户，而不是让一次写盘失败把整个交互带崩。
 */
export function writeStored(key: string, value: string): boolean {
  try {
    const store = safeLocalStorage()
    if (!store) return false
    store.setItem(key, value)
    return true
  } catch {
    return false
  }
}

/** 删一个键；不可用时静默跳过。 */
export function removeStored(key: string): void {
  try {
    safeLocalStorage()?.removeItem(key)
  } catch {
    /* 删不掉也不影响功能 */
  }
}

/** 仅供测试：清掉探测缓存，让下一次调用重新探测。 */
export function resetStorageProbe(): void {
  cached = undefined
}
