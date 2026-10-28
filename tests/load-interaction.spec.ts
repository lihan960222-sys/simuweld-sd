import {test,expect} from '@playwright/test';

test('载荷名称显示、多个勾选与折叠分组保留数据',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#open-model').click();await page.locator('#import-units').selectOption('mm');
  await page.locator('#file').setInputFiles('work/elemtype7_tension.dat');
  await expect(page.locator('#busy')).toBeHidden();
  await page.locator('#tool-surface-load').click();await page.locator('[data-view=xy]').click();
  await page.locator('#viewport canvas').click();
  await expect.poll(async()=>Number(await page.locator('#selected-count').textContent())).toBeGreaterThan(0);
  await expect(page.locator('#force-info')).toContainText('合力');
  await expect(page.locator('#preview,#all-bodies')).toHaveCount(0);
  await page.locator('#load-name').fill('载荷 A');await page.locator('#save-load').click();
  await expect(page.locator('.load-select')).toHaveCount(1);
  await page.locator('#magnitude').fill('2');
  const response=page.waitForResponse(response=>response.url().endsWith('/api/preview') && response.request().postDataJSON().magnitude===2);
  await page.locator('#magnitude').press('Tab');expect((await(await response).json()).pressure).toBe(2);
  await page.locator('#mode').selectOption('manual');
  await page.locator('#dx').fill('3');await page.locator('#dy').fill('0');await page.locator('#dz').fill('-4');
  const directionResponse=page.waitForResponse(response=>response.url().endsWith('/api/preview') && response.request().postDataJSON().direction.join(',')==='3,0,-4');
  await page.locator('#dz').press('Tab');await directionResponse;
  await expect(page.locator('#dx')).toHaveValue('3');await expect(page.locator('#dz')).toHaveValue('-4');
  await expect(page.locator('aside[data-tool=surface-load]')).not.toHaveAttribute('inert','');
  await page.locator('#load-name').fill('载荷 B');await page.locator('#save-load').click();
  await expect(page.locator('.load-select')).toHaveText(['载荷 A','载荷 B']);
  await page.locator('.load-select').first().click();
  await expect(page.getByRole('checkbox',{name:'显示 载荷 A',exact:true})).toBeChecked();
  await expect(page.getByRole('checkbox',{name:'显示 载荷 B',exact:true})).not.toBeChecked();
  const forceA=await page.locator('#force-info').textContent();
  await page.locator('.load-select').last().click();
  expect(await page.locator('#force-info').textContent()).not.toBe(forceA);
  await page.getByRole('checkbox',{name:'显示 载荷 A',exact:true}).check();
  await expect(page.locator('#force-info')).toHaveText('已显示 2 项载荷');
  await expect(page.locator('#show-all-loads')).toHaveAttribute('aria-pressed','true');
  await page.locator('#show-all-loads').click();
  await expect(page.locator('#selected-count')).toHaveText('0');
  await page.locator('#show-all-loads').click();
  await expect(page.locator('[data-load-visible]:checked')).toHaveCount(2);
  await page.locator('#tool-model').click();await page.locator('#tool-surface-load').click();
  await expect(page.locator('[data-load-visible]:checked')).toHaveCount(2);
  const panel=page.locator('aside[data-tool=surface-load]');
  for(const summary of await panel.locator('.panel-group > summary').all()) {
    await summary.click();expect(await summary.evaluate(element=>(element.parentElement as HTMLDetailsElement).open)).toBe(false);
    await summary.click();expect(await summary.evaluate(element=>(element.parentElement as HTMLDetailsElement).open)).toBe(true);
  }
  await expect(page.locator('.load-select')).toHaveCount(2);
  await expect(panel.locator('button.icon-button')).not.toHaveCount(0);
  for(const button of await panel.locator('button.icon-button').all()) {
    await expect(button).toHaveText('');await expect(button).toHaveAttribute('aria-label',/.+/);
  }
  await page.getByRole('button',{name:'编辑 载荷 A',exact:true}).click();
  await expect(page.locator('#magnitude')).toHaveValue('1');
  await page.getByRole('button',{name:'删除 载荷 B',exact:true}).click();
  await expect(page.locator('.load-select')).toHaveCount(1);
  expect(errors).toEqual([]);
});
