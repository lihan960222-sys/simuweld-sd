import {test,expect,type Page} from '@playwright/test';

async function prepare(page:Page) {
  const source=process.env.SFMARC_TEST_TC_DAT,material=process.env.SFMARC_TEST_TC_MATERIAL;
  test.skip(!source || !material,'需要真实 DAT 与材料文件');
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#tool-thermal-cycle').click();
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  const response=page.waitForResponse(item=>item.url().endsWith('/api/thermal'));
  await page.locator('#file').setInputFiles(source!);
  const initial=await (await response).json();
  await expect(page.locator('#busy')).toBeHidden({timeout:90000});
  await page.locator('#tc-material').setInputFiles(material!);
  await expect(page.locator('#tc-export')).toBeEnabled();
  return initial;
}

async function orientation(page:Page) {
  return page.locator('.orientation [data-axis]').evaluateAll(groups=>groups.sort((a,b)=>a.getAttribute('data-axis')!.localeCompare(b.getAttribute('data-axis')!)).flatMap(group=>{
    const line=group.querySelector('line')!;return [Number(line.getAttribute('x2')),Number(line.getAttribute('y2'))];
  }));
}

test('连续修改采用最新分段和搜索半径，保留视角与显示开关',async({page})=>{
  test.slow();
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const initial=await prepare(page),pathId=String(initial.paths[0].id);
  const canvas=page.locator('#viewport canvas'),frame=await canvas.boundingBox();
  await page.getByRole('button',{name:'XZ 平面',exact:true}).click();
  await expect.poll(async()=>(await orientation(page)).map(value=>Math.round(value*1e6)/1e6)).toEqual([82,52,52,52,52,22]);
  const axesBefore=await orientation(page);
  await page.locator('#tc-display > summary').click();
  await page.locator('#tc-paths-visible').uncheck();await page.locator('#tc-nodes-visible').uncheck();
  const previousRequest=page.waitForRequest(request=>request.url().endsWith('/api/thermal/resample'));
  await page.locator('#tc-search').fill('0.4');await previousRequest;
  const finalResponse=page.waitForResponse(response=>{
    if(!response.url().endsWith('/api/thermal/resample'))return false;
    const parameters=response.request().postDataJSON().sampling;
    return parameters.search_mm===0.25 && parameters.divisions[pathId].mode==='length' && parameters.divisions[pathId].value===5;
  });
  const input=page.locator('#tc-path-list .tc-path-row').first().locator('input');
  await input.fill('3');await input.fill('5');await page.locator('#tc-search').fill('0.25');
  await expect(page.locator('#tc-export')).toBeDisabled();
  const response=await finalResponse;expect(response.ok()).toBe(true);
  const updated=await response.json();
  await expect(page.locator('#tc-export')).toBeEnabled({timeout:90000});
  await expect(input).toHaveValue('5');
  await expect(page.locator('#tc-search')).toHaveValue('0.25');
  await expect(page.locator('#tc-paths-visible')).not.toBeChecked();
  await expect(page.locator('#tc-nodes-visible')).not.toBeChecked();
  expect(await canvas.boundingBox()).toEqual(frame);
  const axesAfter=await orientation(page);
  axesAfter.forEach((value,index)=>expect(value).toBeCloseTo(axesBefore[index],5));
  await expect(page.locator('#tc-sampling-status')).toContainText(updated.nodes.length.toLocaleString()+' 个真实节点');
  const exported=page.waitForRequest(request=>request.url().endsWith('/api/thermal/export'));
  const download=page.waitForEvent('download');await page.locator('#tc-export').click();await download;
  expect((await exported).postDataJSON().sampling_id).toBe(updated.sampling_id);
  await expect(page.locator('#tc-temperatures')).toBeEnabled();

  const searched=page.waitForResponse(item=>item.url().endsWith('/api/thermal/resample'));
  await page.locator('#tc-search').fill('0.01');
  await expect(page.locator('#tc-temperatures')).toBeDisabled();
  const restricted=await (await searched).json();
  expect(restricted.nodes.length).toBeLessThan(updated.nodes.length);
  expect(restricted.exported).toBe(false);
  await expect(page.locator('#tc-export')).toBeDisabled();
  await expect(page.locator('#tc-temperatures')).toBeDisabled();
  await expect(page.locator('#tc-sampling-status')).toContainText('没有节点');
  expect(errors).toEqual([]);
});

test('重新导入真实模型清除材料和导出状态，使用新模型参数',async({page})=>{
  test.slow();
  const initial=await prepare(page);
  const downloaded=page.waitForEvent('download');await page.locator('#tc-export').click();await downloaded;
  await expect(page.locator('#tc-temperatures')).toBeEnabled();
  const row=page.locator('#tc-path-list .tc-path-row').first();
  const changed=page.waitForResponse(response=>response.url().endsWith('/api/thermal/resample'));
  await row.locator('input').fill('5');await changed;
  await expect(row.locator('input')).toHaveValue('5');
  await page.locator('#open-model').click();
  const prepared=page.waitForResponse(response=>response.url().endsWith('/api/thermal'));
  await page.locator('#file').setInputFiles(process.env.SFMARC_TEST_TC_DAT!);
  const replacement=await (await prepared).json();
  await expect(page.locator('#busy')).toBeHidden({timeout:90000});
  expect(replacement.nodes.length).toBe(initial.nodes.length);
  await expect(row.locator('input')).toHaveValue('2');
  await expect(page.locator('#tc-melting')).toHaveValue('');
  await expect(page.locator('#tc-export')).toBeDisabled();
  await expect(page.locator('#tc-temperatures')).toBeDisabled();
  await expect(page.locator('#tc-create')).toBeDisabled();
  await expect(page.locator('#tc-temperature-summary')).toContainText('尚未导入');
});
