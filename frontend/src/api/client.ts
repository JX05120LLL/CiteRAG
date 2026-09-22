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
  rag: 'not_configured';
  models: 'not_configured';
}

export type ErrorKind = 'forbidden' | 'unavailable' | 'http' | 'network' | 'invalid-response';

const errorMessages: Record<ErrorKind, string> = {
  forbidden: '请求被本地访问规则拒绝，请使用本机工作台地址重试。',
  unavailable: '服务暂不可用，请检查服务状态后重试。',
  http: '请求未完成，请稍后重试。',
  network: '无法连接服务，请检查服务是否启动及网络连接。',
  'invalid-response': '服务返回的数据不完整，请稍后重试。',
};

const databaseErrors: Record<string, string> = {
  database_not_configured: '业务数据库尚未配置。请按本地开发指南配置数据库并执行初始化迁移，然后重启后端。',
  database_unavailable: '业务数据库暂不可用。请检查数据库连接和初始化迁移，然后重试。',
  persistence_failed: '业务数据库操作未完成。请检查数据库连接和初始化迁移，然后重试。',
};

export class ApiError extends Error {
  constructor(public readonly kind: ErrorKind, public readonly code = '') {
    super(kind === 'unavailable' ? databaseErrors[code] ?? errorMessages[kind] : errorMessages[kind]);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function collection<T>(value: unknown, valid: (item: Record<string, unknown>) => boolean): T[] {
  if (!isRecord(value) || !Array.isArray(value.items) ||
      !value.items.every((item: unknown) => isRecord(item) && valid(item))) throw new ApiError('invalid-response');
  return value.items as T[];
}

export function createApi(fetcher: typeof fetch = globalThis.fetch) {
  async function request(path: string): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetcher(path, {
        method: 'GET', credentials: 'omit', headers: { Accept: 'application/json' }, signal: controller.signal,
      });
      let value: unknown = null;
      try { value = await response.json(); }
      catch { if (response.ok) throw new ApiError('invalid-response'); }
      if (!response.ok) {
        const kind: ErrorKind = response.status === 403 ? 'forbidden' : response.status === 503 ? 'unavailable' : 'http';
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
    knowledgeBases: async (): Promise<KnowledgeBase[]> => collection(await request('/api/knowledge-bases'), (item) =>
      typeof item.id === 'string' && typeof item.name === 'string' && ['empty', 'ready', 'maintaining', 'blocked'].includes(String(item.status))),
    conversations: async (): Promise<Conversation[]> => collection(await request('/api/conversations?limit=20&offset=0'), (item) =>
      ['id', 'owner_id', 'kb_id', 'title', 'created_at'].every((key) => typeof item[key] === 'string')),
    health: async (): Promise<SystemHealth> => {
      const value = await request('/api/status');
      if (!isRecord(value) || value.status !== 'partial' || value.mode !== 'local_single_user' ||
          !['available', 'not_configured', 'unavailable'].includes(String(value.database)) ||
          value.rag !== 'not_configured' || value.models !== 'not_configured') throw new ApiError('invalid-response');
      return value as unknown as SystemHealth;
    },
  };
}

export type ApiClient = ReturnType<typeof createApi>;
