import {test,expect} from '@playwright/test';

test('非线性速度拖条控制实际播放速度',async({page})=>{
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.locator('#tool-welding').click();
  await page.locator('#weld-files').setInputFiles('work/weld-reference/nodes.csv');
  await expect(page.locator('#weld-source-info')).toContainText('295');
  await page.locator('#weld-heat-save').click();
  await page.locator('#weld-start').selectOption('0');await page.locator('#weld-end').selectOption('1');
  await page.locator('#weld-pause').fill('10');await page.locator('#weld-add').click();
  const elapsed=async()=>Number((await page.locator('#weld-time').innerText()).split(' / ')[0]);
  await page.locator('#weld-rate').fill('-50');await page.locator('#weld-play').click();
  await page.waitForTimeout(500);const slow=await elapsed();
  await page.locator('#weld-rate').fill('0');const before=await elapsed();
  await page.waitForTimeout(500);const normal=(await elapsed())-before;
  expect(slow).toBeGreaterThan(0.02);expect(slow).toBeLessThan(0.1);
  expect(normal/slow).toBeGreaterThan(5);expect(normal/slow).toBeLessThan(15);
  await page.locator('#weld-rate').fill('50');
  await expect(page.locator('#weld-rate')).toHaveAttribute('aria-valuetext','20×');
});
