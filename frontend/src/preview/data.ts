import { ApiError, createApi } from '../api/client';
import type { ApiClient } from '../api/client';
import { sampleActive, sampleBases, sampleChats, sampleDeleted, sampleDocuments, sampleHealth,
  sampleJobs, sampleMessages, sampleVoice } from './samples';

export type ReadPreviewApi = Pick<ApiClient, 'knowledgeBases' | 'conversations' | 'conversationMessages' |
  'documentPage' | 'jobPage' | 'job' | 'health' | 'voiceStatus' | 'originalUrl'>;

export function createReadPreviewApi(fetcher: typeof fetch = globalThis.fetch): {
  api: ReadPreviewApi;
  dispose: () => void;
} {
  const lifecycle = new AbortController();
  const guardedFetch: typeof fetch = async (input, init) => {
    if (lifecycle.signal.aborted || (init?.method ?? 'GET') !== 'GET') {
      throw new ApiError('forbidden');
    }
    const signals = [lifecycle.signal, init?.signal].filter((signal): signal is AbortSignal => Boolean(signal));
    // Keep cancellation attached through response-body consumption, not just response headers.
    return fetcher(input, { ...init, signal: AbortSignal.any(signals) });
  };
  const client = createApi(guardedFetch);
  const api: ReadPreviewApi = Object.freeze({
    knowledgeBases: client.knowledgeBases, conversations: client.conversations,
    conversationMessages: client.conversationMessages, documentPage: client.documentPage,
    jobPage: client.jobPage, job: client.job, health: client.health, voiceStatus: client.voiceStatus,
    originalUrl: client.originalUrl,
  });
  return { api, dispose: () => lifecycle.abort() };
}

export function createSamplePreviewApi(): { api: ReadPreviewApi; dispose: () => void } {
  const api: ReadPreviewApi = {
    knowledgeBases: async () => sampleBases,
    conversations: async () => sampleChats,
    conversationMessages: async (id) => sampleMessages[id] ?? [],
    documentPage: async (id, scope, offset = 0) => {
      const items = id === 'design-library-1' ? scope === 'current' ? sampleDocuments : sampleDeleted : [];
      return { items: items.slice(offset, offset + 10), total: items.length,
        counts: { ready: items.filter((item) => item.status === 'ready').length,
          failed: items.filter((item) => item.status === 'failed').length,
          deleted: items.filter((item) => item.status === 'deleted').length } };
    },
    jobPage: async (id, offset = 0) => ({ items: id === 'design-library-1' ? sampleJobs.slice(offset, offset + 10) : [],
      total: id === 'design-library-1' ? sampleJobs.length : 0,
      active_items: id === 'design-library-1' ? sampleActive : [], failed_count: id === 'design-library-1' ? 2 : 0 }),
    job: async (id) => {
      const job = [...sampleActive, ...sampleJobs].find((item) => item.id === id);
      if (!job) throw new ApiError('not-found');
      return job;
    },
    health: async () => sampleHealth, voiceStatus: async () => sampleVoice,
    originalUrl: () => { throw new ApiError('forbidden'); },
  };
  return { api, dispose: () => {} };
}
