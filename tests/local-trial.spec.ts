import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test('未导入模型时也可下载独立结果提取脚本',async({page},testInfo)=>{
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#tool-local-trial').click();
  await page.locator('#lt-results-process').fill('Local-试算');
  const downloaded=page.waitForEvent('download');
  await page.locator('#lt-results-export').click();
  const download=await downloaded;
  expect(download.suggestedFilename()).toBe('export_local_results.py');
  const path=testInfo.outputPath('export_local_results.py');await download.saveAs(path);
  const text=await readFile(path,'utf8');
  expect(text).toContain("PROCESS_NAME = 'Local-试算'");
  expect(text).toContain('element_values_at="integration-points"');
  expect(text).toContain('increments=[number]');
  expect(text).toContain('"PS", "TSTRN", "ESTRN"');
  await expect(page.locator('#message')).toBeHidden();
});

test('真实 DAT 的局部试算裁剪布局与区间验证',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await expect(page.locator('#tools button svg')).toHaveCount(5);
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(process.env.SFMARC_TEST_DAT!);
  await expect(page.locator('#busy')).toBeHidden({timeout:120000});
  await page.locator('#tool-local-trial').click();
  await expect(page.locator('#lt-status')).toContainText('单元',{timeout:120000});
  const axes=await page.locator('.lt-axis-group').evaluateAll(groups=>groups.map(group=>Array.from(group.querySelectorAll('input[type=number]')).map(input=>input.id)));
  expect(axes).toEqual([['lt-box-min-0','lt-box-max-0'],['lt-box-min-1','lt-box-max-1'],['lt-box-min-2','lt-box-max-2']]);
  const range=await page.evaluate(()=>{
    const value=(id:string)=>(document.getElementById(id) as HTMLInputElement).valueAsNumber;
    return {min:Number((document.getElementById('lt-range-min-2') as HTMLInputElement).min),max:Number((document.getElementById('lt-range-max-2') as HTMLInputElement).max),low:value('lt-box-min-2'),high:value('lt-box-max-2')};
  });
  expect(range.low-range.min).toBeGreaterThanOrEqual(.999999);
  expect(range.max-range.high).toBeGreaterThanOrEqual(.999999);
  await expect(page.locator('#lt-wrap')).toBeHidden();
  await expect(page.locator('#lt-trim-track')).toHaveCount(0);
  await expect(page.locator('#lt-selected-path')).toHaveText('点击焊路节点选择范围。');
  await expect(page.locator('#lt-export').locator('xpath=..').locator('#lt-matches')).toHaveCount(1);
  const response=page.waitForResponse(response=>response.url().endsWith('/api/local-trial/preview')&&response.request().postDataJSON().start_mm===200);
  await page.locator('#lt-end').fill('5');await page.locator('#lt-start').fill('200');
  const preview=await response;
  expect(preview.request().postDataJSON()).toMatchObject({wrap:false,trim_start_mm:0,trim_end_mm:0});
  expect(preview.status()).toBe(400);
  const valid=page.waitForResponse(response=>response.url().endsWith('/api/local-trial/preview')&&response.request().postDataJSON().start_mm===2);
  await page.locator('#lt-start').fill('2');
  expect((await valid).ok()).toBe(true);
  expect(errors).toEqual([]);
});
