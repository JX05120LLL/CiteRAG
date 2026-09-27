import type { ReactNode } from 'react';
import { Alert, Button, Empty, Skeleton, Tag, Tooltip } from 'antd';
const labels: Record<string, string> = {
  available: '可用', unverified: '未验证', not_configured: '未配置', disabled: '未启用', unavailable: '不可用',
  configured: '已配置 · 未验证连接', connected: '媒体已连接', empty: '暂无资料', ready: '资料就绪', maintaining: '维护中', blocked: '待修复',
  pending: '已受理，等待处理', parsing: '正在解析', parsed: '解析完成', indexing: '正在建立索引',
  failed: '处理失败', deleting: '正在删除', replacing: '正在替换', deleted: '已删除', queued: '等待处理',
  running: '进行中', succeeded: '核验与清理通过', interrupted: '已中断', partial: '回答未完成',
  answered: '回答已保存', uncommitted: '等待核验与保存', insufficient_evidence: '证据不足', needs_clarification: '需要补充信息',
  conflicting_evidence: '资料存在冲突', accepted: '已受理', verifying: '核验中', cleanup: '清理中', complete: '已完成',
};
export function StateTag({ value }: { value: string }) {
  const color = ['failed', 'unavailable', 'blocked'].includes(value) ? 'error'
    : ['available', 'ready', 'succeeded', 'answered', 'connected'].includes(value) ? 'success'
    : ['running', 'maintaining', 'indexing', 'parsing', 'partial', 'interrupted'].includes(value) ? 'warning' : 'default';
  return <Tag color={color}>{labels[value] ?? '状态未确认'}</Tag>;
}
export function stateLabel(value: string) { return labels[value] ?? '状态未确认'; }
export function DisabledAction({ children, icon, reason = '只读设计预览的写操作已禁用；请在正式工作台操作。' }:
  { children: ReactNode; icon?: ReactNode; reason?: string }) {
  return <Tooltip title={reason}><span className="disabled-action"><Button disabled icon={icon} aria-label={typeof children === 'string' ? children : undefined}>{children}</Button></span></Tooltip>;
}
export function ResourceState({ loading, error, empty }: { loading: boolean; error: string | null; empty?: boolean }) {
  if (loading) return <Skeleton active paragraph={{ rows: 3 }} />;
  if (error) return <Alert type="warning" showIcon title="读取未完成" description={error} />;
  if (empty) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无记录" />;
  return null;
}
const reasons: Record<string, string> = {
  unsupported_document: '文件包含当前解析器不支持的结构。请转换为普通段落或简单表格后重新上传。',
  rebuild_required: '引擎已有改动，需要从受管原文重建并完成核验。历史失败记录会保留。',
  answer_unavailable: '回答模型服务暂不可用。请检查系统状态及供应商配置后重试。',
  answer_source_mismatch: '回答的引用无法核对原文，本次结果未发布。请重试或缩小问题范围。',
  request_interrupted: '处理已中断；现有片段不是完整回答。可通过原有重试流程恢复。',
  ingestion_unavailable: '入库服务暂不可用，请查看系统状态。',
  parse_failed: '文档解析未完成，请检查编码、文件格式和结构。',
  file_empty: '文件未提取到可用文字。扫描件需要先进行 OCR。',
  input_limit: '输入超过供应商限制，需要缩短或分段后重试。',
};
export function safeReason(code: string | null) {
  return code ? reasons[code] ?? '处理未完成。请检查系统状态和任务详情；本页不显示原始供应商诊断。' : '无已记录的失败原因';
}
export function dateLabel(value: string) {
  const date = new Date(value); return Number.isNaN(date.getTime()) ? '时间未确认' : date.toLocaleString('zh-CN', { hour12: false });
}
export function sizeLabel(value: number) { return `${(value / 1024).toFixed(1)} KiB`; }
