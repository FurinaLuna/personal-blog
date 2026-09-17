/**
 * localStorage 安全访问的测试。
 *
 * 重点在「存储不可用时不能抛异常」：主题 store 在 main.ts 里被立即实例化，
 * 模块求值阶段抛错会让 app.mount() 永远不执行 —— 用户看到的是一片空白页。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  readStored,
  removeStored,
  resetStorageProbe,
  safeLocalStorage,
  writeStored,
} from '@/utils/storage'

describe('safeLocalStorage', () => {
  beforeEach(() => {
    resetStorageProbe()
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    resetStorageProbe()
  })

  it('正常环境下返回可用的 Storage', () => {
    expect(safeLocalStorage()).toBe(window.localStorage)
  })

  it('访问 localStorage 抛异常时返回 null，而不是把异常抛给调用方', () => {
    // 模拟 Safari「阻止所有 Cookie」/ 加固 WebView：
    // 注意是**属性访问本身**就抛，不是只有 setItem 才抛
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('SecurityError', 'SecurityError')
    })
    resetStorageProbe()

    expect(safeLocalStorage()).toBeNull()
    expect(() => readStored('k')).not.toThrow()
    expect(readStored('k')).toBeNull()
    expect(writeStored('k', 'v')).toBe(false)
    expect(() => removeStored('k')).not.toThrow()
  })

  it('写入失败（配额用尽）返回 false 而不是抛异常', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError')
    })
    resetStorageProbe()

    expect(writeStored('k', 'v')).toBe(false)
  })
})

describe('读写往返', () => {
  beforeEach(() => {
    resetStorageProbe()
    window.localStorage.clear()
  })

  it('写入后能读回来', () => {
    expect(writeStored('theme', 'dark')).toBe(true)
    expect(readStored('theme')).toBe('dark')
  })

  it('键不存在时返回 null', () => {
    expect(readStored('never-written')).toBeNull()
  })

  it('删除后读回 null', () => {
    writeStored('theme', 'dark')
    removeStored('theme')
    expect(readStored('theme')).toBeNull()
  })
})
