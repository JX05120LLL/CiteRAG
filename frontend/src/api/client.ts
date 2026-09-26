export interface KnowledgeBase {
  id: string;
  name: string;
  status: 'empty' | 'ready' | 'maintaining' | 'blocked';
}

export interface Conversation {
  id: string;
  owner_id: string;
  kb_id: string;
  title: string;
  created_at: string;
}

export interface Citation {
  evidence_id: string;
  document_id: string;
  filename: string;
  locator: Record<string, string | number>;
  excerpt: string;
}

export interface ChatMessage {
  message_id: string;
  attempt_id: string;
  client_message_id: string;
  question: string;
  mode: 'semantic' | 'exact' | 'auto';
  route?: 'semantic' | 'exact' | 'literal' | 'needs_clarification' | 'unsupported';
  status: 'running' | 'answered' | 'insufficient_evidence' | 'needs_clarification' |
    'conflicting_evidence' | 'failed' | 'interrupted' | 'partial';
  text: string;
  citations: Citation[];
  kb_revision: number;
  error_code: string | null;
  created_at: string;
  saved: boolean;
  stale?: boolean;
  hidden?: boolean;
}

export type AnswerProgress = { type: 'accepted'; message: ChatMessage } |
  { type: 'delta'; text: string; saved: boolean };

export interface ManagedDocument {
  id: string;
  filename: string;
  size: number;
  status: 'pending' | 'parsing' | 'parsed' | 'indexing' | 'ready' | 'failed' |
    'deleting' | 'replacing' | 'deleted';
  error_code: string | null;
  created_at: string;
  doc_code: string | null;
  model_code: string | null;
  edition: string | null;
}

export interface ExactFilter {
  doc_code?: string;
  model_code?: string;
  edition?: string;
  phrase?: string;
}

export interface IngestionJob {
  id: string;
  kb_id: string;
  operation: 'upload' | 'rebuild' | 'delete' | 'replace';
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted';
  stage: 'accepted' | 'parsing' | 'parsed' | 'indexing' | 'verifying' | 'cleanup' | 'complete';
  error_code: string | null;
  document_ids: string[];
  created_at: string;
  engine_mutated: boolean;
  can_retry: boolean;
  cleanup_pending?: boolean;
  can_cleanup?: boolean;
}

export interface ParsedBlock {
  ordinal: number;
  text: string;
  locator: Record<string, string | number>;
  start: number;
  end: number;
}

export interface DocumentPage {
  items: ManagedDocument[];
  total: number;
  counts: Partial<Record<ManagedDocument['status'], number>>;
}
export interface JobPage {
  items: IngestionJob[];
  total: number;
  active_items: IngestionJob[];
  failed_count: number;
}

function nonnegativeCount(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

export interface SystemHealth {
  status: 'partial';
  mode: 'local_single_user';
  database: 'available' | 'not_configured' | 'unavailable';
  rag: CapabilityState;
  backup?: 'available' | 'running' | 'unavailable' | 'disabled';
  backup_error_code?: 'engine_configuration_unavailable' | 'backup_verification_failed' |
    'backup_interrupted' | 'backup_failed';
  retention?: 'available' | 'unavailable' | 'disabled';
  last_backup_at?: string;
  models: CapabilityState;
  models_info?: ModelsInfo;
  rag_info?: RagInfo;
}

export type CapabilityState = 'not_configured' | 'unverified' | 'available' | 'unavailable';

export interface ModelsInfo {
  region: string;
  model_names: string[];
  last_verified_at?: string;
}

export interface RagInfo {
  lightrag_commit: string;
  postgresql_major?: number;
  vector_version?: string;
  last_verified_at?: string;
}

export type ErrorKind = 'forbidden' | 'unavailable' | 'http' | 'network' | 'invalid-response' | 'validation' | 'conflict' | 'not-found' | 'local-storage';

export const nameValidationMessage = '名称须为 1–120 个字符，不能仅含空白或包含控制字符。';

const errorMessages: Record<ErrorKind, string> = {
  forbidden: '请求被本地访问规则拒绝，请使用本机工作台地址重试。',
  unavailable: '服务暂不可用，请检查服务状态后重试。',
  http: '请求未完成，请稍后重试。',
  network: '无法连接服务，请检查服务是否启动及网络连接。',
  'invalid-response': '服务返回的数据不完整，请稍后重试。',
  validation: nameValidationMessage,
  conflict: '操作与当前资料状态冲突，请刷新列表后重试。',
  'not-found': '知识库已不存在或不属于当前本地安装，请刷新列表。',
  'local-storage': '浏览器恢复记录暂不可用，请允许当前页面使用会话存储后重试。',
};

const recoveryMessages: Record<string, string> = {
  recovery_read_failed: '浏览器恢复记录无法读取，已暂停创建。请允许当前页面使用会话存储后刷新页面。',
  recovery_write_failed: '浏览器恢复记录无法保存，本次尚未发起创建。请允许当前页面使用会话存储后重试。',
  recovery_clear_failed: '请求已返回，但浏览器恢复记录未能清除。请恢复会话存储后重试确认。',
};

const conflictMessages: Record<string, string> = {
  capacity_exceeded: '最多可创建 5 个知识库，当前已达到上限。',
  idempotency_conflict: '请求与先前的名称不一致，请刷新列表并检查已创建的知识库。',
  kb_not_ready: '知识库尚未就绪，当前不能问答。',
  answer_in_progress: '当前聊天已有提问正在处理。',
  kb_changed: '知识库在回答期间发生变化，本次结果已停止；请刷新后重新提问。',
};

const documentErrors: Record<string, string> = {
  file_count: '每批请选择 1–5 个文件。',
  file_size: '文件须非空且每份不超过 20 MiB。',
  file_type: '请选择 UTF-8 TXT、Markdown、文字 PDF 或普通 DOCX。',
  pending_files_changed: '请选择原请求中的同名、同大小文件。服务端会再次核对文件内容。',
  exact_filter: '精确查询须填写至少一项已确认的文档属性。',
};

const databaseErrors: Record<string, string> = {
  database_not_configured: '业务数据库尚未配置。请按本地开发指南配置数据库并执行初始化迁移，然后重启后端。',
  database_unavailable: '业务数据库暂不可用。请检查数据库连接和初始化迁移，然后重试。',
  persistence_failed: '业务数据库操作未完成。请检查数据库连接和初始化迁移，然后重试。',
  answer_disabled: '文字问答尚未启用；解析完成不等于已入库。',
};

export class ApiError extends Error {
  constructor(public readonly kind: ErrorKind, public readonly code = '') {
    super(kind === 'unavailable' ? databaseErrors[code] ?? errorMessages[kind]
      : kind === 'conflict' ? conflictMessages[code] ?? errorMessages[kind]
      : kind === 'local-storage' ? recoveryMessages[code] ?? errorMessages[kind]
      : kind === 'validation' ? documentErrors[code] ?? errorMessages[kind] : errorMessages[kind]);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isCapabilityState(value: unknown): value is CapabilityState {
  return value === 'not_configured' || value === 'unverified' || value === 'available' || value === 'unavailable';
}

function isVerificationTime(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$/.test(value) && !Number.isNaN(Date.parse(value));
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function isModelsInfo(value: unknown): value is ModelsInfo {
  return isRecord(value) && hasOnlyKeys(value, ['region', 'model_names', 'last_verified_at']) &&
    typeof value.region === 'string' && /^[a-z0-9-]{1,32}$/.test(value.region) &&
    Array.isArray(value.model_names) && value.model_names.length >= 1 && value.model_names.length <= 10 &&
    value.model_names.every((name: unknown) => typeof name === 'string' && /^[a-zA-Z0-9._-]{1,64}$/.test(name)) &&
    (value.last_verified_at === undefined || isVerificationTime(value.last_verified_at));
}

function isRagInfo(value: unknown): value is RagInfo {
  return isRecord(value) && hasOnlyKeys(value, ['lightrag_commit', 'postgresql_major', 'vector_version', 'last_verified_at']) &&
    typeof value.lightrag_commit === 'string' && /^[0-9a-f]{40}$/.test(value.lightrag_commit) &&
    (value.postgresql_major === undefined || (Number.isInteger(value.postgresql_major) && Number(value.postgresql_major) > 0)) &&
    (value.vector_version === undefined || (typeof value.vector_version === 'string' && /^\d+(?:\.\d+){1,2}$/.test(value.vector_version))) &&
    (value.last_verified_at === undefined || isVerificationTime(value.last_verified_at));
}

function collection<T>(value: unknown, valid: (item: Record<string, unknown>) => boolean): T[] {
  if (!isRecord(value) || !Array.isArray(value.items) ||
      !value.items.every((item: unknown) => isRecord(item) && valid(item))) throw new ApiError('invalid-response');
  return value.items as T[];
}

function isKnowledgeBase(value: unknown): value is KnowledgeBase {
  return isRecord(value) && typeof value.id === 'string' && typeof value.name === 'string' &&
    typeof value.status === 'string' && ['empty', 'ready', 'maintaining', 'blocked'].includes(value.status);
}

function knowledgeBase(value: unknown): KnowledgeBase {
  if (!isKnowledgeBase(value)) throw new ApiError('invalid-response');
  return { id: value.id, name: value.name, status: value.status };
}

function isDocument(value: Record<string, unknown>): boolean {
  return typeof value.id === 'string' && typeof value.filename === 'string' &&
    Number.isInteger(value.size) && Number(value.size) >= 0 &&
    ['pending', 'parsing', 'parsed', 'indexing', 'ready', 'failed', 'deleting', 'replacing', 'deleted'].includes(String(value.status)) &&
    (value.error_code === null || typeof value.error_code === 'string') && isVerificationTime(value.created_at) &&
    ['doc_code', 'model_code', 'edition'].every((key) => value[key] == null || typeof value[key] === 'string');
}

function ingestionJob(value: unknown): IngestionJob {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.kb_id !== 'string' ||
      !['upload', 'rebuild', 'delete', 'replace'].includes(String(value.operation)) ||
      !['queued', 'running', 'succeeded', 'failed', 'interrupted'].includes(String(value.status)) ||
      !['accepted', 'parsing', 'parsed', 'indexing', 'verifying', 'cleanup', 'complete'].includes(String(value.stage)) ||
      (value.error_code !== null && typeof value.error_code !== 'string') ||
      !Array.isArray(value.document_ids) || !value.document_ids.every((id) => typeof id === 'string') ||
      !isVerificationTime(value.created_at) || typeof value.engine_mutated !== 'boolean' || typeof value.can_retry !== 'boolean' ||
      (value.status === 'succeeded') !== (value.stage === 'complete') ||
      (value.cleanup_pending !== undefined && typeof value.cleanup_pending !== 'boolean') ||
      (value.can_cleanup !== undefined && typeof value.can_cleanup !== 'boolean')) throw new ApiError('invalid-response');
  // Free-form backend messages may contain provider details; never pass them to the UI.
  return { id: value.id, kb_id: value.kb_id, operation: value.operation as IngestionJob['operation'],
    status: value.status as IngestionJob['status'], stage: value.stage as IngestionJob['stage'],
    error_code: value.error_code, document_ids: value.document_ids as string[], created_at: value.created_at,
    engine_mutated: value.engine_mutated, can_retry: value.can_retry,
    ...(value.cleanup_pending === undefined ? {} : { cleanup_pending: value.cleanup_pending }),
    ...(value.can_cleanup === undefined ? {} : { can_cleanup: value.can_cleanup }) };
}

function parsedBlock(value: Record<string, unknown>): boolean {
  return Number.isInteger(value.ordinal) && Number(value.ordinal) >= 0 && typeof value.text === 'string' &&
    Number.isInteger(value.start) && Number(value.start) >= 0 && Number.isInteger(value.end) && Number(value.end) >= Number(value.start) &&
    isRecord(value.locator) && Object.values(value.locator).every((part) => typeof part === 'string' || (typeof part === 'number' && Number.isFinite(part)));
}

function chatMessage(value: unknown): ChatMessage {
  if (!isRecord(value) || !['message_id', 'attempt_id', 'client_message_id', 'question', 'text'].every((key) =>
      typeof value[key] === 'string') ||
      !['running', 'answered', 'insufficient_evidence', 'needs_clarification',
        'conflicting_evidence', 'failed', 'interrupted', 'partial'].includes(String(value.status)) ||
      !Array.isArray(value.citations) || !value.citations.every((item: unknown) => isRecord(item) &&
        ['evidence_id', 'document_id', 'filename', 'excerpt'].every((key) => typeof item[key] === 'string') &&
        isRecord(item.locator) && Object.values(item.locator).every((part) =>
          typeof part === 'string' || (typeof part === 'number' && Number.isFinite(part)))) ||
      !['semantic', 'exact', 'auto'].includes(String(value.mode)) ||
      !Number.isInteger(value.kb_revision) || Number(value.kb_revision) < 0 ||
      (value.error_code !== null && typeof value.error_code !== 'string') ||
      !isVerificationTime(value.created_at) || typeof value.saved !== 'boolean' ||
      (value.stale !== undefined && typeof value.stale !== 'boolean') ||
      (value.route !== undefined && !['semantic', 'exact', 'literal', 'needs_clarification', 'unsupported'].includes(String(value.route))) ||
      (value.hidden !== undefined && typeof value.hidden !== 'boolean')) throw new ApiError('invalid-response');
  return value as unknown as ChatMessage;
}

export function createApi(fetcher: typeof fetch = globalThis.fetch) {
  async function request(path: string, method: 'GET' | 'POST' | 'PATCH' | 'DELETE' = 'GET', body?: unknown,
                         timeoutMs = 10000): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const multipart = body instanceof FormData;
      const response = await fetcher(path, {
        method, credentials: 'omit',
        headers: { Accept: 'application/json', ...(body === undefined || multipart ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }), signal: controller.signal,
      });
      let value: unknown = null;
      try { value = await response.json(); }
      catch { if (response.ok) throw new ApiError('invalid-response'); }
      if (!response.ok) {
        const kind: ErrorKind = response.status === 403 ? 'forbidden' : response.status === 503 ? 'unavailable'
          : response.status === 422 ? 'validation' : response.status === 409 ? 'conflict'
          : response.status === 404 ? 'not-found' : 'http';
        const code = isRecord(value) && isRecord(value.detail) && typeof value.detail.code === 'string' ? value.detail.code : '';
        throw new ApiError(kind, code);
      }
      return value;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('network');
    } finally { clearTimeout(timeout); }
  }

  async function askMessageStream(id: string, text: string, key: string,
      mode: 'semantic' | 'exact' | 'auto' = 'semantic', exact?: ExactFilter,
      onProgress?: (progress: AnswerProgress) => void): Promise<ChatMessage> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetcher(`/api/conversations/${encodeURIComponent(id)}/messages/stream`, {
        method: 'POST', credentials: 'omit',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_message_id: key, text, mode,
          ...(mode === 'exact' ? { exact } : {}) }), signal: controller.signal,
      });
      if (!response.ok) {
        let value: unknown = null;
        try { value = await response.json(); } catch { /* Failed responses may have no JSON body. */ }
        const code = isRecord(value) && isRecord(value.detail) && typeof value.detail.code === 'string'
          ? value.detail.code : '';
        throw new ApiError(response.status === 409 ? 'conflict' : response.status === 503 ? 'unavailable'
          : response.status === 403 ? 'forbidden' : response.status === 404 ? 'not-found' : 'http', code);
      }
      if (!response.headers.get('Content-Type')?.startsWith('text/event-stream') || !response.body)
        throw new ApiError('invalid-response');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let accepted: ChatMessage | null = null;
      let streamed = '';
      let provisional = false;
      let sequence = 0;
      let result: ChatMessage | null = null;
      while (true) {
        const chunk = await reader.read();
        buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
        buffer = buffer.replaceAll('\r\n', '\n');
        if (buffer.length > 65536) throw new ApiError('invalid-response');
        let boundary: number;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (frame.startsWith(':')) continue;
          const lines = frame.split('\n');
          if (lines.length !== 2 || !lines[0].startsWith('event: ') || !lines[1].startsWith('data: '))
            throw new ApiError('invalid-response');
          let data: unknown;
          try { data = JSON.parse(lines[1].slice(6)); } catch { throw new ApiError('invalid-response'); }
          const event = lines[0].slice(7);
          if (event === 'accepted') {
            const message = chatMessage(data);
            if (accepted || message.status !== 'running' || message.saved ||
                message.client_message_id !== key) throw new ApiError('invalid-response');
            accepted = message;
            onProgress?.({ type: 'accepted', message });
          } else if (event === 'delta') {
            if (!accepted || !isRecord(data) || typeof data.text !== 'string' || typeof data.attempt_id !== 'string' ||
                typeof data.saved !== 'boolean' || data.seq !== sequence + 1 ||
                data.attempt_id !== accepted.attempt_id) throw new ApiError('invalid-response');
            sequence++;
            provisional ||= !data.saved;
            streamed += data.text;
            if (streamed.length > 1500) throw new ApiError('invalid-response');
            onProgress?.({ type: 'delta', text: data.text, saved: data.saved });
          } else if (event === 'saved') {
            const final = chatMessage(data);
            if (!final.saved || final.status === 'running' || final.client_message_id !== key ||
                (accepted && final.attempt_id !== accepted.attempt_id) ||
                (!accepted && sequence > 0) ||
                (sequence > 0 && !provisional && streamed !== final.text) ||
                (provisional && final.status === 'partial' && streamed !== final.text))
              throw new ApiError('invalid-response');
            result = final;
          } else if (event === 'pending') {
            throw new ApiError('conflict', 'answer_in_progress');
          } else if (event === 'error') {
            if (!isRecord(data) || typeof data.code !== 'string' || typeof data.status !== 'number')
              throw new ApiError('invalid-response');
            throw new ApiError(data.status === 409 ? 'conflict' : data.status === 404 ? 'not-found'
              : data.status === 503 ? 'unavailable' : 'http', data.code);
          } else throw new ApiError('invalid-response');
          if (result) return result;
        }
        if (chunk.done) throw new ApiError('network');
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('network');
    } finally { clearTimeout(timeout); }
  }

  return {
    originalUrl: (documentId: string): string => `/api/documents/${encodeURIComponent(documentId)}/original`,
    knowledgeBases: async (): Promise<KnowledgeBase[]> => collection(await request('/api/knowledge-bases'), isKnowledgeBase),
    createKnowledgeBase: async (name: string, clientRequestId: string): Promise<KnowledgeBase> =>
      knowledgeBase(await request('/api/knowledge-bases', 'POST', { name, client_request_id: clientRequestId })),
    renameKnowledgeBase: async (id: string, name: string): Promise<KnowledgeBase> =>
      knowledgeBase(await request(`/api/knowledge-bases/${encodeURIComponent(id)}`, 'PATCH', { name })),
    documents: async (kbId: string): Promise<ManagedDocument[]> =>
      collection<Record<string, unknown>>(await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`), isDocument)
        .map((item) => ({ ...item, doc_code: item.doc_code ?? null, model_code: item.model_code ?? null,
          edition: item.edition ?? null }) as unknown as ManagedDocument),
    documentPage: async (kbId: string, scope: 'current' | 'deleted', offset = 0): Promise<DocumentPage> => {
      const value = await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/documents?scope=${scope}&limit=10&offset=${offset}`);
      if (!isRecord(value) || !nonnegativeCount(value.total) || !isRecord(value.counts) ||
          !Object.values(value.counts).every(nonnegativeCount)) throw new ApiError('invalid-response');
      const items = collection<ManagedDocument>(value, isDocument).map((item) => ({ ...item,
        doc_code: item.doc_code ?? null, model_code: item.model_code ?? null, edition: item.edition ?? null }));
      return { items, total: value.total, counts: value.counts };
    },
    updateDocumentAttributes: async (id: string, values: {doc_code: string | null; model_code: string | null; edition: string | null}): Promise<ManagedDocument> => {
      const value = await request(`/api/documents/${encodeURIComponent(id)}/attributes`, 'PATCH', values);
      if (!isRecord(value) || !isDocument(value)) throw new ApiError('invalid-response');
      return value as unknown as ManagedDocument;
    },
    jobs: async (kbId: string): Promise<IngestionJob[]> =>
      collection<Record<string, unknown>>(await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/jobs`), () => true).map(ingestionJob),
    jobPage: async (kbId: string, offset = 0): Promise<JobPage> => {
      const value = await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/jobs?scope=history&limit=10&offset=${offset}`);
      if (!isRecord(value) || !nonnegativeCount(value.total) || !nonnegativeCount(value.failed_count) ||
          !Array.isArray(value.active_items)) throw new ApiError('invalid-response');
      const items = collection<Record<string, unknown>>(value, () => true).map(ingestionJob);
      const active = value.active_items.map(ingestionJob);
      if (active.some((job) => job.kb_id !== kbId || !['queued', 'running'].includes(job.status)) ||
          items.some((job) => job.kb_id !== kbId || ['queued', 'running'].includes(job.status))) throw new ApiError('invalid-response');
      return { items, total: value.total, active_items: active, failed_count: value.failed_count };
    },
    job: async (id: string): Promise<IngestionJob> => ingestionJob(await request(`/api/jobs/${encodeURIComponent(id)}`)),
    uploadDocuments: async (kbId: string, files: File[], clientRequestId: string): Promise<IngestionJob> => {
      const data = new FormData();
      data.set('client_request_id', clientRequestId);
      for (const file of files) data.append('files', file);
      return ingestionJob(await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`, 'POST', data));
    },
    retryJob: async (id: string): Promise<IngestionJob> => ingestionJob(await request(`/api/jobs/${encodeURIComponent(id)}/retry`, 'POST')),
    cleanupJob: async (id: string): Promise<IngestionJob> => ingestionJob(await request(`/api/jobs/${encodeURIComponent(id)}/cleanup`, 'POST')),
    deleteDocument: async (id: string, clientRequestId: string): Promise<IngestionJob> =>
      ingestionJob(await request(`/api/documents/${encodeURIComponent(id)}/delete`, 'POST', { client_request_id: clientRequestId })),
    replaceDocument: async (id: string, file: File, clientRequestId: string): Promise<IngestionJob> => {
      const data = new FormData();
      data.set('client_request_id', clientRequestId);
      data.set('file', file);
      return ingestionJob(await request(`/api/documents/${encodeURIComponent(id)}/replacement`, 'POST', data));
    },
    rebuild: async (kbId: string, clientRequestId: string): Promise<IngestionJob> =>
      ingestionJob(await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/rebuild`, 'POST', { client_request_id: clientRequestId })),
    blocks: async (id: string): Promise<ParsedBlock[]> =>
      collection(await request(`/api/documents/${encodeURIComponent(id)}/blocks`), parsedBlock),
    conversations: async (limit = 20, offset = 0): Promise<Conversation[]> => collection(await request(
      `/api/conversations?limit=${limit}&offset=${offset}`), (item) =>
      ['id', 'owner_id', 'kb_id', 'title', 'created_at'].every((key) => typeof item[key] === 'string')),
    conversation: async (id: string): Promise<Conversation> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}`);
      if (!isRecord(value) || !['id', 'owner_id', 'kb_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string')) throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    createConversation: async (kbId: string): Promise<Conversation> => {
      const value = await request('/api/conversations', 'POST', { kb_id: kbId });
      if (!isRecord(value) || !['id', 'owner_id', 'kb_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string')) throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    renameConversation: async (id: string, title: string): Promise<Conversation> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}`, 'PATCH', { title });
      if (!isRecord(value) || !['id', 'owner_id', 'kb_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string')) throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    deleteConversation: async (id: string): Promise<void> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}`, 'DELETE');
      if (!isRecord(value) || value.deleted !== true) throw new ApiError('invalid-response');
    },
    conversationMessages: async (id: string): Promise<ChatMessage[]> =>
      collection<Record<string, unknown>>(await request(`/api/conversations/${encodeURIComponent(id)}/messages`),
        () => true).map(chatMessage),
    askMessage: async (id: string, text: string, key: string,
                       mode: 'semantic' | 'exact' | 'auto' = 'semantic', exact?: ExactFilter): Promise<ChatMessage> =>
      chatMessage(await request(`/api/conversations/${encodeURIComponent(id)}/messages`, 'POST',
        { client_message_id: key, text, mode, ...(mode === 'exact' ? { exact } : {}) }, 60000)),
    askMessageStream,
    retryAnswer: async (chatId: string, messageId: string, attemptId: string): Promise<ChatMessage> =>
      chatMessage(await request(`/api/conversations/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}/retry`,
        'POST', { attempt_id: attemptId }, 60000)),
    health: async (): Promise<SystemHealth> => {
      const value = await request('/api/status');
      if (!isRecord(value) || value.status !== 'partial' || value.mode !== 'local_single_user' ||
          !['available', 'not_configured', 'unavailable'].includes(String(value.database)) ||
          !isCapabilityState(value.rag) || !isCapabilityState(value.models) ||
          (value.backup !== undefined && !['available', 'running', 'unavailable', 'disabled'].includes(String(value.backup))) ||
          (value.backup_error_code !== undefined && ![
            'engine_configuration_unavailable', 'backup_verification_failed',
            'backup_interrupted', 'backup_failed',
          ].includes(String(value.backup_error_code))) ||
          (value.retention !== undefined && !['available', 'unavailable', 'disabled'].includes(String(value.retention))) ||
          (value.last_backup_at !== undefined && !isVerificationTime(value.last_backup_at)) ||
          (value.models_info !== undefined && !isModelsInfo(value.models_info)) ||
          (value.rag_info !== undefined && !isRagInfo(value.rag_info))) throw new ApiError('invalid-response');
      return {
        status: 'partial', mode: 'local_single_user',
        database: value.database as SystemHealth['database'],
        rag: value.rag, models: value.models,
        ...(value.backup === undefined ? {} : { backup: value.backup as SystemHealth['backup'] }),
        ...(value.backup_error_code === undefined ? {} : {
          backup_error_code: value.backup_error_code as SystemHealth['backup_error_code'],
        }),
        ...(value.retention === undefined ? {} : { retention: value.retention as SystemHealth['retention'] }),
        ...(value.last_backup_at === undefined ? {} : { last_backup_at: value.last_backup_at }),
        ...(value.models_info === undefined ? {} : { models_info: value.models_info }),
        ...(value.rag_info === undefined ? {} : { rag_info: value.rag_info }),
      };
    },
  };
}

export type ApiClient = ReturnType<typeof createApi>;
