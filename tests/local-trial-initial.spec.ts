import {test,expect} from '@playwright/test';

for(const first of ['model','local-trial'])test(`局部预览自动显示：先打开 ${first}`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.evaluate(async()=>{
    const url='/shared/common.js';const {Viewer}=await import(url);
    const original=Viewer.prototype.setLocalTrial;
    // 记录真实视窗实例，调用及数据保持原样。
    Viewer.prototype.setLocalTrial=function(data:unknown){(window as any).trialViewer=this;return original.call(this,data);};
  });
  if(first==='local-trial')await page.locator('#tool-local-trial').click();
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(process.env.SFMARC_TEST_DAT!);
  await expect(page.locator('#busy')).toBeHidden({timeout:120000});
  if(first==='model')await page.locator('#tool-local-trial').click();
  await expect(page.locator('#lt-status')).toContainText('单元',{timeout:120000});
  const state=()=>page.evaluate(()=>{
    const v=(window as any).trialViewer;
    return {paths:v.trialData.paths.length,pathScene:v.pathScene.visible,trial:v.trial.visible,
      surface:v.trialSurface.visible,meshes:v.trialSurface.children.length,bounds:v.trialBounds.isEmpty(),
      children:v.trial.children.length};
  });
  console.log(first,await state(),errors);
  expect(await state()).toMatchObject({paths:4,pathScene:true,trial:true,surface:true,bounds:false});
  expect((await state()).meshes).toBeGreaterThan(0);
  await page.locator('#tool-model').click();await page.locator('#tool-local-trial').click();
  expect(await state()).toMatchObject({paths:4,pathScene:true,trial:true,surface:true,bounds:false});
  expect(errors).toEqual([]);
});
