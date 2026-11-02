import {test,expect} from '@playwright/test';

test('各模块的图标居中，侧栏和视窗适应宽度',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('mm');
  await page.locator('#file').setInputFiles('work/elemtype7_tension.dat');
  await expect(page.locator('#body-list .body-card')).not.toHaveCount(0);
  await page.locator('#tool-welding').click();
  await page.locator('#weld-files').setInputFiles('work/weld-reference/nodes.csv');
  await expect(page.locator('#weld-source-info')).toContainText('295');
  await page.locator('#weld-heat-save').click();
  await page.locator('#weld-start').selectOption('0');await page.locator('#weld-end').selectOption('1');
  await page.locator('#weld-add').click();await expect(page.locator('#weld-list .weld-card')).toHaveCount(1);
  const addIcon=await page.locator('#weld-add svg').evaluate(svg=>{
    const bounds=(svg as SVGGraphicsElement).getBBox();
    return {x:bounds.x+bounds.width/2,y:bounds.y+bounds.height/2};
  });
  expect(addIcon.x).toBeCloseTo(12,2);expect(addIcon.y).toBeCloseTo(12,2);
  for(const width of [1440,1100,960,768,430]) {
    await page.setViewportSize({width,height:960});
    for(const module of ['生成焊路','面载荷','模型查看']) {
      await page.getByRole('button',{name:module,exact:true}).click();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const alignment=await page.locator('button.icon-button').evaluateAll(buttons=>buttons.flatMap(button=>{
        const svg=button.querySelector('svg'),rect=button.getBoundingClientRect();
        if(!svg || !rect.width || !rect.height || getComputedStyle(button).visibility==='hidden') return [];
        const icon=svg.getBoundingClientRect();
        return [{name:button.getAttribute('aria-label'),dx:Math.abs((icon.left+icon.right-rect.left-rect.right)/2),dy:Math.abs((icon.top+icon.bottom-rect.top-rect.bottom)/2)}];
      }));
      expect(alignment.length).toBeGreaterThan(0);
      for(const icon of alignment) {expect(icon.dx,icon.name || '').toBeLessThan(0.75);expect(icon.dy,icon.name || '').toBeLessThan(0.75);}
    }
  }
  expect(errors).toEqual([]);
});
