export interface KnowledgeBase {
  id: string;
  name: string;
  status: 'empty' | 'ready' | 'maintaining' | 'blocked';
}

export interface Conversation {
  id: string;
  owner_id: string;
  kb_id: string | null;
  title: string;
  created_at: string;
  archived_at?: string | null;
}

export interface VoiceCapability {
  transport: 'disabled' | 'not_configured' | 'configured';
  assistant: 'not_configured' | 'configured';
  purpose: 'media_test' | 'voice_assistant';
}
export interface VoiceConnection {
  server_url: string;
  token: string;
  room: string;
  conversation_id: string;
  assistant: 'not_configured';
  purpose: 'media_test';
}

export interface VoiceSessionConnection extends Omit<VoiceConnection, 'assistant' | 'purpose'> {
  session_id: string;
  control_token: string;
  assistant_identity: string;
  assistant: 'starting';
  purpose: 'voice_assistant';
  lease_seconds: number;
  generation: number;
}
export interface VoiceEvent {
  session_id: string;
  seq: number;
  generation: number;
  type: 'ready' | 'phase' | 'transcript' | 'speech_text' | 'timing' | 'answer' | 'error' | 'interrupted' | 'ended' | 'playout_drained' | 'agent';
  phase?: string;
  text?: string;
  metric?: 'first_text' | 'first_audio_sent';
  elapsed_ms?: number;
  final?: boolean;
  utterance?: number;
  revision?: number;
  answer?: ChatMessage;
  code?: string;
  reason?: string;
  run?: AgentRun;
}
const voiceEventTypes: Record<VoiceEvent['type'], true> = {
  ready: true, phase: true, transcript: true, speech_text: true, timing: true,
  answer: true, error: true, interrupted: true, ended: true, playout_drained: true, agent: true,
};

export interface AgentRun {
  id: string; conversation_id: string; message_id: string; attempt_id: string;
  status: 'running' | 'waiting_input' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled' | 'interrupted' | 'expired';
  generation: number; seq: number; model_rounds: number; tool_attempts: number;
  active_ms: number; error_code: string | null; voice_session_id: string | null;
  created_at: string; finished_at: string | null;
  waiting: { kind: 'input' | 'approval'; prompt?: string; fields?: Record<string, string>;
    call_id?: string; tool_id?: string; tool_version?: string; arguments?: Record<string, unknown>;
    impact?: string; destination?: string; policy_hash?: string } | null;
}

function agentRun(value: unknown, chatId: string): AgentRun {
  if (!isRecord(value) || value.conversation_id !== chatId ||
    !['id', 'message_id', 'attempt_id', 'created_at'].every((key) => typeof value[key] === 'string') ||
    !['running', 'waiting_input', 'waiting_approval', 'completed', 'failed', 'cancelled', 'interrupted', 'expired'].includes(String(value.status)) ||
    !['generation', 'seq', 'model_rounds', 'tool_attempts', 'active_ms'].every((key) => Number.isSafeInteger(value[key]) && Number(value[key]) >= 0) ||
    (value.waiting !== null && (!isRecord(value.waiting) || !['input', 'approval'].includes(String(value.waiting.kind)))))
    throw new ApiError('invalid-response');
  return value as unknown as AgentRun;
}

function localVoiceUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return ['ws:', 'wss:'].includes(url.protocol) &&
      ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) &&
      !url.username && !url.password && !url.search && !url.hash && url.pathname === '/' &&
      value === `${url.protocol}//${url.host}`;
  } catch { return false; }
}

export interface Citation {
  evidence_id: string;
  document_id: string;
  filename: string;
  locator: Record<string, string | number>;
  excerpt: string;
}

export interface ChatMessage {
  phase?: 'waiting_input' | 'waiting_approval';
  message_id: string;
  attempt_id: string;
  client_message_id: string;
  question: string;
  mode: 'semantic' | 'exact' | 'auto';
  route?: 'semantic' | 'exact' | 'literal' | 'general' | 'chat' | 'needs_clarification' | 'unsupported' | null;
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
  images?: ChatImage[];
}

export interface KnowledgeMemory {
  id: string;
  kb_id: string;
  kind: 'preference' | 'background';
  content: string;
  source_conversation_id: string;
  source_message_id: string;
  created_at: string;
  valid: boolean;
}

export interface ToolInfo {
  id: string;
  title: string;
  scope: 'any' | 'knowledge';
  approval_required: boolean;
  impact: string;
  input_schema?: { type?: string; required?: string[]; properties?: Record<string, {
    type?: string; title?: string; default?: unknown; minimum?: number; maximum?: number;
    minLength?: number; maxLength?: number;
  }> };
}

export interface ToolCallRecord {
  id: string;
  conversation_id: string;
  request_id: string;
  kb_id: string | null;
  tool_id: string;
  arguments: Record<string, unknown>;
  impact: string;
  status: 'pending_approval' | 'running' | 'succeeded' | 'failed' | 'rejected' | 'interrupted' | 'unknown';
  run_id?: string | null;
  result: Record<string, unknown> | null;
  error_code: string | null;
  created_at: string;
  approved_at: string | null;
  finished_at: string | null;
  source_type: 'tool';
}

function toolCall(value: unknown, chatId: string): ToolCallRecord {
  if (!isRecord(value) || value.conversation_id !== chatId ||
    !['id', 'request_id', 'tool_id', 'impact', 'created_at'].every((key) => typeof value[key] === 'string') ||
    !['pending_approval', 'running', 'succeeded', 'failed', 'rejected', 'interrupted', 'unknown'].includes(String(value.status)) ||
    !isRecord(value.arguments) || (value.result !== null && !isRecord(value.result)) ||
    (value.error_code !== null && typeof value.error_code !== 'string') ||
    (value.kb_id !== null && typeof value.kb_id !== 'string') || value.source_type !== 'tool')
    throw new ApiError('invalid-response');
  return value as unknown as ToolCallRecord;
}

export interface ChatImage {
  id: string;
  filename: string;
  mime_type: 'image/png' | 'image/jpeg';
  width: number;
  height: number;
  size: number;
  observation: string | null;
  observation_status: 'pending' | 'ready' | 'failed';
  needs_confirmation: boolean;
  confirmed_identifier: string | null;
  expires_at: string;
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

export type LocalCheckState = 'available' | 'unavailable' | 'not_checked';
export type LocalCheckName = 'business_database' | 'model_configuration' | 'rag_database' |
  'voice_transport' | 'model_provider' | 'speech_providers' | 'knowledge_engine';
export interface SystemCheckReport {
  checked_at: string;
  checks: Record<LocalCheckName, { state: LocalCheckState; reason: string; checked_at: string }>;
}

export type FunctionalKind = 'model' | 'asr' | 'tts' | 'knowledge';
export interface FunctionalResult {
  service: string;
  state: 'not_checked' | 'running' | 'available' | 'unavailable';
  reason: string;
  checked_at: string | null;
  expires_at: string | null;
  fingerprint: string | null;
  request_id?: string;
}
export interface FunctionalReport { checks: Record<FunctionalKind, FunctionalResult> }
const functionalKinds: FunctionalKind[] = ['model', 'asr', 'tts', 'knowledge'];

function functionalResult(value: unknown): FunctionalResult {
  if (!isRecord(value) || typeof value.service !== 'string' || value.service.length < 1 ||
      value.service.length > 100 || !['not_checked', 'running', 'available', 'unavailable'].includes(String(value.state)) ||
      typeof value.reason !== 'string' || !/^[a-z_]{1,48}$/.test(value.reason) ||
      (value.checked_at !== null && !isVerificationTime(value.checked_at)) ||
      (value.expires_at !== null && !isVerificationTime(value.expires_at)) ||
      (value.fingerprint !== null && (typeof value.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(value.fingerprint))) ||
      (value.request_id !== undefined && (typeof value.request_id !== 'string' || !/^[a-f0-9-]{36}$/.test(value.request_id))))
    throw new ApiError('invalid-response');
  return value as unknown as FunctionalResult;
}

function functionalReport(value: unknown): FunctionalReport {
  if (!isRecord(value) || !isRecord(value.checks)) throw new ApiError('invalid-response');
  const rawChecks = value.checks;
  if (!functionalKinds.every((kind) => kind in rawChecks)) throw new ApiError('invalid-response');
  const checks = Object.fromEntries(functionalKinds.map((kind) => [kind, functionalResult(rawChecks[kind])]));
  return { checks } as FunctionalReport;
}

const localCheckNames: LocalCheckName[] = ['business_database', 'model_configuration', 'rag_database',
  'voice_transport', 'model_provider', 'speech_providers', 'knowledge_engine'];

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
  tool_in_progress: '当前聊天的工具调用尚未结束。请在“工具与调用记录”中处理待确认调用，或等待运行中的调用完成，再继续提问；也可新建聊天。',
  capacity_exceeded: '最多可创建 5 个知识库，当前已达到上限。',
  idempotency_conflict: '请求与先前的名称不一致，请刷新列表并检查已创建的知识库。',
  kb_not_ready: '知识库尚未就绪，当前不能问答。',
  answer_in_progress: '当前聊天已有提问正在处理。',
  conversation_archived: '聊天已归档。请先恢复对话，再继续提问或通话。',
  kb_changed: '知识库在回答期间发生变化，本次结果已停止；请刷新后重新提问。',
};

const documentErrors: Record<string, string> = {
  file_count: '每批请选择 1–5 个文件。',
  file_size: '文件须非空且每份不超过 20 MiB。',
  file_type: '请选择 UTF-8 TXT、Markdown、文字 PDF 或普通 DOCX。',
  pending_files_changed: '请选择原请求中的同名、同大小文件。服务端会再次核对文件内容。',
  exact_filter: '精确查询须填写至少一项已确认的文档属性。',
  invalid_image: '请选择有效的 PNG 或 JPEG 图片。',
  image_too_large: '每张图片最多 10 MiB。',
  invalid_images: '每条问题最多添加两张不同图片。',
};

const databaseErrors: Record<string, string> = {
  database_not_configured: '业务数据库尚未配置。请按本地开发指南配置数据库并执行初始化迁移，然后重启后端。',
  database_unavailable: '业务数据库暂不可用。请检查数据库连接和初始化迁移，然后重试。',
  persistence_failed: '业务数据库操作未完成。请检查数据库连接和初始化迁移，然后重试。',
  answer_disabled: '文字问答尚未启用；解析完成不等于已入库。',
  image_observation_unavailable: '图片识别未完成，请重试或补充文字。',
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
      (value.route !== undefined && value.route !== null &&
        !['semantic', 'exact', 'literal', 'general', 'chat', 'needs_clarification', 'unsupported'].includes(String(value.route))) ||
      (['general', 'chat'].includes(String(value.route)) && value.citations.length !== 0) ||
      (value.hidden !== undefined && typeof value.hidden !== 'boolean') ||
      (value.images !== undefined && (!Array.isArray(value.images) || !value.images.every(isChatImage)))) throw new ApiError('invalid-response');
  return value as unknown as ChatMessage;
}

function isChatImage(value: unknown): value is ChatImage {
  return isRecord(value) && typeof value.id === 'string' && typeof value.filename === 'string' &&
    ['image/png', 'image/jpeg'].includes(String(value.mime_type)) &&
    Number.isInteger(value.width) && Number(value.width) > 0 &&
    Number.isInteger(value.height) && Number(value.height) > 0 &&
    Number.isInteger(value.size) && Number(value.size) > 0 &&
    (value.observation === null || typeof value.observation === 'string') &&
    ['pending', 'ready', 'failed'].includes(String(value.observation_status)) &&
    typeof value.needs_confirmation === 'boolean' &&
    (value.confirmed_identifier === null || typeof value.confirmed_identifier === 'string') &&
    isVerificationTime(value.expires_at);
}

export function createApi(fetcher: typeof fetch = globalThis.fetch) {
  async function request(path: string, method: 'GET' | 'POST' | 'PATCH' | 'DELETE' = 'GET', body?: unknown,
                         timeoutMs = 10000, extraHeaders: Record<string, string> = {}): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const multipart = body instanceof FormData;
      const response = await fetcher(path, {
        method, credentials: 'omit',
        headers: { Accept: 'application/json', ...extraHeaders, ...(body === undefined || multipart ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }), signal: controller.signal,
      });
      if (response.status === 204 && response.ok) return null;
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
      onProgress?: (progress: AnswerProgress) => void, imageIds: string[] = []): Promise<ChatMessage> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetcher(`/api/conversations/${encodeURIComponent(id)}/messages/stream`, {
        method: 'POST', credentials: 'omit',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_message_id: key, text, mode, image_ids: imageIds,
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
    conversations: async (limit = 20, offset = 0, archived = false): Promise<Conversation[]> => collection(await request(
      `/api/conversations?limit=${limit}&offset=${offset}${archived ? '&archived=true' : ''}`), (item) =>
      ['id', 'owner_id', 'title', 'created_at'].every((key) => typeof item[key] === 'string') &&
      (item.kb_id === null || typeof item.kb_id === 'string')),
    conversation: async (id: string): Promise<Conversation> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}`);
      if (!isRecord(value) || !['id', 'owner_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string') || (value.kb_id !== null && typeof value.kb_id !== 'string')) throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    createConversation: async (kbId: string | null): Promise<Conversation> => {
      const value = await request('/api/conversations', 'POST', { kb_id: kbId });
      if (!isRecord(value) || !['id', 'owner_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string') || (value.kb_id !== null && typeof value.kb_id !== 'string')) throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    renameConversation: async (id: string, title: string): Promise<Conversation> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}`, 'PATCH', { title });
      if (!isRecord(value) || !['id', 'owner_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string') || (value.kb_id !== null && typeof value.kb_id !== 'string')) throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    archiveConversation: async (id: string, archived: boolean): Promise<Conversation> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}/archive`, 'PATCH', { archived });
      if (!isRecord(value) || !['id', 'owner_id', 'title', 'created_at'].every((key) =>
        typeof value[key] === 'string') || (value.kb_id !== null && typeof value.kb_id !== 'string') ||
        (value.archived_at !== null && typeof value.archived_at !== 'string'))
        throw new ApiError('invalid-response');
      return value as unknown as Conversation;
    },
    deleteConversation: async (id: string): Promise<void> => {
      const value = await request(`/api/conversations/${encodeURIComponent(id)}`, 'DELETE');
      if (!isRecord(value) || value.deleted !== true) throw new ApiError('invalid-response');
    },
    agentCapability: async (): Promise<{ enabled: boolean }> => {
      const value = await request('/api/agent/capability');
      if (!isRecord(value) || typeof value.enabled !== 'boolean') throw new ApiError('invalid-response');
      return { enabled: value.enabled };
    },
    agentRuns: async (chatId: string): Promise<AgentRun[]> => collection<Record<string, unknown>>(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/agent-runs`), () => true)
      .map((item) => agentRun(item, chatId)),
    startAgent: async (chatId: string, text: string, requestId: string, imageIds: string[] = []): Promise<AgentRun> => agentRun(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/agent-runs`, 'POST',
        { client_message_id: requestId, text, mode: 'auto', image_ids: imageIds }), chatId),
    retryAgent: async (chatId: string, messageId: string, requestId: string): Promise<AgentRun> => agentRun(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/agent-runs/retry`, 'POST',
        { message_id: messageId, request_id: requestId }), chatId),
    resumeAgent: async (run: AgentRun, requestId: string, input: Record<string, unknown>,
      control?: { voice_session_id: string; control_token: string }): Promise<AgentRun> => agentRun(
      await request(`/api/conversations/${encodeURIComponent(run.conversation_id)}/agent-runs/${encodeURIComponent(run.id)}/resume`,
        'POST', { request_id: requestId, generation: run.generation, input, ...control }), run.conversation_id),
    cancelAgent: async (run: AgentRun, control?: { voice_session_id: string; control_token: string }): Promise<AgentRun> => agentRun(
      await request(`/api/conversations/${encodeURIComponent(run.conversation_id)}/agent-runs/${encodeURIComponent(run.id)}/cancel`,
        'POST', control ? { request_id: crypto.randomUUID(), generation: run.generation, input: {}, ...control } : undefined), run.conversation_id),
    conversationTools: async (chatId: string): Promise<ToolInfo[]> => collection<ToolInfo>(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/tools`),
      (item) => typeof item.id === 'string' && typeof item.title === 'string' &&
        ['any', 'knowledge'].includes(String(item.scope)) &&
        typeof item.approval_required === 'boolean' && typeof item.impact === 'string'),
    toolCalls: async (chatId: string): Promise<ToolCallRecord[]> => collection<Record<string, unknown>>(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/tools/calls`),
      () => true).map((item) => toolCall(item, chatId)),
    invokeTool: async (chatId: string, toolId: string, requestId: string,
      arguments_: Record<string, unknown> = {}): Promise<ToolCallRecord> => toolCall(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/tools/calls`, 'POST',
        { request_id: requestId, tool_id: toolId, arguments: arguments_ }), chatId),
    decideTool: async (chatId: string, callId: string, approve: boolean): Promise<ToolCallRecord> => toolCall(
      await request(`/api/conversations/${encodeURIComponent(chatId)}/tools/calls/${encodeURIComponent(callId)}/decision`,
        'POST', { approve }, 20000), chatId),
    knowledgeMemories: async (kbId: string): Promise<KnowledgeMemory[]> => collection<KnowledgeMemory>(
      await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/memories`),
      (item) => typeof item.id === 'string' &&
        item.kb_id === kbId && ['preference', 'background'].includes(String(item.kind)) &&
        typeof item.content === 'string' && typeof item.source_conversation_id === 'string' &&
        typeof item.source_message_id === 'string' && typeof item.created_at === 'string' &&
        typeof item.valid === 'boolean'),
    createKnowledgeMemory: async (kbId: string, sourceMessageId: string,
      kind: KnowledgeMemory['kind'], content: string): Promise<KnowledgeMemory> => {
      const value = await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/memories`, 'POST',
        { source_message_id: sourceMessageId, kind, content });
      if (!isRecord(value) || typeof value.id !== 'string' || value.kb_id !== kbId ||
        typeof value.content !== 'string') throw new ApiError('invalid-response');
      return value as unknown as KnowledgeMemory;
    },
    deleteKnowledgeMemory: async (kbId: string, id: string): Promise<void> => {
      const value = await request(`/api/knowledge-bases/${encodeURIComponent(kbId)}/memories/${encodeURIComponent(id)}`, 'DELETE');
      if (!isRecord(value) || value.deleted !== true) throw new ApiError('invalid-response');
    },
    conversationMessages: async (id: string): Promise<ChatMessage[]> =>
      collection<Record<string, unknown>>(await request(`/api/conversations/${encodeURIComponent(id)}/messages`),
        () => true).map(chatMessage),
    imageUrl: (chatId: string, imageId: string): string =>
      `/api/conversations/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(imageId)}`,
    uploadImage: async (chatId: string, file: File): Promise<ChatImage> => {
      const form = new FormData(); form.append('file', file);
      const value = await request(`/api/conversations/${encodeURIComponent(chatId)}/attachments`,
        'POST', form, 60000);
      if (!isChatImage(value)) throw new ApiError('invalid-response');
      return value;
    },
    deletePendingImage: async (chatId: string, imageId: string): Promise<void> => {
      await request(`/api/conversations/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(imageId)}`, 'DELETE');
    },
    confirmImage: async (chatId: string, imageId: string, identifier: string): Promise<ChatImage> => {
      const value = await request(`/api/conversations/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(imageId)}/confirm`,
        'POST', { identifier });
      if (!isChatImage(value)) throw new ApiError('invalid-response');
      return value;
    },
    askMessage: async (id: string, text: string, key: string,
                       mode: 'semantic' | 'exact' | 'auto' = 'semantic', exact?: ExactFilter): Promise<ChatMessage> =>
      chatMessage(await request(`/api/conversations/${encodeURIComponent(id)}/messages`, 'POST',
        { client_message_id: key, text, mode, ...(mode === 'exact' ? { exact } : {}) }, 60000)),
    askMessageStream,
    retryAnswer: async (chatId: string, messageId: string, attemptId: string): Promise<ChatMessage> =>
      chatMessage(await request(`/api/conversations/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}/retry`,
        'POST', { attempt_id: attemptId }, 60000)),
    voiceStatus: async (): Promise<VoiceCapability> => {
      const value = await request('/api/voice/status');
      if (!isRecord(value) || !['disabled', 'not_configured', 'configured'].includes(String(value.transport)) ||
          !['not_configured', 'configured'].includes(String(value.assistant)) ||
          !['media_test', 'voice_assistant'].includes(String(value.purpose))) throw new ApiError('invalid-response');
      return value as unknown as VoiceCapability;
    },
    voiceToken: async (conversation: string): Promise<VoiceConnection> => {
      const value = await request(`/api/conversations/${encodeURIComponent(conversation)}/voice/token`, 'POST');
      if (!isRecord(value) || !localVoiceUrl(value.server_url) || typeof value.token !== 'string' ||
          !value.token || value.token.length > 8192 || typeof value.room !== 'string' || !value.room ||
          value.conversation_id !== conversation || value.assistant !== 'not_configured' ||
          value.purpose !== 'media_test') throw new ApiError('invalid-response');
      return value as unknown as VoiceConnection;
    },
    voiceStart: async (conversation: string, key: string): Promise<VoiceSessionConnection> => {
      const value = await request(`/api/conversations/${encodeURIComponent(conversation)}/voice/sessions`, 'POST', { client_request_id: key });
      if (!isRecord(value) || !localVoiceUrl(value.server_url) || value.conversation_id !== conversation ||
          typeof value.token !== 'string' || !value.token || value.token.length > 8192 ||
          typeof value.room !== 'string' || typeof value.session_id !== 'string' ||
          typeof value.control_token !== 'string' || value.control_token.length < 20 ||
          typeof value.assistant_identity !== 'string' || value.assistant !== 'starting' ||
          value.purpose !== 'voice_assistant' || !Number.isInteger(value.generation) ||
          value.lease_seconds !== 40) throw new ApiError('invalid-response');
      return value as unknown as VoiceSessionConnection;
    },
    voiceRenew: async (id: string, control: string, reconnect = false): Promise<{ generation: number }> => {
      const value = await request(`/api/voice/sessions/${encodeURIComponent(id)}/renew`, 'POST', { reconnect }, 10000,
        { 'X-CiteRAG-Voice-Control': control });
      if (!isRecord(value) || !Number.isInteger(value.generation)) throw new ApiError('invalid-response');
      return { generation: Number(value.generation) };
    },
    voiceStop: async (id: string, control: string): Promise<{ generation: number }> => {
      const value = await request(`/api/voice/sessions/${encodeURIComponent(id)}/stop`, 'POST', {}, 10000,
        { 'X-CiteRAG-Voice-Control': control });
      if (!isRecord(value) || !Number.isInteger(value.generation)) throw new ApiError('invalid-response');
      return { generation: Number(value.generation) };
    },
    voiceCorrection: async (id: string, control: string, utterance: number, revision: number, text: string): Promise<{ generation: number }> => {
      const value = await request(`/api/voice/sessions/${encodeURIComponent(id)}/correction`, 'POST', { utterance, revision, text }, 10000,
        { 'X-CiteRAG-Voice-Control': control });
      if (!isRecord(value) || value.status !== 'accepted' || !Number.isInteger(value.generation) || Number(value.generation) < 0)
        throw new ApiError('invalid-response');
      return { generation: Number(value.generation) };
    },
    voiceEnd: async (id: string, control: string) => {
      const value = await request(`/api/voice/sessions/${encodeURIComponent(id)}/end`, 'POST', {}, 10000,
        { 'X-CiteRAG-Voice-Control': control });
      if (!isRecord(value) || value.status !== 'ended') throw new ApiError('invalid-response');
      return value;
    },
    voiceEvents: async (id: string, control: string, signal: AbortSignal, changed: (event: VoiceEvent) => void) => {
      const response = await fetcher(`/api/voice/sessions/${encodeURIComponent(id)}/events`, {
        credentials: 'omit', headers: { Accept: 'text/event-stream', 'X-CiteRAG-Voice-Control': control }, signal,
      });
      if (!response.ok || !response.body || !response.headers.get('Content-Type')?.startsWith('text/event-stream'))
        throw new ApiError('network', 'voice_events_failed');
      const reader = response.body.getReader();
      const decoder = new TextDecoder(); let buffer = ''; let seq = -1;
      try {
        while (!signal.aborted) {
          const chunk = await reader.read();
          if (chunk.done) return;
          buffer += decoder.decode(chunk.value, { stream: true });
          buffer = buffer.replaceAll('\r\n', '\n');
          if (buffer.length > 65536) throw new ApiError('invalid-response');
          let boundary: number;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
            if (frame.startsWith(':')) continue;
            const lines = frame.split('\n');
            if (lines[0] !== 'event: voice' || !lines[1]?.startsWith('data: ')) throw new ApiError('invalid-response');
            const event: unknown = JSON.parse(lines[1].slice(6));
            if (!isRecord(event) || event.session_id !== id || !Number.isInteger(event.seq) ||
                !Number.isInteger(event.generation) || Number(event.generation) < 0 ||
                !Object.hasOwn(voiceEventTypes, String(event.type)))
              throw new ApiError('invalid-response');
            if (Number(event.seq) <= seq) continue;
            if (seq >= 0 && Number(event.seq) !== seq + 1) throw new ApiError('network', 'voice_event_gap');
            seq = Number(event.seq);
            if (event.type === 'answer') {
              const answer = chatMessage(event.answer);
              if (!answer.saved || answer.status === 'running') throw new ApiError('invalid-response');
              event.answer = answer;
            }
            if (event.type === 'agent') {
              if (!isRecord(event.run) || typeof event.run.conversation_id !== 'string') throw new ApiError('invalid-response');
              event.run = agentRun(event.run, event.run.conversation_id);
            }
            if (event.type === 'transcript' && (typeof event.text !== 'string' || event.text.length > 1000 ||
                typeof event.final !== 'boolean' || !Number.isInteger(event.utterance) || !Number.isInteger(event.revision)))
              throw new ApiError('invalid-response');
            changed(event as unknown as VoiceEvent);
          }
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    },
    checkSystem: async (): Promise<SystemCheckReport> => {
      const value = await request('/api/status/check', 'POST');
      const checks = isRecord(value) ? value.checks : null;
      if (!isRecord(value) || !isVerificationTime(value.checked_at) || !isRecord(checks) ||
          !localCheckNames.every((name) => {
            const check = checks[name];
            return isRecord(check) && ['available', 'unavailable', 'not_checked'].includes(String(check.state)) &&
              typeof check.reason === 'string' && check.reason.length > 0 && check.reason.length <= 240 &&
              isVerificationTime(check.checked_at);
          })) throw new ApiError('invalid-response');
      return value as unknown as SystemCheckReport;
    },
    functionalStatus: async (): Promise<FunctionalReport> =>
      functionalReport(await request('/api/status/functional')),
    autoFunctionalChecks: async (force = false): Promise<FunctionalReport> =>
      functionalReport(await request('/api/status/functional/auto', 'POST',
        { accept_cost: true, force })),
    startFunctionalCheck: async (kind: FunctionalKind, requestId: string): Promise<FunctionalResult> =>
      functionalResult(await request(`/api/status/functional/${kind}`, 'POST',
        { request_id: requestId, accept_cost: true })),
    cancelFunctionalCheck: async (kind: FunctionalKind, requestId: string): Promise<FunctionalResult> =>
      functionalResult(await request(`/api/status/functional/${kind}/cancel`, 'POST',
        { request_id: requestId })),
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
