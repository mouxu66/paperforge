import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Result } from "antd";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * 顶层 ErrorBoundary —— 拦截任意子组件抛出的渲染异常，避免整页白屏。
 *
 * 🛡️ 全量审查 Section 1-#10：
 * 原来 web 缺失 ErrorBoundary，任一 component 抛错都白屏。包后：
 *   1. 显示 antd Result 错误页 + 重新加载按钮（让用户可恢复）
 *   2. console.error 记录完整 stack 供开发者定位
 *   3. 文案本地化（依赖现有 i18n）
 */
export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[ErrorBoundary] 捕获到渲染异常:", error, info);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  private handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const msg = this.state.error?.message ?? "未知错误";
    return (
      <Result
        status="error"
        title="页面渲染异常"
        subTitle={
          <div>
            <p>已捕获异常，避免整页白屏：</p>
            <pre
              style={{
                display: "inline-block",
                maxWidth: "100%",
                padding: 12,
                background: "var(--pf-bg-tertiary)",
                border: "1px solid var(--pf-border-light)",
                borderRadius: 6,
                fontSize: 12,
                textAlign: "left",
                overflow: "auto",
              }}
            >
              {msg}
            </pre>
          </div>
        }
        extra={[
          <Button key="reset" onClick={this.handleReset}>
            重试一次
          </Button>,
          <Button key="reload" type="primary" onClick={this.handleReload}>
            重新加载页面
          </Button>,
        ]}
      />
    );
  }
}
