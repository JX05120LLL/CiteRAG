import { Segmented } from 'antd';
import type { PageContext } from './App';
import type { VoicePhase } from './model';
import { usePreviewResource } from './useData';
import { Voice as OfficialVoice } from '../react/Voice';

export function Voice({ location, kb, chat, refresh, navigate, phase, changePhase }: PageContext & {
  phase: VoicePhase; changePhase: (value: VoicePhase) => void;
}) {
  const capability = usePreviewResource(location.data, `voice-capability:${refresh}`, (api) => api.voiceStatus());
  const current = location.data === 'sample' ? phase : 'welcome';
  return <><div className="voice-preview-switch"><span>设计状态</span><Segmented value={current} disabled={location.data !== 'sample'}
    options={['welcome', 'connecting', 'connected', 'reconnecting', 'ending', 'failed'].map((value) => ({ value,
      label: ({ welcome: '欢迎', connecting: '连接中', connected: '已连接', reconnecting: '重连', ending: '挂断中', failed: '失败' } as Record<string, string>)[value] }))}
    onChange={(value) => changePhase(value as VoicePhase)} /><p>只读设计状态，不连接媒体。</p></div>
    <OfficialVoice readOnly context={{ chatId: chat?.id ?? null, chatTitle: chat?.title ?? '', kbName: kb?.name ?? '', kbReady: kb?.status === 'ready', chatPending: false }}
      actions={{ capability: capability.data, checking: capability.loading, capabilityError: !!capability.error,
        media: { phase: current === 'welcome' ? 'idle' : current, muted: false, outputMuted: false, busy: false,
          remoteAudio: 0, playbackRequired: false, meterUnavailable: false, error: current === 'failed' ? 'microphone_denied' : null },
        refresh: async () => {}, connect() {}, hangup() {}, microphone() {}, output() {},
        back: () => navigate('workbench'), status: () => navigate('status') }} />
  </>;
}
