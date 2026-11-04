import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';

test.beforeAll(()=>{
  execFileSync(process.env.SFMARC_TEST_PYTHON || 'python',['tests/export_edge_sample.py'],{stdio:'inherit'});
});

test.beforeEach(async({page})=>{
  await page.goto('/tests/viewer.html');
  await page.evaluate(async()=>{await (window as any).ready;});
});

test('载荷箭头逐帧渐入，最终位置保持，清空后终止过渡',async({page})=>{
  await page.emulateMedia({reducedMotion:'no-preference'});
  const result=await page.evaluate(async()=>{
    const v=(window as any).viewer,loads=await(await fetch('/work/viewer-loads.json')).json(),load=loads[0];
    v.showArrows(load.centers,load.directions,load.area);
    const arrow=v.arrows.children[0],position=arrow.position.toArray(),samples=[arrow.line.material.opacity];
    for(let index=0;index<90 && samples[samples.length-1]<1;index++) {
      await new Promise(requestAnimationFrame);samples.push(arrow.line.material.opacity);
    }
    const final={position:arrow.position.toArray(),line:arrow.line.material.opacity,cone:arrow.cone.material.opacity};
    v.showArrows(loads[1].centers,loads[1].directions,loads[1].area);v.clearArrows();
    for(let index=0;index<3;index++) await new Promise(requestAnimationFrame);
    return {position,samples,final,remaining:v.arrows.children.length};
  });
  expect(result.samples[0]).toBe(0);
  expect(result.samples.some(value=>value>0 && value<1)).toBe(true);
  for(let index=1;index<result.samples.length;index++) expect(result.samples[index]).toBeGreaterThanOrEqual(result.samples[index-1]);
  expect(result.final).toEqual({position:result.position,line:1,cone:1});expect(result.remaining).toBe(0);
});

test('减少动态效果设置下直接显示完整载荷箭头',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  const opacity=await page.evaluate(async()=>{
    const v=(window as any).viewer,loads=await(await fetch('/work/viewer-loads.json')).json(),load=loads[0];
    v.showArrows(load.centers,load.directions,load.area);
    return v.arrows.children[0].cone.material.opacity;
  });
  expect(opacity).toBe(1);
});

test('四种显示模式分别控制实体、原始网格和几何棱边',async({page})=>{
  const result=await page.evaluate(()=>{
    const v=(window as any).viewer,mesh=v.meshes[0],states:Record<string,unknown>={};
    v.setBodyOpacity(mesh.userData.body,.35);
    const before={position:v.camera.position.toArray(),rotation:v.camera.quaternion.toArray()};
    for(const mode of ['solid','wireframe','edges','solid-edges']) {
      v.setRenderMode(mode);
      states[mode]={surface:mesh.material.visible,grid:mesh.children[0].visible,outline:mesh.getObjectByName('solid-edges').visible,opacity:mesh.material.opacity};
    }
    return {states,before,after:{position:v.camera.position.toArray(),rotation:v.camera.quaternion.toArray()}};
  });
  expect(result.states).toEqual({
    solid:{surface:true,grid:false,outline:false,opacity:.35},
    wireframe:{surface:true,grid:true,outline:false,opacity:1},
    edges:{surface:false,grid:false,outline:true,opacity:.35},
    'solid-edges':{surface:true,grid:false,outline:true,opacity:.35},
  });
  expect(result.after).toEqual(result.before);
});

test('STEP 长方体只显示十二条棱边，透明度与坐标尺度保持一致',async({page})=>{
  const results=await page.evaluate(async()=>{
    const v=(window as any).viewer,data=await(await fetch('/work/edge-feature-check/box.json')).json();
    return [1,1e-6,1e6].map(scale=>{
      v.load({...data,points:data.points.map((value:number)=>value*scale)});
      v.setRenderMode('solid-edges');
      const mesh=v.meshes[0],box=mesh.geometry.boundingBox,min=box.min.toArray(),max=box.max.toArray();
      v.setBodyOpacity(mesh.userData.body,.35);
      const before={position:v.camera.position.toArray(),rotation:v.camera.quaternion.toArray()};
      const lines=mesh.children.filter((child:any)=>child.isLineSegments && child.visible);
      const ridges=new Set<string>();let internal=0,length=0;
      for(const line of lines) {
        const positions=line.geometry.attributes.position;
        for(let index=0;index<positions.count;index+=2) {
          const a=[positions.getX(index),positions.getY(index),positions.getZ(index)];
          const b=[positions.getX(index+1),positions.getY(index+1),positions.getZ(index+1)];
          const changing=[0,1,2].filter(axis=>Math.abs(a[axis]-b[axis])>scale*1e-5);
          const fixed=[0,1,2].filter(axis=>axis!==changing[0]);
          if(changing.length!==1 || fixed.some(axis=>Math.min(Math.abs(a[axis]-min[axis]),Math.abs(a[axis]-max[axis]))>scale*1e-5)) internal++;
          else ridges.add(changing[0]+':'+fixed.map(axis=>Math.abs(a[axis]-min[axis])<scale*1e-5?'min':'max').join(','));
          length+=Math.hypot(...a.map((value,axis)=>value-b[axis]));
        }
      }
      v.setRenderMode('wireframe');
      const wireCount=mesh.children.filter((child:any)=>child.isLineSegments && child.visible).reduce((sum:number,line:any)=>sum+line.geometry.attributes.position.count/2,0);
      v.setRenderMode('solid-edges');
      return {scale,internal,ridges:ridges.size,length:length/scale,wireCount,expectedWire:data.edges.length/2,opacity:mesh.material.opacity,transparent:mesh.material.transparent,before,after:{position:v.camera.position.toArray(),rotation:v.camera.quaternion.toArray()}};
    });
  });
  for(const result of results) {
    expect(result.internal,'尺度 '+result.scale).toBe(0);
    expect(result.ridges).toBe(12);expect(result.length).toBeCloseTo(140,3);
    expect(result.wireCount).toBe(result.expectedWire);
    expect(result.opacity).toBe(.35);expect(result.transparent).toBe(true);
    expect(result.after).toEqual(result.before);
  }
});

test('网格表面和网格线不透明，切换模式后恢复实体透明度',async({page})=>{
  const result=await page.evaluate(()=>{
    const v=(window as any).viewer,mesh=v.meshes[0];
    v.setBodyOpacity(mesh.userData.body,.35);v.setRenderMode('wireframe');
    const state=()=>({surface:mesh.material.visible,opacity:mesh.material.opacity,transparent:mesh.material.transparent,depthWrite:mesh.material.depthWrite,lineOpacity:mesh.children[0].material.opacity,lineTransparent:mesh.children[0].material.transparent,lineDepthTest:mesh.children[0].material.depthTest});
    const first=state();v.setBodyOpacity(mesh.userData.body,.2);const edited=state();
    v.setRenderMode('solid');const restored={opacity:mesh.material.opacity,transparent:mesh.material.transparent};
    v.setRenderMode('wireframe');return {first,edited,reentered:state(),restored};
  });
  const opaque={surface:true,opacity:1,transparent:false,depthWrite:true,lineOpacity:1,lineTransparent:false,lineDepthTest:true};
  expect(result.first).toEqual(opaque);expect(result.edited).toEqual(opaque);expect(result.reentered).toEqual(opaque);
  expect(result.restored).toEqual({opacity:.2,transparent:true});
});

test('真实模型加入未引用远处节点后保持爆炸方向与距离',async({page})=>{
  const result=await page.evaluate(async()=>{
    const v=(window as any).viewer,data=await(await fetch('/work/viewer-mesh.json')).json();
    v.setExplosion(0);v.load(data);
    const bounds=v.meshes[0].geometry.boundingBox.clone().makeEmpty();
    for(const mesh of v.meshes)bounds.union(mesh.geometry.boundingBox);
    const size=bounds.getSize(v.origin.clone()).length(),origin=v.origin.toArray();
    v.setExplosion(1);const before=v.meshes.map((mesh:any)=>mesh.position.toArray());
    const far=origin.map((value:number,index:number)=>value+size*20*(index+1));
    v.load({...data,points:[...data.points,...far]});v.setExplosion(1);
    return {before,after:v.meshes.map((mesh:any)=>mesh.position.toArray()),size,origin,changedOrigin:v.origin.toArray()};
  });
  expect(result.changedOrigin).not.toEqual(result.origin);
  result.before.forEach((position:number[],index:number)=>{
    expect(Math.hypot(...position)).toBeCloseTo(result.size*.6,8);
    position.forEach((value,axis)=>expect(Math.abs(value-result.after[index][axis])).toBeLessThan(result.size*1e-4));
  });
});

test('真实模型切换投影保留方向、中心和中心平面比例',async({page})=>{
  const result=await page.evaluate(()=>{
    const v=(window as any).viewer;
    const state=()=>({position:v.camera.position.toArray(),rotation:v.camera.quaternion.toArray(),pivot:v.pivot.toArray(),zoom:v.camera.zoom});
    const before=state();v.setProjection('orthographic');const orthographic=state();
    const height=(v.camera.top-v.camera.bottom)/v.camera.zoom;
    v.updatePathCamera();const pathType=v.pathCamera.type;
    v.setProjection('perspective');
    return {before,orthographic,after:state(),height,pathType,expected:2*Math.tan(20*Math.PI/180)*v.camera.position.distanceTo(v.pivot)/v.camera.zoom};
  });
  expect(result.after).toEqual(result.before);expect(result.orthographic).toEqual(result.before);
  expect(result.height).toBeCloseTo(result.expected,8);expect(result.pathType).toBe('OrthographicCamera');
});

test('真实模型边线、爆炸坐标和载荷箭头随实体显示',async({page})=>{
  const result=await page.evaluate(async()=>{
    const v=(window as any).viewer,data=await(await fetch('/work/viewer-mesh.json')).json();
    const mesh=v.meshes[0],face=mesh.userData.faceIds[0],positions=Array.from(mesh.geometry.attributes.position.array);
    const center=mesh.geometry.boundingBox.getCenter(v.origin.clone()).add(v.origin).toArray();
    v.showArrows([center],[[0,0,1]],1,[face]);const arrow=v.arrows.children[0].position.clone();
    const before=v.camera.position.toArray();v.setRenderMode('wireframe');
    const wire={surface:mesh.material.visible,edges:mesh.children[0].visible,triangles:mesh.material.wireframe,count:v.meshes.reduce((sum:number,m:any)=>sum+m.children[0].geometry.attributes.position.count,0)};
    const previousColor=Array.from(mesh.geometry.attributes.color.array.slice(0,3));
    v.highlight([face]);const colors=mesh.geometry.attributes.color;
    const highlight=[colors.getX(0),colors.getY(0),colors.getZ(0)].some((value,index)=>value!==previousColor[index]);
    v.setExplosion(1);const moved=mesh.position.toArray(),arrowMove=v.arrows.children[0].position.clone().sub(arrow).toArray();
    v.updateBounds();const contained=v.meshes.every((m:any)=>v.bounds.containsBox(m.geometry.boundingBox.clone().translate(m.position)));
    v.setRenderMode('solid-edges');const solid=mesh.material.visible&&mesh.getObjectByName('solid-edges').visible&&!mesh.children[0].visible;
    v.setExplosion(0);
    const restored=mesh.position.toArray(),unchanged=positions.every((value,index)=>value===mesh.geometry.attributes.position.array[index]);
    const after=v.camera.position.toArray();v.clearModel();
    return {wire,highlight,expected:data.edges.length,moved,arrowMove,contained,solid,restored,unchanged,before,after,remaining:v.meshes.length};
  });
  expect(result.wire).toEqual({surface:true,edges:true,triangles:false,count:result.expected});
  expect(result.highlight).toBe(true);
  expect(Math.hypot(...result.moved)).toBeGreaterThan(0);result.arrowMove.forEach((value:number,index:number)=>expect(value).toBeCloseTo(result.moved[index],12));
  expect(result.contained).toBe(true);expect(result.solid).toBe(true);expect(Math.hypot(...result.restored)).toBe(0);
  expect(result.unchanged).toBe(true);expect(result.after).toEqual(result.before);expect(result.remaining).toBe(0);
});

test('正交爆炸图保持原始选面编号，滚轮放大且侧栏宽度变化保留相机',async({page})=>{
  const before=await page.evaluate(()=>{
    const v=(window as any).viewer;v.setProjection('orthographic');v.setExplosion(.35);v.fit('xy');v.showPaths(false);
    return {position:v.camera.position.toArray(),quaternion:v.camera.quaternion.toArray(),target:v.controls.target.toArray(),zoom:v.camera.zoom};
  });
  await page.evaluate(()=>{document.getElementById('viewport')!.style.width='720px';});
  await page.waitForTimeout(100);
  const resized=await page.evaluate(()=>{
    const v=(window as any).viewer;
    return {position:v.camera.position.toArray(),quaternion:v.camera.quaternion.toArray(),target:v.controls.target.toArray(),zoom:v.camera.zoom};
  });
  expect(resized).toEqual(before);
  const point=await page.evaluate(()=>{
    const v=(window as any).viewer,p=v.origin.clone().set(0,0,-Infinity),center=p.clone();let face=-1;
    for(const mesh of v.meshes) {
      const positions=mesh.geometry.attributes.position,normals=mesh.geometry.attributes.normal;
      mesh.userData.faceIds.forEach((id:number,index:number)=>{
        if(normals.getZ(index*3)<.99)return;
        center.set(0,0,0);
        for(let k=0;k<3;k++)center.add(v.origin.clone().fromBufferAttribute(positions,index*3+k).divideScalar(3));
        center.add(mesh.position);
        if(center.z>p.z){p.copy(center);face=id;}
      });
    }
    p.project(v.camera);const rect=v.renderer.domElement.getBoundingClientRect();
    return {face,x:rect.left+(p.x+1)*rect.width/2,y:rect.top+(1-p.y)*rect.height/2};
  });
  expect(point.face).toBeGreaterThanOrEqual(0);
  await page.mouse.click(point.x,point.y);
  expect(await page.evaluate(()=>(window as any).picked)).toBe(point.face);
  await page.mouse.wheel(0,-120);
  await expect.poll(()=>page.evaluate(()=>(window as any).viewer.camera.zoom)).toBeGreaterThan(before.zoom);
  const state=await page.evaluate(()=>{
    const v=(window as any).viewer,position=v.camera.position.toArray(),zoom=v.camera.zoom;
    v.showBody(v.meshes[0].userData.body,false);
    return {before:position,after:v.camera.position.toArray(),zoom,afterZoom:v.camera.zoom};
  });
  expect(state.after).toEqual(state.before);expect(state.afterZoom).toBe(state.zoom);
});
