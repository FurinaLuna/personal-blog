/// <reference types="vite/client" />

/**
 * 环境变量类型声明。
 *
 * 声明成显式字段而不是靠 `any` 兜底：拼错变量名时能直接编译报错，
 * 而不是运行到线上才发现读到的是 undefined。
 *
 * 只声明**真的被消费**的变量：多声明一个没人读的字段，等于向部署者承诺
 * "改这个值能改行为"，而实际什么都不会发生（VITE_SITE_TITLE 就是这样被删掉的）。
 */
interface ImportMetaEnv {
  /** API 基础路径。默认 `/api/v1`，由 vite 的 proxy 转发到后端。 */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
