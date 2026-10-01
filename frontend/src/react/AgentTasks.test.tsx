import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AgentRun, ApiClient } from '../api/client';
import { AgentTaskCard, AgentTasks } from './AgentTasks';

afterEach(() => { cleanup(); vi.useRealTimers(); });
const waiting: AgentRun = { id: 'run', conversation_id: 'chat', message_id: 'message',
  attempt_id: 'attempt', status: 'waiting_approval', generation: 2, seq: 4, model_rounds: 1,
  tool_attempts: 1, active_ms: 12, error_code: null, voice_session_id: null,
  created_at: '2026-10-01T00:00:00Z', finished_at: null,
  waiting: { kind: 'approval', tool_id: 'synthetic.write', tool_version: '1',
    arguments: { target: 'public synthetic' }, destination: 'local', impact: '合成操作' } };

describe('durable task controls', () => {
  it('shows the exact operation and only approves after an explicit click', async () => {
    const resume = vi.fn().mockResolvedValue(undefined);
    render(<AgentTaskCard run={waiting} resume={resume} cancel={vi.fn()} />);
    expect(screen.getByText(/public synthetic/)).toBeTruthy();
    expect(resume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '批准所示操作' }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith({ approve: true }));
  });
  it('validates typed missing parameters and does not send a new question', async () => {
    const resume = vi.fn().mockResolvedValue(undefined);
    render(<AgentTaskCard run={{ ...waiting, status: 'waiting_input', waiting: {
      kind: 'input', prompt: '补充分钟', fields: { minutes: 'integer' } } }} resume={resume} cancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('minutes'), { target: { value: '12.5' } });
    fireEvent.click(screen.getByRole('button', { name: '补充并继续' }));
    expect(resume).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('minutes'), { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: '补充并继续' }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith({ minutes: 30 }));
  });
  it('keeps voice tasks controlled by the current voice page', () => {
    render(<AgentTaskCard run={{ ...waiting, voice_session_id: 'voice' }} resume={vi.fn()} cancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: '批准所示操作' }).hasAttribute('disabled')).toBe(true);
    expect(screen.getByText(/当前通话控制端/)).toBeTruthy();
  });
  it('backs off disconnected reads, recovers and releases the polling timer on unmount', async () => {
    vi.useFakeTimers();
    const agentRuns = vi.fn().mockRejectedValue(new Error('服务连接断开'));
    const view = render(<AgentTasks chatId="chat" api={{ agentRuns } as unknown as ApiClient} />);
    await act(async () => { await Promise.resolve(); });
    expect(agentRuns).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(agentRuns).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(agentRuns).toHaveBeenCalledTimes(2);
    agentRuns.mockResolvedValue([{ ...waiting, status: 'running', waiting: null }]);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(agentRuns).toHaveBeenCalledTimes(3);
    expect(screen.queryByText('服务连接断开')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(agentRuns).toHaveBeenCalledTimes(4);
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(agentRuns).toHaveBeenCalledTimes(4);
  });
});
