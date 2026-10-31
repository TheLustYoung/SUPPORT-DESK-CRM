import {readFileSync} from 'node:fs';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// The local runner (python -m infrastructure.scripts.dev) writes the API port it took; the dashboard proxies /api there.
function apiPort(): string {
  try { return readFileSync(new URL('../local/dev-port.txt', import.meta.url), 'utf-8').trim(); } catch { return '8100'; }
}

export default defineConfig({
  plugins: [react()],
  // Open http://localhost:5173 (not 127.0.0.1): the API accepts changes only from ADMIN_ORIGIN exactly.
  server: {host: 'localhost', port: 5173, strictPort: true, proxy: {'/api': `http://127.0.0.1:${apiPort()}`}},
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/i18n')) return 'i18n';
          if (id.includes('node_modules')) return 'vendor';
        },
      },
    },
  },
});
