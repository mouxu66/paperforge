import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";

// 学术风格主题：深蓝主色 + 衬线标题 + 圆角克制
// 颜色优先读取 CSS 变量，实现动态主题切换；若变量未定义则回退到默认值。
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** 构建 Ant Design 主题配置。resolved 为当前实际应用的主题（light/dark）。 */
export function buildThemeConfig(resolved: "light" | "dark" = "light"): ThemeConfig {
  const isDark = resolved === "dark";
  return {
    algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      // 主色只此一支 navy。fallback 必须与 global.css 的 --pf-primary 一致，
      // 否则变量未就绪时会闪出第二个蓝（历史问题：此处 #1e40af vs CSS #3157c8）。
      colorPrimary: cssVar("--pf-primary", "#003b5c"),
      colorInfo: cssVar("--pf-primary", "#003b5c"),
      colorLink: cssVar("--pf-primary", "#003b5c"),
      colorSuccess: cssVar("--pf-success", "#0e4d3c"),
      colorWarning: cssVar("--pf-warning", "#8a6a1f"),
      colorError: cssVar("--pf-error", "#b22222"),
      borderRadius: 10,
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
      fontSize: 14,
      lineHeight: 1.7,
      colorBgLayout: cssVar("--pf-bg-secondary", "#fafaf7"),
      colorBgContainer: cssVar("--pf-bg-primary", "#ffffff"),
      colorText: cssVar("--pf-text-primary", "#1a1a1a"),
      colorTextSecondary: cssVar("--pf-text-secondary", "#444444"),
      // 阴影分三级消费：L1(sm)=列表行/标签，L2(md)=卡片及其 hover，L3(lg)=浮层。
      // 浮层是 L3 的定义用途，让三级阴影各自有落点，不留悬空令牌。
      boxShadowSecondary: cssVar("--pf-shadow-lg", "0 14px 36px rgba(30, 24, 12, 0.14)"),
    },
    components: {
      Layout: {
        headerBg: cssVar("--pf-bg-primary", "#ffffff"),
        headerHeight: 60,
        headerPadding: "0 24px",
        siderBg: cssVar("--pf-bg-tertiary", "#f7f5f0"),
        bodyBg: cssVar("--pf-bg-secondary", "#fafaf7"),
      },
      Menu: {
        itemBg: "transparent",
        itemSelectedBg: "transparent",
        itemSelectedColor: cssVar("--pf-primary", "#003b5c"),
        itemColor: cssVar("--pf-text-secondary", "#444444"),
        itemHoverBg: "transparent",
        activeBarBorderWidth: 0,
        itemActiveBg: "transparent",
      },
      Card: {
        borderRadiusLG: 16,
        boxShadowTertiary: "0 1px 2px rgba(30, 24, 12, 0.06)",
        colorBgContainer: "transparent",
      },
      Button: {
        controlHeight: 38,
      },
      Table: {
        headerBg: cssVar("--pf-bg-tertiary", "#f7f5f0"),
        headerColor: cssVar("--pf-text-primary", "#1a1a1a"),
        rowHoverBg: cssVar("--pf-bg-tertiary", "#f7f5f0"),
      },
    },
  };
}

/** 兼容旧导出：静态主题配置（浅色模式）。
 *  新代码建议通过 useThemeStore 获取动态主题。
 */
export const themeConfig = buildThemeConfig("light");
