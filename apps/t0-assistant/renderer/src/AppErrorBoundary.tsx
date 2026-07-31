import { Component, type ErrorInfo, type ReactNode } from "react";

interface AppErrorBoundaryState {
  failed: boolean;
}

export class AppErrorBoundary extends Component<
  { children: ReactNode },
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("T0assistant Renderer failed", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="renderer-error" role="alert">
        <strong>图表显示异常</strong>
        <span>界面没有被清空。重新加载即可恢复，错误详情已写入控制台。</span>
        <button type="button" onClick={() => window.location.reload()}>
          重新加载
        </button>
      </main>
    );
  }
}
