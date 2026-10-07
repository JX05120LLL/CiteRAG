import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import type { AppView } from '../app';
import { sampleHealth } from '../preview/samples';
import { Status } from './Management';

beforeEach(() => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
});
afterEach(cleanup);

it('shows not checked, checking and the explicit local result with reason and time', async () => {
  let finish!: (value: unknown) => void;
  const checkSystem = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
  const view = { state: { health: sampleHealth, healthLoading: false },
    voice: { actions: { capability: null, capabilityError: false, refresh: vi.fn() } },
    actions: { loadHealth: vi.fn(), navigate: vi.fn() } } as unknown as AppView;
  render(<Status view={view} api={{ checkSystem } as unknown as ApiClient} />);
  expect(screen.getAllByText('尚未检测').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: '运行本地检测' }));
  expect(checkSystem).toHaveBeenCalledTimes(1);
  expect(screen.getByText('检测中')).toBeTruthy();
  finish({ checked_at: '2026-10-07T07:00:00Z', checks: {
    business_database: { state: 'available', reason: 'Local DB query succeeded', checked_at: '2026-10-07T07:00:00Z' },
    model_provider: { state: 'not_checked', reason: 'Paid request requires approval', checked_at: '2026-10-07T07:00:00Z' },
  } });
  await waitFor(() => expect(screen.getByText('Local DB query succeeded')).toBeTruthy());
  expect(screen.getByText('Paid request requires approval')).toBeTruthy();
  expect(screen.getByText(/检测时间/)).toBeTruthy();
});

it('requires an explicit cost acknowledgement before one supplier check', async () => {
  const functionalStatus = vi.fn(async () => ({ checks: {
    model: { service: 'DashScope qwen-flash', state: 'not_checked', reason: 'never_checked', checked_at: null, expires_at: null, fingerprint: null },
    asr: { service: 'VolcEngine ASR', state: 'not_checked', reason: 'never_checked', checked_at: null, expires_at: null, fingerprint: null },
    tts: { service: 'MiniMax TTS', state: 'not_checked', reason: 'never_checked', checked_at: null, expires_at: null, fingerprint: null },
    knowledge: { service: 'LightRAG', state: 'not_checked', reason: 'configuration_unavailable', checked_at: null, expires_at: null, fingerprint: null },
  } }));
  const startFunctionalCheck = vi.fn(async (_kind: string, _id: string) => ({ state: 'running', reason: 'check_running' }));
  const view = { state: { health: sampleHealth, healthLoading: false },
    voice: { actions: { capability: null, capabilityError: false, refresh: vi.fn() } },
    actions: { loadHealth: vi.fn(), navigate: vi.fn() } } as unknown as AppView;
  render(<Status view={view} api={{ functionalStatus, startFunctionalCheck } as unknown as ApiClient} />);
  await waitFor(() => expect(functionalStatus).toHaveBeenCalledTimes(1));
  expect(startFunctionalCheck).not.toHaveBeenCalled();
  expect(screen.getAllByText(/可能产生供应商费用/).length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: '检测模型服务' }).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: '我已了解费用和检测范围' }));
  fireEvent.click(screen.getByRole('button', { name: '检测模型服务' }));
  await waitFor(() => expect(startFunctionalCheck).toHaveBeenCalledTimes(1));
  expect(startFunctionalCheck.mock.calls[0]?.[0]).toBe('model');
});
