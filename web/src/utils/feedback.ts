/**
 * antd 6 反馈实例 holder（供非 React 上下文使用）。
 *
 * antd 6 官方推荐通过 <App> + App.useApp() 获取 message/modal/notification 实例，
 * 以继承 ConfigProvider 的 theme/locale。但 axios 拦截器等模块级代码不在组件树内，
 * 无法调用 useApp()。本模块提供模块级注入点：
 *   - ThemeWrapper 挂载时用 App.useApp() 拿实例注入（initFeedback）
 *   - api 层通过 getMessage() 读取；未初始化时回退 antd 静态方法（
 *     antd 6 静态方法仍可用，仅缺少主题上下文）
 */
import { message as staticMessage } from "antd";
import type { MessageInstance } from "antd/es/message/interface";

let messageApi: MessageInstance | null = null;

/** 由 ThemeWrapper（<App> 内部）在挂载时注入带上下文的实例。 */
export function initFeedback(instances: { message: MessageInstance }) {
  messageApi = instances.message;
}

/** 获取 message 实例；未初始化时回退静态方法（无主题上下文但可用）。 */
export function getMessage(): MessageInstance {
  return messageApi ?? staticMessage;
}
