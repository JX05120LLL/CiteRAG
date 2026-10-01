import { useState } from 'react';
import { Alert, Button, Card, Descriptions, Drawer, Input, Modal, Space, Tag, Tooltip } from 'antd';
import { AudioOutlined, AudioMutedOutlined, ArrowLeftOutlined, FileTextOutlined, PhoneOutlined, ReloadOutlined, SoundOutlined, UserOutlined } from '@ant-design/icons';
import type { VoiceActions, VoiceContext } from '../pages/voice';
import { StateTag } from '../preview/shared';
import { displayCitations } from '../preview/model';
import { answerRouteLabel } from './answerRoute';
import { locationText } from '../pages/sources';
import { answerFailure } from '../pages/workbench';
import logo from '../../../assets/brand/mark.svg';
import { AgentTaskCard } from './AgentTasks';

// React adaptation of LiveKit's MIT welcome-view and agent-session-view-01.
// Source pinned in vendor/livekit/README.md. Media remains owned by VoiceController.
const reasons: Record<string, string> = {
  microphone_denied: '麦克风权限被拒绝。请在站点权限中允许，再重新连接。',
  microphone_missing: '未找到麦克风。请连接输入设备后重试。',
  microphone_failed: '麦克风操作失败。请挂断后检查设备与权限。',
  playback_failed: '浏览器阻止播放。请再次点击开启声音，或检查输出设备。',
  disconnected: '连接已断开，麦克风已停止。请检查本地 LiveKit 服务后重新连接。',
  connection_failed: '连接失败。请检查本地 LiveKit 服务、端口与浏览器音频权限。',
  voice_transport_disabled: '后端尚未启用本地媒体测试。',
  voice_transport_not_configured: '本地 LiveKit 配置或 voice 可选依赖未准备好。',
  kb_not_ready: '知识库尚未就绪，请先完成资料维护。', answer_running: '当前回答正在处理，请结束后再连接。',
  voice_active: '此聊天已有通话控制端。请在原标签页挂断，或等待租约回收。',
  voice_assistant_not_configured: 'ASR、TTS、VAD 或问答尚未配置。请检查后端配置，或返回文字工作台。',
  voice_cleanup_unavailable: '本地房间回收未通过。请检查 LiveKit 服务后重启 API。',
  voice_cleanup_failed: '本地麦克风已停止，服务端房间撤销尚未确认。请再次挂断；文字门禁仍可能保持。',
  asr_provider_failed: '语音识别服务失败。请核对服务能力、配额和网络，或挂断后改用文字。',
  asr_empty: '没有识别到有效文字。请靠近麦克风说话，或改用文字。',
  asr_audio_limit: '这段语音过长。请分成较短问题再说。',
  asr_text_limit: '转写超过文字问题上限。请缩短问题后重试。',
  tts_provider_failed: '语音合成服务失败。已保存的文字和来源仍可查看，请检查 TTS 配置与配额。',
  tts_decode_failed: '语音解码未完成。请阅读已保存文字，或挂断后继续文字问答。',
  tts_stream_interrupted: '播报流中断。不会自动重播，已保存文字仍可查看。',
  tts_text_limit: '已保存回答较长，本轮转为文字展示。请阅读正文或缩小问题范围后再播报。',
  tts_audio_limit: '语音响应超过处理上限，本轮转为文字展示。请阅读已保存回答或缩短问题。',
  asr_protocol_invalid: '识别响应格式不符合配置。请检查 ASR 资源与协议，或挂断后用文字提问。',
  voice_turn_failed: '本轮语音问答未完成。请核对已保存聊天与服务状态，或挂断后重试文字回答。',
  voice_session_ended: '通话已中止。请刷新知识库与聊天状态后重新开始；旧音频不会重播。',
  voice_lease_expired: '通话租约已失效。请重新开始；已保存的聊天文字仍在。',
  voice_events_failed: '字幕事件连接断开，已停止采集。请检查 API 后重新开始。',
  voice_worker_failed: '语音 worker 不可用。请核对本地 LiveKit、VAD 模型与后端依赖。',
  voice_input_failed: '音频接收中断或积压。请挂断并核对网络、麦克风及识别服务。',
  voice_stop_failed: '停止请求未确认，已停止本地播放与采集。请再次挂断。',
  transcript_stale: '转写已变化。请关闭纠错窗口，再核对最新转写。',
  voice_history_failed: '聊天记录读取失败。请返回文字工作台刷新；此处未伪造历史。',
};
const labels = { idle: '开始一段通话', connecting: '正在连接本地媒体', connected: '媒体已连接',
  reconnecting: '正在重新连接', ending: '正在挂断', failed: '连接未完成' };
export function Voice({ context, actions, levels = [], readOnly = false }: {
  context: VoiceContext; actions: VoiceActions; levels?: readonly number[]; readOnly?: boolean;
}) {
  const media = actions.media;
  const [source, setSource] = useState<{ message: string; evidence: string } | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState('');
  const assistant = actions.capability?.purpose === 'voice_assistant';
  const phaseLabels: Record<string, string> = { starting: '等待助手就绪', listening: '助手就绪，等待说话',
    recognizing: '正在识别', generating: '正在生成并核验', speaking: '正在发送播报音频', ended: '助手已停止', not_configured: '助手未配置',
    waiting_input: '任务等待补充，请在页面填写', waiting_approval: '任务等待审批，请核对操作后点击' };
  const selected = media.answers?.find((answer) => answer.message_id === source?.message);
  const citation = selected && displayCitations(selected).find((item) => item.evidence_id === source?.evidence);
  const active = ['connecting', 'connected', 'reconnecting', 'ending'].includes(media.phase);
  const answers = media.answers ?? [];
  const answerCard = (answer: typeof answers[number]) => <article className="voice-exchange" key={answer.message_id}>
    <div className="voice-user-row"><span className="voice-user-avatar"><UserOutlined /></span><div className="voice-user-card"><strong><AudioOutlined /> 用户（语音输入）</strong>
      {answer.question.length > 160 ? <details className="voice-question"><summary>{answer.question.slice(0, 80)}…（展开全文）</summary><p>{answer.question}</p></details> : <p>{answer.question}</p>}</div></div>
    <div className="voice-assistant-row"><span className="voice-assistant-avatar"><img src={logo} alt="" /></span><div className="voice-assistant-card"><Space wrap><strong>{answerRouteLabel(answer.route).split(' · ')[0]}</strong><StateTag value={answer.status} />{!answer.saved && <Tag>未保存</Tag>}</Space>
    <p className="voice-answer">{answer.hidden || answer.stale ? '资料已变化，旧回答与来源暂停展示。'
      : ['failed', 'interrupted'].includes(answer.status) ? answerFailure(answer.error_code)
        : answer.status === 'insufficient_evidence' ? '当前资料没有足够证据，请补充或缩小问题范围。'
          : !answer.saved ? '回答尚未确认保存，正文暂不展示。' : answer.text}</p>
    <p className="muted voice-route-detail">{answerRouteLabel(answer.route)}</p>
    {displayCitations(answer).map((item) => <Button key={item.evidence_id} icon={<FileTextOutlined />} className="voice-source-button" onClick={() => setSource({ message: answer.message_id, evidence: item.evidence_id })}>查看来源 · {item.filename} · {locationText(item)} <span aria-hidden="true">›</span></Button>)}</div></div>
  </article>;
  const disabled = readOnly || actions.checking || actions.capabilityError || !context.chatId || !context.kbReady ||
    context.chatPending || !!context.error || actions.capability?.transport !== 'configured' ||
    (assistant && actions.capability?.assistant !== 'configured');
  const condition = actions.checking ? '正在读取连接条件' : context.error ?? (!context.chatId ? '请返回工作台选择聊天'
    : !context.kbReady ? '固定知识库尚未就绪' : context.chatPending ? '请等待当前回答结束'
      : actions.capabilityError ? '连接条件读取失败，请刷新' : actions.capability?.transport === 'disabled' ? '本地媒体测试未启用'
        : actions.capability?.transport !== 'configured' ? '本地 LiveKit 尚未配置'
          : assistant && actions.capability?.assistant !== 'configured' ? 'ASR、TTS、VAD 或问答未配置，请使用文字问答'
            : '点击按钮后才会创建通话、连接房间并申请麦克风权限');
  return <div className="official-voice">
    <div className="voice-context-brief"><Space wrap><Tag>{context.chatTitle || '尚未选择聊天'}</Tag>
      <span>{context.chatId && !context.kbName ? '普通聊天 · 不检索知识库' : `固定知识库：${context.kbName || '尚未读取'}`}</span></Space>
      <Button title={readOnly ? '只读设计状态，请使用正式语音入口刷新实际条件' : undefined} icon={<ReloadOutlined />} disabled={readOnly || active || actions.checking} loading={actions.checking} onClick={() => void actions.refresh()}>刷新连接条件</Button></div>
    <Card className="official-voice-stage"><div className="voice-stage-main">
      <div className={`voice-call-symbol${active ? ' is-active' : ''}`} aria-hidden="true"><img src={logo} alt="" /></div>
      <div className="voice-call-state"><Space wrap>{media.phase === 'connected' ? <StateTag value="connected" />
        : media.phase === 'failed' ? <StateTag value="failed" /> : <Tag className="state-tag">媒体待连接</Tag>}
        {assistant && <Tag className="state-tag" color={active && media.assistantPhase === 'listening' ? 'success' : 'warning'}>
          {active && media.assistantPhase === 'listening' ? '助手就绪' : active ? '助手待就绪' : '助手待连接'}</Tag>}</Space>
      <h2>{labels[media.phase]}</h2>
      {assistant && <p role="status">{media.phase === 'idle' && actions.capability?.assistant === 'configured'
        ? '尚未开始，助手连接待验证' : media.phase === 'connecting' && media.assistantPhase === 'not_configured' && actions.capability?.assistant === 'configured'
          ? '等待助手就绪' : phaseLabels[media.assistantPhase ?? 'starting']}</p>}
      <p className="voice-hint">{active ? `当前播放轨道：${media.remoteAudio} · ${media.muted ? '麦克风已静音' : media.phase === 'connected' ? '麦克风采集中' : '等待连接状态'}` : condition}</p></div>
      <div className="voice-meter"><div className="official-voice-bars" aria-hidden="true">{[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15].map((i) => <span key={i}
        style={{ height: active && media.phase === 'connected' && !media.muted && !media.meterUnavailable ? `${6 + Math.min(1, Math.max(0, levels[i % 5] ?? 0)) * (14 + i % 4 * 4)}px` : '6px' }} />)}</div>
        <span><SoundOutlined /> {!active ? '尚未播放' : media.outputMuted || media.playbackRequired ? '播放已关闭' : '播放已开启'}</span></div></div>
      {assistant && active && <div className="voice-inline-caption"><AudioOutlined /> <strong>{media.subtitle ? '正在识别：' : '实时字幕：'}</strong><span aria-live="polite">{media.subtitle || '等待实际语音转写'}</span></div>}
      {assistant && active && media.speechText && <div className="voice-inline-caption"><SoundOutlined /> <strong>正在播报：</strong><span aria-live="polite">{media.speechText}</span></div>}
      {assistant && active && (media.firstTextMs !== undefined || media.firstAudioSentMs !== undefined) &&
        <p className="voice-caption-note muted">最终转写后：首段文字 {media.firstTextMs === undefined ? '等待中' : `${media.firstTextMs} ms`} · 首段音频送出 {media.firstAudioSentMs === undefined ? '等待中' : `${media.firstAudioSentMs} ms`}（服务端计时，不代表实际听见）</p>}
      {media.meterUnavailable && <p className="muted">音量分析不可用，连接状态不受影响。</p>}
      <div className="official-voice-controls">
        {(!active || readOnly) && <Tooltip title={readOnly ? '只读设计预览，不发 Token 或申请麦克风' : condition}><Button aria-label={assistant ? '开始语音通话' : '测试本地音频连接'} type="primary" size="large" icon={<PhoneOutlined />} disabled={disabled} onClick={actions.connect}>{assistant ? '开始语音通话' : '测试本地音频连接'}</Button></Tooltip>}
        {active && <><Button icon={media.muted ? <AudioMutedOutlined /> : <AudioOutlined />} disabled={readOnly || media.phase !== 'connected' || media.busy} onClick={actions.microphone}>{media.muted ? '取消静音' : '静音'}</Button>
          <Button icon={<SoundOutlined />} disabled={readOnly || media.phase !== 'connected' || media.busy} onClick={actions.output}>{media.outputMuted || media.playbackRequired ? '开启声音' : '关闭声音'}</Button>
          {assistant ? <><Button disabled={readOnly || media.busy || !actions.stop || media.phase !== 'connected'} onClick={actions.stop}>停止回答</Button>
            <Button disabled={readOnly || media.busy || !media.finalTranscript || !actions.correct || media.phase !== 'connected'} onClick={() => { setCorrection(media.finalTranscript ?? ''); setCorrecting(true); }}>纠正转写</Button></>
            : <Tooltip title="ASR 与字幕尚未接入"><Button disabled>字幕未接入</Button></Tooltip>}
          <Button aria-label="挂断" danger icon={<PhoneOutlined />} disabled={readOnly || media.phase === 'ending'} onClick={actions.hangup}>挂断</Button></>}
      </div>
      {media.error && <Alert showIcon type="error" title={reasons[media.error] ?? '连接未完成，请检查本地服务与设备权限后重试。'} />}
      {media.error === 'voice_cleanup_failed' && <Button onClick={actions.hangup} disabled={readOnly || media.busy}>重试撤销通话</Button>}
      {context.error && <Alert showIcon type="error" title={context.error} />}
    </Card>
    {(assistant || answers.length > 0) && <section className="voice-dialogue" aria-label="当前对话的语音字幕与已保存回答">
      {assistant && active && <p className="voice-caption-note muted">{media.subtitle && media.subtitle === media.finalTranscript ? '最终转写，保存后进入当前对话' : '临时字幕，尚未保存为问题'}</p>}
      {media.agentRun && <AgentTaskCard key={`${media.agentRun.id}:${media.agentRun.generation}`} run={media.agentRun}
        voiceControl={!readOnly && !!actions.resumeAgent && active}
        resume={async (input) => { await actions.resumeAgent?.(input); }}
        cancel={async () => { await actions.stop?.(); }} />}
      <h2 className="voice-record-heading">当前对话记录</h2>
      {answers.length > 3 && <details className="voice-history"><summary>早前记录（{answers.length - 3} 条）</summary>
        <div className="voice-dialogue">{answers.slice(0, -3).map(answerCard)}</div></details>}
      {answers.slice(-3).map(answerCard)}
      {!answers.length && <p className="muted">最终转写提交后，问题与已保存回答会出现在这里和文字工作台。</p>}
    </section>}
    <details className="voice-explainer"><summary>通话说明与连接条件</summary><p className="muted">{assistant ? '最终转写才创建聊天问题；资料回答核验并保存后才播报。插话与停止会清除旧播报，重连不续播。通话中要发送文字或图片，请先挂断。原始音频不保存。' : '当前只有本地媒体连接，没有识别、字幕与助手回答。'}</p>
    <Descriptions className="voice-capabilities" column={1} items={[
      { key: 'transport', label: '媒体配置', children: actions.capability?.transport === 'configured' ? <Tag>已配置</Tag> : <StateTag value={actions.capability?.transport ?? 'unverified'} /> },
      { key: 'assistant', label: '助手配置', children: actions.capability?.assistant === 'configured' ? <Tag>已配置</Tag> : <StateTag value={actions.capability?.assistant ?? 'unverified'} /> },
    ]} /></details>
    <div className="voice-return"><Button icon={<ArrowLeftOutlined />} onClick={() => void actions.back()}>{active ? '挂断并返回聊天' : '返回文字工作台'}</Button>
      <Button onClick={() => void actions.status()}>查看系统状态</Button></div>
    <Drawer title="语音回答来源" open={!!citation} onClose={() => setSource(null)} size={460} destroyOnHidden>
      {citation && <div className="source-content"><h2>{citation.filename}</h2><Tag>{locationText(citation)}</Tag><blockquote>{citation.excerpt}</blockquote>
        <Button href={actions.originalUrl?.(citation.document_id)} download={citation.filename} disabled={!actions.originalUrl}>下载原文</Button></div>}
    </Drawer>
    <Modal title="纠正最新转写" open={correcting} onCancel={() => setCorrecting(false)} okText="取消旧轮次并重新回答" cancelText="取消"
      confirmLoading={media.busy} okButtonProps={{ disabled: readOnly || !correction.trim() || !actions.correct || !active }}
      onOk={async () => { await actions.correct?.(correction); setCorrecting(false); }}>
      <p>原转写和已保存记录保留。纠正会停止旧回答，创建新的聊天输入。</p>
      <Input.TextArea aria-label="修正转写" value={correction} onChange={(event) => setCorrection(event.target.value)} maxLength={1000} rows={4} />
    </Modal>
  </div>;
}
