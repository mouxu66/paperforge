import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";
import { message } from "antd";

// 统一 baseURL：优先 VITE_API_BASE，兼容旧变量 VITE_ASK_API_BASE，默认走 vite 代理 /api
export const API_BASE =
  import.meta.env.VITE_API_BASE || import.meta.env.VITE_ASK_API_BASE || "/api";

/** localStorage 中存储 API Token 的 key */
const API_TOKEN_KEY = "pf_api_token";

interface HttpClientOptions {
  /** 请求超时（毫秒），默认 15000 */
  timeout?: number;
  /** 兜底错误提示（拦截器无法识别时使用） */
  defaultMessage?: string;
  /** 是否挂载 response 拦截器（message.error + reject），默认 true */
  withInterceptor?: boolean;
}

/** 扩展 axios 请求配置：支持单次请求跳过错误 toast */
declare module "axios" {
  interface AxiosRequestConfig {
    /** 跳过自动 error toast，由调用方自行处理 */
    skipErrorToast?: boolean;
  }
}

/** 从 localStorage 读取当前 API Token（空字符串视为未设置） */
export function getApiToken(): string | null {
  try {
    const token = window.localStorage.getItem(API_TOKEN_KEY);
    return token || null;
  } catch {
    // localStorage 不可用时静默降级
    return null;
  }
}

/** 设置/清除 API Token（写入 localStorage） */
export function setApiToken(token: string | null): void {
  try {
    if (token === null || token === "") {
      window.localStorage.removeItem(API_TOKEN_KEY);
    } else {
      window.localStorage.setItem(API_TOKEN_KEY, token);
    }
  } catch {
    // localStorage 不可用时静默忽略
  }
}

/** 构造带鉴权头的 headers 对象（供原生 fetch SSE 使用） */
export function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getApiToken();
  if (token) {
    headers["X-PaperForge-Token"] = token;
  }
  return headers;
}

/**
 * 创建带统一配置的 axios 实例。
 * - baseURL 统一从 VITE_API_BASE 读取
 * - 请求拦截器自动附加 X-PaperForge-Token（localStorage 中的 pf_api_token）
 * - 可选挂载 response 拦截器：自动提取 detail 字段并 message.error
 */
export function createHttpClient(options: HttpClientOptions = {}): AxiosInstance {
  const { timeout = 15000, defaultMessage = "请求失败", withInterceptor = true } = options;
  const instance = axios.create({ baseURL: API_BASE, timeout });

  // 请求拦截器：统一附加鉴权 token
  instance.interceptors.request.use(
    (config: InternalAxiosRequestConfig) => {
      const token = getApiToken();
      if (token) {
        config.headers.set("X-PaperForge-Token", token);
      }
      return config;
    },
    (error) => Promise.reject(error),
  );

  if (withInterceptor) {
    instance.interceptors.response.use(
      (resp) => resp,
      (error) => {
        // 若请求标记了 skipErrorToast，跳过自动弹 toast（由调用方处理）
        if (!error.config?.skipErrorToast) {
          const msg = error?.response?.data?.detail || error?.message || defaultMessage;
          message.error(msg);
        }
        return Promise.reject(error);
      },
    );
  }
  return instance;
}

// 默认实例：15s 超时 + 错误拦截器，供常规接口使用
const http = createHttpClient({ timeout: 15000 });

// 静默实例：无自动 error toast，供自带错误处理的调用方使用
export const httpSilent = createHttpClient({ timeout: 15000, withInterceptor: false });

export default http;
