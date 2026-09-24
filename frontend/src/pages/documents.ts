import { ApiError } from '../api/client';
import type { ApiClient, IngestionJob, KnowledgeBase, ManagedDocument, ParsedBlock } from '../api/client';
import { clearPendingIngestion, fileMetadata, matchesPendingFiles, restorePendingIngestion, savePendingIngestion, validateFiles } from '../document-draft';
import type { PendingIngestion } from '../document-draft';
import { action, alert, el, heading } from '../shared/dom';
import { kbStatuses } from '../state';

const documentLabels: Record<ManagedDocument['status'], string> = {
  pending: '已受理，等待处理', parsing: '正在解析', parsed: '解析完成，尚未入库',
  indexing: '正在建立索引', ready: '入库核验通过', failed: '处理失败',
};
const stageLabels: Record<IngestionJob['stage'], string> = {
  accepted: '已受理，等待处理', parsing: '正在解析', parsed: '解析完成，尚未入库',
  indexing: '正在建立索引', verifying: '正在核验入库结果', complete: '入库核验通过',
};
const failureLabels: Record<string, string> = {
  duplicate_document: '此知识库已有相同内容，请检查资料列表。',
  idempotency_conflict: '请求内容与原请求不同，请重新选择原文件。',
  kb_busy: '此库已有进行中的任务，请刷新并等待当前任务结束。',
  kb_blocked: '知识库待修复，请重试失败任务或从原文重建。',
  capacity_exceeded: '已达到资料容量上限。',
  storage_unavailable: '原文存储暂不可用，请检查本地存储后重试。',
  invalid_file: '文件格式或内容不符合要求，请转换或修复原文件。',
  invalid_filename: '请使用有效的 TXT、MD、PDF 或 DOCX 文件名，避免路径字符或保留名称。',
  parse_failed: '无法可靠解析原文件，请检查编码、正文或表格结构。',
  parse_timeout: '文件解析超时，请拆分或简化原文件。',
  engine_unavailable: '入库处理尚未启用或暂不可用，资料未入库。',
  index_failed: '索引写入失败，请重试任务或从原文重建。',
  verification_failed: '入库结果未通过核验，请重试任务或重建。',
  interrupted: '处理已中断，请检查任务并手动重试。',
  source_missing: '受管原文缺失，请先恢复原文备份。',
  source_changed: '受管原文校验不一致，请先恢复完整备份。',
  rebuild_required: '当前状态需要从受管原文重建知识库。',
  nothing_to_rebuild: '尚无曾进入索引的受管原文，请先完成资料入库。',
  job_not_retryable: '此任务当前无需重试，请刷新核对状态。',
  stale_job: '此历史任务已被后续重建替代，请查看当前知识库与最新任务。',
  ingestion_unavailable: '资料任务服务不可用，请检查系统状态后重试。',
  attribute_value: '属性值须为 1–80 个可打印字符；清空字段表示不再确认该属性。',
};

function errorText(error: ApiError): string {
  if (error.kind === 'local-storage') return '无法可靠保存或读取本次请求的恢复记录。请检查浏览器会话存储后重试；当前不会发起新请求。';
  return failureLabels[error.code] ?? (error.kind === 'validation' && !['file_count', 'file_size', 'file_type', 'pending_files_changed'].includes(error.code)
    ? '文件或请求未通过检查，请核对文件格式、大小和数量。' : error.message);
}

function locatorLabel(block: ParsedBlock): string {
  const location = block.locator;
  const positive = (value: unknown): value is number => Number.isInteger(value) && Number(value) > 0;
  if (location.kind === 'lines' && positive(location.line_start) && positive(location.line_end)) return `第 ${location.line_start}–${location.line_end} 行`;
  if (location.kind === 'page' && positive(location.page)) return `第 ${location.page} 页`;
  if (location.kind === 'paragraph' && positive(location.paragraph)) return `第 ${location.paragraph} 段`;
  if (location.kind === 'table' && positive(location.table) && positive(location.row)) return `表 ${location.table}，第 ${location.row} 行`;
  return '来源片段';
}

export function createDocumentsPanel(api: ApiClient, changed: () => void, acceptBases: (bases: KnowledgeBase[]) => void) {
  let base: KnowledgeBase | null = null;
  let documents: ManagedDocument[] | null = null;
  let jobs: IngestionJob[] | null = null;
  let files: File[] = [];
  let pending: PendingIngestion | null = null;
  let uncertain = false;
  let busy = false;
  let loading = false;
  let error: ApiError | null = null;
  let readError: ApiError | null = null;
  let notice: string | null = null;
  let recoveryBlocked = false;
  let rebuildConfirmed = false;
  let inspected: { document: ManagedDocument; blocks: ParsedBlock[] | null; error: ApiError | null } | null = null;
  let generation = 0;

  async function refresh() {
    if (!base || busy) return;
    const current = ++generation;
    const kbId = base.id;
    loading = true; readError = null; inspected = null; changed();
    const results = await Promise.allSettled([api.documents(kbId), api.jobs(kbId), api.knowledgeBases()]);
    if (current !== generation) return;
    documents = results[0].status === 'fulfilled' ? results[0].value : null;
    jobs = results[1].status === 'fulfilled' ? results[1].value : null;
    if (results[2].status === 'fulfilled') {
      acceptBases(results[2].value);
      const updated = results[2].value.find((item) => item.id === kbId);
      if (updated) {
        base = updated;
        if (['maintaining', 'blocked'].includes(updated.status)) inspected = null;
      }
      else readError = new ApiError('not-found');
    }
    const failure = results.find((result) => result.status === 'rejected');
    if (failure?.status === 'rejected') readError = failure.reason instanceof ApiError ? failure.reason : new ApiError('http');
    loading = false; changed();
  }

  function open(selected: KnowledgeBase) {
    ++generation;
    base = selected; documents = jobs = null; files = []; pending = null; uncertain = busy = loading = recoveryBlocked = rebuildConfirmed = false;
    error = readError = null; notice = null; inspected = null;
    try { pending = restorePendingIngestion(selected.id); uncertain = pending !== null; }
    catch (reason) { error = reason instanceof ApiError ? reason : new ApiError('local-storage'); recoveryBlocked = true; }
    changed(); void refresh();
  }

  function mutationUnavailable(): boolean {
    return busy || loading || recoveryBlocked || !!readError || !documents || !jobs;
  }

  function activeJob(): boolean { return jobs?.some((job) => ['queued', 'running'].includes(job.status)) ?? false; }

  async function submit(operation: 'upload' | 'rebuild') {
    if (!base || mutationUnavailable() || (pending && pending.operation !== operation) ||
        (!pending && (activeJob() || (operation === 'upload' && ['maintaining', 'blocked'].includes(base.status))))) return;
    const kbId = base.id;
    const wasUncertain = uncertain;
    let sent = false;
    try {
      if (operation === 'upload') {
        validateFiles(files);
        if (pending && !matchesPendingFiles(pending, files)) throw new ApiError('validation', 'pending_files_changed');
      } else if (!pending && !rebuildConfirmed) return;
      pending ??= { key: crypto.randomUUID(), operation, files: operation === 'upload' ? fileMetadata(files) : [] };
      savePendingIngestion(kbId, pending);
      busy = true; error = null; notice = null; inspected = null; changed();
      sent = true;
      const job = operation === 'upload' ? await api.uploadDocuments(kbId, files, pending.key) : await api.rebuild(kbId, pending.key);
      if (job.kb_id !== kbId || job.operation !== operation) throw new ApiError('invalid-response');
      clearPendingIngestion(kbId, pending);
      pending = null; uncertain = false; files = []; rebuildConfirmed = false;
      notice = '任务已受理。解析与入库结果请以下方持久任务为准。';
    } catch (reason) {
      error = reason instanceof ApiError ? reason : new ApiError('http');
      uncertain = wasUncertain || (sent && ['network', 'invalid-response', 'http', 'unavailable', 'local-storage'].includes(error.kind));
      if (!uncertain && pending) {
        try { clearPendingIngestion(kbId, pending); pending = null; }
        catch { uncertain = true; error = new ApiError('local-storage'); }
      }
    } finally {
      busy = false; changed();
      if (!error) await refresh();
    }
  }

  async function retry(job: IngestionJob) {
    if (mutationUnavailable() || pending || activeJob() || !job.can_retry) return;
    busy = true; error = null; notice = null; inspected = null; changed();
    try {
      const accepted = await api.retryJob(job.id);
      if (accepted.id !== job.id || accepted.kb_id !== base?.id) throw new ApiError('invalid-response');
      notice = '原任务已受理重试。请刷新查看处理结果。';
    } catch (reason) { error = reason instanceof ApiError ? reason : new ApiError('http'); }
    finally { busy = false; changed(); await refresh(); }
  }

  async function inspect(document: ManagedDocument) {
    const current = generation;
    inspected = { document, blocks: null, error: null }; changed();
    try {
      const blocks = await api.blocks(document.id);
      if (generation === current && inspected?.document.id === document.id) inspected.blocks = blocks;
    } catch (reason) {
      if (generation === current && inspected?.document.id === document.id) inspected.error = reason instanceof ApiError ? reason : new ApiError('http');
    }
    changed();
  }

  async function saveAttributes(document: ManagedDocument,
                                values: {doc_code: string | null; model_code: string | null; edition: string | null}) {
    if (mutationUnavailable() || base?.status !== 'ready' || document.status !== 'ready') return;
    busy = true; error = null; notice = null; changed();
    try {
      const updated = await api.updateDocumentAttributes(document.id, values);
      if (updated.id !== document.id) throw new ApiError('invalid-response');
      notice = '已确认资料属性。精确查询将按这些原值等值筛选。';
    } catch (reason) { error = reason instanceof ApiError ? reason : new ApiError('http'); }
    finally { busy = false; await refresh(); changed(); }
  }

  function render() {
    const content = el('section', 'management-content document-panel');
    if (!base) return content;
    const back = action('返回知识库列表', 'text-button', () => { if (!busy) { base = null; ++generation; changed(); } });
    back.disabled = busy;
    content.append(back, heading(base.name), el('p', 'metadata', kbStatuses[base.status]));
    content.append(el('p', 'intro document-intro', '原文保存在本机私有目录。解析完成不等于入库成功；入库与核验期间，该库问答暂停。'));
    const form = el('form', 'document-upload');
    form.setAttribute('aria-label', '上传资料'); form.setAttribute('aria-busy', String(busy));
    const label = el('label', 'document-label', '添加资料'); label.htmlFor = 'document-files';
    const input = el('input', 'document-files'); input.type = 'file'; input.id = 'document-files'; input.multiple = true;
    input.accept = '.txt,.md,.pdf,.docx'; input.disabled = busy || recoveryBlocked || pending?.operation === 'rebuild';
    input.setAttribute('aria-describedby', 'document-file-hint');
    input.addEventListener('change', () => {
      files = [...(input.files ?? [])]; error = null;
      try { if (files.length) validateFiles(files); }
      catch (reason) { error = reason instanceof ApiError ? reason : new ApiError('validation'); }
      changed();
    });
    const hint = el('p', 'field-hint', 'UTF-8 TXT、Markdown、文字 PDF、普通 DOCX。每批最多 5 份，每份 20 MiB；PDF 最多 100 页。'); hint.id = 'document-file-hint';
    form.append(label, input, hint);
    if (files.length) form.append(el('p', 'selected-files', `已选择：${files.map((file) => file.name).join('、')}`));
    if (uncertain) form.append(alert(pending?.operation === 'upload'
      ? '受理结果尚未确认。重新选择原文件，使用同一请求重试；不会自动再次上传。'
      : '重建受理结果尚未确认。请使用同一请求重试，避免重复创建重建任务。', 'pending'));
    if (uncertain && pending?.operation === 'upload') {
      form.append(el('p', 'field-hint', `原请求文件：${pending.files.map((file) => file.name).join('、')}。请按此顺序重新选择。`));
      if (files.length && !matchesPendingFiles(pending, files)) form.append(alert('所选文件与原请求不一致，请重新选择原文件。'));
    }
    const submitButton = action(busy ? '正在受理…' : uncertain && pending?.operation === 'upload' ? '同键重试上传' : '上传并处理', 'button primary');
    submitButton.type = 'submit'; submitButton.disabled = mutationUnavailable() || !files.length || error?.kind === 'validation' || pending?.operation === 'rebuild' || (uncertain && !!pending && !matchesPendingFiles(pending, files)) ||
      (!pending && (activeJob() || ['maintaining', 'blocked'].includes(base.status)));
    form.append(submitButton);
    if (!pending && activeJob()) form.append(el('p', 'field-hint', '此库已有待处理任务，请先核对任务进度，再添加新资料。'));
    else if (!pending && base.status === 'blocked') form.append(el('p', 'field-hint', '此库待修复，请先重试失败任务或从原文重建。'));
    form.addEventListener('submit', (event) => { event.preventDefault(); void submit('upload'); });
    content.append(form);
    if (error) content.append(alert(errorText(error)));
    if (notice) content.append(alert(notice, 'success'));
    const toolbar = el('div', 'document-toolbar');
    toolbar.append(el('h2', '', '资料与任务'));
    const reload = action(loading ? '正在刷新…' : '刷新资料与任务', 'button secondary', () => { void refresh(); });
    reload.disabled = busy || loading; toolbar.append(reload); content.append(toolbar);
    if (readError) content.append(alert(errorText(readError)));
    if (loading) content.append(el('p', 'metadata', '正在读取已保存的资料和任务…'));
    if (documents) {
      const list = el('ul', 'managed-documents');
      for (const document of documents) {
        const row = el('li', 'managed-document');
        const description = el('div', 'document-description');
        description.append(el('strong', 'document-filename', document.filename), el('p', 'metadata', `${documentLabels[document.status]} · ${(document.size / 1024).toFixed(1)} KiB`));
        if (document.error_code) description.append(el('p', 'field-hint', failureLabels[document.error_code] ?? '资料处理未完成，请核对任务后重试。'));
        if (document.status === 'ready' && base.status === 'ready') {
          const attributes = el('form', 'document-attributes');
          const fields = {} as Record<'doc_code' | 'model_code' | 'edition', HTMLInputElement>;
          for (const [key, caption] of [
            ['doc_code', '文档编号'], ['model_code', '型号'], ['edition', '资料版本'],
          ] as const) {
            const label = el('label', '', caption);
            const field = el('input', 'name-input');
            field.value = document[key] ?? '';
            field.maxLength = 80;
            field.setAttribute('aria-label', `${document.filename} ${caption}`);
            fields[key] = field;
            label.append(field);
            attributes.append(label);
          }
          const save = action('确认属性', 'button secondary');
          save.type = 'submit'; save.disabled = busy;
          attributes.append(save);
          attributes.addEventListener('submit', (event) => {
            event.preventDefault();
            const values = Object.fromEntries((['doc_code', 'model_code', 'edition'] as const)
              .map((key) => [key, fields[key].value.trim() || null])) as {
                doc_code: string | null; model_code: string | null; edition: string | null;
              };
            if (Object.values(values).some((value) => value && /[\x00-\x1f\x7f]/.test(value))) {
              error = new ApiError('validation', 'attribute_value'); changed(); return;
            }
            void saveAttributes(document, values);
          });
          description.append(attributes);
        }
        const controls = el('div', 'row-actions');
        const original = el('a', 'text-button', '下载原文'); original.href = `/api/documents/${encodeURIComponent(document.id)}/original`; original.download = ''; original.rel = 'noreferrer';
        const inspectButton = action('查看解析位置', 'text-button', () => { void inspect(document); });
        inspectButton.disabled = base.status === 'maintaining' || base.status === 'blocked' || !['parsed', 'ready'].includes(document.status);
        if (base.status === 'maintaining' || base.status === 'blocked') controls.append(el('span', 'metadata', '原文核查暂停'));
        else controls.append(original);
        controls.append(inspectButton); row.append(description, controls); list.append(row);
      }
      content.append(list);
      if (!documents.length) content.append(el('p', 'empty-documents', '尚无受管资料。请选择文件开始上传。'));
    }
    if (inspected) {
      const details = el('section', 'parsed-preview');
      details.append(el('h2', '', `解析位置：${inspected.document.filename}`), el('p', 'metadata', '下列位置来自文件解析，未显示的位置不会补写或推算。'));
      if (inspected.error) details.append(alert(errorText(inspected.error)));
      else if (!inspected.blocks) details.append(el('p', 'metadata', '正在读取解析片段…'));
      else for (const block of inspected.blocks) {
        const item = el('article', 'parsed-block');
        item.append(el('h3', '', locatorLabel(block)), el('pre', '', block.text)); details.append(item);
      }
      if (inspected.blocks?.length === 0) details.append(el('p', 'metadata', '尚无可核查解析片段。'));
      details.append(action('收起解析位置', 'text-button', () => { inspected = null; changed(); })); content.append(details);
    }
    if (jobs) {
      const list = el('ol', 'ingestion-jobs');
      for (const job of jobs) {
        const item = el('li', 'ingestion-job');
        const label = job.status === 'failed' ? '处理失败' : job.status === 'interrupted' ? '处理已中断' : stageLabels[job.stage];
        const title = el('div', 'job-heading'); title.append(el('strong', '', job.operation === 'upload' ? '资料上传任务' : '原文重建任务'), el('span', 'job-state', label));
        item.append(title, el('p', 'metadata', `${new Date(job.created_at).toLocaleString('zh-CN', { hour12: false })} · ${job.document_ids.length} 份资料`));
        if (job.stage === 'parsed' && job.status === 'queued') item.append(el('p', 'field-hint', '解析结果已保存，等待启用入库处理，尚未进入索引。'));
        if (job.error_code) item.append(el('p', 'field-hint', failureLabels[job.error_code] ?? '任务未完成，请核对状态并重试。'));
        if (job.engine_mutated && ['failed', 'interrupted'].includes(job.status)) item.append(el('p', 'job-warning', base.status === 'blocked'
          ? '引擎已发生修改，此库保持待修复，问答暂停。重试或重建通过核验后才恢复。'
          : '此任务曾修改引擎后失败，当前库状态见上方。'));
        if (job.can_retry) {
          const retryButton = action('重试任务', 'button secondary', () => { void retry(job); });
          retryButton.disabled = mutationUnavailable() || !!pending || activeJob(); item.append(retryButton);
        }
        if (job.cleanup_pending) item.append(el('p', 'field-hint', '旧空间仍隔离保留，物理清理尚未完成。'));
        list.append(item);
      }
      content.append(list);
      if (!jobs.length) content.append(el('p', 'empty-documents', '尚无持久任务。任务受理后，刷新页面仍可在此查看。'));
    }
    const repair = el('section', 'document-repair'); repair.append(el('h2', '', '从原文重建'));
    repair.append(el('p', 'field-hint', '重建期间暂停问答并遮蔽此前知识回答与证据。系统在新空间完成核验后切换，旧空间隔离保留；原文不会被清空。'));
    const confirmation = el('label', 'rebuild-confirmation');
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = rebuildConfirmed;
    checkbox.disabled = busy || uncertain; checkbox.addEventListener('change', () => { rebuildConfirmed = checkbox.checked; changed(); });
    confirmation.append(checkbox, el('span', '', '我已了解重建对问答和历史证据的影响')); repair.append(confirmation);
    const rebuild = action(uncertain && pending?.operation === 'rebuild' ? '同键重试重建' : '重建知识库', 'button secondary', () => { void submit('rebuild'); });
    rebuild.disabled = mutationUnavailable() || !documents?.length || pending?.operation === 'upload' || (!pending && activeJob()) || (!rebuildConfirmed && pending?.operation !== 'rebuild');
    repair.append(rebuild); content.append(repair);
    content.append(el('p', 'scope-note', '就绪资料可确认编号、型号和版本用于精确查询；资料删除和替换留待后续。'));
    return content;
  }

  return { open, render, get isOpen() { return base !== null; }, close() { if (!busy) { base = null; ++generation; } } };
}
