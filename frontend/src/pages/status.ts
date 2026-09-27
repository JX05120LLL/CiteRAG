import type { AppState } from '../state';
import { action, alert, el, heading } from '../shared/dom';

const statusLabels: Record<string, string> = {
  partial: '部分能力可用', available: '可用', not_configured: '尚未配置',
  unverified: '等待真实验证', unavailable: '暂不可用', running: '正在备份',
  disabled: '未启用',
  local_single_user: '本地单用户',
};

export const backupErrors: Record<string, string> = {
  engine_configuration_unavailable: '引擎库配置未就绪，未生成备份。',
  backup_verification_failed: '当天备份校验失败，需人工核查。',
  backup_interrupted: '备份中断，需重新核查。',
  backup_failed: '备份失败，需检查本机存储和数据库。',
};

function verificationTime(value: string | number): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function capabilityDetail(state: AppState, key: 'rag' | 'models'): string | null {
  const health = state.health;
  if (!health) return null;
  if (key === 'models' && health.models_info) {
    const info = health.models_info;
    return [info.region, info.model_names.join('、'),
      info.last_verified_at ? `最近验证 ${verificationTime(info.last_verified_at)}` : null]
      .filter(Boolean).join(' · ');
  }
  if (key === 'rag' && health.rag_info) {
    const info = health.rag_info;
    return [`LightRAG ${info.lightrag_commit.slice(0, 8)}`,
      info.postgresql_major ? `PostgreSQL ${info.postgresql_major}` : null,
      info.vector_version ? `pgvector ${info.vector_version}` : null,
      info.last_verified_at ? `最近验证 ${verificationTime(info.last_verified_at)}` : null]
      .filter(Boolean).join(' · ');
  }
  return null;
}

export function renderStatus(main: HTMLElement, state: AppState, refresh: () => Promise<void>) {
  const content = el('section', 'management-content');
  content.append(heading('系统状态'));
  if (state.healthLoading) {
    const pending = el('p', 'intro', '正在读取服务状态…');
    pending.setAttribute('role', 'status');
    content.append(pending);
  } else if (state.healthError) content.append(alert(state.healthError.message));
  else if (state.health) {
    const summary = el('div', 'status-summary');
    summary.append(el('strong', '', statusLabels[state.health.status] ?? '状态待确认'));
    if (state.healthCheckedAt) summary.append(el('span', 'metadata',
      `本次读取 ${verificationTime(state.healthCheckedAt)}`));
    content.append(summary);
    const list = el('dl', 'health-list');
    list.setAttribute('aria-live', 'polite');
    for (const [key, title] of [['mode', '使用方式'], ['database', '业务数据库'], ['rag', '知识引擎'], ['models', '模型服务']] as const) {
      const row = el('div', 'health-row');
      const value = el('dd', '', statusLabels[state.health[key]] ?? '状态待确认');
      if (key === 'rag' || key === 'models') {
        const detail = capabilityDetail(state, key);
        if (detail) value.append(el('small', 'health-detail', detail));
      }
      row.append(el('dt', '', title), value);
      list.append(row);
    }
    if (state.health.backup) {
      const row = el('div', 'health-row');
      const value = el('dd', '', statusLabels[state.health.backup] ?? '状态待确认');
      if (state.health.last_backup_at)
        value.append(el('small', 'health-detail', `最近完成 ${verificationTime(state.health.last_backup_at)}`));
      if (state.health.backup_error_code)
        value.append(el('small', 'health-detail',
          backupErrors[state.health.backup_error_code] ?? '备份状态待核查。'));
      row.append(el('dt', '', '本地备份'), value);
      list.append(row);
    }
    if (state.health.retention) {
      const row = el('div', 'health-row');
      row.append(el('dt', '', '聊天到期清理'),
        el('dd', '', statusLabels[state.health.retention] ?? '状态待确认'));
      list.append(row);
    }
    content.append(list);
  }
  content.append(el('p', 'scope-note', '同一安装使用同一份本地资料。模型服务的标记依据本机配置和验证记录，未在此页发起模型调用。文字问答需单独启用；实时语音与图片提问留待后续。'));
  const retry = action('刷新状态', 'button secondary', () => { void refresh(); });
  retry.disabled = state.healthLoading;
  content.append(retry);
  main.append(content);
}
