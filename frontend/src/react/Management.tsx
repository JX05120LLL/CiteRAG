import { useEffect, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Descriptions, Drawer, Empty, Form, Input, Modal, Pagination, Skeleton, Space, Tag, Tooltip, Upload } from 'antd';
import { BookOutlined, FileTextOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import type { ApiClient, IngestionJob, ManagedDocument } from '../api/client';
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
  return <><div className="section-heading"><h2>我的知识库 <Tag>{s.bases?.length ?? '—'} / {knowledgeLimit}</Tag></h2>
    <Button aria-label="创建知识库" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} disabled={s.loading || !!s.basesError || pending || (s.bases?.length ?? 0) >= knowledgeLimit}>创建知识库</Button></div>
    {s.loading && <Skeleton active />}{s.basesError && <Alert type="error" showIcon title={s.basesError.message} action={<Button onClick={() => void a.refresh()}>重新读取</Button>} />}
    {s.knowledgeNotice && <Alert showIcon type="info" title={s.knowledgeNotice} />}
    {!s.loading && !s.basesError && !s.bases?.length && <Empty description="还没有知识库，创建后添加资料" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    <div className="kb-cards">{s.bases?.map((base) => <Card key={base.id} title={<Space><BookOutlined />{base.name}</Space>} extra={<StateTag value={base.status} />}>
      <p className="muted">聊天固定资料范围，维护期间暂停资料问答。</p>
      <Space wrap><Button type="primary" disabled={pending} onClick={() => a.openDocuments(base)}>管理资料</Button>
        <Button disabled={pending} onClick={() => a.beginRename(base.id)}>知识库改名</Button>
        <Button disabled={pending || s.chatPending || base.status !== 'ready'} onClick={() => { a.selectKb(base.id); a.navigate('workbench'); }}>开始问答</Button></Space></Card>)}</div>
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
  if (!d.base) return <Empty description="请先选择知识库" />;
  const uploadDisabled = d.unavailable || d.pending?.operation === 'rebuild' || (!d.pending && (d.active || ['maintaining', 'blocked'].includes(d.base.status)));
  const mutateDisabled = d.unavailable || !!d.pending || d.active;
  const currentSelected = d.documents?.find((item) => item.id === selected?.id) ?? null;
  const readable = !['maintaining', 'blocked'].includes(d.base.status);
  function rows(items: ManagedDocument[], deleted = false) {
    return <div className="document-list">{items.map((document) => <article className="document-list-row" key={document.id}>
      <FileTextOutlined className="file-icon" /><div className="document-info"><strong>{document.filename}</strong>
        <p>{sizeLabel(document.size)} · {dateLabel(document.created_at)}</p><StateTag value={document.status} />
        {document.error_code && !deleted && <p className="failure-reason">{failureReason(document.error_code)}</p>}</div>
      <div className="row-actions">{!deleted && <>
        {readable && !['deleting', 'replacing', 'deleted'].includes(document.status) ? <Button type="link" href={api.originalUrl(document.id)}>下载原文</Button>
          : <span className="muted">原文核查暂停</span>}
        <Tooltip title={readable && ['parsed', 'ready'].includes(document.status) ? '读取真实解析片段' : '维护期间或尚未解析，定位不可用'}>
          <Button disabled={!readable || !['parsed', 'ready'].includes(document.status)} onClick={() => void panel.inspect(document)}>查看解析位置</Button></Tooltip>
        <Button onClick={() => setSelected(document)}>资料详情</Button>
        {(document.status === 'ready' && d.base?.status === 'ready' || document.status === 'failed' && ['empty', 'ready', 'blocked'].includes(d.base?.status ?? '')) &&
          <Button danger disabled={mutateDisabled} onClick={() => void panel.maintain(document, 'delete')}>{document.status === 'failed' ? '删除失败资料' : '删除资料'}</Button>}
      </>}{deleted && <span className="muted">保留删除标记与历史任务</span>}</div></article>)}</div>;
  }
  const paginator = (kind: 'current' | 'deleted', offset: number, total: number) => <Pagination aria-label={kind === 'current' ? '当前资料分页' : '已删除资料分页'}
    current={Math.floor(offset / 10) + 1} pageSize={10} total={total} showSizeChanger={false} showTotal={(n) => `共 ${n} 份`} disabled={d.busy || d.loading} onChange={(page) => panel.movePage(kind, (page - 1) * 10)} />;
  return <div className="management-main"><div className="library-summary"><div><h2>{d.base.name}</h2><StateTag value={d.base.status} />
    <span className="muted">上传受理、解析完成和入库就绪分别记录。</span></div><Space wrap>
      <Button disabled={d.busy} onClick={view.actions.closeDocuments}>返回知识库</Button><Button icon={<ReloadOutlined />} disabled={d.busy || d.loading} onClick={() => void panel.refresh()}>刷新资料与任务</Button></Space></div>
    {['maintaining', 'blocked'].includes(d.base.status) && <Alert showIcon type="warning" title={d.base.status === 'blocked' ? '此库待修复，问答与新上传暂停。' : '此库维护中，请等待任务核验。'} />}
    {d.readError && <Alert showIcon type="error" title={errorText(d.readError)} />}{d.error && <Alert showIcon type="error" title={errorText(d.error)} />}
    {d.notice && <Alert showIcon type="info" title={d.notice} />}
    <Collapse className="upload-section" defaultActiveKey={d.uncertain ? ['upload'] : []} items={[{ key: 'upload', label: d.uncertain ? '添加资料 · 原受理结果待确认' : '添加资料', children: <>
      <Upload.Dragger accept=".txt,.md,.pdf,.docx" multiple disabled={uploadDisabled} fileList={d.files.map((file, i) => ({ uid: `${i}:${file.name}`, name: file.name, originFileObj: file as never }))}
        beforeUpload={() => false} onChange={({ fileList }) => panel.setFiles(fileList.flatMap((file) => file.originFileObj ? [file.originFileObj] : []))}>
        <p><UploadOutlined /> 选择或拖入资料</p><p>UTF-8 TXT、Markdown、文字 PDF、普通 DOCX</p></Upload.Dragger>
      <p className="muted">每批 1–5 份，每份最多 20 MiB；PDF 最多 100 页。扫描件需要先转为文字资料。</p>
      {d.uncertain && <Alert type="warning" showIcon title="受理结果尚未确认，重新选择原文件后同键重试。" description={d.pending?.files.map((file) => file.name).join('、')} />}
      <Button type="primary" loading={d.busy} disabled={uploadDisabled || !d.files.length || d.error?.kind === 'validation' || (!!d.pending && !matchesPendingFiles(d.pending, d.files))}
        onClick={() => void panel.submit('upload')}>{d.uncertain ? '同键重试上传' : '上传并处理'}</Button>
      {uploadDisabled && <p className="muted">读取未完成、维护中、已有任务或恢复记录不可用时暂停新增上传。</p>}</> }]} />
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
  const rows = (jobs: IngestionJob[]) => <div className="jobs-list">{jobs.map((job) => <article className="job-row" key={job.id}>
    <div><h3>{operations[job.operation]}</h3><p>{dateLabel(job.created_at)} · {job.document_ids.length} 份资料</p>
      <div className="job-stage">{['failed', 'interrupted'].includes(job.status) ? stoppedStage(job.stage) : stages[job.stage]}</div>
      {job.error_code && <p className="failure-reason">{failureReason(job.error_code)}</p>}</div>
    <div className="job-row-actions"><StateTag value={job.status} /><Space wrap><Button disabled={d.busy} onClick={() => void panel.inspectTask(job)}>查看任务详情</Button>
      {job.can_retry && <Button disabled={disabled} onClick={() => void panel.retry(job)}>重试任务</Button>}
      {job.can_cleanup && <Button disabled={disabled} onClick={() => void panel.cleanup(job)}>重试清理</Button>}</Space></div></article>)}</div>;
  const job = d.inspectedJob?.job;
  return <div><div className="section-heading"><h2>{d.base.name} · 活动任务</h2><Button icon={<ReloadOutlined />} disabled={d.busy || d.loading} onClick={() => void panel.refresh()}>刷新资料与任务</Button></div>
    {d.error && <Alert type="error" title={errorText(d.error)} />}{d.readError && <Alert type="error" title={errorText(d.readError)} />}
    {d.notice && <Alert type="info" title={d.notice} />}{d.loading && <Skeleton active />}
    {rows(d.jobPage?.active_items ?? [])}{!d.loading && d.jobPage?.active_items.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无活动任务" />}
    <Alert className="task-history-note" type="info" showIcon title="历史任务保留当次结果，后续成功不会覆盖旧失败记录。" />
    <Collapse activeKey={d.historyOpen ? ['history'] : []} onChange={(keys) => panel.setHistoryOpen(keys.includes('history'))}
      items={[{ key: 'history', label: `已结束任务 · ${d.jobPage?.total ?? '—'} 项 · 失败 ${d.jobPage?.failed_count ?? '—'} 项`, children: <>
        {rows(d.jobPage?.items ?? [])}<Pagination current={Math.floor(d.jobOffset / 10) + 1} total={d.jobPage?.total ?? 0} pageSize={10} showSizeChanger={false}
          disabled={d.busy || d.loading} onChange={(page) => panel.movePage('history', (page - 1) * 10)} /></> }]} />
    <Drawer title="任务详情" open={!!d.inspectedJob} onClose={panel.closeInspector} size={460} destroyOnHidden>
      {d.inspectedJob?.error && <Alert type="error" title={errorText(d.inspectedJob.error)} />}
      {!job && !d.inspectedJob?.error && <Skeleton active />}{job && <><Descriptions column={1} items={[
        { key: 'operation', label: '任务类型', children: operations[job.operation] }, { key: 'status', label: '任务状态', children: <StateTag value={job.status} /> },
        { key: 'stage', label: '实际阶段', children: ['failed', 'interrupted'].includes(job.status) ? stoppedStage(job.stage) : stages[job.stage] },
        { key: 'docs', label: '关联资料', children: `${job.document_ids.length} 份` }, { key: 'engine', label: '引擎写入', children: job.engine_mutated ? '已发生' : '未发生' },
        { key: 'retry', label: '可重试性', children: job.can_retry ? '可按原任务重试' : '当前不能按原任务重试' },
        { key: 'cleanup', label: '旧空间清理', children: job.cleanup_pending ? '存在待清理项' : '无待清理项' },
      ]} />{job.error_code && <Alert showIcon type="error" title={failureReason(job.error_code)} />}
        <Space wrap><Button disabled={d.busy || d.loading} onClick={() => void panel.inspectTask(job)}>刷新任务详情</Button>
          {job.can_retry && <Button disabled={disabled} onClick={() => void panel.retry(job)}>重试任务</Button>}
          {job.can_cleanup && <Button disabled={disabled} onClick={() => void panel.cleanup(job)}>重试清理</Button>}</Space></>}
    </Drawer></div>;
}

export function Status({ view }: { view: AppView }) {
  const s = view.state; const h = s.health;
  return <div className="status-layout"><Alert type="info" showIcon title="配置存在不代表连通；未执行的检查不会标为通过。" />
    <div className="section-heading"><h2>服务能力</h2><Button icon={<ReloadOutlined />} loading={s.healthLoading} onClick={() => { void view.actions.loadHealth(); void view.voice.actions.refresh(); }}>刷新系统状态</Button></div>
    {s.healthLoading && <Skeleton active />}{s.healthError && <Alert showIcon type="error" title={s.healthError.message} />}
    {h && <Descriptions bordered column={1} items={[
      { key: 'mode', label: '使用方式', children: '本地单用户' }, { key: 'database', label: '业务数据库', children: <StateTag value={h.database} /> },
      { key: 'models', label: '模型服务', children: <StateTag value={h.models} /> }, { key: 'rag', label: 'LightRAG', children: <StateTag value={h.rag} /> },
      { key: 'backup', label: '备份', children: <><StateTag value={h.backup ?? 'unverified'} />{h.last_backup_at && <p>最近完成：{dateLabel(h.last_backup_at)}</p>}
        {h.backup_error_code && <Alert type="error" title={backupErrors[h.backup_error_code] ?? '备份状态待核查。'} />}</> }, { key: 'retention', label: '保留清理', children: <StateTag value={h.retention ?? 'unverified'} /> },
    ]} />}
    <div className="status-panels"><Card title="模型与引擎信息"><Descriptions column={1} items={[
      { key: 'names', label: '模型名称', children: h?.models_info?.model_names.join(' / ') || '未提供' },
      { key: 'region', label: '地区', children: h?.models_info?.region || '未提供' },
      { key: 'modelsverified', label: '模型验证时间', children: h?.models_info?.last_verified_at ? dateLabel(h.models_info.last_verified_at) : '尚无记录' },
      { key: 'ragverified', label: '引擎验证时间', children: h?.rag_info?.last_verified_at ? dateLabel(h.rag_info.last_verified_at) : '尚无记录' },
      { key: 'commit', label: 'LightRAG 版本', children: h?.rag_info?.lightrag_commit.slice(0, 8) || '未提供' },
      { key: 'pg', label: 'PostgreSQL / pgvector', children: `${h?.rag_info?.postgresql_major ?? '未提供'} / ${h?.rag_info?.vector_version ?? '未提供'}` },
    ]} /></Card><Card title="语音与媒体"><StateTag value={view.voice.actions.capability?.transport ?? 'unverified'} />
      {view.voice.actions.capabilityError && <Alert type="error" title="媒体状态读取失败，请刷新后重试。" />}
      <p>仅本地媒体测试；语音助手、ASR、TTS 与字幕尚未接入。</p><Button onClick={() => view.actions.navigate('voice')}>查看通话条件</Button></Card></div>
    {s.healthCheckedAt && <p className="muted">状态读取时间：{new Date(s.healthCheckedAt).toLocaleString('zh-CN')}。本次刷新不调用供应商或探测实际模型。</p>}</div>;
}
