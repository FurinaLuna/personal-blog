import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 开发期把 API 与媒体请求代理到后端，前端代码里就能统一用相对路径，
    // 不用维护 VITE_API_BASE_URL 这类环境差异，也不会踩到跨域。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // 把体积大且更新频率低的库单独拆包，业务代码改动不会让用户重新下载它们
        manualChunks: {
          vendor: ['vue', 'vue-router', 'pinia'],
          markdown: ['marked', 'dompurify', 'highlight.js'],
        },
      },
    },
  },
})
