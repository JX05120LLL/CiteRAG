import { fileURLToPath } from 'node:url';
import { defineConfig, mergeConfig } from 'vitest/config';
import base from './vite.config';

export default mergeConfig(base, defineConfig({
  build: { rollupOptions: { input: {
    workbench: fileURLToPath(new URL('./index.html', import.meta.url)),
    voice: fileURLToPath(new URL('./voice.html', import.meta.url)),
    preview: fileURLToPath(new URL('./ui-preview.html', import.meta.url)),
  } } },
}));
