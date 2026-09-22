import type { AppState, Page } from '../state';
import { kbStatuses } from '../state';
import { action, el, icon, alert, heading } from '../shared/dom';

function composer() {
  const area = el('div', 'composer-area');
  const box = el('div', 'composer');
  const input = el('textarea');
  input.disabled = true;
  input.rows = 1;
  input.placeholder = '文字问答尚未开放';
  input.setAttribute('aria-label', '问题输入');
  input.setAttribute('aria-describedby', 'composer-unavailable');
  const toolbar = el('div', 'composer-toolbar');
  const photo = action('添加图片', 'text-button');
  photo.prepend(icon('image'));
  const voice = action('开始语音', 'button voice');
  voice.prepend(icon('mic'));
  const send = action('', 'send-button');
  send.append(icon('arrow'), el('span', 'sr-only', '发送'));
  send.setAttribute('aria-label', '发送');
  for (const control of [photo, voice, send]) {
    control.disabled = true;
    control.setAttribute('aria-describedby', 'composer-unavailable');
  }
  toolbar.append(photo, voice, el('span', 'composer-shortcut', 'Enter 发送'), send);
  box.append(input, toolbar);
  const hint = el('p', 'composer-note', '文字问答、图片提问与实时语音尚未开放。');
  hint.id = 'composer-unavailable';
  area.append(box, hint);
  return area;
}

export function renderWorkbench(main: HTMLElement, state: AppState, navigate: (page: Page) => void, refresh: () => Promise<void>) {
  const {loading, basesError: error, bases} = state;
  const content = el('div', 'conversation-content');
  if (loading) {
    content.append(el('p', 'eyebrow', 'CiteRAG / 本地工作台'), heading('正在连接工作台'), el('p', 'intro', '正在读取本地知识库与聊天…'));
    content.setAttribute('role', 'status');
  } else {
    content.append(el('p', 'eyebrow', '本地工作台'), heading(error ? '本地资料暂不可用' : bases?.length ? '选择资料，开始新的对话' : '还没有知识库'));
    if (error) {
      content.append(alert(error.message), action('重新连接', 'button secondary', () => { void refresh(); }));
    } else {
      content.append(el('p', 'intro', bases?.length ? '这里是你的本地知识库。新建聊天与问答将在后续阶段开放。' : '你可以在「我的知识库」查看本地资料状态。建库、资料上传与入库功能尚未开放。'));
      if (bases?.length) {
        const list = el('ul', 'knowledge-list');
        for (const base of bases) {
          const item = el('li', 'knowledge-row');
          item.append(icon('folder'), el('span', 'knowledge-name', base.name), el('span', 'metadata', kbStatuses[base.status]));
          list.append(item);
        }
        content.append(list);
      }
      content.append(action('前往我的知识库', 'button secondary', () => navigate('knowledge')));
      content.append(el('p', 'scope-note', '一个聊天固定一个知识库。换库时需要新建聊天。'));
    }
  }
  main.append(content, composer());
}
