import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export interface MeshData {
  points: number[]; triangles: number[]; triangle_faces: number[]; body_ids: number[];
}

export class Viewer {
  private renderer = new THREE.WebGLRenderer({antialias: true});
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(40, 1, 0.001, 10000);
  private controls: OrbitControls;
  private models = new THREE.Group();
  private arrows = new THREE.Group();
  private raycaster = new THREE.Raycaster();
  private origin = new THREE.Vector3();
  private size = 1;
  private meshes: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>[] = [];
  private selected = new Set<number>();
  private palette = [0x9cbbc8, 0xc2cdd5, 0x7897a8, 0xa8b2c7, 0x7faaaa, 0xb6bcb2, 0x89a6bf];
  private edgesVisible = false;

  constructor(private host: HTMLElement, onPick: (face: number) => void) {
    this.scene.background = new THREE.Color(0x101d2b);
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.host.append(this.renderer.domElement);
    this.renderer.domElement.setAttribute('aria-label', '三维模型，点击表面选择，拖动旋转，滚轮缩放');
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.scene.add(new THREE.HemisphereLight(0xf1f6ff, 0x617385, 2.5));
    const light = new THREE.DirectionalLight(0xffffff, 3);
    light.position.set(3, -4, 5);
    this.scene.add(light, this.models, this.arrows);
    let down: [number, number] = [0, 0];
    this.renderer.domElement.addEventListener('pointerdown', event => { down = [event.clientX, event.clientY]; });
    this.renderer.domElement.addEventListener('pointerup', event => {
      if (event.button !== 0 || Math.hypot(event.clientX-down[0], event.clientY-down[1]) > 4) return;
      const rect = this.renderer.domElement.getBoundingClientRect();
      this.raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1, -(event.clientY-rect.top)/rect.height*2+1), this.camera);
      const hit = this.raycaster.intersectObjects(this.meshes.filter(mesh => mesh.visible), false)[0];
      if (hit && hit.faceIndex != null) onPick(hit.object.userData.faceIds[hit.faceIndex]);
    });
    new ResizeObserver(() => {
      const width = this.host.clientWidth, height = this.host.clientHeight;
      this.renderer.setSize(width, height);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
    }).observe(this.host);
    this.renderer.setAnimationLoop(() => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  load(data: MeshData) {
    this.clearGroup(this.models);
    this.clearGroup(this.arrows);
    this.meshes = [];
    this.selected.clear();
    const box = new THREE.Box3();
    for (let i=0; i<data.points.length; i+=3) box.expandByPoint(new THREE.Vector3(data.points[i], data.points[i+1], data.points[i+2]));
    box.getCenter(this.origin);
    this.size = box.getSize(new THREE.Vector3()).length();
    const bodyTriangles = new Map<number, number[]>();
    data.triangle_faces.forEach((face, triangle) => {
      const body = data.body_ids[face];
      if (!bodyTriangles.has(body)) bodyTriangles.set(body, []);
      bodyTriangles.get(body)!.push(triangle);
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
      geometry.computeBoundingSphere();
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({vertexColors: true, roughness: 0.72, metalness: 0.08, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1}));
      mesh.userData = {body, faceIds, base: color};
      const edges = new THREE.LineSegments(new THREE.WireframeGeometry(geometry), new THREE.LineBasicMaterial({color: 0x24394b, transparent: true, opacity: 0.35}));
      edges.visible = this.edgesVisible;
      mesh.add(edges);
      this.models.add(mesh);
      this.meshes.push(mesh);
    }
    this.fit();
  }

  fit(view = 'iso') {
    const directions: Record<string, number[]> = {iso: [1,-1,0.85], top: [0,0,1], front: [0,-1,0], side: [1,0,0]};
    this.camera.up.set(0, view === 'top' ? 1 : 0, view === 'top' ? 0 : 1);
    this.camera.position.fromArray(directions[view]).normalize().multiplyScalar(this.size * 1.65);
    this.camera.near = this.size / 10000;
    this.camera.far = this.size * 1000;
    this.camera.updateProjectionMatrix();
    this.controls.target.set(0,0,0);
    this.controls.update();
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

  showBody(body: number, visible: boolean) { this.meshes.filter(mesh=>mesh.userData.body===body).forEach(mesh=>mesh.visible=visible); }
  showEdges(visible: boolean) {
    this.edgesVisible = visible;
    this.meshes.forEach(mesh=>mesh.children.forEach(child=>child.visible=visible));
  }
  showArrows(centers: number[][], directions: number[][]) {
    this.clearGroup(this.arrows);
    centers.forEach((center, i) => {
      const direction = new THREE.Vector3().fromArray(directions[i]);
      const point = new THREE.Vector3().fromArray(center).sub(this.origin);
      const length = this.size * 0.035;
      const arrow = new THREE.ArrowHelper(direction, point.clone().addScaledVector(direction, -length), length, 0x38ded5, length*0.25, length*0.12);
      this.arrows.add(arrow);
    });
  }
  clearArrows() { this.clearGroup(this.arrows); }
  private clearGroup(group: THREE.Group) {
    group.traverse(object => {
      if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
        object.geometry.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach(material=>material.dispose());
      }
    });
    group.clear();
  }
}
