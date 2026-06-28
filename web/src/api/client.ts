import axios, { type AxiosInstance } from 'axios'
import { message } from 'antd'

// 统一 baseURL：优先 VITE_API_BASE，兼容旧变量 VITE_ASK_API_BASE，默认走 vite 代理 /api
const API_BASE =
  import.meta.env.VITE_API_BASE || import.meta.env.VITE_ASK_API_BASE || '/api'

interface HttpClientOptions {
  /** 请求超时（毫秒），默认 15000 */
  timeout?: number
  /** 兜底错误提示（拦截器无法识别时使用） */
  defaultMessage?: string
  /** 是否挂载 response 拦截器（message.error + reject），默认 true */
  withInterceptor?: boolean
}

/**
 * 创建带统一配置的 axios 实例。
 * - baseURL 统一从 VITE_API_BASE 读取
 * - 可选挂载 response 拦截器：自动提取 detail 字段并 message.error
 */
export function createHttpClient(options: HttpClientOptions = {}): AxiosInstance {
  const {
    timeout = 15000,
    defaultMessage = '请求失败',
    withInterceptor = true,
  } = options
  const instance = axios.create({ baseURL: API_BASE, timeout })
  if (withInterceptor) {
    instance.interceptors.response.use(
      (resp) => resp,
      (error) => {
        const msg =
          error?.response?.data?.detail || error?.message || defaultMessage
        message.error(msg)
        return Promise.reject(error)
      },
    )
  }
  return instance
}

// 默认实例：15s 超时 + 错误拦截器，供 papers 等常规接口使用
const http = createHttpClient({ timeout: 15000 })

export default http
