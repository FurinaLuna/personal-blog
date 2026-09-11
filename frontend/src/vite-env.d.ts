/// <reference types="vite/client" />

/**
 * 环境变量类型声明。
 *
 * 声明成显式字段而不是靠 `any` 兜底：拼错变量名时能直接编译报错，
 * 而不是运行到线上才发现读到的是 undefined。
 */
interface ImportMetaEnv {
  /** API 基础路径。默认 `/api/v1`，由 vite 的 proxy 转发到后端。 */
  readonly VITE_API_BASE_URL?: string
  /** 站点默认标题，用于 SEO 兜底。 */
  readonly VITE_SITE_TITLE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
