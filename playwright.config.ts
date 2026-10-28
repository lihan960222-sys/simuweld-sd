import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', testMatch: ['browser.spec.ts','heat.spec.ts','csv-display.spec.ts','workspace.spec.ts','weld-interaction.spec.ts','load-interaction.spec.ts','weld-rate.spec.ts'], timeout: 180000, workers: 1,
  use: { baseURL: process.env.SFMARC_TEST_URL || 'http://127.0.0.1:8765', headless: true, channel: 'msedge', viewport: {width: 1440, height: 960} },
});
