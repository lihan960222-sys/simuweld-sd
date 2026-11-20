import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir:'./tests',testMatch:['local-shell-workflow.spec.ts'],timeout:180000,workers:1,
  use:{baseURL:'http://127.0.0.1:8767',headless:true,channel:'msedge',viewport:{width:1440,height:960}},
});
