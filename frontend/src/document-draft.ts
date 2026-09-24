import { ApiError } from './api/client';

interface FileMetadata { name: string; size: number }
export interface PendingIngestion {
  key: string;
  operation: 'upload' | 'rebuild';
  files: FileMetadata[];
}

const storageKey = (kbId: string) => `citerag.pending-ingestion.v1.${kbId}`;

export function validateFiles(files: File[]): void {
  if (!files.length || files.length > 5) throw new ApiError('validation', 'file_count');
  if (files.some((file) => file.size === 0 || file.size > 20 * 1024 * 1024)) throw new ApiError('validation', 'file_size');
  if (files.some((file) => !/\.(?:txt|md|pdf|docx)$/i.test(file.name))) throw new ApiError('validation', 'file_type');
}

export function fileMetadata(files: File[]): FileMetadata[] {
  return files.map(({ name, size }) => ({ name, size }));
}

export function matchesPendingFiles(pending: PendingIngestion, files: File[]): boolean {
  return pending.operation === 'upload' && JSON.stringify(pending.files) === JSON.stringify(fileMetadata(files));
}

export function restorePendingIngestion(kbId: string): PendingIngestion | null {
  let stored: string | null;
  try { stored = sessionStorage.getItem(storageKey(kbId)); }
  catch { throw new ApiError('local-storage', 'recovery_read_failed'); }
  if (stored === null) return null;
  try {
    const record = stored.length < 10000 ? JSON.parse(stored) as PendingIngestion : null;
    if (record && Object.keys(record).length === 3 && typeof record.key === 'string' &&
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(record.key) &&
        ['upload', 'rebuild'].includes(record.operation) && Array.isArray(record.files) &&
        record.files.length <= 5 && record.files.every((file) => file && Object.keys(file).length === 2 &&
          typeof file.name === 'string' && file.name.length <= 255 && Number.isInteger(file.size) && file.size > 0 && file.size <= 20 * 1024 * 1024) &&
        (record.operation === 'upload' ? record.files.length > 0 : record.files.length === 0)) return record;
  } catch { /* Invalid metadata never triggers a request. */ }
  // An unreadable pending key might already have committed. Do not discard it and create a duplicate.
  throw new ApiError('local-storage', 'ingestion_recovery_invalid');
}

export function savePendingIngestion(kbId: string, record: PendingIngestion): void {
  try { sessionStorage.setItem(storageKey(kbId), JSON.stringify(record)); }
  catch { throw new ApiError('local-storage', 'recovery_write_failed'); }
}

export function clearPendingIngestion(kbId: string, record: PendingIngestion): void {
  try {
    if (sessionStorage.getItem(storageKey(kbId)) === JSON.stringify(record)) sessionStorage.removeItem(storageKey(kbId));
  } catch { throw new ApiError('local-storage', 'recovery_clear_failed'); }
}
