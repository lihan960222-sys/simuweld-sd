import { test, expect } from '@playwright/test';

test('表面圆盘在重合和不同半径下均显示填充并保持尺寸比例',async({page})=>{
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await page.locator('#heat-conical_upper_radius').fill('0.3');
  for(const radius of [0.3,0.12,0.6]) {
    await page.locator('#heat-disc_radius').fill(String(radius));
    const rendered=await page.locator('#heat-diagram').evaluate(async(element)=>{
      const svg=element as SVGSVGElement;
      const image=new Image();
      image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(svg));
      await image.decode();
      const canvas=document.createElement('canvas');canvas.width=svg.viewBox.baseVal.width;canvas.height=svg.viewBox.baseVal.height;
      const context=canvas.getContext('2d')!;context.drawImage(image,0,0,canvas.width,canvas.height);
      const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
      let blue=0;
      for(let index=0;index<pixels.length;index+=4) if(pixels[index+2]>140 && pixels[index+1]>130 && pixels[index+2]>pixels[index]+25 && pixels[index+1]>pixels[index]+15 && pixels[index+3]>100) blue++;
      const disc=svg.querySelector<SVGEllipseElement>('.surface-disc');
      const label=svg.querySelector('.surface-label');
      return {blue,scale:Number(svg.dataset.scale),radius:disc?.rx.baseVal.value,labelColor:label?getComputedStyle(label).fill:''};
    });
    const expectedArea=Math.PI*(radius*rendered.scale)**2*.2;
    expect(rendered.blue).toBeGreaterThan(expectedArea*.45);
    expect(rendered.radius).toBeCloseTo(radius*rendered.scale,5);
    expect(rendered.labelColor).toBe('rgb(20, 101, 129)');
    await expect(page.locator('.heat-legend')).toContainText(`${radius} mm`);
  }
  expect(await page.locator('#heat-diagram text').allTextContents()).toEqual(['R','r','h','Rs']);
  await expect(page.locator('.heat-legend')).toContainText('圆锥体积热源');
  await expect(page.locator('.heat-legend')).toContainText('表面圆盘热源');
});

test('热源参数分组保留数值、验证与实时图例',async({page})=>{
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  const advanced=page.locator('.heat-parameter-group').filter({has:page.getByText('高级参数',{exact:true})});
  await expect(page.locator('[data-group="basic"]')).toBeVisible();
  await expect(page.locator('[data-group="shape"]')).toBeVisible();
  await expect(advanced).not.toHaveAttribute('open','');
  await page.locator('#heat-conical_depth').fill('0.8');
  await expect(page.locator('.heat-legend')).toContainText('0.8 mm');
  await advanced.locator('summary').click();
  await page.locator('#heat-disc_gaussian_parameter').fill('4');
  await expect(page.locator('#heat-error')).toHaveText('请填写有效数值以显示热源。');
  await expect(page.locator('#heat-diagram ellipse')).toHaveCount(0);
  await page.locator('#heat-disc_gaussian_parameter').fill('2');
  await expect(page.locator('#heat-error')).toBeEmpty();
  await advanced.locator('summary').click();
  await advanced.locator('summary').click();
  await expect(page.locator('#heat-disc_gaussian_parameter')).toHaveValue('2');
});
