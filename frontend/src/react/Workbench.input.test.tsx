import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { AgentRun, ApiClient } from '../api/client';
import type { AppView } from '../app';
import { Workbench } from './Workbench';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
});

it('uses the main composer to continue a waiting text task in the same chat', async () => {
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
        client_message_id: 'client', question: 'Weather?', mode: 'auto', route: null,
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
  const input = document.querySelector<HTMLTextAreaElement>('.composer textarea');
  expect(input?.disabled).toBe(false);
  fireEvent.change(input!, { target: { value: 'Hanzhong' } });
  fireEvent.click(screen.getByRole('button', { name: /发送问题/ }));
  fireEvent.click(screen.getByRole('button', { name: /发送问题/ }));
  await waitFor(() => expect(resumeAgent).toHaveBeenCalledWith(run, expect.any(String),
    { detail: 'Hanzhong' }));
  expect(resumeAgent).toHaveBeenCalledTimes(1);
  expect(startAgent).not.toHaveBeenCalled();
});
