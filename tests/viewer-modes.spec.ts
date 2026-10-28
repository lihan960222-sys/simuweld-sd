import { test, expect } from '@playwright/test';

test.beforeEach(async({page})=>{
  await page.goto('/tests/viewer.html');
  await page.evaluate(async()=>{await (window as any).ready;});
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
    v.highlight([face]);const highlight=mesh.children[1].visible&&mesh.children[1].geometry.index.count>0;
    v.setExplosion(1);const moved=mesh.position.toArray(),arrowMove=v.arrows.children[0].position.clone().sub(arrow).toArray();
    v.updateBounds();const contained=v.meshes.every((m:any)=>v.bounds.containsBox(m.geometry.boundingBox.clone().translate(m.position)));
    v.setRenderMode('solid-edges');const solid=mesh.material.visible&&mesh.children[0].visible;
    v.setExplosion(0);
    const restored=mesh.position.toArray(),unchanged=positions.every((value,index)=>value===mesh.geometry.attributes.position.array[index]);
    const after=v.camera.position.toArray();v.clearModel();
    return {wire,highlight,expected:data.edges.length,moved,arrowMove,contained,solid,restored,unchanged,before,after,remaining:v.meshes.length};
  });
  expect(result.wire).toEqual({surface:false,edges:true,triangles:false,count:result.expected});
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
