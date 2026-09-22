import type { ApiError, Conversation, KnowledgeBase, SystemHealth } from './api/client';

export type Page = 'workbench' | 'knowledge' | 'status';

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
}

export const kbStatuses: Record<KnowledgeBase['status'], string> = {
  empty: '暂无资料', ready: '资料就绪', maintaining: '维护中，问答暂停', blocked: '待修复，问答暂停',
};
