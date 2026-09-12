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
    // 显式绑定 IPv4 回环：本机 localhost 可能解析为 ::1（仅 IPv6），
    // 而 Makefile 与 tools/ 下的 E2E 脚本统一走 127.0.0.1，
    // 不显式指定会出现「浏览器能开、脚本连不上」的错位
    host: '127.0.0.1',
    // 开发期把 API 与媒体请求代理到后端，前端代码里就能统一用相对路径，
    // 不用维护 VITE_API_BASE_URL 这类环境差异，也不会踩到跨域。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      // RSS / sitemap 由后端在根路径直出，开发期同样走代理
      '/feed.xml': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/sitemap.xml': { target: 'http://127.0.0.1:8000', changeOrigin: true },
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
