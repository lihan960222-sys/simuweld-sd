import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

test.beforeAll(() => {
  execFileSync(process.env.SFMARC_TEST_PYTHON || 'python', ['tests/export_viewer_mesh.py'], {stdio: 'inherit'});
});

test.beforeEach(async ({page}) => {
  await page.goto('/tests/viewer.html');
  await page.evaluate(async () => { await (window as any).ready; });
});

async function coastFrames(page: Page) {
  await page.evaluate(() => {
    const viewer = (window as any).viewer;
    viewer.renderer.setAnimationLoop(null);
    viewer.fit();
  });
  await page.mouse.move(500, 380);
  await page.mouse.down();
  await page.mouse.move(620, 380, {steps: 6});
  await page.mouse.up();
  return page.evaluate(() => {
    const {controls,camera} = (window as any).viewer;
    const previous = camera.quaternion.clone();
    let first = 0;
    for (let frame=0; frame<200; frame++) {
      controls.update();
      const step = previous.angleTo(camera.quaternion);
      if (frame===0) first=step;
      else if (step <= first*0.05) return frame;
      previous.copy(camera.quaternion);
    }
    throw new Error('拖动后的旋转未在检查范围内停止');
  });
}

test('拖动惯性在约三十帧内衰减到百分之五', async ({page}) => {
  expect(await coastFrames(page)).toBeGreaterThanOrEqual(29);
  expect(await coastFrames(page)).toBeLessThanOrEqual(31);
});

test('平移后的模型围绕自身中心连续翻转', async ({page}) => {
  await page.mouse.move(450,380);
  await page.mouse.down({button:'right'});
  await page.mouse.move(610,440,{steps:15});
  await page.mouse.up({button:'right'});
  await page.evaluate(async()=>{for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);});
  const start = await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    return {center:viewer.origin.clone().set(0,0,0).project(viewer.camera).toArray(), eye:viewer.camera.position.clone().normalize().toArray()};
  });
  expect(Math.hypot(start.center[0],start.center[1])).toBeGreaterThan(0.02);
  let previous=start.eye, angle=0, drift=0;
  for(let stroke=0;stroke<3;stroke++) {
    await page.mouse.move(500,650);
    await page.mouse.down();
    for(let step=1;step<=22;step++) {
      await page.mouse.move(500,650-step*25);
      const state=await page.evaluate(async()=>{
        await new Promise(requestAnimationFrame);
        const viewer=(window as any).viewer;
        return {center:viewer.origin.clone().set(0,0,0).project(viewer.camera).toArray(),eye:viewer.camera.position.clone().normalize().toArray()};
      });
      const dot=previous.reduce((sum:number,value:number,i:number)=>sum+value*state.eye[i],0);
      angle+=Math.acos(Math.max(-1,Math.min(1,dot)));
      drift=Math.max(drift,Math.hypot(state.center[0]-start.center[0],state.center[1]-start.center[1]));
      previous=state.eye;
    }
    await page.mouse.up();
  }
  expect(angle).toBeGreaterThan(Math.PI*1.2);
  expect(drift).toBeLessThan(0.005);
});

test('箭头随实际选中区域面积缩放', async ({page}) => {
  const samples=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    const loads=await (await fetch('/work/viewer-loads.json')).json();
    return loads.map((load:any)=>{
      viewer.showArrows(load.centers,load.directions,load.area);
      const point=viewer.origin.clone().fromArray(load.centers[0]).sub(viewer.origin);
      return {length:viewer.arrows.children[0].position.distanceTo(point),area:load.area,count:load.centers.length};
    });
  });
  for(const item of samples) {
    expect(item.length).toBeGreaterThan(0);
    expect(item.length).toBeLessThan(Math.sqrt(item.area/item.count));
  }
  expect(samples[0].length).toBeLessThan(samples[1].length);
});

test('只显示边缘实体时围绕可见实体中心旋转', async ({page}) => {
  const initial=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    const candidates=viewer.meshes.map((mesh:any)=>{
      mesh.geometry.computeBoundingBox();
      const box=mesh.geometry.boundingBox;
      const center=box.getCenter(viewer.origin.clone());
      return {body:mesh.userData.body,center,ratio:center.length()/box.getSize(center.clone()).length()};
    }).sort((a:any,b:any)=>b.ratio-a.ratio);
    const chosen=candidates[0];
    for(const mesh of viewer.meshes) viewer.showBody(mesh.userData.body,mesh.userData.body===chosen.body);
    viewer.fit('xy');
    const box=viewer.meshes.find((mesh:any)=>mesh.visible).geometry.boundingBox;
    const center=box.getCenter(viewer.origin.clone());
    return {body:chosen.body,center:center.toArray(),size:box.getSize(center.clone()).length(),target:viewer.controls.target.toArray(),projected:center.project(viewer.camera).toArray()};
  });
  const distance=Math.hypot(...initial.target.map((value:number,i:number)=>value-initial.center[i]));
  expect(distance).toBeLessThan(initial.size*1e-6);
  expect(Math.hypot(initial.projected[0],initial.projected[1])).toBeLessThan(1e-6);
  await page.mouse.move(450,380);
  await page.mouse.down({button:'right'});
  await page.mouse.move(530,410,{steps:10});
  await page.mouse.up({button:'right'});
  const panned=await page.evaluate(async(center)=>{
    for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    return viewer.origin.clone().fromArray(center).project(viewer.camera).toArray();
  },initial.center);
  expect(Math.hypot(panned[0],panned[1])).toBeGreaterThan(0.02);
  await page.mouse.move(500,500);
  await page.mouse.down();
  await page.mouse.move(500,180,{steps:20});
  await page.mouse.up();
  const result=await page.evaluate(async(center)=>{
    for(let i=0;i<40;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    return viewer.origin.clone().fromArray(center).project(viewer.camera).toArray();
  },initial.center);
  expect(Math.hypot(result[0]-panned[0],result[1]-panned[1])).toBeLessThan(1e-6);
  const restored=await page.evaluate(body=>{
    const viewer=(window as any).viewer;
    for(const mesh of viewer.meshes) viewer.showBody(mesh.userData.body,false);
    viewer.fit();
    viewer.showBody(body,true);
    const center=viewer.meshes.find((mesh:any)=>mesh.visible).geometry.boundingBox.getCenter(viewer.origin.clone());
    return {visible:viewer.meshes.filter((mesh:any)=>mesh.visible).length,error:center.distanceTo(viewer.controls.target),camera:viewer.camera.position.toArray()};
  },initial.body);
  expect(restored.visible).toBe(1);
  expect(restored.error).toBeLessThan(initial.size*1e-6);
  expect(restored.camera.every(Number.isFinite)).toBe(true);
});

test('滚轮放大达到上限后停止且能够立即缩小', async ({page}) => {
  await page.evaluate(()=>(window as any).viewer.fit('xy'));
  await page.mouse.move(500,380);
  for(let i=0;i<32;i++) {
    await page.mouse.wheel(0,-120);
    await page.evaluate(async()=>{for(let j=0;j<2;j++) await new Promise(requestAnimationFrame);});
  }
  const limit=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    return {distance:viewer.camera.position.distanceTo(viewer.controls.target),min:viewer.controls.minDistance,max:viewer.controls.maxDistance};
  });
  expect(limit.min).toBeGreaterThan(0);
  expect(Number.isFinite(limit.max)).toBe(true);
  expect(limit.distance).toBeCloseTo(limit.min,10);
  await page.mouse.wheel(0,-120);
  const stopped=await page.evaluate(async()=>{
    for(let i=0;i<3;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    return viewer.camera.position.distanceTo(viewer.controls.target);
  });
  expect(stopped).toBeCloseTo(limit.distance,10);
  await page.mouse.wheel(0,120);
  const reversed=await page.evaluate(async()=>{
    for(let i=0;i<3;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    return viewer.camera.position.distanceTo(viewer.controls.target);
  });
  expect(reversed).toBeGreaterThan(stopped);
});

test('坐标平面与平移、窗口调整后的点击选面', async ({page}) => {
  const planes=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    return ['yz','xz','xy'].map(view=>{
      viewer.fit(view);
      return viewer.camera.position.clone().normalize().toArray();
    });
  });
  expect(planes).toEqual([[1,0,0],[0,-1,0],[0,0,1]]);
  await page.mouse.move(450,380);
  await page.mouse.down({button:'right'});
  await page.mouse.move(530,410,{steps:10});
  await page.mouse.up({button:'right'});
  await page.setViewportSize({width:1100,height:800});
  const target=await page.evaluate(async()=>{
    for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    const point=viewer.origin.clone().set(0,0,-Infinity);
    const center=point.clone();
    let face=-1;
    for(const mesh of viewer.meshes) {
      const positions=mesh.geometry.getAttribute('position'), normals=mesh.geometry.getAttribute('normal');
      mesh.userData.faceIds.forEach((id:number,i:number)=>{
        if(normals.getZ(i*3)<0.99) return;
        center.set(0,0,0);
        for(let k=0;k<3;k++) {
          center.x+=positions.getX(i*3+k)/3;
          center.y+=positions.getY(i*3+k)/3;
          center.z+=positions.getZ(i*3+k)/3;
        }
        if(center.z>point.z) {point.copy(center);face=id;}
      });
    }
    point.project(viewer.camera);
    const rect=viewer.renderer.domElement.getBoundingClientRect();
    return {face,x:rect.left+(point.x+1)*rect.width/2,y:rect.top+(1-point.y)*rect.height/2};
  });
  expect(target.face).toBeGreaterThanOrEqual(0);
  await page.mouse.click(target.x,target.y);
  expect(await page.evaluate(()=>(window as any).picked)).toBe(target.face);
  await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    viewer.meshes.forEach((mesh:any)=>viewer.setBodyOpacity(mesh.userData.body,0));
    (window as any).picked=-1;
  });
  await page.mouse.click(target.x,target.y);
  expect(await page.evaluate(()=>(window as any).picked)).toBe(-1);
  await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    viewer.meshes.forEach((mesh:any)=>viewer.setBodyOpacity(mesh.userData.body,0.35));
  });
  await page.mouse.click(target.x,target.y);
  expect(await page.evaluate(()=>(window as any).picked)).toBe(target.face);
});

test('实体颜色与半透明可修改且选择颜色独立', async ({page}) => {
  const result=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    const mesh=viewer.meshes[0], face=mesh.userData.faceIds[0];
    viewer.setBodyColor(mesh.userData.body,'#e45d41');
    viewer.setBodyOpacity(mesh.userData.body,0.35);
    viewer.highlight([face]);
    const colors=mesh.geometry.getAttribute('color');
    const selected=[colors.getX(0),colors.getY(0),colors.getZ(0)];
    viewer.highlight([]);
    const base=mesh.userData.base;
    return {opacity:mesh.material.opacity,transparent:mesh.material.transparent,depthWrite:mesh.material.depthWrite,
      color:base.getHexString(),selected,restored:[colors.getX(0),colors.getY(0),colors.getZ(0)],expected:[base.r,base.g,base.b]};
  });
  expect(result.opacity).toBe(0.35);
  expect(result.transparent).toBe(true);
  expect(result.depthWrite).toBe(false);
  expect(result.color).toBe('e45d41');
  expect(result.selected).not.toEqual(result.restored);
  result.restored.forEach((value:number,i:number)=>expect(value).toBeCloseTo(result.expected[i],6));
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
    for (const [view, scale] of [['xy',1],['iso',1],['xy',0.5]] as const) {
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
