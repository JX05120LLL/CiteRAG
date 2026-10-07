import { StrictMode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { ApiClient, FunctionalReport, FunctionalResult } from '../api/client';
import type { AppView } from '../app';
import { sampleHealth } from '../preview/samples';
import { Status } from './Management';

beforeEach(() => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
});
afterEach(cleanup);

function result(service: string, state: FunctionalResult['state'], reason: string): FunctionalResult {
  return { service, state, reason, checked_at: state === 'available' ? '2026-10-07T07:00:00Z' : null,
    expires_at: state === 'available' ? '2026-10-07T08:00:00Z' : null,
    fingerprint: state === 'available' ? 'a'.repeat(64) : null };
}
function report(): FunctionalReport {
  return { checks: {
    model: result('DashScope qwen-flash', 'available', 'check_succeeded'),
    asr: result('VolcEngine ASR', 'available', 'check_succeeded'),
    tts: result('MiniMax TTS', 'available', 'check_succeeded'),
    knowledge: result('LightRAG', 'not_checked', 'acceptance_kb_readonly_probe_unavailable'),
  } };
}
function view(): AppView {
  return { state: { health: sampleHealth, healthLoading: false },
    voice: { actions: { capability: null, capabilityError: false, refresh: vi.fn() } },
    actions: { loadHealth: vi.fn(), navigate: vi.fn() } } as unknown as AppView;
}

it('starts one automatic batch on entry and shows real evidence on existing cards', async () => {
  const autoFunctionalChecks = vi.fn(async (_force = false) => report());
  const functionalStatus = vi.fn(async () => report());
  const appView = view();
  render(<Status view={appView} api={{ autoFunctionalChecks, functionalStatus } as unknown as ApiClient} />);
  await waitFor(() => expect(autoFunctionalChecks).toHaveBeenCalledTimes(1));
  const cards = document.querySelectorAll('.status-capability');
  await waitFor(() => expect(within(cards[1] as HTMLElement).getByText('可用')).toBeTruthy());
  expect(within(cards[2] as HTMLElement).getByText('未检测')).toBeTruthy();
  expect(within(cards[3] as HTMLElement).getAllByText('可用')).toHaveLength(3);
  expect(screen.queryByRole('region', { name: '实际功能检测' })).toBeNull();
  const refresh = screen.getByRole('button', { name: /刷新系统状态/ });
  await waitFor(() => expect(refresh.className).not.toContain('ant-btn-loading'));
  fireEvent.click(refresh);
  await waitFor(() => expect(autoFunctionalChecks).toHaveBeenCalledTimes(2));
  expect(autoFunctionalChecks.mock.calls[1]?.[0]).toBe(true);
  expect(appView.actions.loadHealth).toHaveBeenCalledTimes(1);
});

it('does not mistake missing configuration or a failed request for availability', async () => {
  const unavailable = report();
  unavailable.checks.model = result('DashScope qwen-flash', 'not_checked', 'configuration_unavailable');
  unavailable.checks.asr = result('VolcEngine ASR', 'unavailable', 'provider_timeout');
  unavailable.checks.tts = result('MiniMax TTS', 'running', 'check_running');
  render(<Status view={view()} api={{ autoFunctionalChecks: vi.fn(async () => unavailable),
    functionalStatus: vi.fn(async () => unavailable) } as unknown as ApiClient} />);
  const cards = document.querySelectorAll('.status-capability');
  await waitFor(() => expect(within(cards[1] as HTMLElement).getByText('不可用')).toBeTruthy());
  expect(within(cards[3] as HTMLElement).getAllByText('不可用')).toHaveLength(2);
  expect(within(cards[3] as HTMLElement).getByText('进行中')).toBeTruthy();
  expect(within(cards[2] as HTMLElement).getByText('未检测')).toBeTruthy();
});

it('keeps the local diagnostic check separate from supplier probes', async () => {
  let finish!: (value: unknown) => void;
  const checkSystem = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
  render(<Status view={view()} api={{ autoFunctionalChecks: vi.fn(async () => report()),
    functionalStatus: vi.fn(async () => report()), checkSystem } as unknown as ApiClient} />);
  fireEvent.click(screen.getByText('运行诊断'));
  fireEvent.click(screen.getByRole('button', { name: '运行本地检查' }));
  expect(checkSystem).toHaveBeenCalledTimes(1);
  finish({ checked_at: '2026-10-07T07:00:00Z', checks: {
    business_database: { state: 'available', reason: 'Local DB query succeeded',
      checked_at: '2026-10-07T07:00:00Z' },
  } });
  await waitFor(() => expect(screen.getByText('Local DB query succeeded')).toBeTruthy());
});

it('deduplicates StrictMode effects and re-renders while retaining the result', async () => {
  let finish!: (value: FunctionalReport) => void;
  const autoFunctionalChecks = vi.fn(() => new Promise<FunctionalReport>((resolve) => { finish = resolve; }));
  const api = { autoFunctionalChecks, functionalStatus: vi.fn(async () => report()) } as unknown as ApiClient;
  const appView = view();
  const mounted = render(<StrictMode><Status view={appView} api={api} /></StrictMode>);
  await waitFor(() => expect(autoFunctionalChecks).toHaveBeenCalledTimes(1));
  const waitingCards = document.querySelectorAll('.status-capability');
  expect(within(waitingCards[2] as HTMLElement).getByText('未检测')).toBeTruthy();
  mounted.rerender(<StrictMode><Status view={appView} api={api} /></StrictMode>);
  expect(autoFunctionalChecks).toHaveBeenCalledTimes(1);
  finish(report());
  const cards = document.querySelectorAll('.status-capability');
  await waitFor(() => expect(within(cards[1] as HTMLElement).getByText('可用')).toBeTruthy());
});

it('shows a request failure without reporting the provider as available', async () => {
  const autoFunctionalChecks = vi.fn(async (): Promise<FunctionalReport> => { throw new Error('offline'); });
  render(<Status view={view()} api={{ autoFunctionalChecks } as unknown as ApiClient} />);
  await waitFor(() => expect(screen.getAllByText('自动检测未能启动，请刷新后重试。').length).toBeGreaterThan(0));
  const cards = document.querySelectorAll('.status-capability');
  expect(within(cards[1] as HTMLElement).getByText('未验证')).toBeTruthy();
});

it('identifies an older local API when the automatic endpoint is missing', async () => {
  const autoFunctionalChecks = vi.fn(async (): Promise<FunctionalReport> => {
    throw new ApiError('not-found');
  });
  render(<Status view={view()} api={{ autoFunctionalChecks } as unknown as ApiClient} />);
  await waitFor(() => expect(screen.getAllByText(
    '自动检测接口未加载：本地 API 与当前页面版本不一致，请更新本地 API 后刷新页面。',
  ).length).toBeGreaterThan(0));
  expect(autoFunctionalChecks).toHaveBeenCalledTimes(1);
});

it('stops polling after a read error and does not leave a stale running card', async () => {
  const running = report();
  running.checks.model = result('DashScope qwen-flash', 'running', 'check_running');
  const functionalStatus = vi.fn(async (): Promise<FunctionalReport> => { throw new Error('offline'); });
  render(<Status view={view()} api={{ autoFunctionalChecks: vi.fn(async () => running),
    functionalStatus } as unknown as ApiClient} />);
  await waitFor(() => expect(functionalStatus).toHaveBeenCalledTimes(1), { timeout: 2500 });
  const cards = document.querySelectorAll('.status-capability');
  await waitFor(() => expect(within(cards[1] as HTMLElement).getByText('未验证')).toBeTruthy());
  await new Promise((resolve) => setTimeout(resolve, 1200));
  expect(functionalStatus).toHaveBeenCalledTimes(1);
});
