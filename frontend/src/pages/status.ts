import type { AppState } from '../state';
import { action, alert, el, heading } from '../shared/dom';

const statusLabels: Record<string, string> = {
  partial: '部分能力可用', available: '可用', not_configured: '尚未配置',
  unavailable: '暂不可用', local_single_user: '本地单用户',
};

export function renderStatus(main: HTMLElement, state: AppState, refresh: () => Promise<void>) {
  const content = el('section', 'management-content');
  content.append(heading('系统状态'));
  if (state.healthLoading) {
    const pending = el('p', 'intro', '正在读取服务状态…');
    pending.setAttribute('role', 'status');
    content.append(pending);
  } else if (state.healthError) content.append(alert(state.healthError.message));
  else if (state.health) {
    content.append(el('p', 'intro', statusLabels[state.health.status] ?? '状态待确认'));
    const list = el('dl', 'health-list');
    for (const [key, title] of [['mode', '使用方式'], ['database', '业务数据库'], ['rag', '知识引擎'], ['models', '模型服务']] as const) {
      const row = el('div', 'health-row');
      row.append(el('dt', '', title), el('dd', '', statusLabels[state.health[key]] ?? '状态待确认'));
      list.append(row);
    }
    content.append(list);
  }
  content.append(el('p', 'scope-note', '同一安装使用同一份本地资料。这里显示服务返回的当前状态；文字问答、实时语音与图片提问尚未开放。'));
  const retry = action('刷新状态', 'button secondary', () => { void refresh(); });
  retry.disabled = state.healthLoading;
  content.append(retry);
  main.append(content);
}
