/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** PaperForge API 基础地址，默认走 vite 代理 /api */
  readonly VITE_API_BASE?: string
  /** @deprecated 已被 VITE_API_BASE 取代，保留兼容 */
  readonly VITE_ASK_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
