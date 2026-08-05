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
      colorPrimary: cssVar("--pf-primary", "#1e40af"),
      colorInfo: cssVar("--pf-primary", "#1e40af"),
      colorLink: cssVar("--pf-primary", "#1e40af"),
      colorSuccess: cssVar("--pf-success", "#16a34a"),
      colorWarning: cssVar("--pf-warning", "#faad14"),
      colorError: cssVar("--pf-error", "#dc2626"),
      borderRadius: 11,
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
      fontSize: 14,
      lineHeight: 1.7,
      colorBgLayout: cssVar("--pf-bg-secondary", "#f5f7fb"),
      colorBgContainer: cssVar("--pf-bg-primary", "#ffffff"),
      colorText: cssVar("--pf-text-primary", "#1a1a2e"),
      colorTextSecondary: cssVar("--pf-text-secondary", "#475569"),
    },
    components: {
      Layout: {
        headerBg: cssVar("--pf-bg-primary", "#ffffff"),
        headerHeight: 60,
        headerPadding: "0 24px",
        siderBg: cssVar("--pf-bg-tertiary", "#fbfbfd"),
        bodyBg: cssVar("--pf-bg-secondary", "#f5f7fb"),
      },
      Menu: {
        itemBg: "transparent",
        itemSelectedBg: "transparent",
        itemSelectedColor: cssVar("--pf-primary", "#1e40af"),
        itemColor: cssVar("--pf-text-secondary", "#475569"),
        itemHoverBg: "transparent",
        activeBarBorderWidth: 0,
        itemActiveBg: "transparent",
      },
      Card: {
        borderRadiusLG: 16,
        boxShadowTertiary: "0 4px 14px rgba(25, 45, 90, 0.05)",
        colorBgContainer: "transparent",
      },
      Button: {
        controlHeight: 38,
      },
      Table: {
        headerBg: cssVar("--pf-bg-tertiary", "#f8fafc"),
        headerColor: cssVar("--pf-text-primary", "#334155"),
        rowHoverBg: cssVar("--pf-bg-tertiary", "#f8fafc"),
      },
    },
  };
}

/** 兼容旧导出：静态主题配置（浅色模式）。
 *  新代码建议通过 useThemeStore 获取动态主题。
 */
export const themeConfig = buildThemeConfig("light");
