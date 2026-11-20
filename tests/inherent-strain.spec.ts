import {test,expect} from '@playwright/test';

test('DAT 自动准备、二维显示、可选参数、真实计算和停止',async({page},testInfo)=>{
  test.skip(!process.env.SFMARC_TEST_INHERENT_DAT||!process.env.SFMARC_TEST_MATERIAL,'需要配置小型固有应变模型与材料');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{
    const observer=new EventTarget();(window as any).__THREE_DEVTOOLS__=observer;
    observer.addEventListener('observe',(event:any)=>{
      const object=event.detail;if(object?.isScene)object.onBeforeRender=(renderer:any,scene:any,camera:any)=>{
        if(scene.getObjectByName('inherent-temperature'))(window as any).temperatureScene={scene,camera,renderer};
      };
    });
  });
  const panel=page.locator('.inherent-panel');
  const scene=()=>page.evaluate(()=>{
    const {scene,camera}=(window as any).temperatureScene;
    const group=scene.getObjectByName('inherent-temperature');
    return {orthographic:!!camera.isOrthographicCamera,quaternion:camera.quaternion.toArray(),
      model:scene.getObjectByName('model-entities').visible,
      layers:group.children.filter((item:any)=>item.isMesh).map((item:any)=>({visible:item.visible,grid:item.children[0].visible})),
      paths:group.children.filter((item:any)=>item.userData.path!==undefined).map((item:any)=>item.visible),
      fixtures:group.children.filter((item:any)=>item.userData.fixture!==undefined).map((item:any)=>item.visible)};
  });
  await page.goto('/#token='+(process.env.SFMARC_TEST_TOKEN||'inherent-browser-check'));
  await page.locator('#tool-inherent-strain').click();
  await expect(page.locator('#is-start-reason')).toContainText('请导入');
  const dat=process.env.SFMARC_TEST_INHERENT_DAT!;
  await page.locator('#file').setInputFiles(dat);
  await expect(panel).toHaveAttribute('data-status','ready',{timeout:60000});
  await expect(page.locator('#is-start-reason')).toContainText('XMT');
  await expect(page.locator('#is-clocks')).toContainText('计算尚未开始');
  await expect(page.locator('#is-advanced')).not.toHaveAttribute('open','');
  await expect(page.locator('#is-spacing_mm')).toHaveValue('0.5');
  await expect(page.locator('#is-end_s')).toHaveValue('0.01');
  await expect(page.locator('#is-source-end_s')).toContainText('DAT');
  await expect(page.locator('#projection')).toBeHidden();
  await expect.poll(async()=> (await scene()).layers.length).toBe(2);
  let state=await scene();expect(state.orthographic).toBe(true);expect(state.model).toBe(false);
  expect(state.layers.map(item=>item.visible)).toEqual([false,true]);expect(state.fixtures.length).toBeGreaterThan(0);
  await page.locator('#is-layer').selectOption('0');
  expect((await scene()).layers.map(item=>item.visible)).toEqual([true,false]);
  await page.locator('#is-grid').uncheck();expect((await scene()).layers.every(item=>!item.grid)).toBe(true);
  await page.locator('#is-grid').check();
  await page.locator('#is-show-paths').uncheck();expect((await scene()).paths.every(value=>!value)).toBe(true);
  await page.locator('#is-show-paths').check();
  await page.locator('#is-show-fixtures').uncheck();expect((await scene()).fixtures.every(value=>!value)).toBe(true);
  await page.locator('#is-show-fixtures').check();
  const camera=(await scene()).quaternion,canvas=page.locator('canvas').first(),box=(await canvas.boundingBox())!;
  await page.mouse.move(box.x+box.width*.4,box.y+box.height*.5);await page.mouse.down();
  await page.mouse.move(box.x+box.width*.6,box.y+box.height*.6,{steps:5});await page.mouse.up();
  expect((await scene()).quaternion).toEqual(camera);
  await page.locator('#is-material').setInputFiles(process.env.SFMARC_TEST_MATERIAL!);
  await expect(page.locator('#is-start')).toBeEnabled({timeout:30000});
  await page.locator('#is-advanced > summary').click();
  await page.locator('#is-spacing_mm').fill('1');await page.locator('#is-weld_spacing_mm').fill('0.5');
  await page.locator('#is-apply').click();await expect(page.locator('#is-start')).toBeEnabled({timeout:60000});
  await page.locator('#is-defaults').click();await page.locator('#is-apply').click();
  await expect(page.locator('#is-start')).toBeEnabled({timeout:60000});
  await expect(page.locator('#is-spacing_mm')).toHaveValue('0.5');
  await page.locator('#is-end_s').fill('0.002');await page.locator('#is-record_interval_s').fill('0.001');
  await page.locator('#is-apply').click();
  const request=page.waitForRequest(request=>request.url().includes('/inherent-strain/start'));
  const mechanics=page.waitForResponse(response=>response.url().includes('/inherent-strain/mechanics?'),{timeout:120000});
  await page.locator('#is-start').click();
  const values=(await request).postDataJSON();expect(values.end_s).toBe(.002);expect(values.record_interval_s).toBe(.001);
  expect(values).not.toHaveProperty('ambient_c');expect(values).not.toHaveProperty('spacing_mm');
  const response=await mechanics;expect(response.status()).toBe(200);const result=await response.json();
  expect(result.released).toBe(false);expect(result.normal_displacement_mm.every(Number.isFinite)).toBe(true);
  await expect(page.locator('#is-mechanical-view')).toBeEnabled();
  await expect(page.locator('#is-clocks')).toContainText('计算耗时');
  await expect(page.locator('#is-frame-time')).toContainText('0.002');
  await expect(page.locator('#is-mechanical-status')).toContainText('Cover 保持约束');
  await page.locator('#is-min').fill('25');await page.locator('#is-max').fill('20');
  await expect(page.locator('#is-legend-error')).not.toBeEmpty();
  await page.locator('#is-max').fill('1500');await expect(page.locator('#is-legend-error')).toBeEmpty();
  for(const mode of ['displacement','peeq','plastic-x','plastic-y','temperature']){
    await page.locator('#is-mechanical-view').selectOption(mode);await expect(page.locator('#is-legend-error')).toBeEmpty();
  }
  expect((await scene()).orthographic).toBe(true);
  await page.locator('#is-view').selectOption('peak');await page.locator('#is-view').selectOption('frame');
  await page.locator('#is-frame').fill('0');await expect(page.locator('#is-frame-time')).toContainText('0 s');
  const download=page.waitForEvent('download');await page.locator('#is-export').click();
  await (await download).saveAs(testInfo.outputPath('browser-result.zip'));
  await page.locator('#is-end_s').fill('100');await page.locator('#is-apply').click();await page.locator('#is-start').click();
  await expect(page.locator('#is-cancel')).toBeEnabled();await page.locator('#is-cancel').click();
  await expect(panel).toHaveAttribute('data-status','cancelled',{timeout:60000});
  await expect(page.locator('#is-status')).toContainText('计算已停止');
  await expect(page.locator('#is-start')).toBeEnabled();
  await page.locator('#tool-local-trial').click();await expect(page.locator('#projection')).toBeVisible();
  await page.locator('#tool-inherent-strain').click();await page.locator('#file').setInputFiles(dat);
  await expect(panel).toHaveAttribute('data-status','ready',{timeout:60000});
  await expect(page.locator('#is-start-reason')).toContainText('XMT');
  await expect(page.locator('#is-export')).toBeDisabled();
  if(!await page.locator('#is-advanced').evaluate(element=>(element as HTMLDetailsElement).open))await page.locator('#is-advanced > summary').click();
  await page.locator('#is-weld_spacing_mm').fill('1');await page.locator('#is-apply').click();
  await expect(panel).toHaveAttribute('data-status','failed',{timeout:60000});
  await expect(page.locator('#is-weld_spacing_mm')).toBeEnabled();
  await page.locator('#is-weld_spacing_mm').fill('0.25');await page.locator('#is-apply').click();
  await expect(panel).toHaveAttribute('data-status','ready',{timeout:60000});
  await expect(page.locator('#is-spacing_mm')).toHaveValue('0.5');expect(errors).toEqual([]);
});
