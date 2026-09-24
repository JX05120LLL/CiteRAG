import { ApiError } from './api/client';

interface PendingCreation {
  name: string;
  key: string;
}

const storageKey = 'citerag.pending-knowledge-create.v1';

export function normalizeKnowledgeName(raw: string): string {
  const name = raw.trim();
  if (!name || [...name].length > 120 || /\p{C}/u.test(raw)) throw new ApiError('validation');
  return name;
}

export function restorePendingCreation(): PendingCreation | null {
  let stored: string | null;
  try { stored = sessionStorage.getItem(storageKey); }
  catch { throw new ApiError('local-storage', 'recovery_read_failed'); }
  if (stored === null) return null;
  try {
    const value: unknown = stored.length <= 1024 ? JSON.parse(stored) : null;
    if (value !== null && typeof value === 'object' && !Array.isArray(value) &&
        Object.keys(value).length === 2 && 'name' in value && 'key' in value &&
        typeof value.name === 'string' && typeof value.key === 'string' &&
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.key) &&
        normalizeKnowledgeName(value.name) === value.name) {
      return { name: value.name, key: value.key };
    }
  } catch { /* Malformed or invalid recovery data must never become a request. */ }
  try { sessionStorage.removeItem(storageKey); }
  catch { throw new ApiError('local-storage', 'recovery_read_failed'); }
  return null;
}

export function savePendingCreation(record: PendingCreation): void {
  try { sessionStorage.setItem(storageKey, JSON.stringify(record)); }
  catch { throw new ApiError('local-storage', 'recovery_write_failed'); }
}

export function clearPendingCreation(record: PendingCreation): void {
  try {
    // A late result must not clear a newer request's recovery record.
    if (sessionStorage.getItem(storageKey) === JSON.stringify(record)) sessionStorage.removeItem(storageKey);
  } catch { throw new ApiError('local-storage', 'recovery_clear_failed'); }
}
