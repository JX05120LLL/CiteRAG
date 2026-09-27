import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { createReadPreviewApi, createSamplePreviewApi } from './data';
import type { ReadPreviewApi } from './data';
import type { PreviewDataMode } from './model';

interface Resource<T> { resourceKey: string; data: T | null; error: string | null; loading: boolean }

// The key includes every resource argument. Old data is never relabeled with a new origin or selection.
export function usePreviewResource<T>(mode: PreviewDataMode, key: string,
    load: (api: ReadPreviewApi) => Promise<T>) {
  const resourceKey = `${mode}:${key}`;
  const loader = useRef(load); loader.current = load;
  const [state, setState] = useState<Resource<T>>({ resourceKey, data: null, error: null, loading: true });
  useEffect(() => {
    let active = true;
    const reader = mode === 'sample' ? createSamplePreviewApi() : createReadPreviewApi();
    setState({ resourceKey, data: null, error: null, loading: true });
    void loader.current(reader.api).then((data) => {
      if (active) setState({ resourceKey, data, error: null, loading: false });
    }).catch((error: unknown) => {
      if (active) setState({ resourceKey, data: null,
        error: error instanceof ApiError ? error.message : '读取未完成，请检查服务状态后重试。', loading: false });
    });
    return () => { active = false; reader.dispose(); };
  }, [mode, resourceKey]);
  return state.resourceKey === resourceKey ? state : { resourceKey, data: null, error: null, loading: true };
}
