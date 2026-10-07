import type { ChatMessage } from '../api/client';

export function answerRouteLabel(route: ChatMessage['route']): string {
  if (route === 'general' || route === 'chat') return '普通回答 · 未检索知识库';
  if (route === 'exact' || route === 'literal') return '知识库回答 · 精确检索';
  if (route === 'semantic') return '知识库回答 · 语义检索';
  if (route === 'needs_clarification') return '需要澄清';
  if (route === 'unsupported') return '当前查询受限';
  return '回答方式未确定';
}
