import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, App as AntdApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import "dayjs/locale/zh-cn";
import App from "./App";
import { buildThemeConfig } from "./styles/theme";
import { useThemeStore } from "./store/useThemeStore";

export default function ThemeWrapper() {
  const { resolved, init } = useThemeStore();
  useEffect(() => {
    init();
  }, [init]);

  const themeConfig = buildThemeConfig(resolved);

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <AntdApp>
        <BrowserRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <App />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
}
