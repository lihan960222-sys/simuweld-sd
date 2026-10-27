import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

test.beforeAll(() => {
  execFileSync(process.env.SFMARC_TEST_PYTHON || 'python', ['tests/export_viewer_mesh.py'], {stdio: 'inherit'});
});

test.beforeEach(async ({page}) => {
  await page.goto('/tests/viewer.html');
  await page.evaluate(async () => { await (window as any).ready; });
});

test('未选中的路径点为灰色，选中范围包含端点且统一为黄色',async({page})=>{
  const colors=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    viewer.setPaths([{id:0,points:[[0,0,0],[1,0,0],[2,0,0],[3,0,0]]},{id:1,points:[[0,1,0],[1,1,0]]}],0,[1,2],()=>{});
    return viewer.paths.children.filter((object:any)=>object.isPoints).map((object:any)=>Array.from(object.geometry.getAttribute('color').array));
  });
  const first=colors[0] as number[],other=colors[1] as number[];
  for(const color of [first.slice(0,3),first.slice(9,12),other.slice(0,3)]) expect(Math.max(...color)-Math.min(...color)).toBeLessThan(1e-7);
  expect(first.slice(3,6)).toEqual(first.slice(6,9));
  expect(first[3]).toBeGreaterThan(first[4]);expect(first[4]).toBeGreaterThan(first[5]);
});

test('已焊接轨迹按热源宽度逐段着色，回到之前进度时恢复',async({page})=>{
  await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    const points=[[0,0,0],[10,0,0],[20,0,0]],second=[[0,10,0],[10,10,0]];
    viewer.setPaths([{id:0,points:points.map(point=>point.map(value=>value*.001))},{id:1,points:second.map(point=>point.map(value=>value*.001))}],0,[0,2],()=>{});
    viewer.fit('xy');viewer.fitPaths();
    viewer.setWeldTrails([{points,radius:1,direction:[0,0,1]},{points:second,radius:2,direction:[0,0,1]}],.001);
  });
  const sample=(point:number[])=>page.evaluate(point=>new Promise<number[]>(resolve=>requestAnimationFrame(()=>{
    const viewer=(window as any).viewer;viewer.updatePathCamera();
    const position=viewer.origin.clone().fromArray(point).multiplyScalar(.001).sub(viewer.origin).project(viewer.pathCamera);
    const gl=viewer.renderer.getContext(),pixels=new Uint8Array(4);
    gl.readPixels(Math.floor((position.x+1)*gl.drawingBufferWidth/2),Math.floor((position.y+1)*gl.drawingBufferHeight/2),1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    resolve(Array.from(pixels));
  })),point);
  const before=await sample([2.5,.4,0]);
  await page.evaluate(()=>(window as any).viewer.showWeldProgress(0,0,[5,0,0]));
  const first=await sample([2.5,.4,0]),future=await sample([7.5,.4,0]);
  expect(first[0]-first[1]).toBeGreaterThan(30);expect(first).not.toEqual(before);
  expect(future[0]-future[1]).toBeLessThan(30);
  const widths=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    return viewer.weldTrails.children.slice(0,2).map((mesh:any)=>{
      const position=mesh.geometry.getAttribute('position');
      return Math.hypot(position.getX(0)-position.getX(1),position.getY(0)-position.getY(1),position.getZ(0)-position.getZ(1));
    });
  });
  expect(widths[0]).toBeCloseTo(.002,8);expect(widths[1]).toBeCloseTo(.004,8);
  await page.evaluate(()=>(window as any).viewer.showWeldProgress(1,0,[5,10,0]));
  const completed=await sample([7.5,.4,0]),second=await sample([2.5,10.8,0]);
  expect(completed[0]-completed[1]).toBeGreaterThan(30);expect(second[0]-second[1]).toBeGreaterThan(30);
  await page.evaluate(()=>(window as any).viewer.showWeldProgress(0,0,[2,0,0]));
  const rewind=await sample([7.5,.4,0]);expect(rewind[0]-rewind[1]).toBeLessThan(30);
  await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    viewer.setWeldTrails([{points:[[0,0,0],[10,0,0]],radius:1,direction:[1,0,0]}],.001);
    viewer.showWeldProgress(0,0,[5,0,0]);
  });
  expect(await page.evaluate(()=>Array.from((window as any).viewer.weldTrails.children[0].geometry.getAttribute('position').array).every(value=>Number.isFinite(value)))).toBe(true);
  await page.evaluate(()=>(window as any).viewer.clearWeldTrails());
  expect(await page.evaluate(()=>(window as any).viewer.weldTrails.children.length)).toBe(0);
});

test('正常缩放下密集黄色路径点保留选择颜色，同时清楚显示已焊接的橙色',async({page})=>{
  await page.evaluate(()=>{
    const viewer=(window as any).viewer,points=Array.from({length:301},(_,index)=>[index*200/300,0,0]);
    viewer.setPaths([{id:0,points:points.map(point=>point.map(value=>value*.001))}],0,[0,300],()=>{});
    viewer.fit('xy');viewer.fitPaths();viewer.setWeldTrails([{points,radius:.18,direction:[0,0,1]}],.001);
    viewer.showWeldProgress(0,150,[100,0,0]);
  });
  const strip=(from:number,to:number)=>page.evaluate(({from,to})=>new Promise<{orange:number;yellow:number;width:number}>(resolve=>requestAnimationFrame(()=>{
    const viewer=(window as any).viewer;viewer.updatePathCamera();
    const projected=(x:number)=>viewer.origin.clone().set(x*.001,0,0).sub(viewer.origin).project(viewer.pathCamera);
    const first=projected(from),last=projected(to),gl=viewer.renderer.getContext();
    const x=Math.ceil((first.x+1)*gl.drawingBufferWidth/2),right=Math.floor((last.x+1)*gl.drawingBufferWidth/2),y=Math.floor((first.y+1)*gl.drawingBufferHeight/2);
    const width=right-x+1,pixels=new Uint8Array(width*9*4);gl.readPixels(x,y-4,width,9,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    let orange=0,yellow=0;
    for(let column=0;column<width;column++) {
      let welded=false,selected=false;
      for(let row=0;row<9;row++) {
        const offset=(row*width+column)*4,red=pixels[offset],green=pixels[offset+1],blue=pixels[offset+2];
        if(red>180 && red-green>90 && blue>45) welded=true;
        if(red>200 && green>140 && green<175 && blue<50) selected=true;
      }
      if(welded) orange++;if(selected) yellow++;
    }
    resolve({orange,yellow,width});
  })),{from,to});
  const welded=await strip(10,90),future=await strip(110,190);
  expect(welded.orange).toBeGreaterThan(welded.width*.75);
  expect(welded.yellow).toBeGreaterThan(welded.width*.75);
  expect(future.orange).toBe(0);
  const pointer=await page.evaluate(()=>{
    const viewer=(window as any).viewer,point=viewer.origin.clone().set(.05,0,0).sub(viewer.origin).project(viewer.pathCamera);
    const rect=viewer.renderer.domElement.getBoundingClientRect();
    return {x:rect.left+(point.x+1)*rect.width/2,y:rect.top+(1-point.y)*rect.height/2};
  });
  const brightness=()=>page.evaluate(pointer=>new Promise<number>(resolve=>requestAnimationFrame(()=>{
    const viewer=(window as any).viewer,gl=viewer.renderer.getContext(),rect=viewer.renderer.domElement.getBoundingClientRect();
    const x=Math.floor((pointer.x-rect.left)*gl.drawingBufferWidth/rect.width),y=Math.floor((rect.bottom-pointer.y)*gl.drawingBufferHeight/rect.height),pixels=new Uint8Array(36);
    gl.readPixels(x-1,y-1,3,3,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    let sum=0;for(let index=0;index<pixels.length;index+=4) sum+=pixels[index]+pixels[index+1]+pixels[index+2];resolve(sum/9);
  })),pointer);
  const beforeHover=await brightness();await page.mouse.move(pointer.x,pointer.y);
  expect(await brightness()).toBeGreaterThan(beforeHover+100);
  await page.mouse.move(5,5);
  await page.evaluate(()=>(window as any).viewer.showWeldProgress(0,0,[0,0,0]));
  expect((await strip(10,90)).orange).toBe(0);
});

test('焊枪与光源沿物理坐标移动，停止期间关闭光源',async({page})=>{
  const result=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    const heat={power_w:250,velocity:50,efficiency:.7,conical_upper_radius:.4,conical_lower_radius:.2,conical_depth:.8,conical_gaussian_parameter:1,volume_heat_fraction:.8,disc_radius:.4,surface_depth:.1,disc_gaussian_parameter:1};
    viewer.configureWeldPreview(heat,[0,0,1],.001);
    viewer.showWeldFrame([10,20,30],.001,true);
    const first=viewer.torch.position.clone().add(viewer.origin).toArray();
    const cone=viewer.heat.children[0];
    const direction=viewer.origin.clone().set(0,1,0).applyQuaternion(viewer.torch.quaternion).toArray();
    const light=viewer.weldLight;
    const world=light.getWorldPosition(viewer.origin.clone()).toArray();
    viewer.showWeldFrame([15,20,30],.001,true);
    const moved=light.getWorldPosition(viewer.origin.clone()).toArray();
    viewer.showWeldFrame([15,20,30],.001,false);
    const hidden=!light.parent.visible;
    return {first,direction,upper:cone.geometry.parameters.radiusTop,depth:cone.geometry.parameters.height,movement:moved[0]-world[0],hidden,light:light.isPointLight,intensity:light.intensity};
  });
  result.first.forEach((value:number,index:number)=>expect(value).toBeCloseTo([.01,.02,.03][index],10));
  expect(result.upper).toBeCloseTo(.0004,10);expect(result.depth).toBeCloseTo(.0008,10);
  expect(result.direction[2]).toBeCloseTo(1,10);expect(result.movement).toBeCloseTo(.005,10);
  expect(result.hidden).toBe(true);expect(result.light).toBe(true);expect(result.intensity).toBeGreaterThan(0);
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

test('路径按模型坐标显示，共用点优先当前路径并只拾取已有点', async ({page}) => {
  const point=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    const mesh=await (await fetch('/work/viewer-mesh.json')).json();
    const xyz=mesh.points.slice(0,3);
    const other=mesh.points.slice(3,6);
    viewer.setPaths([{id:0,points:[xyz,other]},{id:1,points:[xyz,other]}],1,[0,1],(path:number,index:number)=>{(window as any).pathPick={path,index};});
    viewer.fitPaths();
    viewer.camera.updateMatrixWorld();
    const geometry=viewer.paths.children[1].geometry.getAttribute('position');
    const centered=viewer.origin.clone().fromArray(xyz).sub(viewer.origin);
    const distance=centered.distanceTo(viewer.origin.clone().fromBufferAttribute(geometry,0));
    const projected=centered.clone().project(viewer.camera);
    return {x:(projected.x+1)*500,y:(1-projected.y)*380,distance};
  });
  expect(point.distance).toBeLessThan(1e-6);
  await page.mouse.click(point.x,point.y);
  expect(await page.evaluate(()=>(window as any).pathPick)).toEqual({path:1,index:0});
  await page.evaluate(()=>{(window as any).pathPick=null;(window as any).viewer.showPaths(false);});
  await page.mouse.click(point.x,point.y);
  expect(await page.evaluate(()=>(window as any).pathPick)).toBeNull();
});

test('路径点悬停时点亮，移开与拖动时恢复', async ({page}) => {
  const point=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    const mesh=await (await fetch('/work/viewer-mesh.json')).json();
    const first=mesh.points.slice(0,3),second=mesh.points.slice(3,6);
    viewer.setPaths([{id:0,points:[first,second]},{id:1,points:[first,second]}],1,[0,1],()=>{});
    viewer.showPaths(true);viewer.fitPaths();viewer.camera.updateMatrixWorld();
    const projected=viewer.origin.clone().fromArray(first).sub(viewer.origin).project(viewer.camera);
    return {x:(projected.x+1)*500,y:(1-projected.y)*380};
  });
  const brightness=()=>page.evaluate(point=>new Promise<number>(resolve=>requestAnimationFrame(()=>{
    const viewer=(window as any).viewer,gl=viewer.renderer.getContext(),rect=viewer.renderer.domElement.getBoundingClientRect();
    const pixels=new Uint8Array(3*3*4),x=Math.floor((point.x-rect.left)*gl.drawingBufferWidth/rect.width),y=Math.floor((rect.bottom-point.y)*gl.drawingBufferHeight/rect.height);
    gl.readPixels(x-1,y-1,3,3,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    let sum=0;for(let index=0;index<pixels.length;index+=4) sum+=pixels[index]+pixels[index+1]+pixels[index+2];resolve(sum/9);
  })),point);
  const before=await brightness();
  await page.mouse.move(point.x,point.y);
  const hover=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    return {visible:viewer.hoverPoint?.visible,path:viewer.hoverPoint?.userData.path,point:viewer.hoverPoint?.userData.point,cursor:viewer.renderer.domElement.style.cursor};
  });
  expect(hover).toEqual({visible:true,path:1,point:0,cursor:'pointer'});
  expect(await brightness()).toBeGreaterThan(before+100);
  await page.mouse.move(5,5);
  expect(await page.evaluate(()=>(window as any).viewer.hoverPoint.visible)).toBe(false);
  await page.mouse.move(point.x,point.y);await page.mouse.down();
  await page.mouse.move(point.x+20,point.y+20);
  expect(await page.evaluate(()=>(window as any).viewer.hoverPoint.visible)).toBe(false);
  await page.mouse.up();
});

test('平面视图按整个模型取景，切换路径显示保留相机',async({page})=>{
  const result=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    const mesh=await (await fetch('/work/viewer-mesh.json')).json();
    viewer.fit('xy');const size=viewer.size;
    const first=mesh.points.slice(0,3),second=mesh.points.slice(3,6);
    viewer.setPaths([{id:0,points:[first,second]}],0,[0,1],()=>{});
    viewer.fitPaths();
    const before=viewer.camera.position.toArray();
    viewer.showPaths(false);const hidden=viewer.camera.position.toArray();
    viewer.showPaths(true);const shown=viewer.camera.position.toArray();
    const planes=['xy','xz','yz'].map(view=>{viewer.fit(view);return {size:viewer.size,distance:viewer.camera.position.distanceTo(viewer.controls.target)};});
    return {before,hidden,shown,size,planes,visible:viewer.meshes.filter((mesh:any)=>mesh.visible).length};
  });
  expect(result.hidden).toEqual(result.before);expect(result.shown).toEqual(result.before);
  expect(result.visible).toBeGreaterThan(0);
  for(const plane of result.planes) {expect(plane.size).toBeCloseTo(result.size,9);expect(plane.distance).toBeCloseTo(result.size*1.65,9);}
});

test('远处路径不改变模型深度精度、材质和边线显示',async({page})=>{
  const result=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    viewer.fit('xy');viewer.showEdges(true);
    const capture=async()=>{
      for(let i=0;i<2;i++) await new Promise(requestAnimationFrame);
      const gl=viewer.renderer.getContext(),pixels=new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);
      gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
      return {pixels,near:viewer.camera.near,far:viewer.camera.far,materials:viewer.meshes.map((mesh:any)=>({color:mesh.userData.base.getHex(),opacity:mesh.material.opacity,depthWrite:mesh.material.depthWrite,edges:mesh.children[0].visible}))};
    };
    const before=await capture();
    viewer.setPaths([{id:0,points:[[1000000,0,-1000000],[1000001,0,-1000000]]}],0,[0,1],()=>{});viewer.showPaths(true);
    const visible=await capture();viewer.showPaths(false);const hidden=await capture();
    const changed=(pixels:Uint8Array)=>pixels.reduce((count,value,index)=>count+(Math.abs(value-before.pixels[index])>5?1:0),0);
    return {before:{near:before.near,far:before.far,materials:before.materials},visible:{near:visible.near,far:visible.far,materials:visible.materials},hidden:{near:hidden.near,far:hidden.far,materials:hidden.materials},visiblePixels:changed(visible.pixels),hiddenPixels:changed(hidden.pixels)};
  });
  expect(result.visible).toEqual(result.before);expect(result.hidden).toEqual(result.before);
  expect(result.visiblePixels).toBe(0);expect(result.hiddenPixels).toBe(0);
});

test('定位短焊路后保留模型显示的深度范围', async ({page}) => {
  const depths=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    const mesh=await (await fetch('/work/viewer-mesh.json')).json();
    let top=0;
    for(let index=3;index<mesh.points.length;index+=3) if(mesh.points[index+2]>mesh.points[top+2]) top=index;
    const first=mesh.points.slice(top,top+3);
    let nearest:number[] | null=null,distance=Infinity;
    for(let index=0;index<mesh.points.length;index+=3) {
      const point=mesh.points.slice(index,index+3);
      const gap=Math.hypot(...point.map((value:number,axis:number)=>value-first[axis]));
      if(gap>0 && gap<distance) {nearest=point;distance=gap;}
    }
    if(!nearest) throw new Error('模型顶部没有可用路径点');
    viewer.setPaths([{id:0,points:[first,nearest]}],0,[0,1],()=>{});
    viewer.fitPaths(); viewer.fit('xy'); viewer.updateClipping();
    const required=Math.max(...viewer.meshes.filter((mesh:any)=>mesh.visible).map((mesh:any)=>-mesh.geometry.boundingBox.clone().applyMatrix4(viewer.camera.matrixWorldInverse).min.z));
    return {far:viewer.camera.far,required};
  });
  expect(depths.far).toBeGreaterThanOrEqual(depths.required);
});

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

test('回到中心保留旋转角度、相机上方和缩放', async ({page}) => {
  await page.mouse.move(450,380);
  await page.mouse.down({button:'right'});
  await page.mouse.move(610,440,{steps:12});
  await page.mouse.up({button:'right'});
  await page.mouse.wheel(0,-240);
  await page.mouse.move(400,450);
  await page.mouse.down();
  await page.mouse.move(590,240,{steps:15});
  await page.mouse.up();
  const result=await page.evaluate(async()=>{
    for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    const camera=viewer.camera, rotation=camera.quaternion.clone(), up=camera.up.clone();
    const distance=camera.position.distanceTo(viewer.controls.target);
    const before=viewer.pivot.clone().project(camera).toArray();
    viewer.recenter();
    for(let i=0;i<20;i++) await new Promise(requestAnimationFrame);
    return {before,after:viewer.pivot.clone().project(camera).toArray(),
      rotation:rotation.angleTo(camera.quaternion),up:up.distanceTo(camera.up),
      scale:camera.position.distanceTo(viewer.controls.target)/distance};
  });
  expect(Math.hypot(result.before[0],result.before[1])).toBeGreaterThan(0.02);
  expect(Math.hypot(result.after[0],result.after[1])).toBeLessThan(1e-6);
  expect(result.rotation).toBeLessThan(1e-6);
  expect(result.up).toBeLessThan(1e-6);
  expect(result.scale).toBeCloseTo(1,10);
});

test('坐标轴指示随视角变化并与模型投影方向一致', async ({page}) => {
  await expect(page.getByRole('img',{name:'坐标轴方向'})).toBeVisible();
  const inspect=()=>page.evaluate(()=>{
    const viewer=(window as any).viewer;
    const center=viewer.pivot.clone().project(viewer.camera);
    return [...document.querySelectorAll<SVGGElement>('.orientation [data-axis]')].map(group=>{
      const axis='XYZ'.indexOf(group.dataset.axis!);
      const point=viewer.pivot.clone();
      point.setComponent(axis,point.getComponent(axis)+viewer.size*0.1);
      point.project(viewer.camera).sub(center);
      const line=group.querySelector('line')!;
      return {axis:group.dataset.axis,dx:Number(line.getAttribute('x2'))-Number(line.getAttribute('x1')),
        dy:Number(line.getAttribute('y2'))-Number(line.getAttribute('y1')),px:point.x,py:-point.y};
    });
  });
  for(const view of ['xy','xz','yz','iso']) {
    await page.evaluate(async view=>{
      (window as any).viewer.fit(view);
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
    },view);
    const axes=await inspect();
    expect(axes).toHaveLength(3);
    for(const axis of axes) {
      if(Math.hypot(axis.px,axis.py)<1e-8) expect(Math.hypot(axis.dx,axis.dy)).toBeLessThan(1e-6);
      else {
        expect(axis.dx*axis.px+axis.dy*axis.py).toBeGreaterThan(0);
        // 相机投影的横纵比例与方形坐标轴图标分别核对。
        const aspect=1000/760;
        expect(Math.abs(axis.dx*axis.py-axis.dy*axis.px*aspect)).toBeLessThan(1e-5);
      }
    }
  }
  const before=await inspect();
  await page.mouse.move(430,450);
  await page.mouse.down();
  await page.mouse.move(650,240,{steps:15});
  await page.mouse.up();
  await page.evaluate(async()=>{for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);});
  const after=await inspect();
  expect(after).not.toEqual(before);
  for(const axis of after) expect(Math.abs(axis.dx*axis.py-axis.dy*axis.px*1000/760)).toBeLessThan(1e-5);
});

test('显示网格边线与实际原始表面边界一致', async ({page}) => {
  const result=await page.evaluate(async()=>{
    const viewer=(window as any).viewer;
    const data=await (await fetch('/work/viewer-mesh.json')).json();
    const point=(node:number)=>[0,1,2].map(axis=>Math.fround(data.points[node*3+axis]-viewer.origin.getComponent(axis))).join(',');
    const key=(body:number,a:string,b:string)=>body+':'+[a,b].sort().join('|');
    const expected=new Set<string>();
    data.faces.forEach((face:number[],i:number)=>face.forEach((node,j)=>expected.add(key(data.body_ids[i],point(node),point(face[(j+1)%face.length])))));
    viewer.showEdges(true);
    const actual=new Set<string>();
    for(const mesh of viewer.meshes) {
      const lines=mesh.children[0], positions=lines.geometry.getAttribute('position');
      if(!lines.visible) throw new Error('边线未显示');
      for(let i=0;i<positions.count;i+=2) {
        const a=[positions.getX(i),positions.getY(i),positions.getZ(i)].join(',');
        const b=[positions.getX(i+1),positions.getY(i+1),positions.getZ(i+1)].join(',');
        actual.add(key(mesh.userData.body,a,b));
      }
    }
    return {count:actual.size,expected:expected.size,extra:[...actual].filter(edge=>!expected.has(edge)).length,
      missing:[...expected].filter(edge=>!actual.has(edge)).length};
  });
  expect(result.count).toBeGreaterThan(0);
  expect(result.extra).toBe(0);
  expect(result.missing).toBe(0);
  expect(result.count).toBe(result.expected);
});

test('通知在下次点击时渐隐', async ({page}) => {
  await page.goto('/');
  const notice=page.locator('#message');
  await expect(notice).toBeVisible();
  await page.locator('footer').click();
  await expect.poll(()=>notice.evaluate(element=>Number(getComputedStyle(element).opacity)),{intervals:[16]}).toBeLessThan(1);
  await expect(notice).toBeVisible();
  await expect(notice).toBeHidden();
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

test('实体显示开关保留旋转、缩放与平移，回到中心使用可见实体',async({page})=>{
  await page.mouse.move(450,380);await page.mouse.down({button:'right'});
  await page.mouse.move(560,420,{steps:10});await page.mouse.up({button:'right'});
  await page.mouse.wheel(0,-120);
  const result=await page.evaluate(async()=>{
    for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    const state=()=>({position:viewer.camera.position.toArray(),rotation:viewer.camera.quaternion.toArray(),offset:viewer.viewOffset.toArray(),target:viewer.controls.target.toArray()});
    const before=state();
    const chosen=viewer.meshes.at(-1);
    for(const mesh of viewer.meshes) viewer.showBody(mesh.userData.body,mesh===chosen);
    viewer.controls.update();const hidden=state();
    viewer.showBody(chosen.userData.body,false);viewer.showBody(chosen.userData.body,true);
    viewer.controls.update();const shown=state();
    const distance=viewer.camera.position.distanceTo(viewer.controls.target);
    viewer.recenter();
    const center=chosen.geometry.boundingBox.getCenter(viewer.origin.clone());
    return {before,hidden,shown,centerError:center.distanceTo(viewer.controls.target),distance,newDistance:viewer.camera.position.distanceTo(viewer.controls.target),offset:viewer.viewOffset.toArray()};
  });
  for(const state of [result.hidden,result.shown]) {
    expect(state.offset).toEqual(result.before.offset);
    for(const key of ['position','rotation','target'] as const) state[key].forEach((value:number,index:number)=>expect(value).toBeCloseTo(result.before[key][index],9));
  }
  expect(result.centerError).toBeLessThan(1e-8);expect(result.newDistance).toBeCloseTo(result.distance,9);expect(result.offset).toEqual([0,0]);
});

test('滚轮到达原限制后继续放大，增速约为正常的五分之一', async ({page}) => {
  await page.evaluate(()=>(window as any).viewer.fit('xy'));
  await page.mouse.move(500,380);
  const initial=await page.evaluate(()=>{
    const viewer=(window as any).viewer;
    return viewer.camera.zoom/viewer.camera.position.distanceTo(viewer.controls.target);
  });
  await page.mouse.wheel(0,-120);
  const normal=await page.evaluate(async()=>{
    for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    const scale=viewer.camera.zoom/viewer.camera.position.distanceTo(viewer.controls.target);
    viewer.fit('xy');viewer.camera.position.sub(viewer.controls.target).setLength(viewer.controls.minDistance).add(viewer.controls.target);viewer.controls.update();
    return {scale,threshold:viewer.camera.zoom/viewer.camera.position.distanceTo(viewer.controls.target)};
  });
  await page.mouse.wheel(0,-120);
  const continued=await page.evaluate(async()=>{
    for(let i=0;i<90;i++) await new Promise(requestAnimationFrame);
    const viewer=(window as any).viewer;
    return {scale:viewer.camera.zoom/viewer.camera.position.distanceTo(viewer.controls.target),zoom:viewer.camera.zoom};
  });
  expect(continued.zoom).toBeGreaterThan(1);
  expect(Math.log(continued.scale/normal.threshold)/Math.log(normal.scale/initial)).toBeCloseTo(.2,1);
  for(let i=0;i<10;i++) await page.mouse.wheel(0,-120);
  const enlarged=await page.evaluate(async()=>{
    for(let i=0;i<30;i++) await new Promise(requestAnimationFrame);
    return (window as any).viewer.camera.zoom;
  });
  expect(enlarged).toBeGreaterThan(continued.zoom);
  await page.mouse.wheel(0,120);
  const reversed=await page.evaluate(async()=>{
    for(let i=0;i<3;i++) await new Promise(requestAnimationFrame);
    return (window as any).viewer.camera.zoom;
  });
  expect(reversed).toBeLessThan(enlarged);
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
