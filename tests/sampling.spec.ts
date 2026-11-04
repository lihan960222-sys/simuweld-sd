import {test,expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';

async function pixels(page:Page) {
  return page.locator('#viewport canvas').evaluate(canvas=>new Promise<{nodes:number;x:number;y:number;hash:number;bounds:number[]}>(resolve=>requestAnimationFrame(()=>{
    const context=(canvas as HTMLCanvasElement).getContext('webgl2')!,width=context.drawingBufferWidth,height=context.drawingBufferHeight;
    const values=new Uint8Array(width*height*4);context.readPixels(0,0,width,height,context.RGBA,context.UNSIGNED_BYTE,values);
    const rect=canvas.getBoundingClientRect();let nodes=0,x=0,y=0,hash=2166136261;const bounds=[width,height,0,0];
    for(let index=0;index<values.length;index+=4){
      const r=values[index],g=values[index+1],b=values[index+2];hash=Math.imul(hash^r,16777619);hash=Math.imul(hash^g,16777619);hash=Math.imul(hash^b,16777619);
      if(Math.abs(r-237)+Math.abs(g-242)+Math.abs(b-246)>12){const px=index/4%width,py=Math.floor(index/4/width);bounds[0]=Math.min(bounds[0],px);bounds[1]=Math.min(bounds[1],py);bounds[2]=Math.max(bounds[2],px);bounds[3]=Math.max(bounds[3],py);}
      if(g>r*2 && b>r*2 && g>90 && b>90){nodes++;if(nodes===1){x=rect.left+(index/4%width+.5)*rect.width/width;y=rect.top+(height-1-Math.floor(index/4/width)+.5)*rect.height/height;}}
    }
    resolve({nodes,x,y,hash,bounds});
  })));
}

test('真实 DAT 采样设置、连续输入合并与当前预览导出',async({page})=>{
  const source=process.env.SFMARC_TEST_TC_DAT;
  test.skip(!source,'需要真实 C0002.dat');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#tool-thermal-cycle').click();
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  const initialResponse=page.waitForResponse(response=>response.url().endsWith('/api/thermal') && response.ok());
  await page.locator('#file').setInputFiles(source!);
  const initial=await (await initialResponse).json();
  await expect(page.locator('#tc-export')).toBeEnabled({timeout:90000});
  expect(initial.nodes.length).toBeGreaterThan(0);
  await expect(page.locator('#tc-path-list .tc-path-row')).toHaveCount(4);
  await expect(page.locator('#tc-spacing')).toHaveValue('2');
  await expect(page.locator('#tc-search')).toHaveValue('0.25');
  await expect.poll(async()=>(await pixels(page)).nodes).toBeGreaterThan(0);
  const point=await pixels(page);await page.mouse.move(point.x,point.y);
  await expect(page.locator('.tc-node-tooltip')).toContainText(/节点 \d+/);
  const box=(await page.locator('#viewport canvas').boundingBox())!;
  const original=(await pixels(page)).hash;
  await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.down();
  await page.mouse.move(box.x+box.width*.6,box.y+box.height*.58,{steps:8});await page.mouse.up();
  await expect.poll(async()=>(await pixels(page)).hash).not.toBe(original);
  await page.mouse.wheel(0,-200);
  await page.locator('#tc-paths-visible').uncheck();await page.locator('#tc-nodes-visible').uncheck();
  await expect.poll(async()=>(await pixels(page)).nodes).toBe(0);
  await expect.poll(async()=>{const first=(await pixels(page)).hash;await page.waitForTimeout(150);return first===(await pixels(page)).hash;}).toBe(true);
  const viewBefore=(await pixels(page)).bounds;
  const frameBefore=await page.locator('#viewport canvas').boundingBox();
  const axesBefore=await page.locator('.orientation line').evaluateAll(lines=>lines.flatMap(line=>[Number(line.getAttribute('x2')),Number(line.getAttribute('y2'))]));
  let requests=0;page.on('request',request=>{if(request.url().endsWith('/api/thermal/resample'))requests++;});
  const updatedResponse=page.waitForResponse(response=>response.url().endsWith('/api/thermal/resample') && response.ok());
  await page.locator('#tc-spacing').fill('3');
  await expect(page.locator('#tc-export')).toBeDisabled();
  await page.locator('#tc-spacing').fill('4');
  await page.locator('#tc-offsets').fill('0, -0.5, 0.5');
  await page.locator('#tc-surface').selectOption('upper');
  const updated=await (await updatedResponse).json();
  await expect(page.locator('#tc-export')).toBeEnabled({timeout:90000});
  expect(requests).toBe(1);expect(updated.sampling.spacing_mm).toBe(4);expect(updated.sampling.surface).toBe('upper');
  expect(updated.nodes).not.toEqual(initial.nodes);
  expect(await page.locator('#viewport canvas').boundingBox()).toEqual(frameBefore);
  const axesAfter=await page.locator('.orientation line').evaluateAll(lines=>lines.flatMap(line=>[Number(line.getAttribute('x2')),Number(line.getAttribute('y2'))]));
  axesAfter.forEach((value,index)=>expect(value).toBeCloseTo(axesBefore[index],5));
  await expect.poll(async()=>(await pixels(page)).nodes).toBe(0);
  const boundsAfter=(await pixels(page)).bounds;boundsAfter.forEach((value,index)=>expect(Math.abs(value-viewBefore[index])).toBeLessThanOrEqual(1));
  await page.locator('#tc-nodes-visible').check();
  const smallNodes=(await pixels(page)).nodes;
  await page.locator('#tc-node-size').fill('12');
  await expect.poll(async()=>(await pixels(page)).nodes).toBeGreaterThan(smallNodes);
  await expect(page.locator('#tc-sampling-status')).toContainText(`${updated.nodes.length} 个真实节点`);
  const download=page.waitForEvent('download');await page.locator('#tc-export').click();
  const code=await readFile((await (await download).path())!,'utf8');
  for(const node of updated.nodes)expect(code).toMatch(new RegExp(`\\b${node.node_id}\\b`));
  await expect(page.locator('#tc-import-label')).toBeVisible();
  const searchResponse=page.waitForResponse(response=>response.url().endsWith('/api/thermal/resample') && response.ok());
  await page.locator('#tc-search').fill('0.01');
  await expect(page.locator('#tc-import-label')).toBeHidden();
  const searched=await (await searchResponse).json();
  await expect(page.locator('#tc-sampling-status')).toContainText('预览已更新',{timeout:90000});
  expect(searched.missing.length).toBeGreaterThan(updated.missing.length);
  await page.locator('#tc-paths-visible').uncheck();await page.locator('#tc-nodes-visible').uncheck();
  await page.locator('#tc-nodes-visible').check();
  await page.locator('#tc-paths-visible').check();
  expect(errors).toEqual([]);
});

test('真实采样计算期间重新导入模型，新模型参数保持有效',async({page})=>{
  const source=process.env.SFMARC_TEST_TC_DAT;test.skip(!source,'需要真实 C0002.dat');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#tool-thermal-cycle').click();
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(source!);
  await expect(page.locator('#tc-export')).toBeEnabled({timeout:90000});
  const oldRequestReady=page.waitForRequest(request=>request.url().endsWith('/api/thermal/resample'));
  await page.locator('#tc-spacing').fill('0.15');
  await page.locator('#tc-offsets').fill('-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1');
  const oldRequest=await oldRequestReady;
  let oldFinished=false;
  const oldResponse=page.waitForResponse(response=>response.request()===oldRequest).then(response=>{oldFinished=true;return response;});
  await page.locator('#open-model').click();
  await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(source!);
  await expect(page.locator('#tc-export')).toBeEnabled({timeout:90000});
  expect(oldFinished).toBe(false);
  await expect(page.locator('#tc-spacing')).toHaveValue('2');
  const newResponseReady=page.waitForResponse(response=>response.url().endsWith('/api/thermal/resample') && response.request()!==oldRequest);
  await page.locator('#tc-spacing').fill('5');await page.locator('#tc-offsets').fill('0, -0.5, 0.5');
  const newResponse=await newResponseReady;expect(newResponse.ok()).toBe(true);
  const updated=await newResponse.json();expect(updated.sampling.spacing_mm).toBe(5);
  await expect(page.locator('#tc-export')).toBeEnabled({timeout:90000});
  await expect(page.locator('#tc-sampling-status')).toContainText(`${updated.nodes.length} 个真实节点`);
  expect((await oldResponse).status()).toBe(409);
  await expect(page.locator('#tc-spacing')).toHaveValue('5');
  await expect(page.locator('#tc-offsets')).toHaveValue('0, -0.5, 0.5');
  await expect(page.locator('#tc-sampling-status')).toContainText(`${updated.nodes.length} 个真实节点`);
  await expect(page.locator('#tc-export')).toBeEnabled();
  expect(errors).toEqual([]);
});
