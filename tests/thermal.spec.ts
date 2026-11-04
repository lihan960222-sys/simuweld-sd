import {test,expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';

async function overlayPixels(page:Page) {
  return page.locator('#viewport canvas').evaluate(canvas=>new Promise<{paths:number,nodes:number}>(resolve=>requestAnimationFrame(()=>{
    const context=(canvas as HTMLCanvasElement).getContext('webgl2')!;
    const pixels=new Uint8Array(context.drawingBufferWidth*context.drawingBufferHeight*4);
    context.readPixels(0,0,context.drawingBufferWidth,context.drawingBufferHeight,context.RGBA,context.UNSIGNED_BYTE,pixels);
    let paths=0,nodes=0;
    for(let index=0;index<pixels.length;index+=4) {
      const red=pixels[index],green=pixels[index+1],blue=pixels[index+2];
      if(red>150 && red>green*1.25 && green>blue*1.5) paths++;
      if(green>red*2 && blue>red*2 && green>90 && blue>90) nodes++;
    }
    resolve({paths,nodes});
  })));
}

test('真实 DAT 预览、焊路开关、旧温度 CSV 拒绝和新模型清除',async({page})=>{
  const source=process.env.SFMARC_TEST_TC_DAT,temperatures=process.env.SFMARC_TEST_TC_CSV,material=process.env.SFMARC_TEST_TC_MATERIAL;
  test.skip(!source || !temperatures || !material,'需要实际 DAT、温度 CSV 与材料文件');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.getByRole('button',{name:'转换至热循环模式',exact:true}).click();
  await expect(page.locator('#tc-export')).toBeDisabled();
  await expect(page.locator('#tc-import-label')).toBeHidden();
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(source!);
  await expect(page.locator('#busy')).toBeHidden({timeout:90000});
  await expect(page.locator('#tc-source')).toContainText('4 条焊路');
  await expect(page.locator('#explode')).toBeDisabled();
  await expect(page.locator('#tc-path-list .tc-path-row')).toHaveCount(4);
  await expect.poll(async()=>(await overlayPixels(page)).nodes).toBeGreaterThan(200);
  await page.locator('#tc-nodes-visible').uncheck();
  await expect.poll(async()=>(await overlayPixels(page)).paths).toBeGreaterThan(200);
  await page.locator('#tc-paths-visible').uncheck();
  await expect.poll(async()=>(await overlayPixels(page)).paths).toBeLessThan(20);
  await page.locator('#tc-nodes-visible').check();
  await expect.poll(async()=>(await overlayPixels(page)).nodes).toBeGreaterThan(200);
  await page.locator('#tc-paths-visible').check();
  const exportDownload=page.waitForEvent('download');await page.locator('#tc-export').click();
  const exporter=await exportDownload;expect(exporter.suggestedFilename()).toBe('export_node_temperatures.py');
  expect(await readFile((await exporter.path())!,'utf8')).toContain('SolverNodeID');
  await expect(page.locator('#tc-import-label')).toBeVisible();
  await page.locator('#tc-temperatures').setInputFiles(temperatures!);
  await expect(page.locator('#busy')).toBeHidden({timeout:90000});
  await expect(page.locator('#message')).toContainText(/节点|组件/);
  await expect(page.locator('#tc-settings')).toBeHidden();
  await expect(page.locator('#tc-temperature-summary')).toBeHidden();
  const sourceSummary=await page.locator('#tc-source').textContent();
  await page.locator('#tool-welding').click();await page.locator('#tool-thermal-cycle').click();
  await expect(page.locator('#tc-source')).toHaveText(sourceSummary!);
  await expect(page.locator('#tc-settings')).toBeHidden();
  await expect(page.locator('#tc-export')).toBeEnabled();
  const chooser=page.waitForEvent('filechooser');await page.locator('#open-model').click();await chooser;
  await expect(page.locator('#model-dialog')).toBeHidden();
  await expect(page.locator('#tc-settings')).toBeHidden();
  await expect(page.locator('#tc-export')).toBeEnabled();
  expect(errors).toEqual([]);
});
