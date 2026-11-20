import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir:'./tests',testMatch:['inherent-strain.spec.ts'],timeout:240000,workers:1,
  outputDir:'.work/inherent-playwright',
  use:{baseURL:process.env.SFMARC_TEST_URL||'http://127.0.0.1:8768',headless:true,channel:'msedge',viewport:{width:1440,height:960},screenshot:'off',trace:'off',video:'off'},
});
