/**
 * Vitest 配置。
 *
 * 单独一个文件而不是并进 vite.config.ts：两边的关注点不同（构建产物 vs 测试环境），
 * 合在一起会让构建配置里混进 jsdom、setupFiles 这些只对测试有意义的东西，
 * 而且每次改测试配置都要重新构建一次才能确认没把产物配置改坏。
 */
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    // 只收 *.spec.ts：测试文件与源码同目录，避免另起一堆 __tests__ 目录层级
    include: ['src/**/*.spec.ts'],
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
