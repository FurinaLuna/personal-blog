/**
 * 草稿自动保存。
 *
 * 解决的是写作者最痛的丢失场景：写了半小时，误关标签页 / 浏览器崩溃 / 笔记本没电。
 * 后端虽然有草稿状态，但那只在「用户点了保存」之后才生效——本地快照才是保命的。
 *
 * 几个刻意的设计决定：
 * 1. **写 localStorage 而不是定时 PATCH**：自动往服务端写会产生大量中间版本，
 *    也会让「谁改的」这类问题变复杂；本地快照只在用户主动保存时才上云。
 * 2. **按 key 分桶**（`article:<id>` / `article:new`）：不同文章的草稿互不覆盖。
 * 3. **只有表单加载完成后才允许写**：否则加载期间的空表单会把已有草稿冲掉。
 * 4. **隐私模式下 localStorage 不可用**，所有操作静默失效并标记 `available=false`，
 *    绝不能因为「存不了快照」就把编辑器弄崩。
 */
import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

export interface DraftSnapshot<T> {
  savedAt: number
  payload: T
  /** 快照对应的 key，便于恢复时二次确认（防止 key 迁移后张冠李戴）。 */
  key: string
}

export interface DraftRestoreResult<T> {
  payload: T
  savedAt: number
}

export interface DraftAutosaveOptions<T> {
  /** 存储 key，随文章切换而变化（返回新值时自动重新评估草稿）。 */
  key: () => string
  /** 取当前表单快照。返回值会被 JSON 序列化后比较，因此要保证可序列化。 */
  snapshot: () => T
  /** 是否允许写入（通常绑定「表单是否已加载完成」）。 */
  enabled: () => boolean
  /** 停止输入多久后落盘，默认 3 秒。太短会频繁写盘，太长会丢更多内容。 */
  debounceMs?: number
  /** 超过这个年龄的草稿视为过期并清理，默认 30 天。 */
  maxAgeMs?: number
}

const DEFAULT_DEBOUNCE_MS = 3000
const DEFAULT_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000

function storage(): Storage | null {
  try {
    // 隐私模式 / 禁用 Cookie 时访问 localStorage 会直接抛错，必须先探测
    const probe = '__blog_probe__'
    window.localStorage.setItem(probe, '1')
    window.localStorage.removeItem(probe)
    return window.localStorage
  } catch {
    return null
  }
}

export function useDraftAutosave<T>(options: DraftAutosaveOptions<T>) {
  const debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS

  /** 本地快照是否可用（隐私模式 / 配额用尽时为 false）。 */
  const available = ref(storage() !== null)
  /** 最近一次落盘时间。 */
  const savedAt: Ref<number | null> = ref(null)
  /** 是否存在「值得提示用户恢复」的草稿。 */
  const hasDraft = ref(false)

  let timer: ReturnType<typeof setTimeout> | null = null
  /**
   * 上一次写盘的**内容**（不含时间戳）。
   *
   * 必须只比内容：如果拿整段 JSON（含 savedAt）去比较，每次生成的时间戳都不同，
   * 去重永远不会命中——guard 形同虚设，还会让「自动保存于 xx」一直闪烁。
   */
  let lastWritten = ''

  function read(): DraftSnapshot<T> | null {
    if (!available.value) return null
    const store = window.localStorage
    try {
      const raw = store.getItem(options.key())
      if (!raw) return null
      const parsed = JSON.parse(raw) as DraftSnapshot<T>
      // 结构校验：老版本数据或被人手改过的内容一律当没有，避免后面用到时炸
      if (typeof parsed?.savedAt !== 'number' || !('payload' in parsed)) {
        store.removeItem(options.key())
        return null
      }
      if (Date.now() - parsed.savedAt > maxAgeMs) {
        store.removeItem(options.key())
        return null
      }
      return parsed
    } catch {
      // JSON 损坏时清掉，否则每次打开编辑页都要再解析一次坏数据
      store.removeItem(options.key())
      return null
    }
  }

  /** 检查是否已有草稿（用于编辑器首次进入时决定要不要弹恢复提示）。 */
  function checkExisting(): DraftRestoreResult<T> | null {
    const draft = read()
    hasDraft.value = draft !== null
    savedAt.value = draft?.savedAt ?? null
    return draft ? { payload: draft.payload, savedAt: draft.savedAt } : null
  }

  function write(): void {
    if (!available.value || !options.enabled()) return

    // 只取一次快照：以前为了「内容没变就不写」先 JSON.stringify 一遍，
    // 真正落盘时又调了一次 snapshot()。两次调用之间表单可能已被改动，
    // 于是「用来比对的」和「实际存下的」不是同一份内容，去重会误判。
    let payload: T
    let payloadJson: string
    try {
      payload = options.snapshot()
      payloadJson = JSON.stringify(payload)
    } catch {
      // snapshot 里出现循环引用、BigInt 之类的问题：关掉自动保存，不要每 3 秒抛一次
      console.debug('[draft] 快照序列化失败，已停止自动保存')
      available.value = false
      return
    }

    // 内容没变就不写：既能省掉无意义的写盘，也让 savedAt 只在真有改动时更新
    if (payloadJson === lastWritten) return

    try {
      window.localStorage.setItem(
        options.key(),
        JSON.stringify({
          savedAt: Date.now(),
          key: options.key(),
          payload,
        } satisfies DraftSnapshot<T>),
      )
      lastWritten = payloadJson
      savedAt.value = Date.now()
      hasDraft.value = true
    } catch {
      // 配额用尽（QuotaExceededError）等：停止后续尝试，避免每 3 秒报一次错
      console.debug('[draft] 本地草稿写入失败，已停止自动保存')
      available.value = false
    }
  }

  function schedule(): void {
    if (timer) clearTimeout(timer)
    timer = setTimeout(write, debounceMs)
  }

  /** 立即落盘（用于「离开页面前」这类时机）。 */
  function flush(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    write()
  }

  /** 清除草稿（服务端保存成功后调用）。 */
  function clear(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    lastWritten = ''
    hasDraft.value = false
    savedAt.value = null
    if (!available.value) return
    try {
      window.localStorage.removeItem(options.key())
    } catch {
      console.debug('[draft] 本地草稿清理失败')
    }
  }

  // 表单变化 → 延迟落盘；key 变化（切换文章）→ 重新评估是否有草稿。
  // enabled 也放进依赖：它从 false 翻到 true（表单加载完成）时必须补一次落盘机会，
  // 否则「用户在加载期间就输入了内容」会因为此后再无变化而永远不被保存。
  const stop = watch(
    () => [options.key(), options.enabled(), options.snapshot()] as const,
    ([key, enabled], previous) => {
      if (previous && key !== previous[0]) {
        // 切换到另一篇文章：丢弃上一个 key 的待写任务，重新判断
        if (timer) {
          clearTimeout(timer)
          timer = null
        }
        lastWritten = ''
        checkExisting()
        return
      }
      if (!enabled) return
      hasDraft.value = true
      schedule()
    },
    { deep: true },
  )

  // 关页面前补一次落盘：浏览器不保证 beforeunload 里还能做异步操作，
  // 但 localStorage 是同步 API，这里能真正把最后几秒的输入保住
  function onBeforeUnload(): void {
    flush()
  }
  window.addEventListener('beforeunload', onBeforeUnload)

  onBeforeUnmount(() => {
    stop()
    window.removeEventListener('beforeunload', onBeforeUnload)
  })

  return { available, savedAt, hasDraft, checkExisting, flush, clear }
}
