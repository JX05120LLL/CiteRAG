import { useState } from 'react';
import { Alert, Button, Collapse, Descriptions, Drawer, Empty, Form, Input, Modal, Pagination, Tag, Upload } from 'antd';
import { InboxOutlined, FileTextOutlined, ReloadOutlined, InfoCircleOutlined } from '@ant-design/icons';
import type { IngestionJob, ManagedDocument } from '../api/client';
import { createApi } from '../api/client';
import type { PageContext } from './App';
import { usePreviewResource } from './useData';
import { dateLabel, DisabledAction, ResourceState, safeReason, sizeLabel, StateTag, stateLabel } from './shared';
const originalUrl = createApi().originalUrl;
const operationNames = { upload: '资料上传', delete: '资料删除', replace: '资料替换', rebuild: '原文重建' };
function DocumentActions({ item, sample, ready, inspect }: {
    item: ManagedDocument;
    sample: boolean;
    ready: boolean;
    inspect: () => void;
}) {
    return <div className="row-actions"><Button type="link" onClick={inspect}>查看详情</Button>
    {!sample && ready && item.status === 'ready' ? <Button type="link" href={originalUrl(item.id)} target="_blank" rel="noopener noreferrer">下载原文</Button>
            : <DisabledAction reason={sample ? '样例没有对应原文文件。' : '当前资料或知识库状态不允许原文核查。'}>下载原文</DisabledAction>}
    {item.status !== 'deleted' && <DisabledAction>删除资料</DisabledAction>}</div>;
}
function Documents({ items, sample, ready, inspect }: {
    items: ManagedDocument[];
    design: string;
    sample: boolean;
    ready: boolean;
    inspect: (item: ManagedDocument) => void;
}) {
    const rows = items.map((item) => <article className="document-list-row" key={item.id}><div className="file-icon"><FileTextOutlined /></div>
    <div className="document-info"><strong>{item.filename}</strong><p>{sizeLabel(item.size)} · {dateLabel(item.created_at)}</p>
      <StateTag value={item.status}/>{item.status !== 'deleted' && item.error_code && <p className="failure-reason">{safeReason(item.error_code)}</p>}</div>
    <DocumentActions item={item} sample={sample} ready={ready} inspect={() => inspect(item)}/></article>);
    return <>{null}
    <div className={'document-list'}>{rows}</div></>;
}
export function Knowledge({ location, kb, refresh, navigate }: PageContext) {
    const [currentPage, setCurrentPage] = useState(1);
    const [deletedPage, setDeletedPage] = useState(1);
    const [deletedOpen, setDeletedOpen] = useState(false);
    const [selected, setSelected] = useState<ManagedDocument | null>(null);
    const [impactOpen, setImpactOpen] = useState(false);
    const [metadataOpen, setMetadataOpen] = useState(false);
    const documents = usePreviewResource(location.data, `documents:${kb?.id}:${currentPage}:${refresh}`, (api) => kb ? api.documentPage(kb.id, 'current', (currentPage - 1) * 10) : Promise.resolve(null));
    const deleted = usePreviewResource(location.data, `deleted:${kb?.id}:${deletedPage}:${deletedOpen}:${refresh}`, (api) => kb && deletedOpen ? api.documentPage(kb.id, 'deleted', (deletedPage - 1) * 10) : Promise.resolve(null));
    const sample = location.data === 'sample';
    const details = selected && <div className="document-detail"><div className="source-file"><FileTextOutlined /><h3>{selected.filename}</h3></div>
    <Descriptions column={1} size="small" items={[
            { key: 'state', label: '处理状态', children: <StateTag value={selected.status}/> },
            { key: 'size', label: '大小', children: sizeLabel(selected.size) },
            { key: 'time', label: '受理时间', children: dateLabel(selected.created_at) },
            { key: 'code', label: '确认编号', children: selected.doc_code ?? '未确认' },
            { key: 'model', label: '型号', children: selected.model_code ?? '未确认' },
            { key: 'edition', label: '版本', children: selected.edition ?? '未确认' },
        ]}/>
    {selected.status !== 'deleted' && selected.error_code && <Alert showIcon type="error" title="处理失败原因" description={safeReason(selected.error_code)}/>}
    {selected.status === 'deleted' && <Alert type="info" title="已删除记录只保留管理历史，不代表原文仍可下载。"/>}
    <div className="detail-actions"><DisabledAction>替换资料</DisabledAction><DisabledAction>修改确认属性</DisabledAction>
      <Button onClick={() => navigate('tasks')}>查看处理任务</Button></div>
    <p className="muted">候选预览不读取解析正文，不执行上传、替换或删除。</p></div>;
    return <div className="management-layout">
    <section className="management-main"><div className="library-summary"><div><h2>{kb?.name ?? '知识库未读取'}</h2><StateTag value={kb?.status ?? 'unverified'}/>
      <span className="muted">此页查看当前资料；删除记录单独保留。</span></div><div className="toolbar-actions">
        <Button icon={<InfoCircleOutlined />} onClick={() => setMetadataOpen(true)}>知识库信息</Button>
        <Button onClick={() => setImpactOpen(true)}>查看重建影响</Button></div></div>
      {(kb?.status === 'blocked' || kb?.status === 'maintaining') && <Alert type="warning" showIcon title="知识库维护或待修复，问答与原文核查受状态门禁限制。"/>}
      <div className="upload-preview"><Upload.Dragger disabled multiple showUploadList={false}><p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <strong>添加资料</strong><p>TXT、Markdown、文字 PDF、普通 DOCX</p><p>预览上传已禁用 · 每批最多 5 份，每份 20 MiB</p></Upload.Dragger></div>
      <section className="records-section"><div className="section-heading"><h2>当前资料</h2><span className="muted">{documents.data ? `${documents.data.total} 份` : '数量未读取'}</span></div>
        <ResourceState {...documents} empty={documents.data?.total === 0}/>
        {documents.data && <><Documents items={documents.data.items} design={location.design} sample={sample} ready={kb?.status === 'ready'} inspect={setSelected}/>
          <Pagination current={currentPage} total={documents.data.total} pageSize={10} showSizeChanger={false} hideOnSinglePage onChange={(value) => { setCurrentPage(value); setSelected(null); }} showTotal={(total) => `共 ${total} 份`}/></>}
      </section>
      <Collapse className="history-collapse" activeKey={deletedOpen ? ['deleted'] : []} onChange={(keys) => setDeletedOpen(keys.includes('deleted'))} items={[{ key: 'deleted', label: '已删除记录 · 展开查看与分页', children: <><ResourceState {...deleted} empty={deleted.data?.total === 0}/>
          {deleted.data && <><Documents items={deleted.data.items} design={location.design} sample={sample} ready={false} inspect={setSelected}/>
            <Pagination current={deletedPage} total={deleted.data.total} pageSize={10} showSizeChanger={false} hideOnSinglePage onChange={setDeletedPage}/></>}
          <p className="muted">记录供核查历史；本页不提供恢复已清理原文的操作。</p></> }]}/>
    </section>
    {null}
    <Drawer title="资料详情" aria-label="资料详情" open={Boolean(selected) && true} onClose={() => setSelected(null)} destroyOnHidden size={440}>{details}</Drawer>
    <Modal title="重建影响说明" open={impactOpen} onCancel={() => setImpactOpen(false)} footer={<><Button onClick={() => setImpactOpen(false)}>关闭说明</Button><Button disabled type="primary">确认重建</Button></>}>
      <Alert type="warning" showIcon title="重建将暂停问答，并屏蔽旧知识回答和证据。"/>
      <p>从当前受管原文重新解析、建立索引、核验，再切换活动空间。通过核验后清理旧空间，失败时保持待修复。</p>
      <p>不会用新成功结果覆盖旧失败历史。实际操作需沿用归属、修订、维护、幂等键和进行中门禁。</p><p className="muted">此处只展示影响，预览不提交重建请求。</p>
    </Modal>
    <Modal title="知识库信息" open={metadataOpen} onCancel={() => setMetadataOpen(false)} footer={<Button onClick={() => setMetadataOpen(false)}>关闭</Button>}>
      <Form layout="vertical"><Form.Item label="当前知识库名称"><Input value={kb?.name ?? ''} disabled/></Form.Item>
        <Form.Item label="当前状态"><StateTag value={kb?.status ?? 'unverified'}/></Form.Item></Form>
      <DisabledAction>保存名称</DisabledAction><p className="muted">名称和状态来自当前数据源；预览不改名或创建知识库。</p></Modal>
  </div>;
}
function JobDetails({ job }: {
    job: IngestionJob;
}) {
    return <div className="job-detail"><Descriptions column={1} size="small" items={[
            { key: 'operation', label: '任务类型', children: operationNames[job.operation] },
            { key: 'status', label: '任务状态', children: <StateTag value={job.status}/> },
            { key: 'stage', label: '当前阶段', children: stateLabel(job.stage) },
            { key: 'time', label: '受理时间', children: dateLabel(job.created_at) },
            { key: 'docs', label: '关联资料', children: `${job.document_ids.length} 份` },
            { key: 'mutation', label: '引擎写入', children: job.engine_mutated ? '已发生修改' : '未发生' },
            { key: 'retry', label: '可重试性', children: job.can_retry ? '可按原任务重试' : '当前不能按原任务重试' },
            { key: 'cleanup', label: '旧空间清理', children: job.cleanup_pending ? '仍有待清理项' : '无待清理项' },
        ]}/>
    {job.error_code && <Alert showIcon type="error" title="已记录的失败原因" description={safeReason(job.error_code)}/>}
    <div className="detail-actions">{job.can_retry && <DisabledAction>重试任务</DisabledAction>}
      {job.can_cleanup && <DisabledAction>重试清理</DisabledAction>}</div>
    <p className="muted">历史任务记录本次读取的事实，不代表知识库当前仍处于该阶段。</p></div>;
}
export function Tasks({ location, kb, refresh }: PageContext) {
    const [page, setPage] = useState(1);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [jobId, setJobId] = useState('');
    const [detailRefresh, setDetailRefresh] = useState(0);
    const jobs = usePreviewResource(location.data, `jobs:${kb?.id}:${page}:${refresh}`, (api) => kb ? api.jobPage(kb.id, (page - 1) * 10) : Promise.resolve(null));
    const detail = usePreviewResource(location.data, `job:${jobId}:${detailRefresh}:${refresh}`, (api) => jobId ? api.job(jobId) : Promise.resolve(null));
    function rows(items: IngestionJob[]) {
        return <div className="jobs-list">{items.map((job) => <article className={`job-row ${job.id === jobId ? 'selected' : ''}`} key={job.id}>
      <div><h3>{operationNames[job.operation]}任务</h3><p>{dateLabel(job.created_at)} · {job.document_ids.length} 份资料</p>
        <div className="job-stage">当前阶段：{stateLabel(job.stage)}</div>{job.error_code && <p className="failure-reason">{safeReason(job.error_code)}</p>}</div>
      <div className="job-row-actions"><StateTag value={job.status}/><Button onClick={() => setJobId(job.id)}>查看任务详情</Button></div></article>)}</div>;
    }
    const details = <><div className="section-heading"><h2>任务详情</h2>{jobId && <Button icon={<ReloadOutlined />} onClick={() => setDetailRefresh((value) => value + 1)} aria-label="刷新任务详情"/>}</div>
    <ResourceState {...detail}/>{detail.data ? <JobDetails job={detail.data}/> : !jobId && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择任务查看阶段、错误与恢复条件"/>}</>;
    return <div className="tasks-layout"><section className="tasks-main"><div className="section-heading"><h2>进行中的任务</h2><Tag>{jobs.data?.active_items.length ?? '—'} 项</Tag></div>
    <ResourceState {...jobs} empty={jobs.data?.active_items.length === 0}/>{jobs.data && rows(jobs.data.active_items)}
    <Alert className="task-history-note" type="info" showIcon title="活动任务常驻显示；已结束记录折叠保留。" description="任务失败原因和恢复条件以每个任务为准。后续成功不会改写早先的失败。"/>
    <Collapse activeKey={historyOpen ? ['history'] : []} onChange={(keys) => setHistoryOpen(keys.includes('history'))} items={[
            { key: 'history', label: `已结束任务 · ${jobs.data?.total ?? '未读取'} 项 · 展开查看`, children: <>
        {rows(jobs.data?.items ?? [])}
        {null}
        <Pagination current={page} total={jobs.data?.total ?? 0} pageSize={10} showSizeChanger={false} hideOnSinglePage onChange={setPage}/>
      </> }
        ]}/></section>
    {null}
    <Drawer title="任务详情" aria-label="任务详情" open={Boolean(jobId) && true} onClose={() => setJobId('')} destroyOnHidden size={430}>{details}</Drawer>
  </div>;
}
export function Status({ location, refresh }: PageContext) {
    const [localRefresh, setLocalRefresh] = useState(0);
    const health = usePreviewResource(location.data, `health:${refresh}:${localRefresh}`, (api) => api.health());
    const voice = usePreviewResource(location.data, `voice-status:${refresh}:${localRefresh}`, (api) => api.voiceStatus());
    const data = health.data;
    return <div className="status-layout"><Alert type="info" showIcon title="配置存在，不等于服务已连通。" description="本页只读取已有状态，不发起供应商验证、数据库迁移或媒体连接。"/>
    <div className="section-heading"><h2>服务能力</h2><Button icon={<ReloadOutlined />} onClick={() => setLocalRefresh((value) => value + 1)}>刷新系统状态</Button></div>
    <ResourceState {...health}/>{data && <Descriptions bordered column={1} items={[
                { key: 'mode', label: '使用方式', children: '本地单用户' },
                { key: 'database', label: '业务数据库', children: <><StateTag value={data.database}/>当前状态来自后端</> },
                { key: 'models', label: '模型服务', children: <><StateTag value={data.models}/>{data.models === 'unverified' ? '尚无连通性验证结果' : '不新增模型调用'}</> },
                { key: 'rag', label: '知识检索引擎', children: <><StateTag value={data.rag}/>保留 LightRAG 与活动空间门禁</> },
                { key: 'backup', label: '备份', children: data.backup ? <StateTag value={data.backup}/> : '状态未提供' },
                { key: 'retention', label: '保留清理', children: data.retention ? <StateTag value={data.retention}/> : '状态未提供' },
            ]}/>}
    <div className="status-panels"><section><h2>模型与引擎信息</h2>{data ? <Descriptions column={1} items={[
                { key: 'modelnames', label: '模型名称', children: data.models_info?.model_names.join(' / ') || '未提供' },
                { key: 'region', label: '地区', children: data.models_info?.region || '未提供' },
                { key: 'modelsverified', label: '模型验证时间', children: data.models_info?.last_verified_at ? dateLabel(data.models_info.last_verified_at) : '尚无记录' },
                { key: 'engineverified', label: '引擎验证时间', children: data.rag_info?.last_verified_at ? dateLabel(data.rag_info.last_verified_at) : '尚无记录' },
            ]}/> : <p className="muted">等待状态读取。</p>}</section><section><h2>语音与媒体</h2><ResourceState {...voice}/>
      {voice.data && <><StateTag value={voice.data.transport}/><p>当前用途：本地媒体测试。</p><StateTag value={voice.data.assistant}/><p>语音助手、ASR、TTS、字幕和知识库语音回答尚未接入。</p></>}
      <p className="muted">预览未申请麦克风、发 Token 或连接房间。</p></section></div>
  </div>;
}
