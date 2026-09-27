import { useEffect, useRef } from 'react';
import { Alert, Button, Drawer, Empty, Space, Tag, Tooltip } from 'antd';
import { Bubble, Sender } from '@ant-design/x';
import { AudioOutlined, BookOutlined, FileTextOutlined, PictureOutlined } from '@ant-design/icons';
import type { ApiClient } from '../api/client';
import type { AppView } from '../app';
import { answerFailure } from '../pages/workbench';
import { locationText } from '../pages/sources';
import { displayCitations } from '../preview/model';
import { StateTag } from '../preview/shared';

export function Workbench({ view, api }: { view: AppView; api: ApiClient }) {
  const { state: s, actions: a } = view;
  const base = s.bases?.find((item) => item.id === s.selectedKbId);
  const busy = s.chatPending || s.chatMessages.some((item) => item.status === 'running');
  const enabled = base?.status === 'ready' && !!s.selectedChatId && !busy && !s.loading;
  const sourceMessage = base?.status === 'ready' ? s.chatMessages.find((item) => item.message_id === s.selectedCitation?.messageId && displayCitations(item).length > 0) : undefined;
  const citation = sourceMessage?.citations.find((item) => item.evidence_id === s.selectedCitation?.evidenceId);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef('');
  useEffect(() => {
    if (citation || !trigger.current) return;
    let frame = 0;
    const restore = () => {
      if (document.querySelector('.formal-source-drawer')) return;
      observer.disconnect();
      frame = requestAnimationFrame(() => Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[data-source-key]') ?? [])
        .find((button) => button.dataset.sourceKey === trigger.current)?.focus());
    };
    const observer = new MutationObserver(restore);
    observer.observe(document.body, { childList: true, subtree: true }); restore();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [citation]);
  return <div ref={root} className="conversation-column formal-conversation">
    <div className="chat-context"><BookOutlined /><span>一个聊天固定一个知识库</span><div className="context-actions">
      <Button size="small" disabled={!s.selectedChatId || busy} onClick={() => void a.renameChat()}>聊天改名</Button>
      <Button size="small" disabled={!s.selectedChatId || busy} onClick={() => void a.deleteChat()}>删除聊天</Button></div></div>
    {s.chatError && <Alert showIcon type="error" title={s.chatError.message} />}
    <div className="message-scroll" aria-live="polite" aria-busy={busy}>
      {!s.chatMessages.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={s.selectedChatId ? '直接提问，自动区分交流与资料查询' : '选择知识库，新建或打开聊天'} />}
      {s.chatMessages.map((m) => {
        const readable = !m.hidden && !m.stale && (m.route === 'general' || m.route === 'chat' || base?.status === 'ready');
        const cited = base?.status === 'ready' && displayCitations(m).length > 0;
        const streaming = m.status === 'running' && s.chatStreamAttemptId === m.attempt_id;
        const content = !readable ? '知识库已变化，此回答与来源已暂停展示。' : m.status === 'running' || !m.saved ? '正在生成并核验，正文提交后再展示。'
          : ['failed', 'interrupted'].includes(m.status) ? answerFailure(m.error_code) : m.text;
        return <article key={m.message_id} className="message-pair"><Bubble placement="end" className="question-bubble" content={m.question} />
          <div><div className="answer-heading"><strong>CiteRAG</strong><StateTag value={m.status === 'answered' && !m.saved ? 'uncommitted' : m.status} />
            <Tag>{m.saved ? '已保存' : '尚未保存'}</Tag></div>
            <div className="answer-text">{content}</div>
            {streaming && <p className="muted">{s.chatStreamSaved ? '结果已保存，等待最终提交' : '核验与保存处理中'}</p>}
            {readable && <p className="muted">{m.route === 'chat' ? '普通交流' : m.route === 'general' ? '通用回答 · 未检索知识库'
              : m.status === 'answered' && m.saved ? '资料回答 · 已保存核验结果' : '结果状态以保存记录为准'}</p>}
            {readable && m.error_code && <p className="failure-reason">{answerFailure(m.error_code)}{ /^[a-z][a-z0-9_]{0,63}$/.test(m.error_code) && <><br />错误代码：{m.error_code}</>}</p>}
            {cited && <div className="source-buttons">{m.citations.map((c) => <Button key={c.evidence_id} icon={<FileTextOutlined />} data-source-key={`${m.message_id}:${c.evidence_id}`}
              onClick={() => { trigger.current = `${m.message_id}:${c.evidence_id}`; a.selectCitation(m.message_id, c.evidence_id); }}>{c.filename} · {locationText(c)}</Button>)}</div>}
            {readable && ['failed', 'interrupted', 'partial'].includes(m.status) && <Button className="retry-answer" disabled={busy || base?.status !== 'ready'} onClick={() => void a.retryChat(m.message_id)}>重试回答</Button>}
          </div></article>;
      })}
    </div>
    <div className="composer"><Sender value={s.chatDraft} onChange={(value) => a.setDraft(value.slice(0, 1000))} onSubmit={() => { if (enabled && s.chatDraft.trim()) void a.sendChat(); }}
      disabled={!enabled} loading={busy} autoSize={{ minRows: 2, maxRows: 6 }}
      placeholder={enabled ? '输入问题，自动识别普通交流或知识库查询' : '先选择就绪知识库并打开聊天'}
      suffix={false} footer={<div className="composer-controls"><Space wrap><Tooltip title="图片提问尚未接入"><Button icon={<PictureOutlined />} disabled>添加图片</Button></Tooltip>
        <span className="composer-kb"><BookOutlined />{base?.name || '未选择知识库'}</span></Space>
        <Space wrap><Button icon={<AudioOutlined />} disabled={busy} onClick={() => a.navigate('voice')}>语音通话</Button>
          <Button type="primary" loading={busy} disabled={!enabled || !s.chatDraft.trim()} onClick={() => void a.sendChat()}>发送问题 ↑</Button></Space></div>} />
      <p className="composer-note">Enter 发送 · Shift+Enter 换行。资料回答只展示已提交且有原文依据的结果。</p></div>
    <Drawer title="原文来源" aria-label="原文来源" rootClassName="formal-source-drawer" focusable={{ focusTriggerAfterClose: false }} open={!!citation} onClose={a.closeCitation} size={460} destroyOnHidden>
      {citation && <div className="source-content"><h2>{citation.filename}</h2><Tag>{locationText(citation)}</Tag>
        <blockquote>{citation.excerpt}</blockquote><Button aria-label="下载原文" href={api.originalUrl(citation.document_id)} icon={<FileTextOutlined />}>下载原文</Button>
        <p className="muted">摘录、文件和定位均来自已保存的核验结果。</p>
        <div className="source-buttons">{sourceMessage?.citations.map((c) => <Button key={c.evidence_id} onClick={() => a.selectCitation(sourceMessage.message_id, c.evidence_id)}>{c.filename} · {locationText(c)}</Button>)}</div></div>}
    </Drawer>
  </div>;
}
