import { StrictMode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ApiError, createApi, type FunctionalReport, type IngestionJob } from '../api/client';
import type { AppView } from '../app';
import { sampleBases, sampleDeleted, sampleDocuments, sampleHealth, sampleJobs } from '../preview/samples';
import { Theme } from './App';
import { Documents, Knowledge, Status, Tasks } from './Management';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => computed(element));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

type Snapshot = AppView['documents']['snapshot'];
function fixture(overrides: Partial<Snapshot> = {}) {
  const snapshot = {
    base: sampleBases[0], documents: sampleDocuments.slice(0, 3), jobs: sampleJobs,
    files: [], pending: null, uncertain: false, busy: false, loading: false,
    error: null, readError: null, notice: null, recoveryBlocked: false, rebuildConfirmed: false,
    inspected: null, inspectedJob: null, documentPage: { items: sampleDocuments.slice(0, 3), total: 17, counts: { deleted: 1, failed: 1, ready: 16 } },
    jobPage: { items: sampleJobs.slice(0, 3), total: 12, active_items: [], failed_count: 2 },
    deletedPage: { items: sampleDeleted, total: 1, counts: { deleted: 1 } },
    documentOffset: 0, deletedOffset: 0, jobOffset: 0, deletedOpen: false, historyOpen: false,
    unavailable: false, active: false, ...overrides,
  } satisfies Snapshot;
  const panel = { snapshot, isOpen: true, refresh: vi.fn(), maintain: vi.fn(), submit: vi.fn(),
    inspect: vi.fn(), inspectTask: vi.fn(), retry: vi.fn(), cleanup: vi.fn(), closeInspector: vi.fn(),
    movePage: vi.fn(), setDeletedOpen: vi.fn(), setFiles: vi.fn(), setConfirmed: vi.fn(), saveAttributes: vi.fn() };
  const actions = { navigate: vi.fn(), closeDocuments: vi.fn(), openDocuments: vi.fn(),
    selectKb: vi.fn(), beginRename: vi.fn(), refresh: vi.fn(), loadHealth: vi.fn() };
  const view = { documents: panel, actions, state: { health: sampleHealth, healthLoading: false, healthError: null,
    bases: sampleBases, loading: false, basesError: null, knowledgeNotice: null, chatPending: false,
    createDraft: { name: '', pending: false, error: null, requestId: null, requestName: null, uncertain: false }, renameDraft: null },
    voice: { actions: { refresh: vi.fn(), capability: { transport: 'not_configured' }, capabilityError: false } } } as unknown as AppView;
  const api = createApi(vi.fn(async () => { throw new Error('No actual service is called by management rendering tests'); }));
  return { view, panel, actions, api };
}

it('keeps failed document deletion available in a blocked library while original and parsing access remain paused', async () => {
  const document = sampleDocuments[2];
  const { view, panel, api } = fixture({ base: { ...sampleBases[0], status: 'blocked' }, documents: [document] });
  render(<Theme><Documents view={view} api={api} /></Theme>);
  expect(screen.getByText('此库待修复，问答与新上传暂停。')).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '添加资料' }).disabled).toBe(true);
  expect(screen.getByText('文件包含不支持的结构，请转换为普通段落或简单表格后重新上传。')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: `${document.filename}的更多操作` }));
  const original = await screen.findByRole('menuitem', { name: '原文核查暂停' });
  expect(original.getAttribute('aria-disabled')).toBe('true');
  expect(within(original).queryByRole('link')).toBeNull();
  expect(screen.getByRole('menuitem', { name: '查看解析位置' }).getAttribute('aria-disabled')).toBe('true');
  fireEvent.click(screen.getByRole('menuitem', { name: '删除失败资料' }));
  expect(panel.maintain).toHaveBeenCalledWith(document, 'delete');
}, 20000);

it('retains pagination and the deleted archive without exposing actions on deleted records', () => {
  const { view, panel, api } = fixture({ deletedOpen: true });
  render(<Theme><Documents view={view} api={api} /></Theme>);
  const current = screen.getByRole('navigation', { name: '当前资料分页' });
  fireEvent.click(within(current).getByTitle('2'));
  expect(panel.movePage).toHaveBeenCalledWith('current', 10);
  const deletedRow = screen.getByText(sampleDeleted[0].filename).closest('article')!;
  expect(within(deletedRow).queryByRole('button')).toBeNull();
  expect(within(deletedRow).getByText('保留删除标记与历史任务')).toBeTruthy();
  fireEvent.click(screen.getByText('已删除记录 · 1 份'));
  expect(panel.setDeletedOpen).toHaveBeenCalledWith(false);
});

it('keeps task history collapsible, paginated and separate from the actual active task count', () => {
  const active: IngestionJob = { ...sampleJobs[0], id: 'synthetic-active', status: 'running', stage: 'indexing', error_code: null, can_retry: false };
  const { view, panel } = fixture({ jobPage: { items: sampleJobs.slice(0, 3), total: 12, active_items: [active], failed_count: 2 } });
  render(<Theme><Tasks view={view} /></Theme>);
  expect(screen.getByText('共 13 项任务')).toBeTruthy();
  expect(within(screen.getByRole('region', { name: '进行中的任务' })).getByText('正在建立索引')).toBeTruthy();
  const history = screen.getByRole('navigation', { name: '历史任务分页' });
  fireEvent.click(within(history).getByTitle('2'));
  expect(panel.movePage).toHaveBeenCalledWith('history', 10);
  const task = screen.getAllByRole('button', { name: /查看任务详情：资料上传任务/ })[1];
  fireEvent.click(task);
  expect(panel.inspectTask).toHaveBeenCalledWith(sampleJobs[0]);
  const collapse = screen.getByText('已结束任务 · 12 项').closest('[role="button"]')!;
  expect(collapse.getAttribute('aria-expanded')).toBe('true');
  fireEvent.click(collapse);
  expect(collapse.getAttribute('aria-expanded')).toBe('false');
});

it.each([true, false])('keeps selected task details reachable without moving desktop focus (narrow=%s)', (narrow) => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: narrow && media === '(max-width: 1050px)',
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const { view, panel } = fixture({ inspectedJob: { id: sampleJobs[0].id, job: sampleJobs[0], error: null } });
  render(<Theme><Tasks view={view} /></Theme>);
  const task = screen.getAllByRole('button', { name: /查看任务详情：资料上传任务/ })[0];
  task.focus();
  fireEvent.click(task);
  expect(panel.inspectTask).toHaveBeenCalledWith(sampleJobs[0]);
  expect(document.activeElement).toBe(narrow ? screen.getByRole('region', { name: '任务详情' }) : task);
  fireEvent.click(within(screen.getByRole('region', { name: '任务详情' })).getByRole('button', { name: '关闭' }));
  expect(panel.closeInspector).toHaveBeenCalled();
  expect(document.activeElement).toBe(task);
});

it('focuses a task detail already opened from its document on mobile', () => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: media === '(max-width: 1050px)',
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const { view } = fixture({ inspectedJob: { id: sampleJobs[0].id, job: sampleJobs[0], error: null } });
  render(<Theme><Tasks view={view} /></Theme>);
  expect(document.activeElement).toBe(screen.getByRole('region', { name: '任务详情' }));
});

it.each(['job', 'error'] as const)('repositions mobile task detail after %s loads into the existing controller snapshot only once', (outcome) => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: media === '(max-width: 1050px)',
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const inspected: NonNullable<Snapshot['inspectedJob']> = { id: sampleJobs[0].id, job: null, error: null };
  const { view } = fixture({ inspectedJob: inspected });
  const tree = () => <Theme><Tasks view={view} /></Theme>;
  const { rerender } = render(tree());
  const detail = screen.getByRole('region', { name: '任务详情' });
  const scroll = vi.fn();
  detail.scrollIntoView = scroll;
  fireEvent.click(screen.getAllByRole('button', { name: /查看任务详情：资料上传任务/ })[0]);
  expect(scroll).toHaveBeenCalledTimes(1);
  if (outcome === 'job') inspected.job = sampleJobs[0];
  else inspected.error = new ApiError('http');
  rerender(tree());
  expect(scroll).toHaveBeenCalledTimes(2);
  if (outcome === 'job') inspected.job = { ...sampleJobs[0] };
  else inspected.error = new ApiError('http');
  rerender(tree());
  expect(scroll).toHaveBeenCalledTimes(2);
});

it('shows persisted failure detail and dispatches permitted retry and cleanup to the existing controller', () => {
  const job = { ...sampleJobs[0], can_retry: true, can_cleanup: true, cleanup_pending: true, engine_mutated: true };
  const { view, panel } = fixture({ inspectedJob: { id: job.id, job, error: null } });
  render(<Theme><Tasks view={view} /></Theme>);
  const detail = screen.getByRole('region', { name: '任务详情' });
  expect(within(detail).getByText('解析阶段（已停止）')).toBeTruthy();
  expect(within(detail).getByText('已发生')).toBeTruthy();
  expect(within(detail).getByText('存在待清理项')).toBeTruthy();
  expect(within(detail).getByText('文件包含不支持的结构，请转换为普通段落或简单表格后重新上传。')).toBeTruthy();
  fireEvent.click(within(detail).getByRole('button', { name: '重试任务' }));
  fireEvent.click(within(detail).getByRole('button', { name: '重试清理' }));
  expect(panel.retry).toHaveBeenCalledWith(job);
  expect(panel.cleanup).toHaveBeenCalledWith(job);
});

it('does not imply unread knowledge or task totals are zero and retains knowledge maintenance gates', () => {
  const data = fixture({ jobPage: null, documentPage: null });
  const tasks = render(<Theme><Tasks view={data.view} /></Theme>);
  expect(screen.getByText('共 — 项任务')).toBeTruthy();
  expect(screen.queryByText('共 0 项任务')).toBeNull();
  tasks.unmount();
  data.panel.isOpen = false;
  render(<Theme><Knowledge view={data.view} api={data.api} /></Theme>);
  const maintaining = screen.getByRole('heading', { name: sampleBases[1].name }).closest<HTMLElement>('.knowledge-card')!;
  expect(within(maintaining).getByRole<HTMLButtonElement>('button', { name: '开始问答' }).disabled).toBe(true);
  fireEvent.click(within(maintaining).getByRole('button', { name: '管理资料' }));
  expect(data.actions.openDocuments).toHaveBeenCalledWith(sampleBases[1]);
});

it('retains actual detection states and fee disclosure without equating configured media with a ready assistant', async () => {
  const { view, api } = fixture();
  const report: FunctionalReport = { checks: {
    model: { service: 'synthetic-model', state: 'unavailable', reason: 'configuration_unavailable', checked_at: null, expires_at: null, fingerprint: null },
    asr: { service: 'synthetic-asr', state: 'available', reason: 'check_succeeded', checked_at: null, expires_at: null, fingerprint: null },
    tts: { service: 'synthetic-tts', state: 'unavailable', reason: 'provider_timeout', checked_at: null, expires_at: null, fingerprint: null },
    knowledge: { service: 'synthetic-knowledge', state: 'not_checked', reason: 'acceptance_kb_readonly_probe_unavailable', checked_at: null, expires_at: null, fingerprint: null },
  } };
  const autoFunctionalChecks = vi.fn(async () => report);
  render(<StrictMode><Theme><Status view={view} api={{ ...api, autoFunctionalChecks }} /></Theme></StrictMode>);
  await waitFor(() => expect(screen.getByText('缺少必要配置')).toBeTruthy());
  expect(autoFunctionalChecks).toHaveBeenCalledTimes(1);
  const voice = screen.getByRole('heading', { name: '语音与媒体' }).closest<HTMLElement>('.status-capability')!;
  expect(within(voice).getAllByText('不可用')).toHaveLength(2);
  expect(within(voice).getByText('ASR：实际调用成功；TTS：供应商请求超时')).toBeTruthy();
  expect(screen.getByText(/可能产生供应商费用；不发送聊天或资料正文/)).toBeTruthy();
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新系统状态' }).classList.contains('ant-btn-loading')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '刷新系统状态' }));
  await waitFor(() => expect(autoFunctionalChecks).toHaveBeenCalledWith(true));
  expect(view.actions.loadHealth).toHaveBeenCalledTimes(1);
  expect(view.voice.actions.refresh).toHaveBeenCalledTimes(1);
}, 20000);
