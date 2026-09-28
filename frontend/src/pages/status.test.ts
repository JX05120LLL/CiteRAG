import { describe, expect, it } from 'vitest';
import type { CapabilityState, SystemHealth } from '../api/client';
import type { AppState } from '../state';
import { renderStatus } from './status';

function show(models: CapabilityState, rag = models,
    backup: Pick<SystemHealth, 'backup' | 'backup_error_code'> = {}) {
  const root = document.createElement('main');
  const state: AppState = {
    bases: [], chats: [], page: 'status', loading: false, basesError: null,
    chatsError: null, healthError: null, healthLoading: false,
    createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false },
    renameDraft: null, knowledgeNotice: null,
    selectedKbId: null, selectedChatId: null, chatMessages: [], chatDraft: '', chatImages: [], chatUploadedImages: [],
    chatPending: false, chatError: null, chatRequestKey: null, chatRequestText: null,
    health: { status: 'partial', mode: 'local_single_user', database: 'available', models, rag,
      ...backup },
  };
  renderStatus(root, state, async () => {});
  return root;
}

describe('system-status capability evidence', () => {
  it('shows enabled daily backup state and last successful time without local paths', () => {
    const root = document.createElement('main');
    const state: AppState = {
      bases: [], chats: [], page: 'status', loading: false, basesError: null,
      chatsError: null, healthError: null, healthLoading: false,
      createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false },
      renameDraft: null, knowledgeNotice: null,
      selectedKbId: null, selectedChatId: null, chatMessages: [], chatDraft: '', chatImages: [], chatUploadedImages: [],
      chatPending: false, chatError: null, chatRequestKey: null, chatRequestText: null,
      health: { status: 'partial', mode: 'local_single_user', database: 'available',
        models: 'not_configured', rag: 'not_configured', backup: 'available',
        last_backup_at: '2026-09-24T00:00:00Z' },
    };
    renderStatus(root, state, async () => {});
    expect(root.textContent).toContain('本地备份');
    expect(root.textContent).toContain('最近完成');
    expect(root.textContent).not.toContain('.local');
  });

  it('shows a bounded backup failure category without private diagnostics', () => {
    const root = show('not_configured', 'not_configured',
      { backup: 'unavailable', backup_error_code: 'backup_failed' });
    expect(root.textContent).toContain('备份失败，需检查本机存储和数据库');
    expect(root.textContent).not.toContain('private');
  });
  it('marks the read time and does not present stored model evidence as a live probe', () => {
    const root = show('available');
    root.replaceChildren();
    const state: AppState = {
      bases: [], chats: [], page: 'status', loading: false, basesError: null,
      chatsError: null, healthError: null, healthLoading: false,
      createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false },
      renameDraft: null, knowledgeNotice: null,
      selectedKbId: null, selectedChatId: null, chatMessages: [], chatDraft: '', chatImages: [], chatUploadedImages: [],
      chatPending: false, chatError: null, chatRequestKey: null, chatRequestText: null,
      healthCheckedAt: Date.parse('2026-09-25T07:00:00Z'),
      health: { status: 'partial', mode: 'local_single_user', database: 'available',
        models: 'available', rag: 'unverified' },
    };
    renderStatus(root, state, async () => {});
    expect(root.textContent).toContain('本次读取');
    expect(root.textContent).toContain('模型服务的标记依据本机配置和验证记录');
    expect(root.textContent).toContain('未在此页发起模型调用');
  });
  it.each([
    ['not_configured', '尚未配置'], ['unverified', '等待真实验证'],
    ['available', '可用'], ['unavailable', '暂不可用'],
  ] as const)('renders %s as %s', (state, label) => {
    const root = show(state);
    expect(root.querySelector('.health-list')?.textContent).toContain(label);
  });

  it('shows safe metadata but no workspace or credential fields', () => {
    const root = show('available');
    const state: AppState = {
      bases: [], chats: [], page: 'status', loading: false, basesError: null,
      chatsError: null, healthError: null, healthLoading: false,
      createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false },
      renameDraft: null, knowledgeNotice: null,
      selectedKbId: null, selectedChatId: null, chatMessages: [], chatDraft: '', chatImages: [], chatUploadedImages: [],
      chatPending: false, chatError: null, chatRequestKey: null, chatRequestText: null,
      health: {
        status: 'partial', mode: 'local_single_user', database: 'available',
        models: 'available', rag: 'unverified',
        models_info: { region: 'cn-beijing', model_names: ['qwen-flash'], last_verified_at: '2026-09-23T00:00:00+00:00' },
        rag_info: { lightrag_commit: 'a'.repeat(40), postgresql_major: 17, vector_version: '0.8.1' },
      },
    };
    root.replaceChildren();
    renderStatus(root, state, async () => {});
    expect(root.textContent).toContain('cn-beijing');
    expect(root.textContent).toContain('qwen-flash');
    expect(root.textContent).toContain('pgvector 0.8.1');
    expect(root.textContent).toContain('aaaaaaaa');
    expect(root.textContent).not.toContain('private-workspace');
  });
});
