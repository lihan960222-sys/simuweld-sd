import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', testMatch: 'browser.spec.ts', timeout: 180000, workers: 1,
  use: { baseURL: process.env.SFMARC_TEST_URL || 'http://127.0.0.1:8765', headless: true, channel: 'msedge', viewport: {width: 1440, height: 960} },
});
