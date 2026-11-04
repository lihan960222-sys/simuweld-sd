import { test, expect } from '@playwright/test';

test('表面圆盘在重合和不同半径下均显示填充并保持尺寸比例',async({page})=>{
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await page.locator('.heat-preview-group > summary').click();
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
  await expect(page.locator('.heat-parameter-group h3')).toHaveText(['基本参数','形状尺寸','高级参数']);
  await expect(page.locator('details.heat-parameter-group')).toHaveCount(0);
  await expect(page.locator('[data-group="basic"]')).toBeVisible();
  await expect(page.locator('[data-group="shape"]')).toBeVisible();
  await expect(page.locator('[data-group="advanced"]')).toBeVisible();
  const diagram=page.locator('.heat-preview-group');
  await expect(diagram).not.toHaveAttribute('open','');
  await page.locator('#heat-conical_depth').fill('0.8');
  await diagram.locator('summary').click();
  await expect(page.locator('.heat-legend')).toContainText('0.8 mm');
  await page.locator('#heat-disc_gaussian_parameter').fill('4');
  await expect(page.locator('#heat-error')).toHaveText('请填写有效数值以显示热源。');
  await expect(page.locator('#heat-diagram ellipse')).toHaveCount(0);
  await page.locator('#heat-disc_gaussian_parameter').fill('2');
  await expect(page.locator('#heat-error')).toBeEmpty();
  await diagram.locator('summary').click();
  await diagram.locator('summary').click();
  await expect(page.locator('#heat-disc_gaussian_parameter')).toHaveValue('2');
});

test('热源按填写、预览、保存的顺序操作，并反馈新增和修改状态',async({page})=>{
  await page.goto('/#token='+process.env.SFMARC_TEST_TOKEN);
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await expect(page.locator('#weld-heat-library')).toBeHidden();
  const order=await page.locator('#weld-heat-save').evaluate(button=>[...document.querySelectorAll('.heat-fields input')].every(input=>!!(input.compareDocumentPosition(button)&Node.DOCUMENT_POSITION_FOLLOWING)));
  expect(order).toBe(true);
  await expect(page.locator('#weld-heat-save')).toHaveText('创建热源');
  await page.locator('#heat-efficiency').fill('2');
  await expect(page.locator('#weld-heat-save')).toBeDisabled();
  await page.locator('#heat-efficiency').fill('0.7');
  await page.locator('#weld-heat-save').click();
  await expect(page.locator('#weld-heat-library')).toBeVisible();
  const layout=await page.locator('#weld-heat-library').evaluate(library=>{
    const name=document.getElementById('weld-heat-name')!,save=document.getElementById('weld-heat-save')!;
    return {nameFirst:!!(name.compareDocumentPosition(library)&Node.DOCUMENT_POSITION_FOLLOWING),belowSave:library.getBoundingClientRect().top>=save.getBoundingClientRect().bottom};
  });
  expect(layout).toEqual({nameFirst:true,belowSave:true});
  await expect(page.locator('#weld-heat-save')).toBeDisabled();
  await expect(page.locator('#weld-heat-status')).toContainText('已保存');
  await page.locator('#weld-heat-new').click();
  await expect(page.locator('#weld-heat-name')).toBeFocused();
  await expect(page.locator('#weld-heat-save')).toHaveText('创建热源');
  await page.locator('#weld-heat-name').fill('Laser-B');
  await page.locator('#weld-heat-save').click();
  await expect(page.locator('#weld-entry-heat')).toHaveValue('Laser-B');
  await page.locator('#heat-power_w').fill('80');
  await expect(page.locator('#weld-heat-save')).toHaveText('保存热源修改');
  await expect(page.locator('#weld-heat-save')).toBeEnabled();
  await page.locator('#weld-heat-cancel').click();
  await expect(page.locator('#heat-power_w')).toHaveValue('35');
  await expect(page.locator('#weld-heat-save')).toBeDisabled();
  await page.getByRole('button',{name:'模型查看',exact:true}).click();
  await page.getByRole('button',{name:'生成焊路',exact:true}).click();
  await expect(page.locator('#weld-heat-name')).toHaveValue('Laser-B');
});
