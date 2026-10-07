import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Descriptions, Drawer, Dropdown, Empty, Form, Input, Modal, Pagination, Skeleton, Space, Tag, Upload } from 'antd';
import { BookOutlined, DatabaseOutlined, FileTextOutlined, MoreOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import type { ApiClient, FunctionalKind, FunctionalReport, IngestionJob, ManagedDocument, SystemCheckReport } from '../api/client';
import type { AppView } from '../app';
import { isKnowledgePending, knowledgeLimit } from '../state';
import { errorText, failureReason, locatorLabel, stoppedStage } from '../pages/documents';
import { matchesPendingFiles } from '../document-draft';
import { backupErrors } from '../pages/status';
import { StateTag, dateLabel, sizeLabel } from '../preview/shared';

export function Knowledge({ view, api }: { view: AppView; api: ApiClient }) {
  const { state: s, actions: a } = view;
  const [createOpen, setCreateOpen] = useState(false);
  const pending = isKnowledgePending(s);
  useEffect(() => { if (s.knowledgeNotice && !s.createDraft.pending && !s.createDraft.error && !s.createDraft.name) setCreateOpen(false); },
    [s.knowledgeNotice, s.createDraft.pending, s.createDraft.error, s.createDraft.name]);
  if (view.documents.isOpen) return <Documents view={view} api={api} />;
  return <><div className="section-heading knowledge-heading"><span>{s.bases?.length ?? '—'} / {knowledgeLimit}</span>
    <Button aria-label="创建知识库" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} disabled={s.loading || !!s.basesError || pending || (s.bases?.length ?? 0) >= knowledgeLimit}>创建知识库</Button></div>
    {s.loading && <Skeleton active />}{s.basesError && <Alert type="error" showIcon title={s.basesError.message} action={<Button onClick={() => void a.refresh()}>重新读取</Button>} />}
    {s.knowledgeNotice && <Alert showIcon type="info" title={s.knowledgeNotice} />}
    {!s.loading && !s.basesError && !s.bases?.length && <Empty description="还没有知识库，创建后添加资料" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    <div className="kb-cards">{s.bases?.map((base) => <Card key={base.id} className={`knowledge-card is-${base.status}`}><div className="knowledge-card-title"><span className="knowledge-card-icon"><BookOutlined /></span><h2>{base.name}</h2><StateTag value={base.status} /></div>
      <p className="knowledge-card-note">聊天固定在此知识库；维护期间暂停资料问答。</p>
      {base.status !== 'ready' && <Alert type="warning" title="此库当前不能开始资料问答，请查看任务状态。" />}
      <div className="knowledge-card-actions"><Button icon={<FileTextOutlined />} aria-label="管理资料" disabled={pending} onClick={() => a.openDocuments(base)}>管理资料</Button>
        <Button type="primary" disabled={pending || s.chatPending || base.status !== 'ready'} onClick={() => { a.selectKb(base.id); a.navigate('workbench'); }}>开始问答</Button>
        <Button disabled={pending} onClick={() => a.beginRename(base.id)}>知识库改名</Button></div></Card>)}</div>
    <Alert className="knowledge-tip" type="info" showIcon title="知识库聊天固定单库；也可新建不检索资料的普通聊天。" />
    <Modal title="创建知识库" open={createOpen || s.createDraft.uncertain} onCancel={() => { if (!pending) setCreateOpen(false); }}
      confirmLoading={s.createDraft.pending} okText={s.createDraft.uncertain ? '同键重试创建' : '创建'}
      okButtonProps={{ disabled: !s.createDraft.name.trim() || s.loading || !!s.basesError || pending || s.createDraft.error?.code === 'recovery_read_failed' }}
      onOk={() => void a.createKnowledgeBase()}>
      <Form layout="vertical"><Form.Item label="知识库名称"><Input autoFocus maxLength={120} value={s.createDraft.name} disabled={pending || s.createDraft.uncertain} onChange={(e) => a.setKnowledgeName(e.target.value)} /></Form.Item></Form>
      {s.createDraft.uncertain && <Alert type="warning" showIcon title="受理结果尚未确认，将沿用原名称和幂等键重试。" />}
      {s.createDraft.error && <Alert type="error" showIcon title={s.createDraft.error.message} />}</Modal>
    <Modal title="知识库改名" open={!!s.renameDraft} onCancel={a.cancelRename} confirmLoading={s.renameDraft?.pending} okText="保存名称"
      okButtonProps={{ disabled: !s.renameDraft?.name.trim() || pending }} onOk={() => void a.renameKnowledgeBase()}>
      <Form layout="vertical"><Form.Item label="知识库名称"><Input maxLength={120} value={s.renameDraft?.name ?? ''} disabled={pending} onChange={(e) => a.setKnowledgeName(e.target.value, true)} /></Form.Item></Form>
      {s.renameDraft?.error && <Alert type="error" showIcon title={s.renameDraft.error.message} />}</Modal></>;
}

function Attributes({ document, view }: { document: ManagedDocument; view: AppView }) {
  return <Form layout="vertical" initialValues={{ doc_code: document.doc_code ?? '', model_code: document.model_code ?? '', edition: document.edition ?? '' }}
    onFinish={(values: Record<'doc_code' | 'model_code' | 'edition', string>) => void view.documents.saveAttributes(document, {
      doc_code: values.doc_code.trim() || null, model_code: values.model_code.trim() || null, edition: values.edition.trim() || null,
    })}>
    {([['doc_code', '文档编号'], ['model_code', '型号'], ['edition', '资料版本']] as const).map(([name, label]) => <Form.Item key={name} name={name} label={label}
      rules={[{ max: 80 }, { validator: (_, value: string) => [...(value ?? '')].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
        ? Promise.reject(new Error('不能包含控制字符')) : Promise.resolve() }]}><Input maxLength={80} /></Form.Item>)}
    <Button htmlType="submit" disabled={view.documents.snapshot.unavailable || view.documents.snapshot.active || !!view.documents.snapshot.pending}>确认属性</Button>
    <p className="muted">属性由你核对原文后确认，用于资料精确定位。</p></Form>;
}

export function Documents({ view, api }: { view: AppView; api: ApiClient }) {
  const panel = view.documents; const d = panel.snapshot;
  const [selected, setSelected] = useState<ManagedDocument | null>(null);
  const [rebuildOpen, setRebuildOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  if (!d.base) return <Empty description="请先选择知识库" />;
  const uploadDisabled = d.unavailable || d.pending?.operation === 'rebuild' || (!d.pending && (d.active || ['maintaining', 'blocked'].includes(d.base.status)));
  const mutateDisabled = d.unavailable || !!d.pending || d.active;
  const currentSelected = d.documents?.find((item) => item.id === selected?.id) ?? null;
  const readable = !['maintaining', 'blocked'].includes(d.base.status);
  function rows(items: ManagedDocument[], deleted = false) {
    return <div className="document-list">{items.map((document) => <article className="document-list-row" key={document.id}>
      <FileTextOutlined className={`file-icon file-${document.filename.split('.').pop()?.toLowerCase() ?? 'other'}`} /><div className="document-info"><strong>{document.filename}</strong>
        <p>{sizeLabel(document.size)} · {dateLabel(document.created_at)}</p></div>
      <div className="document-status"><StateTag value={document.status} />
        {document.error_code && !deleted && <p className="failure-reason">{failureReason(document.error_code)}</p>}</div>
      <div className="row-actions">{!deleted ? <><Button onClick={() => setSelected(document)}>资料详情</Button>
        <Dropdown trigger={['click']} menu={{ items: [
          { key: 'download', label: readable && !['deleting', 'replacing', 'deleted'].includes(document.status)
            ? <a href={api.originalUrl(document.id)}>下载原文</a> : '原文核查暂停', disabled: !readable || ['deleting', 'replacing', 'deleted'].includes(document.status) },
          { key: 'location', label: '查看解析位置', disabled: !readable || !['parsed', 'ready'].includes(document.status) },
          { key: 'delete', label: document.status === 'failed' ? '删除失败资料' : '删除资料', danger: true,
            disabled: mutateDisabled || !(document.status === 'ready' && d.base?.status === 'ready' || document.status === 'failed' && ['empty', 'ready', 'blocked'].includes(d.base?.status ?? '')) },
        ], onClick: ({ key }) => { if (key === 'location') void panel.inspect(document);
          if (key === 'delete') void panel.maintain(document, 'delete'); } }}>
          <Button type="text" icon={<MoreOutlined />} aria-label={`${document.filename}的更多操作`} /></Dropdown></> : <span className="muted">保留删除标记与历史任务</span>}</div></article>)}</div>;
  }
  const paginator = (kind: 'current' | 'deleted', offset: number, total: number) => <Pagination aria-label={kind === 'current' ? '当前资料分页' : '已删除资料分页'}
    current={Math.floor(offset / 10) + 1} pageSize={10} total={total} showSizeChanger={false} showTotal={(n) => `共 ${n} 份`} disabled={d.busy || d.loading} onChange={(page) => panel.movePage(kind, (page - 1) * 10)} />;
  return <div className="management-main"><div className="library-summary"><Space wrap>
      <Button disabled={d.busy} onClick={view.actions.closeDocuments}>返回知识库</Button><Button icon={<ReloadOutlined />} disabled={d.busy || d.loading} onClick={() => void panel.refresh()}>刷新资料与任务</Button></Space></div>
    {['maintaining', 'blocked'].includes(d.base.status) && <Alert showIcon type="warning" title={d.base.status === 'blocked' ? '此库待修复，问答与新上传暂停。' : '此库维护中，请等待任务核验。'} />}
    {d.readError && <Alert showIcon type="error" title={errorText(d.readError)} />}{d.error && <Alert showIcon type="error" title={errorText(d.error)} />}
    {d.notice && <Alert showIcon type="info" title={d.notice} />}
    <div className="upload-strip"><Button type="primary" icon={<PlusOutlined />} aria-expanded={uploadOpen || d.uncertain}
      disabled={uploadDisabled && !d.uncertain} onClick={() => setUploadOpen(!uploadOpen)}>添加资料</Button><span>支持格式：TXT / MD / 文字 PDF / DOCX</span>
      {uploadDisabled && <small>读取未完成、维护中或已有任务，暂不接收新资料。</small>}</div>
    {(uploadOpen || d.uncertain) && <div className="upload-section">
      <Upload.Dragger accept=".txt,.md,.pdf,.docx" multiple disabled={uploadDisabled} fileList={d.files.map((file, i) => ({ uid: `${i}:${file.name}`, name: file.name, originFileObj: file as never }))}
        beforeUpload={() => false} onChange={({ fileList }) => panel.setFiles(fileList.flatMap((file) => file.originFileObj ? [file.originFileObj] : []))}>
        <p><UploadOutlined /> 选择或拖入资料</p><p>UTF-8 TXT、Markdown、文字 PDF、普通 DOCX</p></Upload.Dragger>
      <p className="muted">每批 1–5 份，每份最多 20 MiB；PDF 最多 100 页。扫描件需要先转为文字资料。</p>
      {d.uncertain && <Alert type="warning" showIcon title="受理结果尚未确认，重新选择原文件后同键重试。" description={d.pending?.files.map((file) => file.name).join('、')} />}
      <Button type="primary" loading={d.busy} disabled={uploadDisabled || !d.files.length || d.error?.kind === 'validation' || (!!d.pending && !matchesPendingFiles(d.pending, d.files))}
        onClick={() => void panel.submit('upload')}>{d.uncertain ? '同键重试上传' : '上传并处理'}</Button>
      {uploadDisabled && <p className="muted">读取未完成、维护中、已有任务或恢复记录不可用时暂停新增上传。</p>}</div>}
    <div className="section-heading"><h2>当前资料</h2><Tag>{d.documentPage?.total ?? '—'} 份</Tag></div>
    {d.loading && <Skeleton active />}{!d.loading && d.documents?.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无当前资料" />}
    {rows(d.documents ?? [])}{d.documentPage && paginator('current', d.documentOffset, d.documentPage.total)}
    <Collapse className="history-collapse" activeKey={d.deletedOpen ? ['deleted'] : []} onChange={(keys) => panel.setDeletedOpen(keys.includes('deleted'))}
      items={[{ key: 'deleted', label: `已删除记录 · ${d.documentPage?.counts.deleted ?? 0} 份`, children: <>{rows(d.deletedPage?.items ?? [], true)}
        {d.deletedPage && paginator('deleted', d.deletedOffset, d.deletedPage.total)}</> }]} />
    <div className="detail-actions"><Button onClick={() => view.actions.navigate('tasks')}>查看处理任务</Button>
      <Button disabled={d.unavailable || d.active || d.pending?.operation === 'upload' || !d.documentPage?.total} onClick={() => setRebuildOpen(true)}>从原文重建</Button></div>
    <Modal title="从受管原文重建" open={rebuildOpen} destroyOnHidden onCancel={() => { if (!d.busy) { setRebuildOpen(false); panel.setConfirmed(false); } }} okText={d.uncertain ? '同键重试重建' : '重建知识库'}
      confirmLoading={d.busy} okButtonProps={{ disabled: d.unavailable || d.active || d.pending?.operation === 'upload' || (!d.rebuildConfirmed && !d.uncertain) }} onOk={() => void panel.submit('rebuild')}>
      <Alert showIcon type="warning" title="重建期间暂停问答并遮蔽旧知识回答与来源。" description="重新解析、索引和核验；通过后切换活动空间并清理旧空间。失败保留待修复，不覆盖旧任务事实。" />
      <Checkbox checked={d.rebuildConfirmed} disabled={d.busy} onChange={(e) => panel.setConfirmed(e.target.checked)}>我已了解重建对问答和历史证据的影响</Checkbox>
      {d.error && <Alert type="error" title={errorText(d.error)} />}{d.notice && <Alert type="info" title={d.notice} />}</Modal>
    <Drawer title="资料详情" open={!!currentSelected} onClose={() => setSelected(null)} size={460} destroyOnHidden>
      {currentSelected && <><h2>{currentSelected.filename}</h2><StateTag value={currentSelected.status} />
        {currentSelected.error_code && <Alert showIcon type="error" title={failureReason(currentSelected.error_code)} />}
        {currentSelected.status === 'ready' && d.base.status === 'ready' && <><h3>编号与版本</h3><Attributes key={currentSelected.id} document={currentSelected} view={view} />
          <h3>替换资料</h3><p className="muted">替换后该库旧知识回答与来源会暂停访问；选择文件后还需确认。</p>
          <Upload accept=".txt,.md,.pdf,.docx" showUploadList={false} disabled={mutateDisabled} beforeUpload={(file) => { void panel.maintain(currentSelected, 'replace', file); return false; }}>
            <Button disabled={mutateDisabled}>选择替换文件</Button></Upload></>}
        {d.jobs?.filter((job) => job.document_ids.includes(currentSelected.id)).map((job) => <Button key={job.id} onClick={() => { setSelected(null); view.actions.navigate('tasks'); void panel.inspectTask(job); }}>查看关联任务</Button>)}</>}
    </Drawer>
    <Drawer title="解析位置" open={!!d.inspected} onClose={panel.closeInspector} size={520} destroyOnHidden>
      {d.inspected && <><h2>{d.inspected.document.filename}</h2>{d.inspected.error && <Alert type="error" title={errorText(d.inspected.error)} />}
        {!d.inspected.blocks && !d.inspected.error && <Skeleton active />}{d.inspected.blocks?.map((block) => <section key={block.ordinal} className="parsed-block"><Tag>{locatorLabel(block)}</Tag><p>{block.text}</p></section>)}</>}
    </Drawer>
  </div>;
}

const operations = { upload: '资料上传任务', rebuild: '原文重建任务', delete: '资料删除任务', replace: '资料替换任务' };
const stages = { accepted: '已受理，等待处理', parsing: '正在解析', parsed: '解析完成，尚未入库', indexing: '正在建立索引', verifying: '正在核验入库结果', cleanup: '正在清理旧空间', complete: '核验与清理通过' };
export function Tasks({ view }: { view: AppView }) {
  const panel = view.documents; const d = panel.snapshot;
  const disabled = d.unavailable || d.active || !!d.pending;
  if (!d.base) return <Empty description="选择知识库后查看资料与任务" />;
  const jobName = (item: IngestionJob) => {
    const related = d.documents?.find((document) => item.document_ids.includes(document.id));
    return related ? `${operations[item.operation]} · ${related.filename}` : operations[item.operation];
  };
  const rows = (jobs: IngestionJob[]) => <div className="jobs-list">{jobs.map((item) => <button type="button" className={`job-row${d.inspectedJob?.job?.id === item.id ? ' selected' : ''}`} key={item.id}
    disabled={d.busy} onClick={() => void panel.inspectTask(item)} aria-label={`查看任务详情：${jobName(item)}`}>
    <span className="job-name"><FileTextOutlined /><span><strong>{jobName(item)}</strong><small>{item.document_ids.length} 份资料</small></span></span>
    <span className="job-stage">{['failed', 'interrupted'].includes(item.status) ? stoppedStage(item.stage) : stages[item.stage]}</span>
    <StateTag value={item.status} /><time dateTime={item.created_at}>{dateLabel(item.created_at)}</time><span aria-hidden="true">›</span></button>)}</div>;
  const job = d.inspectedJob?.job;
  return <div className="tasks-layout"><div className="task-summary">
      <div><span>进行中</span><strong>{d.jobPage?.active_items.length ?? '—'}</strong></div>
      <div><span>需要处理</span><strong>{d.jobPage?.failed_count ?? '—'}</strong></div>
      <div><span>已结束</span><strong>{d.jobPage?.total ?? '—'}</strong></div></div>
    {d.error && <Alert type="error" title={errorText(d.error)} />}{d.readError && <Alert type="error" title={errorText(d.readError)} />}
    {d.notice && <Alert type="info" title={d.notice} />}{d.loading && <Skeleton active />}
    <div className="task-columns"><section className="task-table"><div className="task-table-header"><strong>共 {(d.jobPage?.total ?? 0) + (d.jobPage?.active_items.length ?? 0)} 项任务</strong>
      <Button icon={<ReloadOutlined />} disabled={d.busy || d.loading} onClick={() => void panel.refresh()}>刷新任务</Button></div>
      <div className="job-columns" aria-hidden="true"><span>任务名称 / 资料文件</span><span>当前阶段</span><span>处理结果</span><span>创建时间</span></div>
      {rows(d.jobPage?.active_items ?? [])}{rows(d.jobPage?.items ?? [])}
      {!d.loading && !d.jobPage?.active_items.length && !d.jobPage?.items.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" />}
      <Pagination current={Math.floor(d.jobOffset / 10) + 1} total={d.jobPage?.total ?? 0} pageSize={10} showSizeChanger={false}
        disabled={d.busy || d.loading} onChange={(page) => panel.movePage('history', (page - 1) * 10)} />
      <p className="muted task-history-note">历史任务保留当次结果；后续成功不会覆盖旧失败记录。</p></section>
    <section className="task-detail" aria-label="任务详情"><div className="task-detail-header"><h2>任务详情</h2>{d.inspectedJob && <Button type="text" onClick={panel.closeInspector}>关闭</Button>}</div>
      {d.inspectedJob?.error && <Alert type="error" title={errorText(d.inspectedJob.error)} />}
      {!d.inspectedJob && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择左侧任务查看详情" />}
      {d.inspectedJob && !job && !d.inspectedJob.error && <Skeleton active />}{job && <><Descriptions column={1} items={[
        { key: 'operation', label: '任务类型', children: operations[job.operation] }, { key: 'status', label: '任务状态', children: <StateTag value={job.status} /> },
        { key: 'stage', label: '实际阶段', children: ['failed', 'interrupted'].includes(job.status) ? stoppedStage(job.stage) : stages[job.stage] },
        { key: 'docs', label: '关联资料', children: `${job.document_ids.length} 份` }, { key: 'engine', label: '引擎写入', children: job.engine_mutated ? '已发生' : '未发生' },
        { key: 'retry', label: '可重试性', children: job.can_retry ? '可按原任务重试' : '当前不能按原任务重试' },
        { key: 'cleanup', label: '旧空间清理', children: job.cleanup_pending ? '存在待清理项' : '无待清理项' },
      ]} />{job.error_code && <Alert showIcon type="error" title={failureReason(job.error_code)} />}
        <Space wrap><Button disabled={d.busy || d.loading} onClick={() => void panel.inspectTask(job)}>刷新任务详情</Button>
          {job.can_retry && <Button disabled={disabled} onClick={() => void panel.retry(job)}>重试任务</Button>}
          {job.can_cleanup && <Button disabled={disabled} onClick={() => void panel.cleanup(job)}>重试清理</Button>}</Space></>}
    </section></div></div>;
}

export function Status({ view, api }: { view: AppView; api: ApiClient }) {
  const s = view.state; const h = s.health;
  const [report, setReport] = useState<SystemCheckReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [functional, setFunctional] = useState<FunctionalReport | null>(null);
  const [functionalError, setFunctionalError] = useState<string | null>(null);
  const [costAccepted, setCostAccepted] = useState(false);
  const [submitting, setSubmitting] = useState<FunctionalKind | null>(null);
  const submittingRef = useRef(false);
  const functionalKinds: FunctionalKind[] = ['model', 'asr', 'tts', 'knowledge'];
  const functionalLabels: Record<FunctionalKind, string> = {
    model: '模型服务', asr: '语音识别', tts: '语音合成', knowledge: '知识检索端到端',
  };
  const reasonLabels: Record<string, string> = {
    never_checked: '尚未检测', check_running: '检测中', check_succeeded: '实际请求成功',
    check_expired: '结果已过期，需重新检测', configuration_changed: '配置已变化，需重新检测',
    configuration_unavailable: '检测所需配置不可用', interrupted: '上次检测中断，需重新检测',
    cancelled: '检测已取消，需重新检测', authentication_rejected: '供应商拒绝认证',
    quota_rejected: '供应商拒绝配额或限流', provider_timeout: '供应商请求超时',
    provider_unavailable: '供应商请求失败', response_invalid: '供应商结果不符合检测要求',
    acceptance_kb_readonly_probe_unavailable: '当前没有可证明只读的验收知识库检索路径，尚未检查',
  };
  useEffect(() => {
    if (!api.functionalStatus) return;
    let mounted = true;
    void api.functionalStatus().then((value) => { if (mounted) setFunctional(value); })
      .catch(() => { if (mounted) setFunctionalError('功能检测记录读取失败'); });
    return () => { mounted = false; };
  }, [api]);
  const anyRunning = functionalKinds.some((kind) => functional?.checks[kind].state === 'running');
  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(() => {
      void api.functionalStatus().then(setFunctional)
        .catch(() => setFunctionalError('功能检测记录读取失败'));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [api, anyRunning]);
  async function startFunctional(kind: FunctionalKind) {
    if (!costAccepted || submittingRef.current || submitting || anyRunning || kind === 'knowledge') return;
    const requestId = crypto.randomUUID();
    submittingRef.current = true;
    setSubmitting(kind); setFunctionalError(null);
    try {
      const result = await api.startFunctionalCheck(kind, requestId);
      setFunctional((previous) => previous && {
        checks: { ...previous.checks, [kind]: result },
      });
    } catch { setFunctionalError(`${functionalLabels[kind]}检测未启动，请先刷新记录`); }
    finally { submittingRef.current = false; setSubmitting(null); }
  }
  async function cancelFunctional(kind: FunctionalKind) {
    const requestId = functional?.checks[kind].request_id;
    if (!requestId) return;
    try {
      const result = await api.cancelFunctionalCheck(kind, requestId);
      setFunctional((previous) => previous && {
        checks: { ...previous.checks, [kind]: result },
      });
    } catch { setFunctionalError(`${functionalLabels[kind]}取消未确认，请刷新记录`); }
  }
  const checkLabels: Record<string, string> = {
    business_database: '业务数据库', model_configuration: '模型配置', rag_database: '知识引擎本地数据库',
    voice_transport: '本地媒体端口', model_provider: '模型服务实测',
    speech_providers: '语音识别与合成实测', knowledge_engine: '知识检索端到端实测',
  };
  async function runLocalCheck() {
    setChecking(true);
    setCheckError(null);
    try { setReport(await api.checkSystem()); }
    catch { setCheckError('本地检测未完成，请重试。'); }
    finally { setChecking(false); }
  }
  const capabilities = [
    { name: '业务数据库', value: h?.database ?? 'unverified', icon: <DatabaseOutlined />, note: '聊天、知识库与任务记录的本地存储。' },
    { name: '模型服务', value: h?.models ?? 'unverified', icon: <BookOutlined />, note: '配置状态与真实调用验证分别记录。' },
    { name: '知识引擎', value: h?.rag ?? 'unverified', icon: <FileTextOutlined />, note: '检索与问答引擎的当前状态。' },
    { name: '语音与媒体', value: view.voice.actions.capability?.assistant ?? 'unverified', icon: <UploadOutlined />, note: '通话条件须在语音页实际验证。' },
  ];
  return <div className="status-layout"><div className="status-actions"><Button type="primary" icon={<ReloadOutlined />} loading={s.healthLoading} onClick={() => { void view.actions.loadHealth(); void view.voice.actions.refresh(); }}>刷新系统状态</Button>
    <Button loading={checking} onClick={() => void runLocalCheck()}>运行本地检测</Button>
    {s.healthCheckedAt && <span>本次读取 {new Date(s.healthCheckedAt).toLocaleTimeString('zh-CN')}</span>}</div>
    {!report && !checking && !checkError && <p>尚未检测</p>}
    {checking && <p>检测中</p>}
    {checkError && <Alert type="error" title={checkError} />}
    {report && <section aria-label="本地检测结果"><h2>本地检测结果</h2><p>检测时间：{dateLabel(report.checked_at)}</p>
      {Object.entries(report.checks).map(([name, result]) => <div key={name}>
        <strong>{checkLabels[name] ?? name}</strong> <StateTag value={result.state} /> <span>{result.reason}</span>
      </div>)}</section>}
    <section className="functional-checks" aria-label="实际功能检测"><h2>实际功能检测</h2>
      <Alert type="warning" showIcon title="功能检测可能产生供应商费用：模型最多 1 次，语音合成最多 1 次，语音识别最多 1 次合成和 1 次识别。只在手动确认后发起，不自动重试。知识库检索尚无可证明只读的同库验收路径，因此保持未检查。" />
      <Checkbox checked={costAccepted} onChange={(event) => setCostAccepted(event.target.checked)}>我已了解费用和检测范围</Checkbox>
      {functionalError && <Alert type="error" title={functionalError} />}
      {!functional && <p>尚未检测</p>}
      {functional && functionalKinds.map((kind) => {
        const item = functional.checks[kind];
        return <div className="functional-check-row" key={kind}><div className="functional-check-content">
          <div className="functional-check-title"><strong>{functionalLabels[kind]}</strong>
            <span className="muted">{item.service}</span><StateTag value={item.state} /></div>
          <div>{reasonLabels[item.reason] ?? '检测状态未知'}</div>
          <div className="functional-check-meta">
            {item.checked_at && <span>检测时间：{dateLabel(item.checked_at)}</span>}
            {item.expires_at && <span>有效至：{dateLabel(item.expires_at)}</span>}
            {item.fingerprint && <span>配置指纹：{item.fingerprint.slice(0, 12)}</span>}
          </div></div><div className="functional-check-actions">
          {kind !== 'knowledge' && <Button disabled={!costAccepted || anyRunning || submitting !== null}
            onClick={() => void startFunctional(kind)}>检测{functionalLabels[kind]}</Button>}
          {item.state === 'running' && item.request_id && <Button onClick={() => void cancelFunctional(kind)}>取消检测</Button>}
          </div>
        </div>;
      })}</section>
    <Alert type="info" showIcon title="以下显示接口报告的状态；配置存在不代表实际供应商调用或真人设备验收通过。" />
    <div className="section-heading"><h2>服务能力</h2><span className="muted">本地服务与组件的当前配置和可用性</span></div>
    {s.healthLoading && <Skeleton active />}{s.healthError && <Alert showIcon type="error" title={s.healthError.message} />}
    <div className="status-capabilities">{capabilities.map((item) => <Card key={item.name} className={`status-capability tone-${['available', 'ready', 'succeeded'].includes(item.value) ? 'ok' : ['unavailable', 'failed', 'blocked', 'not_configured'].includes(item.value) ? 'off' : 'caution'}`}><div className="status-capability-head"><span className="status-capability-icon">{item.icon}</span><div><h3>{item.name}</h3><StateTag value={item.value} /></div></div><p>{item.note}</p></Card>)}</div>
    <div className="status-panels"><Card title="模型与引擎"><Descriptions column={1} items={[
      { key: 'names', label: '模型名称', children: h?.models_info?.model_names.join(' / ') || '未提供' },
      { key: 'region', label: '地区', children: h?.models_info?.region || '未提供' },
      { key: 'modelsverified', label: '模型验证时间', children: h?.models_info?.last_verified_at ? dateLabel(h.models_info.last_verified_at) : '尚无记录' },
      { key: 'ragverified', label: '引擎验证时间', children: h?.rag_info?.last_verified_at ? dateLabel(h.rag_info.last_verified_at) : '尚无记录' },
      { key: 'commit', label: 'LightRAG 版本', children: h?.rag_info?.lightrag_commit.slice(0, 8) || '未提供' },
      { key: 'pg', label: 'PostgreSQL / pgvector', children: `${h?.rag_info?.postgresql_major ?? '未提供'} / ${h?.rag_info?.vector_version ?? '未提供'}` },
    ]} /></Card><Card title="备份与保留"><div className="status-backup"><StateTag value={h?.backup ?? 'unverified'} /><p>最近完成：{h?.last_backup_at ? dateLabel(h.last_backup_at) : '尚无记录'}</p>
      {h?.backup_error_code && <Alert type="error" title={backupErrors[h.backup_error_code] ?? '备份状态待核查。'} />}
      <p>保留清理：<StateTag value={h?.retention ?? 'unverified'} /></p>
      <p>语音媒体：<StateTag value={view.voice.actions.capability?.transport ?? 'unverified'} /></p>
      {view.voice.actions.capabilityError && <Alert type="error" title="媒体状态读取失败，请刷新后重试。" />}
      <Button onClick={() => view.actions.navigate('voice')}>查看通话条件</Button></div></Card></div>
    {s.healthCheckedAt && <p className="muted">状态读取时间：{new Date(s.healthCheckedAt).toLocaleString('zh-CN')}。本次刷新不调用供应商或探测实际模型。</p>}</div>;
}
