import type { ChatMessage, Citation } from '../api/client';

export type PreviewDesign = 'b';
export type PreviewPage = 'workbench' | 'knowledge' | 'tasks' | 'status' | 'voice';
export type PreviewDataMode = 'live' | 'sample';
export type VoicePhase = 'welcome' | 'connecting' | 'connected' | 'reconnecting' | 'ending' | 'failed';
export interface PreviewLocation {
  design: PreviewDesign;
  page: PreviewPage;
  data: PreviewDataMode;
  phase: VoicePhase;
  source: boolean;
}

export function parsePreviewLocation(search: string): PreviewLocation {
  const params = new URLSearchParams(search);
  const page = params.get('page'); const phase = params.get('phase');
  return {
    design: 'b',
    page: ['knowledge', 'tasks', 'status', 'voice'].includes(page ?? '') ? page as PreviewPage : 'workbench',
    data: params.get('data') === 'sample' ? 'sample' : 'live',
    phase: ['connecting', 'connected', 'reconnecting', 'ending', 'failed'].includes(phase ?? '')
      ? phase as VoicePhase : 'welcome',
    source: params.get('source') !== 'closed',
  };
}

export function displayCitations(message: ChatMessage): Citation[] {
  return message.status === 'answered' && message.saved && !message.hidden && !message.stale &&
    message.route !== 'general' && message.route !== 'chat' ? message.citations : [];
}

export const pageTitles: Record<PreviewPage, string> = {
  workbench: '对话工作台', knowledge: '知识库与资料', tasks: '处理任务', status: '系统状态', voice: '语音通话',
};

export const designs = {
  b: { name: '对话优先知识助手', summary: '对话居中，资料按需展开', accent: '#24765d', radius: 10, density: 'middle' },
} as const;
