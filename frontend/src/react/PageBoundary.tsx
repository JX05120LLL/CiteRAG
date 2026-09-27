import { Component, type ReactNode } from 'react';
import { Alert, Button } from 'antd';

export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <Alert showIcon type="error" title="页面加载未完成"
      description="请检查前端服务和网络后重新加载；已受理操作的结果以保存记录为准。"
      action={<Button onClick={() => window.location.reload()}>重新加载页面</Button>} /> : this.props.children;
  }
}
