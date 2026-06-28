import type { ThemeConfig } from 'antd'

// 学术风格主题：深蓝主色 + 衬线标题 + 圆角克制
export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: '#1e40af',
    colorInfo: '#1e40af',
    colorLink: '#1e40af',
    colorSuccess: '#15803d',
    colorWarning: '#b45309',
    colorError: '#b91c1c',
    borderRadius: 8,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
    fontSize: 14,
    lineHeight: 1.8,
    colorBgLayout: '#f5f7fb',
  },
  components: {
    Layout: {
      headerBg: '#ffffff',
      headerHeight: 60,
      headerPadding: '0 24px',
      siderBg: '#fbfbfd',
      bodyBg: '#f5f7fb',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: 'transparent',
      itemSelectedColor: '#1e40af',
      itemColor: '#475569',
      itemHoverBg: 'transparent',
      activeBarBorderWidth: 0,
      itemActiveBg: 'transparent',
    },
    Card: {
      borderRadiusLG: 14,
      boxShadowTertiary: '0 1px 3px rgba(15, 23, 42, 0.06)',
      colorBgContainer: 'transparent',
    },
    Button: {
      controlHeight: 36,
    },
    Table: {
      headerBg: '#f8fafc',
      headerColor: '#334155',
      rowHoverBg: '#f8fafc',
    },
  },
}
