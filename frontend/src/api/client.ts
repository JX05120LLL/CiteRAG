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

export interface SystemHealth {
  status: 'partial';
  mode: 'local_single_user';
  database: 'available' | 'not_configured' | 'unavailable';
  rag: CapabilityState;
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
};

const databaseErrors: Record<string, string> = {
  database_not_configured: '业务数据库尚未配置。请按本地开发指南配置数据库并执行初始化迁移，然后重启后端。',
  database_unavailable: '业务数据库暂不可用。请检查数据库连接和初始化迁移，然后重试。',
  persistence_failed: '业务数据库操作未完成。请检查数据库连接和初始化迁移，然后重试。',
};

export class ApiError extends Error {
  constructor(public readonly kind: ErrorKind, public readonly code = '') {
    super(kind === 'unavailable' ? databaseErrors[code] ?? errorMessages[kind]
      : kind === 'conflict' ? conflictMessages[code] ?? errorMessages[kind]
      : kind === 'local-storage' ? recoveryMessages[code] ?? errorMessages[kind] : errorMessages[kind]);
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

export function createApi(fetcher: typeof fetch = globalThis.fetch) {
  async function request(path: string, method: 'GET' | 'POST' | 'PATCH' = 'GET', body?: unknown): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetcher(path, {
        method, credentials: 'omit',
        headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: controller.signal,
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

  return {
    knowledgeBases: async (): Promise<KnowledgeBase[]> => collection(await request('/api/knowledge-bases'), isKnowledgeBase),
    createKnowledgeBase: async (name: string, clientRequestId: string): Promise<KnowledgeBase> =>
      knowledgeBase(await request('/api/knowledge-bases', 'POST', { name, client_request_id: clientRequestId })),
    renameKnowledgeBase: async (id: string, name: string): Promise<KnowledgeBase> =>
      knowledgeBase(await request(`/api/knowledge-bases/${encodeURIComponent(id)}`, 'PATCH', { name })),
    conversations: async (): Promise<Conversation[]> => collection(await request('/api/conversations?limit=20&offset=0'), (item) =>
      ['id', 'owner_id', 'kb_id', 'title', 'created_at'].every((key) => typeof item[key] === 'string')),
    health: async (): Promise<SystemHealth> => {
      const value = await request('/api/status');
      if (!isRecord(value) || value.status !== 'partial' || value.mode !== 'local_single_user' ||
          !['available', 'not_configured', 'unavailable'].includes(String(value.database)) ||
          !isCapabilityState(value.rag) || !isCapabilityState(value.models) ||
          (value.models_info !== undefined && !isModelsInfo(value.models_info)) ||
          (value.rag_info !== undefined && !isRagInfo(value.rag_info))) throw new ApiError('invalid-response');
      return {
        status: 'partial', mode: 'local_single_user',
        database: value.database as SystemHealth['database'],
        rag: value.rag, models: value.models,
        ...(value.models_info === undefined ? {} : { models_info: value.models_info }),
        ...(value.rag_info === undefined ? {} : { rag_info: value.rag_info }),
      };
    },
  };
}

export type ApiClient = ReturnType<typeof createApi>;
