import {test,expect} from '@playwright/test';

test('真实 DAT 读取显示阶段与真实计数，完成后结束查询',async({page})=>{
  const source=process.env.SFMARC_TEST_TC_DAT;test.skip(!source,'需要真实 C0002.dat');
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  const observed:{text:string;value:string|null;max:string|null}[]=[];
  await page.exposeFunction('recordImportProgress',(entry:{text:string;value:string|null;max:string|null})=>observed.push(entry));
  await page.evaluate(()=>{
    const progress=document.getElementById('import-progress')!;
    const observer=new MutationObserver(()=>{
      if(progress.hidden)return;
      void (window as unknown as {recordImportProgress:(entry:object)=>Promise<void>}).recordImportProgress({text:document.getElementById('busy-text')!.textContent,value:progress.getAttribute('value'),max:progress.getAttribute('max')});
    });
    observer.observe(progress,{attributes:true});
  });
  let reads=0;const states:{active:boolean;completed:number|null;total:number|null}[]=[];
  page.on('response',async response=>{if(response.url().endsWith('/api/import-progress')){reads++;states.push(await response.json());}});
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('m');
  await page.locator('#file').setInputFiles(source!);
  await expect(page.locator('#import-progress')).toBeVisible();
  await expect(page.locator('#import-progress')).not.toHaveAttribute('value');
  await expect(page.locator('#busy')).toBeHidden({timeout:90000});
  await expect(page.locator('#import-progress')).toBeHidden();
  expect(observed.some(item=>item.value===null)).toBe(true);
  expect(observed.some(item=>item.value!==null && item.text.includes('已完成'))).toBe(true);
  for(const item of observed.filter(item=>item.value!==null)){
    expect(Number(item.value)).toBeGreaterThanOrEqual(0);expect(Number(item.value)).toBeLessThanOrEqual(Number(item.max));
    expect(states.some(state=>state.completed===Number(item.value) && state.total===Number(item.max))).toBe(true);
  }
  expect(states.some(item=>item.active && item.completed!==null && item.total!==null)).toBe(true);
  await page.waitForTimeout(500);const finishedReads=reads;await page.waitForTimeout(750);expect(reads).toBe(finishedReads);
});
