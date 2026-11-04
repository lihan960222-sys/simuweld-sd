import {spawn, execFileSync} from 'node:child_process';
import {resolve, dirname} from 'node:path';
import {readFileSync, existsSync, writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';

const executable=process.env.SFMARC_DESKTOP_EXE;
assert.ok(executable, '需要指定桌面 EXE');
const python=resolve('../../work/venv/Scripts/python.exe');
const port=process.env.SFMARC_DESKTOP_DEBUG_PORT || '19387';
const app=spawn(executable, [], {env:{...process.env, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port} --proxy-server=http://127.0.0.1:9 --proxy-bypass-list=127.0.0.1;localhost`}, stdio:'ignore'});
const exited=new Promise(resolve=>app.once('exit',(code)=>resolve(code)));
const native=(action,...args)=>execFileSync(python,['tests/native_dialog.py',String(app.pid),action,...args],{encoding:'utf8'});
let browser;
try {
  const deadline=Date.now()+30000;
  while(true) {
    if(app.exitCode!==null)throw new Error(`桌面程序退出：${app.exitCode}`);
    try {if((await fetch(`http://127.0.0.1:${port}/json/version`)).ok)break;} catch(error) {if(Date.now()>deadline)throw error;}
    if(Date.now()>deadline)throw new Error('WebView2 启动超时');
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const context=browser.contexts()[0];
  const page=context.pages()[0] || await context.waitForEvent('page');
  await page.waitForSelector('#open-model');
  const runtimePaths=JSON.parse(execFileSync('powershell.exe',['-NoProfile','-Command',`ConvertTo-Json -InputObject @(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${app.pid} AND Name = 'msedgewebview2.exe'" | Select-Object -ExpandProperty ExecutablePath)`],{encoding:'utf8'}));
  assert.ok(runtimePaths.length>0);
  const expectedRuntime=resolve(dirname(executable),'_internal/webview2/msedgewebview2.exe');
  for(const runtimePath of runtimePaths)assert.equal(runtimePath.toLowerCase(),expectedRuntime.toLowerCase());
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const address=new URL(page.url());
  await page.locator('#import-units').selectOption('mm');
  await page.locator('#open-model').click();
  native('file',resolve('work/geometry-tests/two_blocks.inp'));
  await page.waitForSelector('.body-card');
  assert.equal(await page.locator('.body-card').count(),2);
  assert.ok(await page.locator('#viewport canvas').evaluate(canvas=>!!canvas.getContext('webgl2')));
  await page.locator('#tool-welding').click();
  await page.locator('#weld-files').setInputFiles('work/weld-reference/nodes.csv');
  await page.locator('#weld-heat-save').click();
  await page.locator('#weld-start').selectOption('0');
  await page.locator('#weld-end').selectOption('1');
  await page.locator('#weld-add').click();
  const output=resolve(`work/desktop-download-${Date.now()}.py`);
  await page.locator('#export').click();
  native('file',output);
  const downloadDeadline=Date.now()+15000;
  while(!existsSync(output) && Date.now()<downloadDeadline)await new Promise(resolve=>setTimeout(resolve,100));
  assert.ok(readFileSync(output,'utf8').length>100);
  native('close');
  native('cancel');
  assert.equal(app.exitCode,null);
  assert.equal(await page.locator('.body-card').count(),2);
  native('close');
  native('accept');
  const code=await Promise.race([exited,new Promise((_,reject)=>setTimeout(()=>reject(new Error('桌面程序未退出')),15000))]);
  assert.equal(code,0);
  let reachable=true;
  try {await fetch(address.origin+'/');} catch {reachable=false;}
  assert.equal(reachable,false);
  assert.deepEqual(errors,[]);
  writeFileSync('work/desktop-smoke-result.json',JSON.stringify({bundledRuntime:expectedRuntime,externalProxyUnreachable:true,nativeFileImport:true,webgl:true,download:true,closeCancel:true,exitCode:code,serviceStopped:true},null,2));
  console.log('桌面窗口、原生文件导入、WebGL、脚本保存、关闭确认和服务退出检查通过。');
} catch(error) {
  console.error(native('list'));
  if(browser)console.error(await browser.contexts()[0].pages()[0].locator('#message').textContent());
  throw error;
} finally {
  if(browser)await browser.close();
  if(app.exitCode===null)app.kill();
}
