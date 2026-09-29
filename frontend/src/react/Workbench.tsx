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
import { answerRouteLabel } from './answerRoute';
import logo from '../../../assets/brand/mark.svg';

const messageTime = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });

export function Workbench({ view, api }: { view: AppView; api: ApiClient }) {
  const { state: s, actions: a } = view;
  const base = s.bases?.find((item) => item.id === s.selectedKbId);
  const busy = s.chatPending || s.chatMessages.some((item) => item.status === 'running');
  const enabled = base?.status === 'ready' && !!s.selectedChatId && !busy && !s.loading;
  const sourceMessage = base?.status === 'ready' ? s.chatMessages.find((item) => item.message_id === s.selectedCitation?.messageId && displayCitations(item).length > 0) : undefined;
  const citation = sourceMessage?.citations.find((item) => item.evidence_id === s.selectedCitation?.evidenceId);
  const root = useRef<HTMLDivElement>(null);
  const imageInput = useRef<HTMLInputElement>(null);
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
    <div className="chat-context"><BookOutlined /><span>当前对话固定知识库 · {base?.name || '未选择'}</span></div>
    {s.chatError && <Alert showIcon type="error" title={s.chatError.message} />}
    <div className="message-scroll" aria-live="polite" aria-busy={busy}>
      {!s.chatMessages.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={s.selectedChatId ? '直接提问，自动区分交流与资料查询' : '选择知识库，新建或打开聊天'} />}
      {s.chatMessages.map((m) => {
        const readable = !m.hidden && !m.stale && (m.route === 'general' || m.route === 'chat' || base?.status === 'ready');
        const cited = base?.status === 'ready' && displayCitations(m).length > 0;
        const streaming = m.status === 'running' && s.chatStreamAttemptId === m.attempt_id;
        const content = !readable ? '知识库已变化，此回答与来源已暂停展示。' : m.status === 'running' || !m.saved ? '正在生成并核验，正文提交后再展示。'
          : ['failed', 'interrupted'].includes(m.status) ? answerFailure(m.error_code)
            : m.status === 'insufficient_evidence' ? '当前知识库没有足够的可核查证据，暂不作答。'
              : m.status === 'needs_clarification' ? m.text || '请补充查询对象或范围。'
                : m.status === 'conflicting_evidence' ? m.text || '当前资料存在冲突，请核查原文。' : m.text;
        return <article key={m.message_id} className="message-pair"><div className="question-line"><Bubble placement="end" className="question-bubble" content={m.question} /><time dateTime={m.created_at}>{messageTime(m.created_at)}</time></div>
          {!!m.images?.length && <div className="message-images">{m.images.map((image) =>
            <div key={image.id} className="message-image"><span>{image.filename}</span>
              {Date.parse(image.expires_at) > Date.now() ?
                <img src={api.imageUrl(s.selectedChatId!, image.id)} alt={`提问图片：${image.filename}`} /> :
                <span className="muted">图片已过期</span>}
              {image.observation && <p><strong>图片观察：</strong>{image.observation}</p>}
              {image.observation_status === 'failed' && <p className="failure-reason">图片识别失败，具体原因见下方。排除原因后可重试回答。</p>}
              {image.needs_confirmation && !image.confirmed_identifier && <Button disabled={busy}
                onClick={() => void a.confirmChatImage(m.message_id, image.id)}>确认图片编号</Button>}
              {image.confirmed_identifier && <p>已确认编号：{image.confirmed_identifier}</p>}
            </div>)}</div>}
          <div className="answer-line"><span className="answer-avatar"><img src={logo} alt="" /></span><div className="answer-card"><div className="answer-heading"><strong>{answerRouteLabel(m.route).split(' · ')[0]}</strong><StateTag value={m.status === 'answered' && !m.saved ? 'uncommitted' : m.status} />
            {!m.saved && <Tag>尚未保存</Tag>}</div>
            <div className="answer-text">{content}</div>
            {streaming && <p className="muted">{s.chatStreamSaved ? '结果已保存，等待最终提交' : '核验与保存处理中'}</p>}
            {readable && m.route !== 'needs_clarification' && <p className="muted">{answerRouteLabel(m.route)}</p>}
            {readable && m.error_code && <p className="failure-reason">{!['failed', 'interrupted'].includes(m.status) && <>{answerFailure(m.error_code)}<br /></>}{ /^[a-z][a-z0-9_]{0,63}$/.test(m.error_code) && <>错误代码：{m.error_code}</>}</p>}
            {cited && <div className="source-buttons">{m.citations.map((c) => <Button key={c.evidence_id} icon={<FileTextOutlined />} data-source-key={`${m.message_id}:${c.evidence_id}`}
              onClick={() => { trigger.current = `${m.message_id}:${c.evidence_id}`; a.selectCitation(m.message_id, c.evidence_id); }}>查看来源 · {c.filename} · {locationText(c)} <span aria-hidden="true">›</span></Button>)}</div>}
            {readable && (['failed', 'interrupted', 'partial'].includes(m.status) || m.status === 'needs_clarification' &&
              !!m.images?.some((image) => image.needs_confirmation) &&
              !!m.images?.every((image) => !image.needs_confirmation || !!image.confirmed_identifier)) && <Button className="retry-answer" disabled={busy || base?.status !== 'ready'}
                onClick={() => void a.retryChat(m.message_id)}>重试回答</Button>}
          </div><time className="answer-time" dateTime={m.created_at}>{messageTime(m.created_at)}</time></div></article>;
      })}
    </div>
    <div className="composer"><input ref={imageInput} type="file" accept="image/png,image/jpeg" multiple hidden
      aria-label="选择提问图片" onChange={(event) => { a.selectChatImages(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
      {!!s.chatImages.length && <div className="selected-images" aria-label="待发送图片">{s.chatImages.map((file, index) =>
        <span key={`${file.name}-${index}`}>{file.name} <Button size="small" disabled={busy}
          onClick={() => a.selectChatImages(s.chatImages.filter((_, item) => item !== index))}>移除</Button></span>)}</div>}
      <Sender value={s.chatDraft} onChange={(value) => a.setDraft(value.slice(0, 1000))} onSubmit={() => { if (enabled && s.chatDraft.trim()) void a.sendChat(); }}
      disabled={!enabled} loading={busy} autoSize={{ minRows: 2, maxRows: 6 }}
      placeholder={enabled ? '输入问题，自动识别普通交流或知识库查询' : '先选择就绪知识库并打开聊天'}
      suffix={false} footer={<div className="composer-controls"><Space wrap><Tooltip title="每条问题最多 2 张 PNG/JPEG，每张 10 MiB"><Button icon={<PictureOutlined />}
        disabled={!enabled} onClick={() => imageInput.current?.click()}>添加图片</Button></Tooltip>
        <span className="composer-kb"><BookOutlined />{base?.name || '未选择知识库'}</span></Space>
        <Space wrap><Button icon={<AudioOutlined />} disabled={busy} onClick={() => a.navigate('voice')}>语音通话</Button>
          <Button type="primary" loading={busy} disabled={!enabled || !s.chatDraft.trim()} onClick={() => void a.sendChat()}>发送问题 ↑</Button></Space></div>} />
      <p className="composer-note">Enter 发送 · Shift+Enter 换行。资料回答只展示已提交且有原文依据的结果。</p></div>
    <Drawer title="原文来源" aria-label="原文来源" rootClassName="formal-source-drawer" focusable={{ focusTriggerAfterClose: false }} open={!!citation} onClose={a.closeCitation} size={460} destroyOnHidden
      footer={citation && <div className="source-drawer-actions"><Button aria-label="下载原文" href={api.originalUrl(citation.document_id)} icon={<FileTextOutlined />}>下载原文</Button><Button type="primary" onClick={a.closeCitation}>关闭来源</Button></div>}>
      {citation && <div className="source-content"><div className="source-file-card"><h2><FileTextOutlined />{citation.filename}</h2><p><BookOutlined />{locationText(citation)}</p></div>
        <h3>原文内容（节选）</h3><blockquote>{citation.excerpt}</blockquote>
        <p className="muted">摘录、文件和定位均来自已保存的核验结果。</p>
        <div className="source-buttons">{sourceMessage?.citations.map((c) => <Button key={c.evidence_id} onClick={() => a.selectCitation(sourceMessage.message_id, c.evidence_id)}>{c.filename} · {locationText(c)}</Button>)}</div></div>}
    </Drawer>
  </div>;
}
