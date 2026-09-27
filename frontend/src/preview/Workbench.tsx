import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Tag } from 'antd';
import { Bubble, Sender } from '@ant-design/x';
import { AudioOutlined, BookOutlined, FileTextOutlined, LockOutlined, PictureOutlined, ArrowUpOutlined } from '@ant-design/icons';
import { createApi, type Citation } from '../api/client';
import type { PageContext } from './App';
import { displayCitations } from './model';
import { usePreviewResource } from './useData';
import { DisabledAction, ResourceState, safeReason, StateTag } from './shared';
const originalUrl = createApi().originalUrl;
export function SourceContent({ citation, sample }: {
    citation: Citation;
    sample: boolean;
}) {
    const locator = Object.entries(citation.locator).map(([key, value]) => `${({ page: '页', paragraph: '段落', table: '表格', row: '行' } as Record<string, string>)[key] ?? key} ${value}`).join(' · ');
    return <div className="source-content"><div className="source-file"><FileTextOutlined /><strong>{citation.filename}</strong></div>
    <Tag>{locator || '定位未记录'}</Tag><div className="section-label">引用摘录</div><blockquote>{citation.excerpt}</blockquote>
    <p className="muted">{sample ? '此文件、摘录和定位为合成设计内容。没有对应的原文文件。' : '摘录与定位来自已保存回答；原文可通过现有下载接口核查。'}</p>
    {sample ? <DisabledAction reason="合成设计样例没有原文文件，不提供虚构下载地址。">下载原文</DisabledAction>
            : <Button href={originalUrl(citation.document_id)} target="_blank" rel="noopener noreferrer">下载原文</Button>}
  </div>;
}
export function Workbench({ location, kb, chat, refresh, navigate }: PageContext) {
    const messages = usePreviewResource(location.data, `messages:${chat?.id ?? ''}:${refresh}`, (api) => chat ? api.conversationMessages(chat.id) : Promise.resolve([]));
    const [source, setSource] = useState<Citation | null>(null);
    const [sourceClosed, setSourceClosed] = useState(!location.source);
    const root = useRef<HTMLDivElement>(null);
    const sourceTrigger = useRef('');
    const focusFrame = useRef<number | null>(null);
    const availableSources = kb?.status === 'ready' ? messages.data?.flatMap(displayCitations) ?? [] : [];
    // Keep only selection identity; the current read must still authorize every displayed source.
    const activeSource = availableSources.find((item) => item.evidence_id === source?.evidence_id && item.document_id === source.document_id) ?? availableSources[0];
    const opened = !sourceClosed && Boolean(activeSource);
    const routes: Record<string, string> = { chat: '普通交流', general: '通用回答 · 未检索资料', semantic: '知识库检索',
        exact: '知识库精确定位', literal: '知识库精确定位', needs_clarification: '待澄清', unsupported: '暂不支持' };
    function close() { setSourceClosed(true); }
    function restoreSourceFocus() {
        // Bubble may replace its footer while opening. Resolve the current button, not a detached node.
        if (focusFrame.current !== null)
            window.cancelAnimationFrame(focusFrame.current);
        focusFrame.current = window.requestAnimationFrame(() => {
            const buttons = root.current?.querySelectorAll<HTMLButtonElement>('[data-source-trigger]');
            const trigger = Array.from(buttons ?? []).find((item) => item.dataset.sourceTrigger === sourceTrigger.current);
            trigger?.focus();
            focusFrame.current = null;
        });
    }
    useEffect(() => () => {
        if (focusFrame.current !== null)
            window.cancelAnimationFrame(focusFrame.current);
    }, []);
    useEffect(() => {
        if (!sourceClosed || !sourceTrigger.current)
            return;
        ;
        // A fast Escape can cancel the opening animation before afterOpenChange fires.
        // Restore once this drawer's portal is removed, and stop observing on unmount.
        const observer = new MutationObserver(() => {
            if (!document.querySelector('.preview-source-drawer')) {
                observer.disconnect();
                restoreSourceFocus();
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
        if (!document.querySelector('.preview-source-drawer')) {
            observer.disconnect();
            restoreSourceFocus();
        }
        return () => observer.disconnect();
    }, [sourceClosed, location.design]);
    const sourcePanel = activeSource && <><div className="source-heading"><div><span className="eyebrow">EVIDENCE</span><h2>原文来源</h2></div>
    <Button onClick={close} size="small">关闭来源</Button></div><SourceContent citation={activeSource} sample={location.data === 'sample'}/></>;
    return <div ref={root} className={`workbench-layout ${opened ? 'with-source' : ''}`}>
    <section className="conversation-column" aria-label="聊天与输入">
      <div className="chat-context"><LockOutlined /><span>一个聊天固定一个知识库</span><div className="context-actions"><DisabledAction>聊天改名</DisabledAction><DisabledAction>删除聊天</DisabledAction></div></div>
      <div className="message-scroll"><ResourceState {...messages} empty={!messages.data?.length}/>
        {messages.data?.map((message) => {
            const citations = kb?.status === 'ready' ? displayCitations(message) : [];
            const visible = !message.hidden && !message.stale && message.saved && ['answered', 'partial', 'insufficient_evidence', 'needs_clarification', 'conflicting_evidence'].includes(message.status);
            return <div className="message-pair" key={message.message_id}>
            <Bubble placement="end" content={message.question} variant="filled" shape="round" className="question-bubble"/>
            <Bubble placement="start" variant="borderless" content={<div className="answer-text">{visible ? message.text : message.hidden || message.stale
                        ? '资料已变化，此回答暂不展示。请核对当前知识库后重新提问。' : message.status === 'running' || !message.saved && message.status === 'answered'
                        ? '等待回答核验与保存，尚未发布正文和引用。' : safeReason(message.error_code)}</div>} header={<div className="answer-heading"><strong>CiteRAG 回答</strong><StateTag value={message.status === 'answered' && !message.saved ? 'uncommitted' : message.status}/></div>} footer={<div className="answer-footer"><Tag>{routes[message.route ?? 'semantic'] ?? '路由未确认'}</Tag>
                {message.status === 'partial' && <Alert type="warning" title="未完成片段，不是完整回答；预览中不能重试。"/>}
                <div className="source-buttons">{citations.map((citation) => <Button icon={<FileTextOutlined />} key={citation.evidence_id} data-source-trigger={`${citation.document_id}:${citation.evidence_id}`} aria-label={`查看来源：${citation.filename}`} onClick={() => { sourceTrigger.current = `${citation.document_id}:${citation.evidence_id}`; setSource(citation); setSourceClosed(false); }}>{citation.filename}</Button>)}</div>
                {['failed', 'interrupted', 'partial'].includes(message.status) && <DisabledAction>重试回答</DisabledAction>}</div>}/>
          </div>;
        })}
      </div>
      <div className="composer"><Sender disabled placeholder="输入问题；普通交流、通用回答与资料检索自动分流" autoSize={{ minRows: 2, maxRows: 5 }} suffix={<Button disabled icon={<ArrowUpOutlined />} aria-label="发送问题">发送问题</Button>} footer={<div className="composer-controls"><div><DisabledAction icon={<PictureOutlined />} reason="图片提问尚未接入，候选预览也不上传图片。">添加图片</DisabledAction>
          <span className="composer-kb"><BookOutlined />{kb?.name ?? '未选择知识库'}</span></div>
          <Button icon={<AudioOutlined />} onClick={() => navigate('voice')}>语音通话</Button></div>}/>
        <p className="composer-note">预览不发送问题。选版后保留自动分流与原有 SSE 核验、保存和引用协议。</p></div>
    </section>
    {null}
    <Drawer title="原文来源" aria-label="原文来源" open={opened && true} onClose={close} destroyOnHidden size={420} rootClassName="preview-source-drawer" focusable={{ focusTriggerAfterClose: false }}>{sourcePanel}</Drawer>
  </div>;
}
