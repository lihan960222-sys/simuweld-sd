import { test, expect } from '@playwright/test';

test('焊路循环、停止时间、顺序播放与拖动排序',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await expect(page.locator('aside.left [data-tool=welding]')).toBeHidden();
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await expect(page.locator('aside.left #weld-files')).toHaveCount(1);
  await expect(page.locator('aside.right #weld-files')).toHaveCount(0);
  await expect(page.locator('aside.right[data-tool=welding] .panel-group > summary h2')).toHaveText(['热源','焊路','Simufact 设置']);
  expect(await page.locator('#weld-add').evaluate(element=>!!(element.compareDocumentPosition(document.getElementById('weld-list')!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await page.locator('#weld-rate').fill('-50');await expect(page.locator('#weld-rate-value')).toHaveText('0.1×');
  await page.locator('#weld-rate').fill('-25');await expect(page.locator('#weld-rate-value')).toHaveText('0.32×');
  await page.locator('#weld-rate').fill('50');await expect(page.locator('#weld-rate-value')).toHaveText('20×');
  await page.locator('#weld-rate').fill('0');await expect(page.locator('#weld-rate-value')).toHaveText('1×');
  await expect(page.locator('#weld-rate')).toHaveAttribute('aria-valuetext','1×');
  await page.locator('#weld-files').setInputFiles('work/weld-reference/nodes.csv');
  await expect(page.locator('#weld-source-info')).toContainText('个点');
  const selectedPixels=()=>page.locator('#viewport canvas').evaluate(canvas=>new Promise<number>(resolve=>requestAnimationFrame(()=>{
    const gl=(canvas as HTMLCanvasElement).getContext('webgl2')!,pixels=new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);
    gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    let count=0;for(let i=0;i<pixels.length;i+=4) if(pixels[i]===229&&pixels[i+1]===155&&pixels[i+2]===38) count++;
    resolve(count);
  })));
  await expect.poll(selectedPixels).toBeGreaterThan(100);
  await page.locator('#weld-visible').uncheck();await expect.poll(selectedPixels).toBe(0);
  await page.getByRole('button',{name:'模型查看',exact:true}).click();
  await expect(page.locator('aside.left [data-tool=welding]')).toBeHidden();
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await expect(page.locator('#weld-visible')).not.toBeChecked();await expect.poll(selectedPixels).toBe(0);
  await page.locator('#weld-visible').check();await expect.poll(selectedPixels).toBeGreaterThan(100);
  await page.locator('#weld-heat-save').click();
  for(const name of ['循环焊路 A','循环焊路 B']) {
    await page.locator('#weld-start').selectOption('0');
    await page.locator('#weld-end').selectOption('1');
    await page.locator('#weld-name').fill(name);
    await page.locator('#weld-pause').fill('0.4');
    await page.locator('#weld-pick-end').click();
    await page.locator('#weld-add').click();
    await expect(page.locator('#weld-pick-start')).toHaveAttribute('aria-pressed','true');
    await expect(page.locator('#weld-pick-end')).toHaveAttribute('aria-pressed','false');
  }
  const names=page.locator('.weld-entry-name');
  await expect(names).toHaveText(['1. 循环焊路 A','2. 循环焊路 B']);
  const point=await page.locator('#viewport canvas').evaluate(canvas=>new Promise<{x:number;y:number}|null>(resolve=>requestAnimationFrame(()=>{
    const gl=(canvas as HTMLCanvasElement).getContext('webgl2')!;
    const width=gl.drawingBufferWidth,height=gl.drawingBufferHeight,pixels=new Uint8Array(width*height*4);
    gl.readPixels(0,0,width,height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    for(let y=120;y<height-120;y++) for(let x=50;x<width-120;x++) {
      const index=(y*width+x)*4;
      if(pixels[index]===155 && pixels[index+1]===155 && pixels[index+2]===155) {
        resolve({x:(x+.5)/width*canvas.clientWidth,y:(height-y-.5)/height*canvas.clientHeight});return;
      }
    }
    resolve(null);
  })));
  expect(point).not.toBeNull();
  await page.locator('#viewport canvas').click({position:point!});
  await expect(page.locator('#weld-start')).not.toHaveValue('0');
  await expect(page.locator('#weld-end')).toHaveValue('1');
  await expect(page.locator('#weld-pick-end')).toHaveAttribute('aria-pressed','true');
  await names.first().click();
  await expect(page.locator('#weld-play')).toHaveAccessibleName('暂停');
  const samples=await page.locator('#weld-time').evaluate(element=>new Promise<string[]>(resolve=>{
    const samples:string[]=[];const started=performance.now();
    const sample=()=>{samples.push(element.textContent!);if(performance.now()-started>1800) resolve(samples);else requestAnimationFrame(sample);};sample();
  }));
  expect(samples.every(value=>value.includes('循环焊路 A'))).toBe(true);
  expect(samples.some(value=>value.includes('焊后停止'))).toBe(true);
  const elapsed=samples.map(value=>Number(value.split(' / ')[0]));
  expect(elapsed.some((value,index)=>index>0 && value<elapsed[index-1])).toBe(true);
  await page.locator('#weld-play').click();
  await page.locator('#weld-progress').fill('0.95');
  await expect(page.locator('#weld-time')).toContainText('焊后停止');
  const singleDuration=Number((await page.locator('#weld-time').innerText()).split(' / ')[1].split(' s')[0]);
  await page.locator('#weld-play').click();
  await expect(names.first()).toHaveAttribute('aria-pressed','true');
  await page.locator('#weld-rate').fill('-50');
  await page.locator('#weld-all').click();
  await page.locator('#weld-play').click();
  const totalDuration=Number((await page.locator('#weld-time').innerText()).split(' / ')[1].split(' s')[0]);
  expect(totalDuration).toBeCloseTo(singleDuration*2,2);
  await page.locator('#weld-progress').fill('0.75');
  await expect(page.locator('#weld-time')).toContainText('循环焊路 B');
  await page.locator('.weld-grip').first().dragTo(page.locator('.weld-card').last());
  await expect(names).toHaveText(['1. 循环焊路 B','2. 循环焊路 A']);
  await page.locator('.weld-grip').last().focus();
  await page.keyboard.press('ArrowUp');
  await expect(names).toHaveText(['1. 循环焊路 A','2. 循环焊路 B']);
  await expect(page.locator('.weld-grip').first()).toBeFocused();
  await names.first().click();
  await page.locator('#weld-pick-start').click();
  await expect(page.locator('#weld-play')).toHaveAccessibleName('播放');
  await expect(names.first()).toHaveAttribute('aria-pressed','false');
  await names.first().click();
  await page.getByRole('button',{name:'编辑 循环焊路 A',exact:true}).click();
  await expect(page.locator('#weld-play')).toHaveAccessibleName('播放');
  await expect(page.locator('#weld-add')).toHaveAccessibleName('保存焊路修改');
  await page.locator('#weld-cancel').click();
  await names.first().click();
  await page.locator('#heat-power_w').fill('40');
  await expect(page.locator('#weld-play')).toBeDisabled();
  await expect(names.first()).toBeDisabled();
  await page.locator('#weld-heat-save').click();
  await names.first().click();
  await page.getByRole('button',{name:'模型查看',exact:true}).click();
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await expect(page.locator('#weld-play')).toHaveAccessibleName('播放');
  await expect(names.first()).toHaveAttribute('aria-pressed','false');
  expect(errors).toEqual([]);
});
