import type { ApiError, ChatMessage, Conversation, ExactFilter, KnowledgeBase, SystemHealth } from './api/client';

export type Page = 'workbench' | 'knowledge' | 'status';

export interface KnowledgeDraft {
  name: string;
  pending: boolean;
  error: ApiError | null;
}

export interface CreateKnowledgeDraft extends KnowledgeDraft {
  requestId: string | null;
  requestName: string | null;
  uncertain: boolean;
}

export interface RenameKnowledgeDraft extends KnowledgeDraft {
  id: string;
}

export interface AppState {
  bases: KnowledgeBase[] | null;
  chats: Conversation[] | null;
  page: Page;
  loading: boolean;
  basesError: ApiError | null;
  chatsError: ApiError | null;
  health: SystemHealth | null;
  healthError: ApiError | null;
  healthLoading: boolean;
  createDraft: CreateKnowledgeDraft;
  renameDraft: RenameKnowledgeDraft | null;
  knowledgeNotice: string | null;
  selectedKbId: string | null;
  selectedChatId: string | null;
  chatMessages: ChatMessage[];
  chatStreamText?: string;
  chatStreamAttemptId?: string | null;
  chatDraft: string;
  chatPending: boolean;
  chatError: ApiError | null;
  chatRequestKey: string | null;
  chatRequestText: string | null;
  chatMode: 'semantic' | 'exact';
  exactFilter: ExactFilter;
}

export const knowledgeLimit = 5;

export function isKnowledgePending(state: AppState): boolean {
  return state.createDraft.pending || state.renameDraft?.pending === true;
}

export const kbStatuses: Record<KnowledgeBase['status'], string> = {
  empty: '暂无资料，不可问答', ready: '资料就绪', maintaining: '维护中，问答暂停', blocked: '待修复，问答暂停',
};
