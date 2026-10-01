import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Space, Tag } from 'antd';
import type { AgentRun, ApiClient } from '../api/client';

const labels: Record<AgentRun['status'], string> = { running: '任务执行中', waiting_input: '等待补充',
  waiting_approval: '等待审批', completed: '任务已完成', failed: '任务失败', cancelled: '任务已取消',
  interrupted: '任务已中断', expired: '等待已过期' };
const active = new Set(['running', 'waiting_input', 'waiting_approval']);

export function AgentTaskCard({ run, resume, cancel, voiceControl = false }: {
  run: AgentRun; resume: (input: Record<string, unknown>) => Promise<unknown>;
  cancel: () => Promise<unknown>; voiceControl?: boolean;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const disabled = busy || (!!run.voice_session_id && !voiceControl);
  const fields = run.waiting?.fields ?? {};
  const resumable = run.status === 'interrupted' && ['server_restarted', 'agent_execution_lost'].includes(run.error_code ?? '');
  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '任务操作未确认，请刷新状态后重试。'); }
    finally { setBusy(false); }
  }
  function submit() {
    const input: Record<string, unknown> = {};
    for (const [key, kind] of Object.entries(fields)) {
      const value = values[key] ?? '';
      if (!value.trim()) { setError(`请填写 ${key}`); return; }
      if (kind === 'integer' || kind === 'number') {
        const number = Number(value);
        if (!Number.isFinite(number) || kind === 'integer' && !Number.isSafeInteger(number)) {
          setError(`${key} 需要${kind === 'integer' ? '整数' : '数字'}`); return;
        }
        input[key] = number;
      } else if (kind === 'boolean') input[key] = value === 'true';
      else input[key] = value;
    }
    if (!Object.keys(fields).length) {
      if (!values.detail?.trim()) { setError('请补充说明'); return; }
      input.detail = values.detail.trim();
    }
    void perform(() => resume(input));
  }
  return <section className="agent-task" aria-label={labels[run.status]}>
    <Space wrap><strong>{labels[run.status]}</strong><Tag>模型 {run.model_rounds}/6 · 工具 {run.tool_attempts}/4</Tag></Space>
    {run.waiting?.kind === 'approval' && <>
      <p>工具：{run.waiting.tool_id} · 版本：{run.waiting.tool_version}</p>
      <p>目标：{run.waiting.destination}</p><p>{run.waiting.impact}</p>
      <pre className="agent-arguments">{JSON.stringify(run.waiting.arguments, null, 2)}</pre>
      <Space wrap><Button type="primary" disabled={disabled} onClick={() => void perform(() => resume({ approve: true }))}>批准所示操作</Button>
        <Button disabled={disabled} onClick={() => void perform(() => resume({ approve: false }))}>拒绝操作</Button></Space>
    </>}
    {run.waiting?.kind === 'input' && <div className="agent-inputs"><p>{run.waiting.prompt}</p>
      {Object.entries(fields).map(([key, kind]) => <label key={key}>{key}
        {kind === 'boolean' ? <Select aria-label={key} value={values[key]} disabled={disabled}
          options={[{ value: 'true', label: '是' }, { value: 'false', label: '否' }]}
          onChange={(value: string) => setValues({ ...values, [key]: value })} /> :
          <Input aria-label={key} value={values[key] ?? ''} disabled={disabled} maxLength={1000}
            onChange={(event) => setValues({ ...values, [key]: event.target.value })} />}</label>)}
      {!Object.keys(fields).length && <Input.TextArea aria-label="补充说明" value={values.detail ?? ''}
        disabled={disabled} maxLength={1000} onChange={(event) => setValues({ detail: event.target.value })} />}
      <Button disabled={disabled} type="primary" onClick={submit}>补充并继续</Button>
    </div>}
    {resumable && <Button disabled={disabled} onClick={() => void perform(() => resume({}))}>恢复中断任务</Button>}
    {active.has(run.status) && <Button danger disabled={disabled} onClick={() => void perform(cancel)}>取消任务</Button>}
    {!!run.voice_session_id && !voiceControl && <p className="muted">此任务属于通话，请在当前通话控制端操作；挂断后不会恢复旧音频。</p>}
    {run.error_code && <p className="failure-reason">任务代码：{run.error_code}</p>}
    {error && <Alert type="error" showIcon title={error} />}
  </section>;
}

export function AgentTasks({ chatId, api, changed }: {
  chatId: string; api: ApiClient; changed?: (chatId: string) => Promise<void>;
}) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState('');
  const callback = useRef(changed); callback.current = changed;
  const resumeKeys = useRef(new Map<string, string>());
  const latest = useRef(new Map<string, AgentRun>());
  function merge(next: AgentRun[]) {
    return next.map((run) => {
      const old = latest.current.get(run.id);
      const current = old && (old.generation > run.generation || old.seq > run.seq) ? old : run;
      latest.current.set(run.id, current);
      return current;
    });
  }
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let previous = '';
    let delay = 1000;
    let failures = 0;
    async function read() {
      try {
        const response = await api.agentRuns(chatId);
        if (disposed) return;
        const next = merge(response);
        failures = 0;
        delay = next.some((run) => run.status === 'running') ? 1000 : 5000;
        setRuns(next); setError('');
        const signature = next.map((run) => `${run.id}:${run.seq}:${run.status}`).join(',');
        if (signature !== previous) { previous = signature; await callback.current?.(chatId); }
      } catch (reason) {
        if (!disposed) {
          failures += 1;
          delay = Math.min(30000, 1000 * 2 ** Math.min(failures, 5));
          setError(reason instanceof Error ? reason.message : '任务状态读取失败，请刷新');
        }
      } finally { if (!disposed) timer = setTimeout(() => { void read(); }, delay); }
    }
    void read();
    return () => { disposed = true; clearTimeout(timer); };
  }, [api, chatId]);
  const card = (run: AgentRun) => <AgentTaskCard key={`${run.id}:${run.generation}`} run={run}
    resume={async (input) => {
      const fingerprint = `${run.id}:${run.generation}:${JSON.stringify(input)}`;
      const key = resumeKeys.current.get(fingerprint) ?? crypto.randomUUID();
      resumeKeys.current.set(fingerprint, key);
      const result = await api.resumeAgent(run, key, input);
      const current = merge([result])[0];
      setRuns((old) => old.map((item) => item.id === run.id ? current : item));
      await callback.current?.(chatId);
    }} cancel={async () => {
      const result = await api.cancelAgent(run);
      const current = merge([result])[0];
      setRuns((old) => old.map((item) => item.id === run.id ? current : item));
      await callback.current?.(chatId);
    }} />;
  const pending = runs.filter((run) => active.has(run.status) || run.status === 'interrupted');
  const history = runs.filter((run) => !pending.includes(run));
  return <div className="agent-tasks">{error && <Alert title={error} type="error" showIcon />}
    {pending.map(card)}
    {!!history.length && <details><summary>最近任务记录（{history.length}）</summary>{history.map(card)}</details>}
  </div>;
}
