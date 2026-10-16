import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, type AgentRun, type ApiClient, type ChatMessage } from '../api/client';
import type { AppView } from '../app';
import { Workbench } from './Workbench';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function composerFixture(options: { knowledge?: boolean; pending?: boolean; loading?: boolean;
  selected?: boolean; maintaining?: boolean } = {}) {
  const state = {
    agentEnabled: false, selectedChatId: options.selected === false ? null : 'chat',
    selectedKbId: options.knowledge ? 'kb' : null,
    bases: options.knowledge ? [{ id: 'kb', name: '公开合成库', status: options.maintaining ? 'maintaining' : 'ready' }] : [],
    chats: [], chatMessages: [], chatDraft: '', chatPending: !!options.pending,
    loading: !!options.loading, chatImages: [], chatUploadedImages: [], chatError: null,
    selectedCitation: null,
  };
  const sendChat = vi.fn(async () => {});
  function Harness() {
    const [, refresh] = useState(0);
    const actions = { setDraft: (text: string) => { state.chatDraft = text; refresh((n) => n + 1); },
      sendChat };
    return <Workbench view={{ state, actions } as unknown as AppView} api={{} as ApiClient} />;
  }
  render(<Harness />);
  const input = document.querySelector<HTMLTextAreaElement>('.composer textarea')!;
  return { input, sendChat, state };
}

it.each([false, true])('sends Enter after typing into an initially empty composer (%s knowledge)', (knowledge) => {
  const { input, sendChat } = composerFixture({ knowledge });
  fireEvent.change(input, { target: { value: '公开合成问题' } });
  expect(screen.getByRole<HTMLButtonElement>('button', { name: /发送问题/ }).disabled).toBe(false);
  expect(fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 })).toBe(false);
  expect(sendChat).toHaveBeenCalledTimes(1);
});

it.each(['shiftKey', 'ctrlKey', 'altKey', 'metaKey'])('does not send modified Enter (%s)', (modifier) => {
  const { input, sendChat } = composerFixture();
  fireEvent.change(input, { target: { value: '公开合成问题' } });
  expect(fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', [modifier]: true })).toBe(true);
  expect(sendChat).not.toHaveBeenCalled();
});

it('confirms Chinese composition without sending, then sends a separate Enter', () => {
  const { input, sendChat } = composerFixture();
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: '公开合成中文' } });
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13, isComposing: true });
  expect(sendChat).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input, { data: '公开合成中文' });
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 });
  expect(sendChat).toHaveBeenCalledTimes(1);
});

it.each([{ isComposing: true, keyCode: 13 }, { isComposing: false, keyCode: 229 }])(
  'does not send native IME confirmation without a compositionstart (%j)', (ime) => {
    const { input, sendChat } = composerFixture();
    fireEvent.change(input, { target: { value: '公开合成中文' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', ...ime });
    expect(sendChat).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 });
    expect(sendChat).toHaveBeenCalledTimes(1);
  },
);

it('keeps empty and whitespace input unsendable by Enter and click', () => {
  const { input, sendChat } = composerFixture();
  for (const value of ['', ' \n ']) {
    fireEvent.change(input, { target: { value } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 });
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /发送问题/ });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
  }
  expect(sendChat).not.toHaveBeenCalled();
});

it.each([{ pending: true }, { loading: true }, { selected: false },
  { knowledge: true, maintaining: true }])('keeps keyboard and click behind the existing gate (%j)', (options) => {
  const { input, sendChat } = composerFixture(options);
  fireEvent.change(input, { target: { value: '公开合成问题' } });
  expect(input.disabled).toBe(true);
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 });
  fireEvent.click(screen.getByRole('button', { name: /发送问题/ }));
  expect(sendChat).not.toHaveBeenCalled();
});

it.each(['click', 'enter'])('uses the main composer to continue a waiting text task in the same chat (%s)', async (method) => {
  const run: AgentRun = {
    id: 'run', conversation_id: 'chat', message_id: 'message', attempt_id: 'attempt',
    status: 'waiting_input', generation: 1, seq: 3, model_rounds: 1, tool_attempts: 0,
    active_ms: 20, error_code: null, voice_session_id: null,
    created_at: '2026-10-01T00:00:00Z', finished_at: null,
    waiting: { kind: 'input', prompt: 'Which location?', fields: { location: 'string' } },
  };
  const resumeAgent = vi.fn(async () => ({ ...run, status: 'running' as const }));
  const startAgent = vi.fn();
  const api = { agentRuns: async () => [run], resumeAgent, startAgent } as unknown as ApiClient;
  function Harness() {
    const [, refresh] = useState(0);
    const [state] = useState(() => ({
      agentEnabled: true, selectedChatId: 'chat', selectedKbId: null, bases: [],
      chats: [], chatMessages: [{ message_id: 'message', attempt_id: 'attempt',
        client_message_id: 'client', question: 'Weather?', mode: 'auto', route: 'general',
        status: 'running', phase: 'waiting_input', text: '', citations: [], kb_revision: 0,
        error_code: null, created_at: '2026-10-01T00:00:00Z', saved: false }],
      chatDraft: '', chatPending: false, loading: false, chatImages: [],
      chatUploadedImages: [], chatError: null, selectedCitation: null,
    }));
    const actions = { setDraft: (text: string) => { state.chatDraft = text; refresh((n) => n + 1); },
      refreshAgentMessages: async () => {}, sendChat: startAgent };
    return <Workbench view={{ state, actions } as unknown as AppView} api={api} />;
  }
  render(<Harness />);
  await screen.findByText('Which location?');
  expect(document.querySelector('.message-scroll .agent-task')).not.toBeNull();
  await waitFor(() => expect(document.querySelector('.answer-card .answer-text')?.textContent).toBe('Which location?'));
  const input = document.querySelector<HTMLTextAreaElement>('.composer textarea');
  expect(input?.disabled).toBe(false);
  fireEvent.change(input!, { target: { value: 'Hanzhong' } });
  if (method === 'click') fireEvent.click(screen.getByRole('button', { name: /补充并继续/ }));
  else fireEvent.keyDown(input!, { key: 'Enter', code: 'Enter', keyCode: 13 });
  if (method === 'click') fireEvent.click(screen.getByRole('button', { name: /补充并继续/ }));
  else fireEvent.keyDown(input!, { key: 'Enter', code: 'Enter', keyCode: 13 });
  await waitFor(() => expect(resumeAgent).toHaveBeenCalledWith(run, expect.any(String),
    { detail: 'Hanzhong' }));
  expect(resumeAgent).toHaveBeenCalledTimes(1);
  expect(startAgent).not.toHaveBeenCalled();
});

it('restores image and voice actions promptly after cancelling a waiting task', async () => {
  const run: AgentRun = {
    id: 'waiting-run', conversation_id: 'chat', message_id: 'message', attempt_id: 'attempt',
    status: 'waiting_input', generation: 1, seq: 3, model_rounds: 4, tool_attempts: 0,
    active_ms: 20, error_code: null, voice_session_id: null,
    created_at: '2026-10-07T00:00:00Z', finished_at: null,
    waiting: { kind: 'input', prompt: 'Which location?', fields: { location: 'string' } },
  };
  let currentRun = run;
  const cancelAgent = vi.fn(async () => {
    currentRun = { ...run, status: 'cancelled', seq: 4, waiting: null };
    return currentRun;
  });
  const api = { agentRuns: async () => [currentRun], cancelAgent } as unknown as ApiClient;
  function Harness() {
    const [, refresh] = useState(0);
    const [state] = useState(() => ({
      agentEnabled: true, selectedChatId: 'chat', selectedKbId: null, bases: [],
      chats: [], chatMessages: [{ message_id: 'message', attempt_id: 'attempt',
        client_message_id: 'client', question: 'Weather?', mode: 'auto', route: 'general',
        status: 'running', phase: 'waiting_input', text: '', citations: [], kb_revision: 0,
        error_code: null, created_at: '2026-10-07T00:00:00Z', saved: false }],
      chatDraft: '', chatPending: false, loading: false, chatImages: [],
      chatUploadedImages: [], chatError: null, selectedCitation: null,
    }));
    const actions = {
      setDraft: (text: string) => { state.chatDraft = text; refresh((n) => n + 1); },
      refreshAgentMessages: async () => {
        if (currentRun.status === 'cancelled') {
          state.chatMessages[0].status = 'cancelled';
          refresh((n) => n + 1);
        }
      },
    };
    return <Workbench view={{ state, actions } as unknown as AppView} api={api} />;
  }
  render(<Harness />);
  await screen.findByText('Which location?');
  expect(screen.getByRole<HTMLButtonElement>('button', { name: /添加图片/ }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>('button', { name: /语音通话/ }).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
  await waitFor(() => expect(cancelAgent).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>('button', { name: /添加图片/ }).disabled).toBe(false));
  expect(screen.getByRole<HTMLButtonElement>('button', { name: /语音通话/ }).disabled).toBe(false);
});

it('keeps successful and failed terminal answers in the chat messages', () => {
  const created_at = '2026-10-07T00:00:00Z';
  const messages = [
    { message_id: 'success', attempt_id: 'success-attempt', client_message_id: 'success-client',
      question: 'Weather?', mode: 'auto', route: 'general', status: 'answered', phase: 'complete',
      text: 'Synthetic weather complete.', citations: [], kb_revision: 0,
      error_code: null, created_at, saved: true },
    { message_id: 'failure', attempt_id: 'failure-attempt', client_message_id: 'failure-client',
      question: 'Weather again?', mode: 'auto', route: 'general', status: 'failed', phase: 'complete',
      text: '', citations: [], kb_revision: 0,
      error_code: 'weather_auth_failed', created_at, saved: true },
  ];
  const view = { state: { agentEnabled: false, selectedChatId: 'chat', selectedKbId: null,
    bases: [], chats: [], chatMessages: messages, chatDraft: '', chatPending: false,
    loading: false, chatImages: [], chatUploadedImages: [], chatError: null,
    selectedCitation: null }, actions: {} } as unknown as AppView;
  render(<Workbench view={view} api={{} as ApiClient} />);
  const answers = Array.from(document.querySelectorAll<HTMLElement>('.answer-card .answer-text'));
  expect(answers.map((item) => item.textContent)).toEqual([
    'Synthetic weather complete.', expect.stringMatching(/.+/),
  ]);
  expect(screen.getByText(/weather_auth_failed/)).toBeTruthy();
});

function historyFixture() {
  const message: ChatMessage = { message_id: 'recent', attempt_id: 'recent-attempt', client_message_id: 'recent-client',
    question: 'Recent question', mode: 'auto', route: 'general', status: 'answered',
    text: 'Recent answer', citations: [], kb_revision: 0, error_code: null,
    created_at: '2026-10-07T00:00:00Z', saved: true };
  const loadMoreMessages = vi.fn(async () => {});
  const stopChat = vi.fn();
  const sendChat = vi.fn(async () => {});
  const view = { state: { agentEnabled: false, selectedChatId: 'chat', selectedKbId: null,
    bases: [], chats: [], chatMessages: [message], chatDraft: 'A new question', chatPending: false,
    loading: false, chatImages: [], chatUploadedImages: [], chatError: null, selectedCitation: null },
    messageHistory: { hasMore: true, loading: false, error: null, totalLoaded: 1 },
    textAnswer: { canStop: false, stopping: false, notice: null },
    actions: { loadMoreMessages, stopChat, sendChat, setDraft: vi.fn() } } as unknown as AppView;
  const api = {} as ApiClient;
  const result = render(<Workbench view={view} api={api} />);
  return { view, message, loadMoreMessages, stopChat, sendChat,
    update: () => result.rerender(<Workbench view={view} api={api} />) };
}

it('loads earlier messages once while leaving a new question available', async () => {
  const { loadMoreMessages, sendChat } = historyFixture();
  let finish!: () => void;
  loadMoreMessages.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  const load = screen.getByRole<HTMLButtonElement>('button', { name: '加载更早消息' });
  fireEvent.click(load);
  fireEvent.click(load);
  expect(loadMoreMessages).toHaveBeenCalledTimes(1);
  expect(load.disabled).toBe(true);
  const send = screen.getByRole<HTMLButtonElement>('button', { name: /发送问题/ });
  expect(send.disabled).toBe(false);
  fireEvent.click(send);
  expect(sendChat).toHaveBeenCalledTimes(1);
  expect(screen.getByText('已加载 1 条消息')).toBeTruthy();
  expect(screen.queryByText(/共 1 条消息/)).toBeNull();
  await act(async () => finish());
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '加载更早消息' }).disabled).toBe(false);
});

it('shows a failed history read and allows retry without hiding loaded messages', async () => {
  const { view, update, loadMoreMessages } = historyFixture();
  view.messageHistory!.error = new ApiError('network');
  update();
  expect(screen.getAllByRole('alert').some((alert) => alert.textContent?.includes(view.messageHistory!.error!.message))).toBe(true);
  expect(screen.getByText('Recent answer')).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '加载更早消息' })));
  expect(loadMoreMessages).toHaveBeenCalledTimes(1);
});

it('hides the history action when no earlier page remains', () => {
  const { view, update } = historyFixture();
  view.messageHistory!.hasMore = false;
  update();
  expect(screen.queryByRole('button', { name: '加载更早消息' })).toBeNull();
  expect(screen.getByText('已加载 1 条消息')).toBeTruthy();
});

it.each([0, 234])('keeps the current reading position when an earlier page is prepended despite %i pixels of unrelated layout change', async (layoutChange) => {
  const { view, message, update, loadMoreMessages } = historyFixture();
  const scroller = document.documentElement;
  vi.spyOn(scroller, 'scrollHeight', 'get').mockImplementation(() =>
    600 + document.querySelectorAll('.message-pair').length * 400 +
      (document.querySelectorAll('.message-pair').length > 1 ? layoutChange : 0));
  scroller.scrollTop = 180;
  const originalMessage = screen.getByText('Recent question').closest('article')!;
  vi.spyOn(originalMessage, 'getBoundingClientRect').mockImplementation(() => new DOMRect(0,
    100 + (document.querySelectorAll('.message-pair').length - 1) * 400 - scroller.scrollTop, 300, 400));
  const originalY = originalMessage.getBoundingClientRect().top;
  let finish!: () => void;
  loadMoreMessages.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '加载更早消息' }));
  view.state.chatMessages = [{ ...message, message_id: 'earlier', question: 'Earlier question' }, ...view.state.chatMessages];
  view.messageHistory!.totalLoaded = 2;
  update();
  expect(scroller.scrollTop).toBe(580);
  expect(originalMessage.getBoundingClientRect().top).toBe(originalY);
  expect(screen.getByText('已加载 2 条消息')).toBeTruthy();
  await act(async () => finish());
  scroller.scrollTop = 0;
});

it('keeps a history reload notice visible alongside loaded messages', () => {
  const { view, update } = historyFixture();
  view.messageHistory!.notice = '聊天上下文已更新，已重新读取当前历史。';
  update();
  expect(screen.getByText('聊天上下文已更新，已重新读取当前历史。')).toBeTruthy();
  expect(screen.getByText('Recent answer')).toBeTruthy();
});

it('does not restore a previous chat reading position into a newly selected chat', async () => {
  const { view, message, update, loadMoreMessages } = historyFixture();
  const scroller = document.documentElement;
  vi.spyOn(scroller, 'scrollHeight', 'get').mockImplementation(() =>
    view.state.selectedChatId === 'chat' ? 1000 : 1800);
  scroller.scrollTop = 180;
  let finish!: () => void;
  loadMoreMessages.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '加载更早消息' }));
  view.state.selectedChatId = 'other-chat';
  view.state.chatMessages = [{ ...message, message_id: 'other-message' }];
  update();
  expect(scroller.scrollTop).toBe(180);
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '加载更早消息' }).disabled).toBe(false);
  await act(async () => finish());
  scroller.scrollTop = 0;
});

it('only exposes stop for an accepted text answer and reports the supplied stopping state', () => {
  const { view, update, stopChat } = historyFixture();
  view.state.chatPending = true;
  view.state.loading = true;
  update();
  expect(screen.queryByRole('button', { name: '停止回答' })).toBeNull();
  view.textAnswer = { canStop: true, stopping: false, notice: null };
  update();
  const stop = screen.getByRole('button', { name: '停止回答' });
  fireEvent.click(stop);
  fireEvent.click(stop);
  expect(stopChat).toHaveBeenCalledTimes(1);
  view.textAnswer = { canStop: true, stopping: true, notice: '已请求停止，正在核对保存状态' };
  update();
  const stopping = screen.getByRole<HTMLButtonElement>('button', { name: '正在停止…' });
  expect(stopping.disabled).toBe(true);
  expect(screen.getByRole('status').textContent).toBe('已请求停止，正在核对保存状态');
  fireEvent.click(stopping);
  expect(stopChat).toHaveBeenCalledTimes(1);
  view.textAnswer = { canStop: false, stopping: false, notice: '已停止' };
  update();
  expect(screen.queryByRole('button', { name: /停止回答|正在停止/ })).toBeNull();
  expect(screen.getByRole('status').textContent).toBe('已停止');
});
