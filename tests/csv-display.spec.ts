import { test, expect, type Page } from '@playwright/test';

async function visiblePixels(page: Page) {
  return page.locator('#viewport canvas').evaluate(canvas=>new Promise<{selected:number;model:number;welded:number}>(resolve=>requestAnimationFrame(()=>{
    const gl=(canvas as HTMLCanvasElement).getContext('webgl2')!;
    const pixels=new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);
    gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    let selected=0,model=0,welded=0;
    for(let index=0;index<pixels.length;index+=4) {
      if(Math.abs(pixels[index]-229)<=2 && Math.abs(pixels[index+1]-155)<=2 && Math.abs(pixels[index+2]-38)<=2) selected++;
      if(pixels[index]<220 && pixels[index+1]>pixels[index] && pixels[index+2]>pixels[index+1]) model++;
      if(pixels[index]>180 && pixels[index]-pixels[index+1]>90 && pixels[index+2]>45) welded++;
    }
    resolve({selected,model,welded});
  })));
}

test('真实 DAT 和 CSV 叠加显示，左侧复选框控制路径',async({page})=>{
  test.skip(!process.env.SFMARC_TEST_PAIR_DAT || !process.env.SFMARC_TEST_PAIR_CSV,'需要指定真实 DAT 和 CSV');
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(process.env.SFMARC_TEST_PAIR_DAT!);
  await expect(page.locator('#busy')).toBeHidden({timeout:90000});
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await page.locator('#weld-files').setInputFiles(process.env.SFMARC_TEST_PAIR_CSV!);
  await expect(page.locator('#weld-source-info')).toContainText('295');
  await expect.poll(async()=>(await visiblePixels(page)).model).toBeGreaterThan(1000);
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(100);
  await page.locator('#weld-visible').uncheck();
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBe(0);
  await expect.poll(async()=>(await visiblePixels(page)).model).toBeGreaterThan(1000);
  await page.locator('#weld-visible').check();
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(100);
});

for(const withModel of [false,true]) test(withModel?'导入远处CSV保持模型大小':'直接导入CSV可以看到路径点',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  if(withModel) {
    await page.locator('#open-model').click();
    await page.locator('#import-units').selectOption('mm');
    await page.locator('#file').setInputFiles('work/elemtype7_tension.dat');
    await expect(page.locator('#busy')).toBeHidden();
    await expect(page.locator('#body-list .body-card')).not.toHaveCount(0);
  }
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  const before=await visiblePixels(page);
  await page.locator('#weld-files').setInputFiles(process.env.SFMARC_TEST_WELD_CSV!);
  await expect(page.locator('#weld-source-info')).toContainText('295');
  await expect(page.locator('#busy')).toBeHidden();
  if(withModel) {
    expect(before.model).toBeGreaterThan(1000);
    await expect.poll(async()=>(await visiblePixels(page)).model).toBe(before.model);
    await expect(page.locator('#weld-coordinate-warning')).toHaveCount(0);
    for(const view of ['xy','xz','yz']) {
      await page.locator(`[data-view=${view}]`).click();
      await expect.poll(async()=>(await visiblePixels(page)).model).toBeGreaterThan(1000);
    }
  } else await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(100);
  await expect(page.locator('#empty')).toBeHidden();
  if(withModel) await expect(page.locator('#body-list input[type=checkbox]').first()).toBeChecked();
  expect(errors).toEqual([]);
});

for(const model of [
  {file:'work/elemtype7_tension.dat',unit:'mm'},
  ...(process.env.SFMARC_TEST_DAT?[{file:process.env.SFMARC_TEST_DAT,unit:'m'}]:[]),
]) test(`${model.unit} 模型原始节点导出的CSV与模型同时显示，播放时只显示焊线`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#open-model').click();
  await page.locator('#import-units').selectOption(model.unit);
  const imported=page.waitForResponse(response=>response.url().endsWith('/api/model') && response.request().method()==='POST');
  await page.locator('#file').setInputFiles(model.file);
  const info=await (await imported).json();
  await expect(page.locator('#busy')).toBeHidden();
  const mesh=await (await page.request.get('/api/mesh',{headers:{'X-Session-Token':process.env.SFMARC_TEST_TOKEN!,'X-Model-Id':info.id}})).json();
  const points:number[][]=[];
  for(let index=0;index<mesh.points.length;index+=3) points.push(mesh.points.slice(index,index+3));
  const top=points.reduce((value,point)=>Math.max(value,point[2]),-Infinity);
  const bottom=points.reduce((value,point)=>Math.min(value,point[2]),Infinity);
  const candidates=points.filter(point=>point[2]>=top-(top-bottom)*.1).sort((first,second)=>first[1]-second[1] || first[0]-second[0]);
  const surface=candidates.filter((_,index)=>index%Math.max(1,Math.ceil(candidates.length/200))===0);
  expect(surface.length).toBeGreaterThan(2);
  const csv='Index,X,Y,Z\n'+surface.map((point,index)=>[index+1,...point.map(value=>value*(model.unit==='m'?1000:1))].join(',')).join('\n');
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await page.locator('#weld-files').setInputFiles({name:'model_surface_nodes.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
  await expect(page.locator('#weld-source-info')).toContainText(surface.length+' 个点');
  await expect(page.locator('#weld-coordinate-warning')).toHaveCount(0);
  for(const tool of ['生成焊路','模型查看','生成焊路']) {
    await page.getByRole('button',{name:tool,exact:true}).click();
    await expect.poll(async()=>(await visiblePixels(page)).model).toBeGreaterThan(1000);
    if(tool==='生成焊路') await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(100);
    else await expect.poll(async()=>(await visiblePixels(page)).selected).toBe(0);
  }
  for(const view of ['xy','xz','yz']) {
    await page.locator(`[data-view=${view}]`).click();
    await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(10);
    await expect.poll(async()=>(await visiblePixels(page)).model).toBeGreaterThan(1000);
  }
  await page.locator('[data-view=xy]').click();
  await page.locator('#weld-heat-save').click();
  await page.locator('#weld-add').click();
  await page.locator('#weld-rate').fill('-50');
  await page.locator('#weld-play').click();
  await expect(page.locator('#weld-play')).toHaveAccessibleName('暂停');
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBe(0);
  await page.locator('#weld-play').click();
  await page.locator('#weld-progress').fill('0.5');
  await expect.poll(async()=>(await visiblePixels(page)).welded).toBeGreaterThan(100);
  expect((await visiblePixels(page)).selected).toBe(0);
  await expect.poll(async()=>(await visiblePixels(page)).model).toBeGreaterThan(1000);
  await page.locator('#weld-progress').fill('1');
  await expect(page.locator('#weld-time')).toContainText('完成');
  expect((await visiblePixels(page)).selected).toBe(0);
  await page.locator('#weld-pick-start').click();
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(100);
  expect((await visiblePixels(page)).welded).toBe(0);
  await page.locator('#weld-progress').fill('0.5');
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBe(0);
  await page.locator('#weld-list .weld-card').getByRole('button',{name:/^编辑 /}).click();
  await expect.poll(async()=>(await visiblePixels(page)).selected).toBeGreaterThan(100);
  expect((await visiblePixels(page)).welded).toBe(0);
  expect(errors).toEqual([]);
});
