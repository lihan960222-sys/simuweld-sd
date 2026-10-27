import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import type { HeatParameters } from './heatSettings';

export interface MeshData {
  points: number[]; triangles: number[]; triangle_faces: number[]; body_ids: number[];
  edges: number[]; edge_bodies: number[];
}
export interface PathData { id: number; points: number[][]; }
export interface WeldTrailPath { points: number[][]; radius: number; direction: number[]; }

export class Viewer {
  private renderer = new THREE.WebGLRenderer({antialias: true});
  private scene = new THREE.Scene();
  private pathScene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(40, 1, 0.001, 10000);
  private pathCamera = new THREE.PerspectiveCamera();
  private controls: TrackballControls;
  private orientation = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  private orientationRotation = new THREE.Quaternion(0,0,0,0);
  private viewOffset = new THREE.Vector2();
  private models = new THREE.Group();
  private arrows = new THREE.Group();
  private paths = new THREE.Group();
  private weldTrails = new THREE.Group();
  private trailMeshes: THREE.Mesh<THREE.BufferGeometry,THREE.MeshBasicMaterial>[]=[];
  private trailLines: THREE.Line<THREE.BufferGeometry,THREE.LineBasicMaterial>[]=[];
  private trailPoints: THREE.Vector3[][]=[];
  private trailSides: THREE.Vector3[][]=[];
  private trailBounds = new THREE.Box3();
  private trailScale = 1;
  private partialWeldTrail: THREE.Mesh<THREE.BufferGeometry,THREE.MeshBasicMaterial> | null=null;
  private partialWeldLine: THREE.Line<THREE.BufferGeometry,THREE.LineBasicMaterial> | null=null;
  private hoverPoint = new THREE.Points(new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute([0,0,0],3)),new THREE.PointsMaterial({color:0xffe89a,size:14,sizeAttenuation:false,depthTest:false,depthWrite:false,transparent:true}));
  private torch = new THREE.Group();
  private heat = new THREE.Group();
  private weldLight = new THREE.PointLight(0xffa84c,0,0,0);
  private heatExtent = 0;
  private pathBounds = new THREE.Box3();
  private pathFocus = new THREE.Box3();
  private pathPick: ((path: number, point: number)=>void) | null = null;
  private raycaster = new THREE.Raycaster();
  private origin = new THREE.Vector3();
  private pivot = new THREE.Vector3();
  private panShift = new THREE.Vector3();
  private bounds = new THREE.Box3();
  private viewBounds = new THREE.Box3();
  private size = 1;
  private arrowLength = 0;
  private meshes: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>[] = [];
  private selected = new Set<number>();
  private palette = [0x9cbbc8, 0xc2cdd5, 0x7897a8, 0xa8b2c7, 0x7faaaa, 0xb6bcb2, 0x89a6bf];
  private edgesVisible = false;

  constructor(private host: HTMLElement, onPick: (face: number) => void) {
    this.scene.background = new THREE.Color(0xedf2f6);
    this.renderer.autoClear=false;
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.host.append(this.renderer.domElement);
    this.orientation.setAttribute('class','orientation');
    this.orientation.setAttribute('viewBox','0 0 104 104');
    this.orientation.setAttribute('role','img');
    this.orientation.setAttribute('aria-label','坐标轴方向');
    this.orientation.innerHTML = `<circle class="axis-background" cx="52" cy="52" r="48"/>`+['X','Y','Z'].map(axis=>
      `<g data-axis="${axis}" class="axis-${axis.toLowerCase()}"><line x1="52" y1="52"/><path d="M-3 6L0 0L3 6"/><circle r="8"/><text text-anchor="middle" dominant-baseline="central">${axis}</text></g>`).join('');
    this.host.append(this.orientation);
    this.renderer.domElement.setAttribute('aria-label', '三维模型，点击表面选择，拖动旋转，滚轮缩放');
    this.camera.position.set(0,0,1);
    this.controls = this.createControls();
    this.scene.add(new THREE.HemisphereLight(0xf1f6ff, 0x617385, 2.5));
    const light = new THREE.DirectionalLight(0xffffff, 3);
    light.position.set(3, -4, 5);
    this.scene.add(light, this.models, this.arrows, this.torch);
    this.pathScene.add(this.weldTrails,this.paths,this.hoverPoint);
    const glow=document.createElement('canvas');glow.width=64;glow.height=64;
    const context=glow.getContext('2d')!,gradient=context.createRadialGradient(32,32,2,32,32,32);
    gradient.addColorStop(0,'#ffffff');gradient.addColorStop(.25,'#ffffff');gradient.addColorStop(1,'#ffffff00');
    context.fillStyle=gradient;context.fillRect(0,0,64,64);
    this.hoverPoint.material.map=new THREE.CanvasTexture(glow);this.hoverPoint.renderOrder=22;this.hoverPoint.visible=false;
    this.torch.visible=false;
    let down: [number, number] = [0, 0];
    this.renderer.domElement.addEventListener('pointerdown', event => { down = [event.clientX, event.clientY];this.clearPathHover(); });
    this.renderer.domElement.addEventListener('pointermove',event=>{
      if(event.buttons) {this.clearPathHover();return;}
      const hit=this.pathPointAt(event.clientX,event.clientY);
      this.hoverPoint.visible=!!hit;this.renderer.domElement.style.cursor=hit?'pointer':'';
      if(hit) {this.hoverPoint.position.copy(hit.position);this.hoverPoint.userData={path:hit.path,point:hit.point};}
    });
    this.renderer.domElement.addEventListener('pointerleave',()=>this.clearPathHover());
    this.renderer.domElement.addEventListener('wheel',event=>{
      this.clearPathHover();
      const distance=this.camera.position.distanceTo(this.controls.target);
      if(this.camera.zoom===1 && (event.deltaY>=0 || distance>this.controls.minDistance*(1+1e-8))) return;
      event.preventDefault();event.stopImmediatePropagation();
      const unit=event.deltaMode===2?.025:event.deltaMode===1?.01:.00025;
      // 到达模型外侧后，通过视角放大继续观察，滚轮增速降为五分之一。
      this.camera.zoom=Math.max(1,this.camera.zoom*Math.exp(-event.deltaY*unit*this.controls.zoomSpeed*.2/this.controls.dynamicDampingFactor));
      this.controls.panSpeed=.3/this.camera.zoom;this.camera.updateProjectionMatrix();
    },{capture:true,passive:false});
    this.renderer.domElement.addEventListener('pointerup', event => {
      if (event.button !== 0 || Math.hypot(event.clientX-down[0], event.clientY-down[1]) > 4) return;
      const rect = this.renderer.domElement.getBoundingClientRect();
      if (this.paths.visible && this.pathPick) {
        const closest=this.pathPointAt(event.clientX,event.clientY);
        if (closest) this.pathPick(closest.path,closest.point);
        return;
      }
      this.raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1, -(event.clientY-rect.top)/rect.height*2+1), this.camera);
      const hit = this.raycaster.intersectObjects(this.meshes.filter(mesh => mesh.visible && mesh.material.opacity > 0), false)[0];
      if (hit && hit.faceIndex != null) onPick(hit.object.userData.faceIds[hit.faceIndex]);
    });
    new ResizeObserver(() => {
      const width = this.host.clientWidth, height = this.host.clientHeight;
      this.renderer.setSize(width, height);
      this.camera.aspect = width / height;
      this.updateViewOffset();
      this.controls.handleResize();
    }).observe(this.host);
    this.renderer.setAnimationLoop(() => {
      this.controls.update();
      const shift = this.panShift.subVectors(this.controls.target, this.pivot);
      if (shift.lengthSq() > 0) {
        // 将平移保存在视窗位置，使旋转中心保持在可见实体中心。
        const scale = this.camera.zoom*this.host.clientHeight / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * this.camera.position.distanceTo(this.controls.target));
        const right = new THREE.Vector3(1,0,0).applyQuaternion(this.camera.quaternion);
        const up = new THREE.Vector3(0,1,0).applyQuaternion(this.camera.quaternion);
        this.viewOffset.x += shift.dot(right) * scale;
        this.viewOffset.y -= shift.dot(up) * scale;
        this.camera.position.sub(shift);
        this.controls.target.copy(this.pivot);
        this.updateViewOffset();
      }
      this.updateClipping();
      this.renderer.clear();
      this.renderer.render(this.scene, this.camera);
      if(this.paths.visible && !this.pathBounds.isEmpty()) {
        this.updatePathCamera();this.renderer.render(this.pathScene,this.pathCamera);
      }
      this.updateOrientation();
    });
  }

  private clearPathHover() { this.hoverPoint.visible=false;this.renderer.domElement.style.cursor=''; }

  private pathPointAt(clientX: number,clientY: number) {
    if(!this.paths.visible || !this.pathPick || this.pathBounds.isEmpty()) return null;
    this.updatePathCamera();
    const rect=this.renderer.domElement.getBoundingClientRect(),projected=new THREE.Vector3(),position=new THREE.Vector3();
    let closest: {path:number;point:number;distance:number;active:boolean;position:THREE.Vector3} | null=null;
    for(const object of this.paths.children) if(object instanceof THREE.Points && object.userData.path!=null) {
      const positions=object.geometry.getAttribute('position');
      for(let index=0;index<positions.count;index++) {
        position.fromBufferAttribute(positions,index);projected.copy(position).project(this.pathCamera);
        if(Math.abs(projected.z)>1) continue;
        const distance=Math.hypot((projected.x+1)*rect.width/2-(clientX-rect.left),(1-projected.y)*rect.height/2-(clientY-rect.top));
        const active=object.userData.active as boolean;
        if(distance<=8 && (!closest || (active && !closest.active) || (active===closest.active && distance<closest.distance))) closest={path:object.userData.path,point:index,distance,active,position:position.clone()};
      }
    }
    return closest;
  }

  private updatePathCamera() {
    this.camera.updateMatrixWorld();this.pathCamera.copy(this.camera);
    const box=this.pathBounds.clone().union(this.trailBounds).applyMatrix4(this.camera.matrixWorldInverse),margin=Math.max(this.size*.05,1e-9);
    this.pathCamera.near=Math.max(1e-12,-box.max.z-margin);
    this.pathCamera.far=Math.max(this.pathCamera.near*2,-box.min.z+margin);
    this.pathCamera.updateProjectionMatrix();
  }

  private updateOrientation() {
    if (this.orientationRotation.equals(this.camera.quaternion)) return;
    this.orientationRotation.copy(this.camera.quaternion);
    const rotation = this.camera.quaternion.clone().invert();
    const axes = [...this.orientation.querySelectorAll<SVGGElement>('[data-axis]')].map(group=>({
      group, direction: new THREE.Vector3().setComponent('XYZ'.indexOf(group.dataset.axis!),1).applyQuaternion(rotation),
    })).sort((a,b)=>a.direction.z-b.direction.z);
    for (const {group,direction} of axes) {
      const x=direction.x, y=-direction.y;
      const line=group.querySelector('line')!, head=group.querySelector('path')!;
      line.setAttribute('x2',String(52+30*x)); line.setAttribute('y2',String(52+30*y));
      head.setAttribute('transform',`translate(${52+30*x} ${52+30*y}) rotate(${THREE.MathUtils.radToDeg(Math.atan2(y,x))+90})`);
      head.setAttribute('visibility',Math.hypot(x,y)<0.1?'hidden':'visible');
      group.querySelector('circle')!.setAttribute('cx',String(52+40*x));
      group.querySelector('circle')!.setAttribute('cy',String(52+40*y));
      group.querySelector('text')!.setAttribute('x',String(52+40*x));
      group.querySelector('text')!.setAttribute('y',String(52+40*y));
      group.setAttribute('opacity',direction.z<0?'.55':'1');
      this.orientation.append(group);
    }
  }

  private createControls() {
    const controls = new TrackballControls(this.camera, this.renderer.domElement);
    controls.target.copy(this.pivot);
    controls.rotateSpeed = 2.5;
    // 每帧保留 0.9025 的旋转惯性。
    controls.dynamicDampingFactor = 1 - 0.9025 ** 2;
    // 保留模型外侧的观察位置；近距离放大由 camera.zoom 继续完成。
    controls.minDistance = this.size * 0.55;
    controls.maxDistance = this.size * 20;
    controls.panSpeed=.3/this.camera.zoom;
    controls.update();
    return controls;
  }

  private updateViewOffset() {
    const width = this.host.clientWidth, height = this.host.clientHeight;
    this.camera.setViewOffset(width, height, this.viewOffset.x, this.viewOffset.y, width, height);
  }

  load(data: MeshData) {
    this.hideWeldPreview();
    this.clearWeldTrails();
    this.clearPathHover();
    this.clearGroup(this.models);
    this.clearArrows();
    this.clearGroup(this.paths); this.pathBounds.makeEmpty();
    this.meshes = [];
    this.selected.clear();
    const box = new THREE.Box3();
    for (let i=0; i<data.points.length; i+=3) box.expandByPoint(new THREE.Vector3(data.points[i], data.points[i+1], data.points[i+2]));
    box.getCenter(this.origin);
    const bodyTriangles = new Map<number, number[]>();
    data.triangle_faces.forEach((face, triangle) => {
      const body = data.body_ids[face];
      if (!bodyTriangles.has(body)) bodyTriangles.set(body, []);
      bodyTriangles.get(body)!.push(triangle);
    });
    const bodyEdges = new Map<number, number[]>();
    data.edge_bodies.forEach((body, edge) => {
      if (!bodyEdges.has(body)) bodyEdges.set(body, []);
      bodyEdges.get(body)!.push(edge);
    });
    for (const [body, triangles] of bodyTriangles) {
      const positions = new Float32Array(triangles.length * 9);
      const colors = new Float32Array(positions.length);
      const color = new THREE.Color(this.palette[body % this.palette.length]);
      const faceIds: number[] = [];
      triangles.forEach((triangle, index) => {
        faceIds.push(data.triangle_faces[triangle]);
        for (let vertex=0; vertex<3; vertex++) {
          const node = data.triangles[triangle*3+vertex];
          for (let axis=0; axis<3; axis++) positions[index*9+vertex*3+axis] = data.points[node*3+axis] - this.origin.getComponent(axis);
          colors.set([color.r, color.g, color.b], index*9+vertex*3);
        }
      });
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      geometry.computeBoundingSphere();
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({vertexColors: true, roughness: 0.72, metalness: 0.08, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1}));
      mesh.userData = {body, faceIds, base: color};
      const originalEdges = bodyEdges.get(body)!;
      const edgePositions = new Float32Array(originalEdges.length * 6);
      originalEdges.forEach((edge,index)=>{
        for (let vertex=0; vertex<2; vertex++) {
          const node=data.edges[edge*2+vertex];
          for (let axis=0; axis<3; axis++) edgePositions[index*6+vertex*3+axis]=data.points[node*3+axis]-this.origin.getComponent(axis);
        }
      });
      const edgeGeometry = new THREE.BufferGeometry();
      edgeGeometry.setAttribute('position',new THREE.BufferAttribute(edgePositions,3));
      const edges = new THREE.LineSegments(edgeGeometry, new THREE.LineBasicMaterial({color: 0x24394b, transparent: true, opacity: 0.35}));
      edges.visible = this.edgesVisible;
      mesh.add(edges);
      this.models.add(mesh);
      this.meshes.push(mesh);
    }
    this.fit();
  }

  fit(view = 'iso') {
    this.camera.zoom=1;this.camera.updateProjectionMatrix();this.clearPathHover();
    const directions: Record<string, number[]> = {iso: [1,-1,0.85], xy: [0,0,1], xz: [0,-1,0], yz: [1,0,0]};
    this.camera.up.set(0, view === 'xy' ? 1 : 0, view === 'xy' ? 0 : 1);
    this.frame(new THREE.Vector3().fromArray(directions[view]).normalize());
  }

  recenter() {
    this.updateBounds();
    if (this.bounds.isEmpty()) return;
    this.controls.dispose();
    this.bounds.getCenter(this.pivot);
    this.camera.position.sub(this.controls.target).add(this.pivot);
    this.viewOffset.set(0,0);
    this.camera.clearViewOffset();
    this.controls = this.createControls();
    this.updateClipping();
  }

  private frame(direction: THREE.Vector3,pathsOnly=false) {
    if(pathsOnly) this.bounds.copy(this.pathFocus.isEmpty()?this.pathBounds:this.pathFocus);else this.updateBounds();
    if (this.bounds.isEmpty()) return;
    this.bounds.getCenter(this.pivot);
    this.size = Math.max(this.bounds.getSize(new THREE.Vector3()).length(),1e-9);
    this.controls.dispose();
    this.viewOffset.set(0,0);
    this.camera.clearViewOffset();
    this.camera.position.copy(direction).multiplyScalar(this.size * 1.65).add(this.pivot);
    this.camera.lookAt(this.pivot);
    this.controls = this.createControls();
    this.updateClipping();
  }

  private updateBounds() {
    this.bounds.makeEmpty();
    for (const mesh of this.meshes) if (mesh.visible) this.bounds.union(mesh.geometry.boundingBox!);
    if (this.paths.visible && this.bounds.isEmpty()) this.bounds.copy(this.pathBounds);
  }

  private updateClipping() {
    if (this.bounds.isEmpty()) return;
    this.camera.updateMatrixWorld();
    this.viewBounds.copy(this.bounds);
    for (const mesh of this.meshes) if (mesh.visible) this.viewBounds.union(mesh.geometry.boundingBox!);
    if (this.torch.visible) {
      const radius=new THREE.Vector3().setScalar(this.heatExtent);
      this.viewBounds.union(new THREE.Box3(this.torch.position.clone().sub(radius),this.torch.position.clone().add(radius)));
    }
    this.viewBounds.applyMatrix4(this.camera.matrixWorldInverse);
    // 保留载荷箭头的显示空间，同时提高相近表面的深度精度。
    const margin = Math.max(this.size * 0.05, this.arrowLength);
    const near = Math.max(this.size * 1e-5, -this.viewBounds.max.z - margin);
    const far = Math.max(near * 2, -this.viewBounds.min.z + margin);
    if (near !== this.camera.near || far !== this.camera.far) {
      this.camera.near = near;
      this.camera.far = far;
      this.camera.updateProjectionMatrix();
    }
  }

  highlight(faces: number[]) {
    this.selected = new Set(faces);
    const selectedColor = new THREE.Color(0xffbc58);
    for (const mesh of this.meshes) {
      const colors = mesh.geometry.getAttribute('color') as THREE.BufferAttribute;
      (mesh.userData.faceIds as number[]).forEach((face, triangle) => {
        const color: THREE.Color = this.selected.has(face) ? selectedColor : mesh.userData.base;
        for (let k=0; k<3; k++) colors.setXYZ(triangle*3+k, color.r, color.g, color.b);
      });
      colors.needsUpdate = true;
    }
  }

  showBody(body: number, visible: boolean) {
    this.meshes.filter(mesh=>mesh.userData.body===body).forEach(mesh=>mesh.visible=visible);
    this.updateClipping();
  }
  bodyColor(body: number) { return '#'+this.meshes.find(mesh=>mesh.userData.body===body)!.userData.base.getHexString(); }
  setBodyColor(body: number, color: string) {
    this.meshes.find(mesh=>mesh.userData.body===body)!.userData.base.set(color);
    this.highlight([...this.selected]);
  }
  setBodyOpacity(body: number, opacity: number) {
    const material = this.meshes.find(mesh=>mesh.userData.body===body)!.material;
    material.opacity = opacity;
    material.transparent = opacity < 1;
    material.depthWrite = opacity === 1;
    material.needsUpdate = true;
  }
  showEdges(visible: boolean) {
    this.edgesVisible = visible;
    this.meshes.forEach(mesh=>mesh.children.forEach(child=>child.visible=visible));
  }
  showArrows(centers: number[][], directions: number[][], area: number) {
    this.clearArrows();
    if (!centers.length) return;
    const length = this.arrowLength = 0.6 * Math.sqrt(area / centers.length);
    centers.forEach((center, i) => {
      const direction = new THREE.Vector3().fromArray(directions[i]);
      const point = new THREE.Vector3().fromArray(center).sub(this.origin);
      const arrow = new THREE.ArrowHelper(direction, point.clone().addScaledVector(direction, -length), length, 0x078b94, length*0.25, length*0.12);
      this.arrows.add(arrow);
    });
  }
  clearArrows() { this.clearGroup(this.arrows); this.arrowLength = 0; }
  configureWeldPreview(parameters: HeatParameters, direction: number[], scale: number) {
    this.clearGroup(this.torch); this.heat=new THREE.Group();
    const upper=parameters.conical_upper_radius*scale, lower=parameters.conical_lower_radius*scale, depth=parameters.conical_depth*scale;
    const extent=Math.max(upper,lower,depth,parameters.disc_radius*scale);
    this.heatExtent=extent*6;
    const cone=new THREE.Mesh(new THREE.CylinderGeometry(upper,lower,depth,32),new THREE.MeshBasicMaterial({color:0xffa13f,transparent:true,opacity:0.45,depthWrite:false}));
    cone.position.y=-depth/2;
    const disc=new THREE.Mesh(new THREE.CircleGeometry(parameters.disc_radius*scale,32),new THREE.MeshBasicMaterial({color:0xffd46a,transparent:true,opacity:0.6,side:THREE.DoubleSide,depthWrite:false}));
    disc.rotation.x=-Math.PI/2;disc.position.y=extent*0.005;
    const nozzle=new THREE.Mesh(new THREE.CylinderGeometry(extent*.45,extent*.18,extent*2,16),new THREE.MeshStandardMaterial({color:0x586b80,metalness:0.6,roughness:0.4}));
    nozzle.position.y=extent*2;
    const beam=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(),new THREE.Vector3(0,extent,0)]),new THREE.LineBasicMaterial({color:0xffa02c}));
    const marker=new THREE.Points(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3()]),new THREE.PointsMaterial({color:0xff8b1f,size:9,sizeAttenuation:false,depthTest:false}));
    marker.renderOrder=30;
    this.weldLight.intensity=Math.log1p(parameters.power_w*parameters.efficiency);
    this.weldLight.distance=extent*6;this.weldLight.position.set(0,extent*.1,0);
    cone.visible=parameters.volume_heat_fraction>0;disc.visible=parameters.volume_heat_fraction<1;
    this.heat.add(cone,disc,beam,marker,this.weldLight);this.torch.add(nozzle,this.heat);
    this.torch.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),new THREE.Vector3().fromArray(direction).normalize());
  }
  showWeldFrame(point: number[], scale: number, welding: boolean) {
    this.torch.position.fromArray(point).multiplyScalar(scale).sub(this.origin);
    this.torch.visible=true;this.heat.visible=welding;
  }
  hideWeldPreview() { this.torch.visible=false; }
  setWeldTrails(paths: WeldTrailPath[],scale: number) {
    if(!Number.isFinite(scale) || scale<=0) throw new Error('焊路显示比例必须大于零。');
    this.clearWeldTrails();this.trailScale=scale;
    for(const path of paths) {
      const normalLength=Math.hypot(...path.direction);
      if(!Number.isFinite(path.radius) || path.radius<=0 || path.direction.length!==3 || !Number.isFinite(normalLength) || !normalLength || path.points.length<2) throw new Error('焊接轨迹尺寸、方向或点数无效。');
      if(path.points.some(point=>point.length!==3 || !point.every(Number.isFinite))) throw new Error('焊接轨迹坐标无效。');
      const normal=new THREE.Vector3().fromArray(path.direction).divideScalar(normalLength);
      const points=path.points.map(point=>new THREE.Vector3().fromArray(point).multiplyScalar(scale).sub(this.origin));
      const positions=new Float32Array((points.length-1)*18),sides: THREE.Vector3[]=[];
      for(let index=0;index<points.length-1;index++) {
        const direction=points[index+1].clone().sub(points[index]);
        if(!Number.isFinite(direction.length()) || direction.lengthSq()===0) throw new Error('相邻轨迹点距离必须大于零。');
        const side=new THREE.Vector3().crossVectors(normal,direction);
        if(side.lengthSq()<direction.lengthSq()*1e-20) {
          const axis=Math.abs(direction.x)<=Math.abs(direction.y) && Math.abs(direction.x)<=Math.abs(direction.z)?0:Math.abs(direction.y)<=Math.abs(direction.z)?1:2;
          side.crossVectors(new THREE.Vector3().setComponent(axis,1),direction);
        }
        side.normalize().multiplyScalar(path.radius*scale);sides.push(side);
        this.writeTrailSegment(positions,index*18,points[index],points[index+1],side);
      }
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
      geometry.computeBoundingBox();geometry.computeBoundingSphere();geometry.setDrawRange(0,0);
      const mesh=new THREE.Mesh(geometry,this.trailMaterial());mesh.renderOrder=19;
      this.trailBounds.union(geometry.boundingBox!);this.weldTrails.add(mesh);this.trailMeshes.push(mesh);this.trailPoints.push(points);this.trailSides.push(sides);
      const center=new THREE.BufferGeometry().setFromPoints(points);center.setDrawRange(0,0);
      const line=new THREE.Line(center,this.trailLineMaterial());line.renderOrder=21.5;this.trailLines.push(line);
    }
    this.weldTrails.add(...this.trailLines);
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(18),3).setUsage(THREE.DynamicDrawUsage));
    this.partialWeldTrail=new THREE.Mesh(geometry,this.trailMaterial());
    this.partialWeldTrail.renderOrder=19;this.partialWeldTrail.frustumCulled=false;this.partialWeldTrail.visible=false;this.weldTrails.add(this.partialWeldTrail);
    const center=new THREE.BufferGeometry().setAttribute('position',new THREE.BufferAttribute(new Float32Array(6),3).setUsage(THREE.DynamicDrawUsage));
    this.partialWeldLine=new THREE.Line(center,this.trailLineMaterial());
    this.partialWeldLine.renderOrder=21.5;this.partialWeldLine.frustumCulled=false;this.partialWeldLine.visible=false;this.weldTrails.add(this.partialWeldLine);
  }
  showWeldProgress(pathIndex: number,segmentIndex: number,point: number[]) {
    if(!Number.isInteger(pathIndex) || !Number.isInteger(segmentIndex) || !this.trailPoints[pathIndex]?.[segmentIndex+1] || point.length!==3 || !point.every(Number.isFinite) || !this.partialWeldTrail || !this.partialWeldLine) throw new Error('焊接轨迹进度无效。');
    this.trailMeshes.forEach((mesh,index)=>mesh.geometry.setDrawRange(0,index<pathIndex?mesh.geometry.getAttribute('position').count:index===pathIndex?segmentIndex*6:0));
    this.trailLines.forEach((line,index)=>line.geometry.setDrawRange(0,index<pathIndex?line.geometry.getAttribute('position').count:index===pathIndex?segmentIndex+1:0));
    const start=this.trailPoints[pathIndex][segmentIndex],end=new THREE.Vector3().fromArray(point).multiplyScalar(this.trailScale).sub(this.origin);
    const position=this.partialWeldTrail.geometry.getAttribute('position') as THREE.BufferAttribute;
    this.writeTrailSegment(position.array as Float32Array,0,start,end,this.trailSides[pathIndex][segmentIndex]);
    position.needsUpdate=true;this.partialWeldTrail.visible=true;
    const center=this.partialWeldLine.geometry.getAttribute('position') as THREE.BufferAttribute;
    start.toArray(center.array,0);end.toArray(center.array,3);center.needsUpdate=true;this.partialWeldLine.visible=true;
  }
  clearWeldTrails() {
    this.clearGroup(this.weldTrails);this.trailMeshes=[];this.trailLines=[];this.trailPoints=[];this.trailSides=[];this.trailBounds.makeEmpty();this.partialWeldTrail=null;this.partialWeldLine=null;
  }
  private trailMaterial() { return new THREE.MeshBasicMaterial({color:0xe86b40,side:THREE.DoubleSide,transparent:true,opacity:.85,depthTest:false,depthWrite:false}); }
  // 正常缩放时，中心线保留完成颜色，实体带仍使用热源的物理宽度。
  private trailLineMaterial() { return new THREE.LineBasicMaterial({color:0xe86b40,transparent:true,depthTest:false,depthWrite:false}); }
  private writeTrailSegment(positions: Float32Array,offset: number,start: THREE.Vector3,end: THREE.Vector3,side: THREE.Vector3) {
    const first=start.clone().add(side),second=start.clone().sub(side),third=end.clone().add(side),fourth=end.clone().sub(side);
    [first,second,third,third,second,fourth].forEach((point,index)=>point.toArray(positions,offset+index*3));
  }
  setPaths(data: PathData[], selected: number, range: [number,number], onPick: (path: number, point: number)=>void) {
    this.clearPathHover();
    this.clearGroup(this.paths); this.pathBounds.makeEmpty(); this.pathFocus.makeEmpty(); this.pathPick=onPick;
    if (!this.meshes.length) {
      const box=new THREE.Box3();
      for (const path of data) for (const xyz of path.points) box.expandByPoint(new THREE.Vector3().fromArray(xyz));
      if (!box.isEmpty()) box.getCenter(this.origin);
    }
    const [low,high]=[Math.min(...range),Math.max(...range)];
    for (const path of data) {
      const coordinates=new Float32Array(path.points.length*3), colors=new Float32Array(coordinates.length);
      const active=path.id===selected;
      path.points.forEach((xyz,index)=>{
        const point=new THREE.Vector3().fromArray(xyz).sub(this.origin);
        point.toArray(coordinates,index*3); this.pathBounds.expandByPoint(point);
        if (active && index>=low && index<=high) this.pathFocus.expandByPoint(point);
        const color=new THREE.Color(active && index>=low && index<=high?0xe59b26:0x9b9b9b);
        color.toArray(colors,index*3);
      });
      const geometry=new THREE.BufferGeometry();
      geometry.setAttribute('position',new THREE.BufferAttribute(coordinates,3));
      geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));
      const line=new THREE.Line(geometry,new THREE.LineBasicMaterial({vertexColors:true,depthTest:false,transparent:true,opacity:active?1:0.45}));
      line.renderOrder=20;
      const points=new THREE.Points(geometry,new THREE.PointsMaterial({vertexColors:true,size:active?7:5,sizeAttenuation:false,depthTest:false}));
      points.renderOrder=21; points.userData={path:path.id,active};
      this.paths.add(line,points);
    }
  }
  showPaths(visible: boolean) {
    this.paths.visible=visible;
    if(!visible) this.clearPathHover();
  }
  fitPaths() {
    if(this.pathBounds.isEmpty()) return;
    this.paths.visible=true;this.camera.zoom=1;this.camera.updateProjectionMatrix();
    this.frame(this.camera.position.clone().sub(this.controls.target).normalize(),true);
  }
  private clearGroup(group: THREE.Group) {
    group.traverse(object => {
      if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Points) {
        object.geometry.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach(material=>material.dispose());
      }
    });
    group.clear();
  }
}
