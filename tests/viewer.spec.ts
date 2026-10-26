import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

test.beforeAll(() => {
  execFileSync(process.env.SFMARC_TEST_PYTHON || 'python', ['tests/export_viewer_mesh.py'], {stdio: 'inherit'});
});

test.beforeEach(async ({page}) => {
  await page.goto('/tests/viewer.html');
  await page.evaluate(async () => { await (window as any).ready; });
});

async function coastFrames(page: Page, baseline: boolean) {
  await page.evaluate(baseline => {
    const viewer = (window as any).viewer;
    viewer.renderer.setAnimationLoop(null);
    viewer.controls.enableDamping = false;
    viewer.controls.update();
    viewer.controls.enableDamping = true;
    if (baseline) viewer.controls.dampingFactor = 0.05;
    viewer.fit();
  }, baseline);
  await page.mouse.move(500, 380);
  await page.mouse.down();
  await page.mouse.move(620, 380, {steps: 6});
  await page.mouse.up();
  return page.evaluate(() => {
    const controls = (window as any).viewer.controls;
    let previous = controls.getAzimuthalAngle();
    let first = 0;
    for (let frame=0; frame<200; frame++) {
      controls.update();
      const current = controls.getAzimuthalAngle();
      const step = Math.abs(current-previous);
      if (frame===0) first=step;
      else if (step <= first*0.05) return frame;
      previous=current;
    }
    throw new Error('拖动后的旋转未在检查范围内停止');
  });
}

test('拖动惯性的衰减时间为原来的一半', async ({page}) => {
  const current = await coastFrames(page, false);
  const baseline = await coastFrames(page, true);
  expect(current/baseline).toBeCloseTo(0.5, 1);
});

test('实际模型的相近表面显示与高精度参考一致', async ({page}) => {
  const samples = await page.evaluate(async () => {
    const viewer = (window as any).viewer;
    const selected = new Set<number>();
    for (const mesh of viewer.meshes) {
      const normals = mesh.geometry.getAttribute('normal');
      mesh.userData.faceIds.forEach((face: number, i: number) => {
        if (normals.getZ(i*3)>0.8) selected.add(face);
      });
    }
    viewer.highlight([...selected]);
    const renderer = viewer.renderer, camera = viewer.camera;
    const gl = renderer.getContext();
    const capture = () => {
      renderer.render(viewer.scene, camera);
      const values = new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);
      gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,values);
      return values;
    };
    const results = [];
    for (const [view, scale] of [['top',1],['iso',1],['top',0.5]] as const) {
      viewer.fit(view);
      camera.position.multiplyScalar(scale);
      viewer.controls.update();
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
      const actual = capture();
      let selectedPixels = 0;
      for (let i=0; i<actual.length; i+=4) {
        if (actual[i]>80 && actual[i]-actual[i+2]>30) selectedPixels++;
      }
      const near = camera.near, far = camera.far;
      const distance = camera.position.length();
      camera.near = distance/3; camera.far = distance*3;
      camera.updateProjectionMatrix();
      const reference = capture();
      let changed = 0;
      for (let i=0; i<actual.length; i+=4) {
        if (Math.max(Math.abs(actual[i]-reference[i]),Math.abs(actual[i+1]-reference[i+1]),Math.abs(actual[i+2]-reference[i+2]))>20) changed++;
      }
      results.push({view,scale,selectedPixels,ratio:changed/(actual.length/4)});
      camera.near=near; camera.far=far; camera.updateProjectionMatrix();
    }
    return results;
  });
  for (const sample of samples) {
    expect(sample.selectedPixels, JSON.stringify(sample)).toBeGreaterThan(100);
    expect(sample.ratio, JSON.stringify(sample)).toBeLessThan(0.001);
  }
});
