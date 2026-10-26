import './style.css';
import { Viewer, type MeshData } from './viewer';

interface ModelInfo {
  id: string; filename: string; nodes: number; elements: number; faces: number;
  bodies: {id: number; name: string}[]; cases: {id: number; name: string; initial: boolean}[];
  follow: string[]; materials: string[]; missing_materials: string[];
}
interface Load {
  name: string; faces: number[]; magnitude: number; mode: string; direction: number[]; cases: number[];
  length_unit: string; force_unit: string; pressure_unit: string;
}
interface Preview { area: number; force: number[]; face_count: number; centers: number[][]; directions: number[][]; area_unit: string; force_unit: string; }

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header><div class="brand"><span class="brand-icon">sf</span><div>sfMarc <strong>Workbench</strong><small>面载荷编辑</small></div></div><span class="local"><i></i>本机处理 · 文件不上传</span><button id="export" class="primary" disabled>导出 DAT 文件组 ↗</button></header>
  <main>
    <aside class="left">
      <section><span class="eyebrow">01 / 模型</span><h2>打开模型</h2><p class="muted">选择 Simufact Welding 求解输入文件</p>
        <label class="file-button">选择 DAT 文件<input id="file" type="file" accept=".dat" /></label>
        <label class="directory-button">选择模型目录（含 INCLUDE）<input id="directory" type="file" webkitdirectory multiple /></label>
        <div id="root-picker" hidden><label>主 DAT 文件<select id="root-file"></select></label><button id="import-directory">读取所选模型</button></div>
        <div id="model-info" class="model-info">尚未导入模型</div>
      </section>
      <section><div class="section-title"><h2>实体</h2><span id="body-count" class="badge">0</span></div><div id="body-list"><p class="muted">导入后可以分别显示实体。</p></div></section>
      <section class="help"><h3>视图操作</h3><p>拖动左键：旋转<br>拖动右键：平移<br>滚动滚轮：缩放<br>点击表面：选择连续区域</p><div class="legend"><i class="amber"></i>已选表面<i class="cyan"></i>载荷方向</div></section>
      <details><summary>模型与计算设置</summary><p id="solver-info">导入后显示。</p><p id="material-info"></p><p>导出文件保留原有焊接、材料和求解设置。尚未通过 sfMarc 求解验证。</p></details>
    </aside>
    <div class="workspace"><div class="view-tools"><button id="fit">适合窗口</button><button data-view="top">俯视</button><button data-view="front">正视</button><button data-view="side">侧视</button><label><input id="edges" type="checkbox">网格边线</label></div>
      <div id="viewport"></div><div id="empty"><span class="empty-shape">◇</span><h1>在模型表面定义载荷</h1><p>导入 DAT 文件，点击一个面开始选择。</p></div>
      <div id="busy" hidden><span class="spinner"></span><span id="busy-text">正在读取模型…</span></div>
      <div class="selection-bar"><div><strong id="selected-count">0</strong><span>已选面</span></div><div><strong id="area-info">—</strong><span>表面积</span></div><button id="clear">清空选择</button></div>
      <div id="message" role="status" hidden></div>
    </div>
    <aside class="right">
      <section><span class="eyebrow">02 / 选择表面</span><h2>按夹角连续选择</h2><label>相邻面最大夹角 <span id="angle-value">20°</span><input id="angle" type="range" min="0" max="90" step="1" value="20"></label><p class="hint">从点击的面沿相邻表面扩展。</p><select id="selection-mode" aria-label="选择方式"><option value="replace">替换当前选择</option><option value="add">增加选择</option><option value="remove">移除选择</option></select></section>
      <section><span class="eyebrow">03 / 载荷</span><h2 id="load-heading">设置面载荷</h2><label>名称<input id="load-name" value="载荷 1" maxlength="80"></label>
        <div class="input-row"><label>大小<input id="magnitude" type="number" min="0" step="any" value="1"></label><label>压力单位<select id="pressure-unit"><option>MPa</option><option>kPa</option><option>Pa</option></select></label></div>
        <label>方向<select id="mode"><option value="inward">沿表面法向 · 向内</option><option value="outward">沿表面法向 · 向外</option><option value="manual">手动方向 · 全局坐标</option></select></label>
        <div id="direction" class="input-row" hidden><label>X<input id="dx" type="number" step="any" value="0"></label><label>Y<input id="dy" type="number" step="any" value="0"></label><label>Z<input id="dz" type="number" step="any" value="-1"></label></div>
        <details class="units" open><summary>模型单位</summary><div class="input-row"><label>长度<select id="length-unit"><option>m</option><option>mm</option></select></label><label>力<select id="force-unit"><option>N</option><option>kN</option></select></label></div><label class="check"><input id="unit-confirm" type="checkbox">已确认与原模型单位一致</label></details>
        <label>生效阶段<select id="case-mode"><option value="all">全部计算阶段</option><option value="custom">指定阶段</option></select></label>
        <div id="case-list" hidden></div>
        <button id="preview" class="full">预览方向与合力</button><div id="force-info" class="force-info">选面后显示预计合力</div><button id="save-load" class="primary full">保存载荷</button>
      </section>
      <section><div class="section-title"><h2>已保存载荷</h2><span id="load-count" class="badge">0</span></div><div id="load-list"><p class="muted">可以添加多个载荷。</p></div><div id="combined" hidden><label>查看阶段合力<select id="combined-case"></select></label><p id="combined-force"></p><p class="hint">同一阶段重叠面的载荷相加。</p></div></section>
    </aside>
  </main><footer><span id="status">就绪</span><span>原文件保留 · 导出到新文件</span></footer>`;

const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const input = (id: string) => get<HTMLInputElement>(id);
const select = (id: string) => get<HTMLSelectElement>(id);
const number = (id: string) => Number(input(id).value);
const format = (value: number) => new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 5}).format(value);
const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let info: ModelInfo | null = null;
let selected: number[] = [];
let lastSeed: number | null = null;
let baseSelection: number[] = [];
let mesh: MeshData | null = null;
let busy = false;
let editing: number | null = null;
let files: File[] = [];
const loads: {spec: Load; result: Preview}[] = [];

async function api(path: string, body?: object | FormData): Promise<Response> {
  const headers: Record<string,string> = {'X-Session-Token': token};
  if (info) headers['X-Model-Id'] = info.id;
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch('/api/'+path, {method: body ? 'POST' : 'GET', headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined});
  if (!response.ok) {
    const error = await response.json();
    throw new Error(typeof error.detail === 'string' ? error.detail : '输入内容不完整，请检查数值和选择。');
  }
  return response;
}

function message(text: string) { get('message').textContent = text; get('message').hidden = false; }
async function run(text: string, action: ()=>Promise<void>) {
  if (busy) return;
  busy = true; get('busy').hidden = false; get('busy-text').textContent = text; get('message').hidden = true;
  document.querySelectorAll<HTMLElement>('aside, header, .selection-bar').forEach(element=>element.inert=true);
  get('status').textContent = text;
  try { await action(); get('status').textContent = '就绪'; }
  catch (error) { message((error as Error).message); get('status').textContent = '操作未完成'; }
  finally {
    busy = false; get('busy').hidden = true;
    document.querySelectorAll<HTMLElement>('aside, header, .selection-bar').forEach(element=>element.inert=false);
    updateExport();
  }
}

const viewer = new Viewer(get('viewport'), face => { void run('正在选择表面…', async()=>{
  baseSelection = [...selected]; lastSeed = face; await pick();
}); });

function updateSelection() {
  viewer.highlight(selected);
  get('selected-count').textContent = String(selected.length);
  get('area-info').textContent = '—'; get('force-info').textContent = '点击预览查看面积和合力'; viewer.clearArrows();
}

async function pick() {
  if (lastSeed == null) return;
  const result = await (await api('select', {seed: lastSeed, angle: number('angle')})).json();
  const region: number[] = result.faces;
  const mode = select('selection-mode').value;
  selected = mode === 'add' ? [...new Set([...baseSelection, ...region])] : mode === 'remove' ? baseSelection.filter(face=>!region.includes(face)) : region;
  updateSelection();
  if (selected.length) await showPreview();
}

function currentLoad(): Load {
  if (!info) throw new Error('请导入模型');
  const cases = select('case-mode').value === 'all' ? info.cases.filter(c=>!c.initial).map(c=>c.id) : Array.from(get('case-list').querySelectorAll<HTMLInputElement>('input:checked')).map(c=>Number(c.value));
  return {name: input('load-name').value.trim() || '面载荷', faces: [...selected], magnitude: number('magnitude'), mode: select('mode').value,
    direction: [number('dx'),number('dy'),number('dz')], cases, length_unit: select('length-unit').value, force_unit: select('force-unit').value, pressure_unit: select('pressure-unit').value};
}

async function showPreview(): Promise<Preview> {
  const result: Preview = await (await api('preview', currentLoad())).json();
  viewer.showArrows(result.centers, result.directions);
  get('area-info').textContent = format(result.area)+' '+result.area_unit;
  get('force-info').textContent = `合力 (${result.force.map(format).join(', ')}) ${result.force_unit}`;
  return result;
}

function updateExport() {
  const consistent = loads.every(({spec})=>spec.length_unit===select('length-unit').value && spec.force_unit===select('force-unit').value);
  get<HTMLButtonElement>('export').disabled = busy || !loads.length || !consistent || !input('unit-confirm').checked;
}
function renderLoads() {
  const list = get('load-list'); list.replaceChildren();
  loads.forEach(({spec, result}, index) => {
    const row = document.createElement('div'); row.className = 'load-card';
    const title = document.createElement('strong'); title.textContent = spec.name;
    const detail = document.createElement('small'); detail.textContent = `${format(spec.magnitude)} ${spec.pressure_unit} · ${spec.faces.length.toLocaleString()} 个面`;
    const buttons = document.createElement('div'); buttons.className = 'row-buttons';
    const edit = document.createElement('button'); edit.textContent = '编辑'; edit.onclick = ()=>editLoad(index);
    const remove = document.createElement('button'); remove.textContent = '删除'; remove.onclick = ()=>{
      if (busy) return; loads.splice(index,1); editing = null; renderLoads();
    };
    buttons.append(edit,remove); row.append(title,detail,buttons); list.append(row);
  });
  get('load-count').textContent = String(loads.length); get('combined').hidden = !loads.length;
  input('load-name').value = '载荷 '+(loads.length+1);
  get('load-heading').textContent = '设置面载荷'; updateCombined(); updateExport();
}

function editLoad(index: number) {
  if (busy) return;
  editing = index; const spec = loads[index].spec;
  selected = [...spec.faces]; lastSeed = null; updateSelection();
  for (const [id,value] of Object.entries({'load-name':spec.name, magnitude:spec.magnitude, mode:spec.mode, dx:spec.direction[0],dy:spec.direction[1],dz:spec.direction[2], 'pressure-unit':spec.pressure_unit})) input(id).value=String(value);
  select('case-mode').value='custom'; get('case-list').hidden=false;
  get('case-list').querySelectorAll<HTMLInputElement>('input').forEach(element=>element.checked=spec.cases.includes(Number(element.value)));
  get('direction').hidden=spec.mode!=='manual'; get('load-heading').textContent='编辑：'+spec.name;
  void run('正在预览载荷…', async()=>{await showPreview();});
}

function updateCombined() {
  const id = Number(select('combined-case').value);
  const force = [0,0,0];
  for (const {spec,result} of loads) if (spec.cases.includes(id)) result.force.forEach((value,i)=>force[i]+=value*(spec.force_unit==='kN'?1000:1));
  get('combined-force').textContent=`合力 (${force.map(format).join(', ')}) N`;
}

function fileName(file: File) { return file.webkitRelativePath ? file.webkitRelativePath.split('/').slice(1).join('/') : file.name; }
async function importFiles(root: string) {
  const data = new FormData(); data.append('root',root);
  files.forEach(file=>data.append('files',file,fileName(file)));
  const next: ModelInfo = await (await api('model',data)).json();
  info=next;
  mesh=await (await api('mesh')).json();
  viewer.load(mesh!); get('empty').hidden=true; selected=[]; loads.length=0; editing=null; lastSeed=null;
  get('model-info').textContent=`${info.filename}\n${info.nodes.toLocaleString()} 节点\n${info.elements.toLocaleString()} 单元 · ${info.faces.toLocaleString()} 外表面`;
  get('body-count').textContent=String(info.bodies.length);
  const bodies=get('body-list'); bodies.replaceChildren();
  info.bodies.forEach(body=>{
    const label=document.createElement('label'); label.className='body-row';
    const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=true;
    checkbox.onchange=()=>{ viewer.showBody(body.id,checkbox.checked); if (!checkbox.checked) {selected=selected.filter(face=>mesh!.body_ids[face]!==body.id);lastSeed=null;updateSelection();} };
    label.append(checkbox,document.createTextNode(body.name)); bodies.append(label);
  });
  const cases=get('case-list'), combined=select('combined-case'); cases.replaceChildren(); combined.replaceChildren();
  info.cases.forEach(item=>{
    const label=document.createElement('label'); const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.value=String(item.id); checkbox.checked=!item.initial;
    label.append(checkbox,document.createTextNode(item.name+(item.initial?'（初始）':''))); cases.append(label);
    if (!item.initial) combined.add(new Option(item.name,String(item.id)));
  });
  get('solver-info').textContent=info.follow.length ? '法向随动设置：'+info.follow.join('; ') : '原文件没有 FOLLOW FOR；法向预览依据初始几何。';
  get('material-info').textContent=info.missing_materials.length ? '未选择的材料文件：'+info.missing_materials.join('、')+'。计算时需要原运行环境提供。' : '';
  input('unit-confirm').checked=false; updateSelection(); renderLoads();
}

input('file').onchange=()=>{files=Array.from(input('file').files||[]); if(files[0]) void run('正在读取模型，请稍候…',()=>importFiles(files[0].name));};
input('directory').onchange=()=>{
  files=Array.from(input('directory').files||[]); const roots=files.filter(file=>file.name.toLowerCase().endsWith('.dat'));
  const picker=select('root-file'); picker.replaceChildren(); roots.forEach(file=>picker.add(new Option(fileName(file),fileName(file))));
  get('root-picker').hidden=false; if(!roots.length) message('所选目录没有 DAT 文件');
};
get('import-directory').onclick=()=>{void run('正在读取模型目录…',()=>importFiles(select('root-file').value));};
get('fit').onclick=()=>viewer.fit();
document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button=>button.onclick=()=>viewer.fit(button.dataset.view!));
input('edges').onchange=()=>viewer.showEdges(input('edges').checked);
input('angle').oninput=()=>{get('angle-value').textContent=input('angle').value+'°';};
input('angle').onchange=()=>{if(lastSeed!==null) void run('正在更新选择…',pick);};
get('clear').onclick=()=>{if(!busy){selected=[];lastSeed=null;editing=null;updateSelection();}};
select('mode').onchange=()=>{get('direction').hidden=select('mode').value!=='manual';viewer.clearArrows();};
select('case-mode').onchange=()=>{get('case-list').hidden=select('case-mode').value!=='custom';};
input('unit-confirm').onchange=updateExport;
for (const id of ['length-unit','force-unit']) select(id).onchange=()=>{
  input('unit-confirm').checked=false; updateExport(); viewer.clearArrows();
  void run('正在按模型单位更新载荷…',async()=>{
    const updated = await Promise.all(loads.map(async load=>{
      const spec = {...load.spec, length_unit:select('length-unit').value, force_unit:select('force-unit').value};
      const result:Preview = await (await api('preview',spec)).json();
      return {spec,result};
    }));
    loads.splice(0,loads.length,...updated); updateCombined();
    if(selected.length) await showPreview();
  });
};
select('combined-case').onchange=updateCombined;
get('preview').onclick=()=>{void run('正在计算载荷…',async()=>{await showPreview();});};
get('save-load').onclick=()=>{void run('正在保存载荷…',async()=>{
  const spec=currentLoad(); const result:Preview=await (await api('preview',spec)).json();
  if(editing!==null) loads[editing]={spec,result}; else loads.push({spec,result});
  editing=null; renderLoads();
});};
get('export').onclick=()=>{void run('正在生成新文件…',async()=>{
  if(!input('unit-confirm').checked) throw new Error('请确认模型单位');
  const response=await api('export',{loads:loads.map(load=>load.spec)});
  const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;
  link.download=info!.filename.split('/').pop()!.replace(/\.dat$/i,'')+'_loads.zip';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),10000);message('已生成文件组，原文件保持不变。');
});};
if(!token) message('请通过启动程序打开工具。');
