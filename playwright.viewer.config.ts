import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', testMatch: 'viewer.spec.ts', timeout: 120000, workers: 1,
  webServer: { command: 'npx vite --host 127.0.0.1 --port 5173 --strictPort', url: 'http://127.0.0.1:5173', reuseExistingServer: false },
  use: { baseURL: 'http://127.0.0.1:5173', headless: true, channel: 'msedge', viewport: {width: 1000, height: 760} },
});
