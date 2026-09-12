/**
 * 测试环境补丁。
 *
 * jsdom 没有实现 `matchMedia`，而主题 store 在模块初始化时就会读它——
 * 缺这个桩会让任何 import 到 theme store 的测试直接抛错。
 */
import { vi } from 'vitest'

if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  })
}

// jsdom 也没实现 IntersectionObserver，目录组件在 mount 时会用到
if (!window.IntersectionObserver) {
  class NoopIntersectionObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): [] {
      return []
    }
  }
  Object.defineProperty(window, 'IntersectionObserver', {
    writable: true,
    value: NoopIntersectionObserver,
  })
}
