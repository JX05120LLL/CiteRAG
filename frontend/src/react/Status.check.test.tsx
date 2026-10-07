import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import type { AppView } from '../app';
import { sampleHealth } from '../preview/samples';
import { Status } from './Management';

beforeEach(() => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
});

it('shows not checked, checking and the explicit local result with reason and time', async () => {
  let finish!: (value: unknown) => void;
  const checkSystem = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
  const view = { state: { health: sampleHealth, healthLoading: false },
    voice: { actions: { capability: null, capabilityError: false, refresh: vi.fn() } },
    actions: { loadHealth: vi.fn(), navigate: vi.fn() } } as unknown as AppView;
  render(<Status view={view} api={{ checkSystem } as unknown as ApiClient} />);
  expect(screen.getByText('尚未检测')).toBeTruthy();
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
