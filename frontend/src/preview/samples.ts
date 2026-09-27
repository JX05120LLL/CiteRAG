// Deliberately synthetic design content. Never used as an API fallback or business evidence.
import type { ChatMessage, Conversation, IngestionJob, KnowledgeBase, ManagedDocument,
  SystemHealth, VoiceCapability } from '../api/client';

export const sampleBases: KnowledgeBase[] = [
  { id: 'design-library-1', name: '产品说明与故障查询', status: 'ready' },
  { id: 'design-library-2', name: '团队制度与操作流程', status: 'maintaining' },
  { id: 'design-library-3', name: '待整理资料', status: 'empty' },
];

export const sampleChats: Conversation[] = [
  { id: 'design-chat-1', owner_id: 'design-only', kb_id: 'design-library-1',
    title: '了解资料检索项目', created_at: '2026-09-27T01:21:00Z' },
  { id: 'design-chat-2', owner_id: 'design-only', kb_id: 'design-library-1',
    title: '通用概念与普通交流', created_at: '2026-09-26T08:42:00Z' },
  { id: 'design-chat-3', owner_id: 'design-only', kb_id: 'design-library-1',
    title: '中断恢复状态样例', created_at: '2026-09-26T03:13:00Z' },
];

export const sampleMessages: Record<string, ChatMessage[]> = {
  'design-chat-1': [{
    message_id: 'design-message-1', attempt_id: 'design-attempt-1', client_message_id: 'design-input-1',
    question: '介绍一下资料中的项目，以及它如何保证回答有依据。', mode: 'auto', route: 'semantic',
    status: 'answered', text: '资料中的项目围绕“查询后核查原文”组织。它先在当前知识库检索相关片段，再依据片段整理回答。\n\n回答与来源分开处理：正文可以概括，引用摘录保留原文。资料更新期间暂停新查询，避免用旧版本的内容回答。\n\n这是 B 版合成设计内容，用于查看阅读与核查布局。',
    citations: [{ evidence_id: 'E1', document_id: 'design-document-1', filename: '合成产品手册.md',
      locator: { paragraph: 4 }, excerpt: '系统在当前知识库检索相关片段。回答正文可以概括，但引用摘录保留原文。资料更新期间暂停新查询，避免使用旧版本内容。' }],
    kb_revision: 3, error_code: null, created_at: '2026-09-27T01:22:00Z', saved: true,
  }],
  'design-chat-2': [{
    message_id: 'design-message-2', attempt_id: 'design-attempt-2', client_message_id: 'design-input-2',
    question: 'RAG 是什么？', mode: 'auto', route: 'general', status: 'answered',
    text: 'RAG 是检索增强生成：先查找相关资料，再让模型基于这些资料回答。这里是通用概念说明，未查询当前知识库。',
    citations: [], kb_revision: 3, error_code: null, created_at: '2026-09-26T08:43:00Z', saved: true,
  }, {
    message_id: 'design-message-3', attempt_id: 'design-attempt-3', client_message_id: 'design-input-3',
    question: '谢谢', mode: 'auto', route: 'chat', status: 'answered', text: '不客气！想继续了解资料或通用知识，可以直接提问。',
    citations: [], kb_revision: 3, error_code: null, created_at: '2026-09-26T08:44:00Z', saved: true,
  }],
  'design-chat-3': [{
    message_id: 'design-message-4', attempt_id: 'design-attempt-4', client_message_id: 'design-input-4',
    question: '这份说明里有哪些维护步骤？', mode: 'auto', route: 'semantic', status: 'partial',
    text: '维护前先核对正在进行的任务…（合成未完成片段）', citations: [], kb_revision: 3,
    error_code: 'request_interrupted', created_at: '2026-09-26T03:14:00Z', saved: true,
  }],
};

export const sampleDocuments: ManagedDocument[] = Array.from({ length: 17 }, (_, index) => ({
  id: `design-document-${index + 1}`, filename: index === 0 ? '合成产品手册.md'
    : index === 1 ? '合成设备维护步骤与常见问题说明_长文件名换行测试.pdf'
    : index === 2 ? '合成复杂表格说明.docx' : `合成操作说明_${String(index + 1).padStart(2, '0')}.md`,
  size: 14231 + index * 2739, status: index === 2 ? 'failed' : 'ready',
  error_code: index === 2 ? 'unsupported_document' : null,
  created_at: `2026-09-${String(27 - Math.min(index, 15)).padStart(2, '0')}T02:15:00Z`,
  doc_code: index === 2 ? null : `DS-${String(index + 1).padStart(3, '0')}`,
  model_code: index === 0 ? '样例资料' : null, edition: index === 0 ? 'V3（设计样例）' : null,
}));

export const sampleDeleted: ManagedDocument[] = [{
  id: 'design-deleted-1', filename: '合成旧版手册.md', size: 9318, status: 'deleted', error_code: null,
  created_at: '2026-09-12T06:22:00Z', doc_code: null, model_code: null, edition: null,
}];

export const sampleActive: IngestionJob[] = [{
  id: 'design-job-active', kb_id: 'design-library-1', operation: 'upload', status: 'running',
  stage: 'parsing', error_code: null, document_ids: ['design-document-18'],
  created_at: '2026-09-27T01:34:00Z', engine_mutated: false, can_retry: false,
}];

export const sampleJobs: IngestionJob[] = Array.from({ length: 12 }, (_, index) => ({
  id: `design-job-${index + 1}`, kb_id: 'design-library-1', operation: index === 1 ? 'delete' : 'upload',
  status: index === 0 ? 'failed' : index === 3 ? 'interrupted' : 'succeeded',
  stage: index === 0 ? 'parsing' : index === 3 ? 'indexing' : 'complete',
  error_code: index === 0 ? 'unsupported_document' : index === 3 ? 'rebuild_required' : null,
  document_ids: [`design-document-${index + 1}`], created_at: `2026-09-${String(26 - index).padStart(2, '0')}T06:37:00Z`,
  engine_mutated: index !== 0, can_retry: index === 0, cleanup_pending: index === 3, can_cleanup: false,
}));

export const sampleHealth: SystemHealth = {
  status: 'partial', mode: 'local_single_user', database: 'available', models: 'unverified',
  rag: 'unverified', backup: 'disabled', retention: 'available',
  models_info: { region: '设计样例，不是实际配置', model_names: ['回答模型（样例）', '向量模型（样例）'] },
};
export const sampleVoice: VoiceCapability = { transport: 'configured', assistant: 'not_configured', purpose: 'media_test' };
