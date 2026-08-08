import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, App as AntdApp } from "antd";
import { initFeedback } from "./utils/feedback";
import zhCN from "antd/locale/zh_CN";
import "dayjs/locale/zh-cn";
import App from "./App";
import { buildThemeConfig } from "./styles/theme";
import { useThemeStore } from "./store/useThemeStore";

/** 必须在 <AntdApp> 内部调用 useApp()，把带上下文的实例注入模块级 holder。 */
function FeedbackBridge() {
  const { message } = AntdApp.useApp();
  useEffect(() => {
    initFeedback({ message });
  }, [message]);
  return null;
}

export default function ThemeWrapper() {
  const { resolved, init } = useThemeStore();
  useEffect(() => {
    init();
  }, [init]);

  const themeConfig = buildThemeConfig(resolved);

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <AntdApp>
        <FeedbackBridge />
        <BrowserRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <App />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
}
