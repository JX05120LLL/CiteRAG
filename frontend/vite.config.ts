import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    fs: { allow: [fileURLToPath(new URL('.', import.meta.url)), fileURLToPath(new URL('../assets/brand', import.meta.url))] },
    proxy: { '/api': { target: 'http://127.0.0.1:8000' } },
  },
  test: { environment: 'jsdom', clearMocks: true, restoreMocks: true },
});
