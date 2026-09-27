import { Alert, Button, Card, Descriptions, Space, Tag, Tooltip } from 'antd';
import { AudioOutlined, AudioMutedOutlined, ArrowLeftOutlined, PhoneOutlined, ReloadOutlined, SoundOutlined } from '@ant-design/icons';
import type { VoiceActions, VoiceContext } from '../pages/voice';
import { StateTag } from '../preview/shared';

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
};
const labels = { idle: '开始一段通话', connecting: '正在连接本地媒体', connected: '媒体已连接',
  reconnecting: '正在重新连接', ending: '正在挂断', failed: '连接未完成' };
export function Voice({ context, actions, levels = [], readOnly = false }: {
  context: VoiceContext; actions: VoiceActions; levels?: readonly number[]; readOnly?: boolean;
}) {
  const media = actions.media;
  const active = ['connecting', 'connected', 'reconnecting', 'ending'].includes(media.phase);
  const disabled = readOnly || actions.checking || actions.capabilityError || !context.chatId || !context.kbReady ||
    context.chatPending || !!context.error || actions.capability?.transport !== 'configured';
  const condition = actions.checking ? '正在读取连接条件' : context.error ?? (!context.chatId ? '请返回工作台选择聊天'
    : !context.kbReady ? '固定知识库尚未就绪' : context.chatPending ? '请等待当前回答结束'
      : actions.capabilityError ? '连接条件读取失败，请刷新' : actions.capability?.transport === 'disabled' ? '本地媒体测试未启用'
        : actions.capability?.transport !== 'configured' ? '本地 LiveKit 尚未配置' : '点击按钮后才会发 Token、连接房间并申请麦克风权限');
  return <div className="official-voice">
    <div className="voice-context-brief"><Space wrap><Tag>{context.chatTitle || '尚未选择聊天'}</Tag>
      <span>固定知识库：{context.kbName || '尚未读取'}</span></Space>
      <Button title={readOnly ? '只读设计状态，请使用正式语音入口刷新实际条件' : undefined} icon={<ReloadOutlined />} disabled={readOnly || active || actions.checking} loading={actions.checking} onClick={() => void actions.refresh()}>刷新连接条件</Button></div>
    <Card className="official-voice-stage">
      <div className="voice-stage-heading"><span>本地音频连接</span><StateTag value={media.phase === 'connected' ? 'connected' : media.phase === 'failed' ? 'failed' : 'unverified'} /></div>
      {active ? <div className="official-voice-bars" aria-hidden="true">{[0, 1, 2, 3, 4].map((i) => <span key={i}
        style={{ height: media.phase === 'connected' && !media.muted && !media.meterUnavailable ? `${16 + Math.min(1, Math.max(0, levels[i] ?? 0)) * 80}px` : '16px' }} />)}</div>
        : <div className="official-welcome-bars" aria-hidden="true">{[22, 54, 38, 22, 30].map((height, i) => <span key={i} style={{ height }} />)}</div>}
      <h2>{labels[media.phase]}</h2>
      <p className="voice-hint">{active ? `当前播放轨道：${media.remoteAudio} · ${media.muted ? '麦克风已静音' : media.phase === 'connected' ? '麦克风采集中' : '等待连接状态'}` : condition}</p>
      {media.meterUnavailable && <p className="muted">音量分析不可用，连接状态不受影响。</p>}
      <div className="official-voice-controls">
        {(!active || readOnly) && <Tooltip title={readOnly ? '只读设计预览，不发 Token 或申请麦克风' : condition}><Button aria-label="测试本地音频连接" type="primary" size="large" icon={<PhoneOutlined />} disabled={disabled} onClick={actions.connect}>测试本地音频连接</Button></Tooltip>}
        {active && <><Button icon={media.muted ? <AudioMutedOutlined /> : <AudioOutlined />} disabled={readOnly || media.phase !== 'connected' || media.busy} onClick={actions.microphone}>{media.muted ? '取消静音' : '静音'}</Button>
          <Button icon={<SoundOutlined />} disabled={readOnly || media.phase !== 'connected' || media.busy} onClick={actions.output}>{media.outputMuted || media.playbackRequired ? '开启声音' : '关闭声音'}</Button>
          <Tooltip title="ASR 与字幕尚未接入"><Button disabled>字幕未接入</Button></Tooltip>
          <Button aria-label="挂断" danger icon={<PhoneOutlined />} disabled={readOnly || media.phase === 'ending'} onClick={actions.hangup}>挂断</Button></>}
      </div>
      {media.error && <Alert showIcon type="error" title={reasons[media.error] ?? '连接未完成，请检查本地服务与设备权限后重试。'} />}
      {context.error && <Alert showIcon type="error" title={context.error} />}
      <Alert className="voice-capability-note" showIcon type="info" title="目前仅接通媒体，语音助手尚未接入。"
        description="ASR、知识库语音回答、TTS 和字幕均未接入；这里不会生成模拟字幕或回答。欢迎图形是静态装饰，通话音量仅来自真实采集。" />
    </Card>
    <Descriptions className="voice-capabilities" column={1} items={[
      { key: 'transport', label: '媒体配置', children: <StateTag value={actions.capability?.transport ?? 'unverified'} /> },
      { key: 'assistant', label: '语音助手', children: <StateTag value={actions.capability?.assistant ?? 'unverified'} /> },
    ]} />
    <div className="voice-return"><Button icon={<ArrowLeftOutlined />} onClick={() => void actions.back()}>{active ? '挂断并返回聊天' : '返回文字工作台'}</Button>
      <Button onClick={() => void actions.status()}>查看系统状态</Button></div>
  </div>;
}
