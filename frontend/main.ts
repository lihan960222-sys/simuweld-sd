import './style.css';
import './import.css';
import { Viewer, type MeshData } from './viewer';
import { WeldingTool } from './welding';
import { setupSidebarResize } from './sidebarResize';
import { iconButton, setIcon, makeCollapsible } from './ui';

interface ModelInfo {
  supports_loads: boolean; length_unit?: string;
  id: string; filename: string; nodes: number; elements: number; faces: number;
  bodies: {id: number; name: string; volume: number | null}[]; cases: {id: number; name: string; initial: boolean}[];
}
interface Load {
  name: string; faces: number[]; magnitude: number; mode: string; direction: number[]; cases: number[];
  length_unit: string; force_unit: string; pressure_unit: string;
}
interface Preview { area: number; force: number[]; face_count: number; face_ids: number[]; centers: number[][]; directions: number[][]; area_unit: string; force_unit: string; }
interface WorkspaceTool { id: string; label: string; activate?: ()=>void; pick?: (face: number)=>void; }

const planeIcon = (plane: string) => `<svg class="plane-axis" viewBox="0 0 36 36" aria-hidden="true" focusable="false"><g class="axis-${plane[0]}"><path d="M10 26h14m-3-3 3 3-3 3"/><text x="26" y="32">${plane[0].toUpperCase()}</text></g><g class="axis-${plane[1]}"><path d="M10 26V12m-3 3 3-3 3 3"/><text x="7" y="9">${plane[1].toUpperCase()}</text></g><circle cx="10" cy="26" r="1.5" fill="currentColor"/></svg>`;

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header><div class="brand"><img class="brand-icon" src="/icon.svg" alt="激光焊接标识" width="36" height="36"><strong>SIMUWELD SD</strong></div><nav id="tools" aria-label="功能"></nav><button id="export" class="primary" disabled>导出 DAT 文件组 ↗</button></header>
  <main>
    <aside class="left">
      <section class="model-entry">
        <div class="model-actions"><button id="open-model" class="primary full">打开模型</button><div id="model-info-wrap" class="info-wrap" hidden><button id="model-info-button" aria-label="模型信息" aria-expanded="false" aria-controls="model-info">!</button><div id="model-info" class="model-info" role="tooltip" hidden></div></div></div>
      </section>
      <section><div class="section-title"><h2>实体</h2><span id="body-count" class="badge">0</span></div><div id="body-list"><p class="muted">导入后可以分别显示实体。</p></div></section>
    </aside>
    <div id="resize-left" class="sidebar-resizer" role="separator" aria-label="调整左侧栏宽度" aria-orientation="vertical" tabindex="0"></div>
    <div class="workspace"><div class="view-tools"><button id="fit" aria-label="回到中心" title="回到中心"><svg viewBox="0 0 36 36" aria-hidden="true" focusable="false"><path class="fit-corners" d="M11 4H4v7m21-7h7v7M4 25v7h7m21-7v7h-7"/><path class="fit-model" d="M18 11v14m-7-7h14"/><circle class="fit-model" cx="18" cy="18" r="4"/></svg></button><button data-view="xy" aria-label="XY 平面" title="XY 平面">${planeIcon('xy')}</button><button data-view="xz" aria-label="XZ 平面" title="XZ 平面">${planeIcon('xz')}</button><button data-view="yz" aria-label="YZ 平面" title="YZ 平面">${planeIcon('yz')}</button><select id="projection" aria-label="投影方式"><option value="perspective">透视</option><option value="orthographic">正交</option></select><select id="render-mode" aria-label="渲染模式"><option value="solid">实体</option><option value="wireframe">框线</option><option value="solid-edges">实体与边线</option></select><button id="explode" aria-label="爆炸图" title="爆炸图" aria-expanded="false" disabled>爆炸图</button><label id="explosion-control" hidden>展开程度<input id="explosion" aria-label="爆炸图展开程度" type="range" min="0" max="100" value="0"><output id="explosion-value">0%</output></label></div>
      <div id="viewport"></div><div id="empty"><img class="empty-shape" src="/icon.svg" alt="" width="64" height="64"><h1>打开模型</h1><p>支持 DAT、INP、BDF 和 STEP 等格式。</p></div>
      <div id="busy" hidden><span class="spinner"></span><span id="busy-text">正在读取模型…</span></div>
      <div class="selection-bar" data-tool="surface-load" hidden><div><strong id="selected-count">0</strong><span>已选面</span></div><div><strong id="area-info">—</strong><span>表面积</span></div><button id="clear">清空选择</button></div>
      <div id="message" role="status" hidden></div>
    </div>
    <div id="resize-right" class="sidebar-resizer" role="separator" aria-label="调整右侧栏宽度" aria-orientation="vertical" tabindex="0"></div>
    <aside class="right" data-tool="surface-load" aria-label="面载荷参数" hidden>
      <div class="tool-heading"><h1>面载荷</h1><p class="hint">选择表面，设置压力与方向。</p></div>
      <section><h2>按夹角连续选择</h2><label>相邻面最大夹角 <span id="angle-value">20°</span><input id="angle" type="range" min="0" max="90" step="1" value="20"></label><p class="hint">从点击的面沿相邻表面扩展。</p><select id="selection-mode" aria-label="选择方式"><option value="replace">替换当前选择</option><option value="add">增加选择</option><option value="remove">移除选择</option></select></section>
      <section><h2 id="load-heading">设置面载荷</h2><label>名称<input id="load-name" value="载荷 1" maxlength="80"></label>
        <div class="input-row"><label>大小<input id="magnitude" type="number" min="0" step="any" value="1"></label><label>压力单位<select id="pressure-unit"><option>MPa</option><option>kPa</option><option>Pa</option></select></label></div>
        <label>方向<select id="mode"><option value="inward">沿表面法向 · 向内</option><option value="outward">沿表面法向 · 向外</option><option value="manual">手动方向 · 全局坐标</option></select></label>
        <div id="direction" class="input-row" hidden><label>X<input id="dx" type="number" step="any" value="0"></label><label>Y<input id="dy" type="number" step="any" value="0"></label><label>Z<input id="dz" type="number" step="any" value="-1"></label></div>
        <label>生效阶段<select id="case-mode"><option value="all">全部计算阶段</option><option value="custom">指定阶段</option></select></label>
        <div id="case-list" hidden></div>
        <div id="force-info" class="force-info">选面后显示合力</div><button id="save-load" class="primary full">保存载荷</button>
      </section>
      <section><div class="section-title"><h2>载荷</h2><button id="show-all-loads" disabled aria-pressed="false"></button><span id="load-count" class="badge">0</span></div><div id="load-list"></div><div id="combined" hidden><label>阶段合力<select id="combined-case"></select></label><p id="combined-force"></p></div></section>
    </aside>
  </main><footer>copyright LH</footer>
  <dialog id="model-dialog" class="model-dialog" aria-labelledby="model-dialog-title">
    <div class="import-heading"><h2 id="model-dialog-title">打开模型</h2><button id="close-model-dialog" aria-label="关闭模型导入">×</button></div>
    <label>输入格式<select id="model-format"><option value="">按文件扩展名</option><option value="marc">Marc / sfMarc DAT</option><option value="inp">Abaqus INP</option><option value="bdf">Nastran BDF / NAS / DAT</option><option value="step">CAD STP / STEP</option></select></label>
    <label>模型文件单位制<select id="import-units"><option value="m">m · kg · s · N · Pa</option><option value="mm">mm · t · s · N · MPa</option></select></label>
    <button id="choose-model-files" class="file-button full">选择模型文件</button><input id="file" type="file" accept=".dat,.inp,.bdf,.nas,.stp,.step,.inc,.include" multiple hidden />
    <button id="choose-model-directory" class="directory-button">选择模型目录（含 INCLUDE）</button><input id="directory" type="file" webkitdirectory multiple hidden />
    <p class="hint">Marc 的关联文件可以一起选择。DAT 默认按 Marc 读取，Nastran DAT 请在输入格式中指定。STEP 使用文件内的长度定义。</p>
    <div id="root-picker" hidden><label>主模型文件<select id="root-file"></select></label><button id="import-directory" class="primary full">读取所选模型</button></div>
    <p id="model-import-status" role="status" hidden>正在读取模型…</p><p id="model-import-error" role="alert" hidden></p>
  </dialog>`;

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
const displayedLoads=new Set<number>();
let activeTool: WorkspaceTool;
let currentPreview: Preview | null = null;
let previewGeneration=0;
let modelLengthUnit='m';
const modelScale=()=>modelLengthUnit==='m'?1000:1;

function confirmLeave(event: BeforeUnloadEvent) {
  event.preventDefault();
  event.returnValue = true;
}
document.addEventListener('input',()=>window.addEventListener('beforeunload',confirmLeave));

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

let messageAnimation: Animation | null = null;
function hideMessage() {
  messageAnimation?.cancel(); messageAnimation=null; get('message').hidden=true;
}
function message(text: string) {
  hideMessage(); get('message').textContent=text; get('message').hidden=false;
  if(get<HTMLDialogElement>('model-dialog').open) {get('model-import-error').textContent=text;get('model-import-error').hidden=false;}
}
function fadeMessage() {
  const notice=get('message');
  if(notice.hidden || messageAnimation) return;
  const animation=notice.animate([{opacity:1},{opacity:0}],{duration:matchMedia('(prefers-reduced-motion: reduce)').matches?1:300,fill:'forwards'});
  messageAnimation=animation;
  animation.onfinish=()=>{if(messageAnimation===animation) hideMessage();};
}
document.addEventListener('pointerdown',fadeMessage);
async function run(text: string, action: ()=>Promise<void>) {
  if (busy) return;
  busy = true; get('busy').hidden = false; get('busy-text').textContent = text; fadeMessage();
  document.querySelectorAll<HTMLElement>('aside, header, .selection-bar, #model-dialog').forEach(element=>element.inert=true);
  get('model-import-error').hidden=true;get('model-import-status').hidden=false;
  try { await action(); }
  catch (error) { message((error as Error).message); }
  finally {
    busy = false; get('busy').hidden = true;
    document.querySelectorAll<HTMLElement>('aside, header, .selection-bar, #model-dialog').forEach(element=>element.inert=false);
    get('model-import-status').hidden=true;
    updateExport();
  }
}

const viewer = new Viewer(get('viewport'), face => activeTool.pick?.(face));
const welding = new WeldingTool(viewer, {api,run,message,unit:()=>'mm',hasModel:()=>!!info,changed:()=>{
  window.addEventListener('beforeunload',confirmLeave);
  get('empty').hidden=!!info || welding.hasPaths;
  updateExport();
}});
makeCollapsible();
setIcon(get('open-model'),'upload','打开模型');setIcon(get('model-info-button'),'info','模型信息');
setIcon(get('save-load'),'save','保存载荷');setIcon(get('clear'),'close','清空选择');
setIcon(get('show-all-loads'),'all','显示全部载荷');setIcon(get('explode'),'explode','爆炸图');
const workspaceTools: WorkspaceTool[] = [
  {id:'model', label:'模型查看'},
  {id:'surface-load', label:'面载荷', activate:()=>{if(displayedLoads.size) drawSavedLoads();else {viewer.highlight(selected); drawPreview();}}, pick:face=>{
    void run('正在选择表面…',async()=>{baseSelection=[...selected];lastSeed=face;await pick();});
  }},
  {id:'welding', label:'生成焊路'},
];
for (const tool of workspaceTools) {
  const button=document.createElement('button'); button.textContent=tool.label; button.id='tool-'+tool.id;
  button.onclick=()=>activateTool(tool); get('tools').append(button);
}
function activateTool(tool: WorkspaceTool) {
  if(busy || activeTool===tool) return;
  if(tool.id==='surface-load' && info?.supports_loads===false) {message('面载荷需要导入 Marc DAT 模型。');return;}
  activeTool=tool;
  if(tool.id==='welding') resetExplosion();
  get<HTMLButtonElement>('explode').disabled=!info || tool.id==='welding';
  document.querySelectorAll<HTMLElement>('[data-tool]').forEach(panel=>panel.hidden=panel.dataset.tool!==tool.id);
  document.querySelector('main')!.classList.toggle('has-tool-panel',!!document.querySelector('aside.right:not([hidden])'));
  workspaceTools.forEach(item=>get('tool-'+item.id).setAttribute('aria-pressed',String(item===tool)));
  viewer.highlight([]); viewer.clearArrows(); welding.activate(tool.id==='welding'); tool.activate?.(); updateExport();
  get('empty').hidden=!!info || (tool.id==='welding' && welding.hasPaths);
}
activateTool(workspaceTools[0]);

function updateSelection() {
  previewGeneration++;
  displayedLoads.clear();updateLoadChecks();
  viewer.highlight(activeTool.id==='surface-load'?selected:[]);
  currentPreview=null;
  get('selected-count').textContent = String(selected.length);
  get('area-info').textContent = '—'; get('force-info').textContent = '选面后显示合力'; viewer.clearArrows();
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
    direction: [number('dx'),number('dy'),number('dz')], cases, length_unit: modelLengthUnit, force_unit: 'N', pressure_unit: select('pressure-unit').value};
}

async function showPreview(): Promise<Preview> {
  const generation=++previewGeneration,modelId=info?.id;
  const result: Preview = await (await api('preview', currentLoad())).json();
  if(generation!==previewGeneration || modelId!==info?.id) return result;
  currentPreview=result; drawPreview();
  if(activeTool.id==='surface-load') viewer.highlight(selected);
  get('selected-count').textContent=String(selected.length);
  get('area-info').textContent = format(result.area*modelScale()**2)+' mm²';
  get('force-info').textContent = `合力 (${result.force.map(format).join(', ')}) N`;
  return result;
}
function drawPreview() {
  if(activeTool.id==='surface-load' && currentPreview) viewer.showArrows(currentPreview.centers.map(point=>point.map(value=>value*modelScale())),currentPreview.directions,currentPreview.area*modelScale()**2,currentPreview.face_ids);
}

function updateLoadChecks() {
  get('load-list').querySelectorAll<HTMLInputElement>('[data-load-visible]').forEach(box=>box.checked=displayedLoads.has(Number(box.dataset.loadVisible)));
  get('load-list').querySelectorAll<HTMLButtonElement>('.load-select').forEach(button=>button.setAttribute('aria-pressed',String(displayedLoads.has(Number(button.dataset.loadIndex)))));
  get('show-all-loads').setAttribute('aria-pressed',String(!!loads.length && displayedLoads.size===loads.length));
  get<HTMLButtonElement>('show-all-loads').disabled=!loads.length;
}
function drawSavedLoads() {
  previewGeneration++;
  currentPreview=null;updateLoadChecks();
  const shown=[...displayedLoads].map(index=>loads[index]);
  const faces=[...new Set(shown.flatMap(load=>load.spec.faces))];
  viewer.highlight(faces);
  viewer.showArrows(shown.flatMap(load=>load.result.centers.map(point=>point.map(value=>value*modelScale()))),shown.flatMap(load=>load.result.directions),shown.reduce((area,load)=>area+load.result.area*modelScale()**2,0),shown.flatMap(load=>load.result.face_ids));
  get('selected-count').textContent=String(faces.length);
  get('area-info').textContent=shown.length===1?format(shown[0].result.area*modelScale()**2)+' mm²':'—';
  get('force-info').textContent=shown.length===1?`合力 (${shown[0].result.force.map(format).join(', ')}) N`:`已显示 ${shown.length} 项载荷`;
}

function updateExport() {
  if(activeTool?.id==='welding') {
    setIcon(get('export'),'download','导出 Python 脚本');
    get<HTMLButtonElement>('export').disabled=busy || !welding.canExport;
    return;
  }
  setIcon(get('export'),'download','导出 DAT 文件组');
  const consistent = loads.every(({spec})=>spec.length_unit===modelLengthUnit && spec.force_unit==='N');
  get<HTMLButtonElement>('export').disabled = busy || !loads.length || !consistent;
}
function renderLoads() {
  const list = get('load-list'); list.replaceChildren();
  loads.forEach(({spec, result}, index) => {
    const row = document.createElement('div'); row.className = 'load-card';
    const title = document.createElement('button'); title.className='load-select';title.textContent = spec.name;title.dataset.loadIndex=String(index);title.title='显示 '+spec.name;
    title.onclick=()=>{displayedLoads.clear();displayedLoads.add(index);drawSavedLoads();};
    const check=document.createElement('input');check.type='checkbox';check.dataset.loadVisible=String(index);check.setAttribute('aria-label','显示 '+spec.name);
    check.onchange=()=>{if(check.checked) displayedLoads.add(index);else displayedLoads.delete(index);drawSavedLoads();};
    const detail = document.createElement('small'); detail.textContent = `${format(spec.magnitude)} ${spec.pressure_unit} · ${spec.faces.length.toLocaleString()} 个面`;
    const edit = iconButton('edit','编辑 '+spec.name); edit.onclick = ()=>editLoad(index);
    const remove = iconButton('delete','删除 '+spec.name); remove.onclick = ()=>{
      if (busy) return; loads.splice(index,1); editing = null;displayedLoads.clear(); renderLoads();drawSavedLoads();
    };
    const heading=document.createElement('div');heading.className='load-row';heading.append(check,title,edit,remove);
    row.append(heading,detail); list.append(row);
  });
  get('load-count').textContent = String(loads.length); get('combined').hidden = !loads.length;
  input('load-name').value = '载荷 '+(loads.length+1);
  get('load-heading').textContent = '设置面载荷'; updateCombined(); updateExport();updateLoadChecks();
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
  await displayModel(next);
}

async function displayModel(next: ModelInfo) {
  info=next;
  modelLengthUnit=info.length_unit || select('import-units').value;
  mesh=await (await api('mesh')).json();
  mesh!.points=mesh!.points.map(value=>value*modelScale());
  viewer.load(mesh!); get('empty').hidden=true; selected=[]; loads.length=0; editing=null; lastSeed=null;
  resetExplosion();get<HTMLButtonElement>('explode').disabled=activeTool.id==='welding';
  setIcon(get('open-model'),'upload','删除并重新上传');get('model-info-wrap').hidden=false;
  get('model-info').textContent=`${info.filename}\n${info.nodes.toLocaleString()} 节点\n${info.elements.toLocaleString()} 单元 · ${info.faces.toLocaleString()} 外表面`;
  get('body-count').textContent=String(info.bodies.length);
  const bodies=get('body-list'); bodies.replaceChildren();
  const tree=document.createElement('details');tree.className='body-tree';tree.open=true;
  const heading=document.createElement('summary'),all=document.createElement('input'),name=document.createElement('span');
  all.type='checkbox';all.checked=true;all.setAttribute('aria-label','全部实体显示');
  name.textContent=info.filename;name.title=info.filename;heading.append(all,name);tree.append(heading);bodies.append(tree);
  const visibilityInputs:HTMLInputElement[]=[];
  const updateAll=()=>{const count=visibilityInputs.filter(checkbox=>checkbox.checked).length;all.checked=count===visibilityInputs.length;all.indeterminate=count>0 && !all.checked;};
  const setAll=(visible:boolean)=>{for(const checkbox of visibilityInputs){checkbox.checked=visible;viewer.showBody(Number(checkbox.dataset.bodyVisible),visible);} if(!visible){selected=[];lastSeed=null;updateSelection();}updateAll();};
  all.onclick=event=>event.stopPropagation();all.onchange=()=>setAll(all.checked);
  [...info.bodies].sort((first,second)=>(second.volume ?? -1)-(first.volume ?? -1) || first.id-second.id).forEach(body=>{
    const card=document.createElement('details'); card.className='body-card';card.dataset.bodyId=String(body.id);
    const summary=document.createElement('summary');
    const label=document.createElement('label'); label.className='body-row';
    const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=true;
    checkbox.dataset.bodyVisible=String(body.id);visibilityInputs.push(checkbox);
    checkbox.onclick=event=>event.stopPropagation();
    checkbox.onchange=()=>{ viewer.showBody(body.id,checkbox.checked);updateAll(); if (!checkbox.checked) {selected=selected.filter(face=>mesh!.body_ids[face]!==body.id);lastSeed=null;updateSelection();} };
    label.append(checkbox,document.createTextNode(body.name));
    const options=document.createElement('div'); options.className='body-options';
    const color=document.createElement('input'); color.type='color'; color.value=viewer.bodyColor(body.id); color.setAttribute('aria-label',body.name+' 颜色');
    color.title=body.name+' 颜色';color.onclick=event=>event.stopPropagation();
    color.oninput=()=>viewer.setBodyColor(body.id,color.value);
    const transparent=document.createElement('label'); transparent.className='check';
    const opacity=document.createElement('input'); opacity.type='checkbox'; opacity.setAttribute('aria-label',body.name+' 半透明');
    transparent.append(opacity,document.createTextNode('半透明'));
    const transparency=document.createElement('label'); transparency.className='transparency';
    const amount=document.createElement('output'); amount.value='65%';
    const slider=document.createElement('input'); slider.type='range'; slider.min='0'; slider.max='100'; slider.step='1'; slider.value='65'; slider.disabled=true;
    slider.id='transparency-'+body.id; slider.setAttribute('aria-label',body.name+' 透明度'); slider.setAttribute('aria-valuetext','65%');
    transparency.htmlFor=slider.id; amount.htmlFor.add(slider.id);
    const applyOpacity=()=>{ amount.value=slider.value+'%'; slider.setAttribute('aria-valuetext',amount.value); viewer.setBodyOpacity(body.id,opacity.checked?1-Number(slider.value)/100:1); };
    slider.oninput=applyOpacity;
    opacity.onchange=()=>{slider.disabled=!opacity.checked;applyOpacity();};
    transparency.append(document.createTextNode('透明度'),amount,slider);
    const volume=document.createElement('small');volume.className='body-volume';volume.textContent=body.volume===null?'体积未确定':format(body.volume*modelScale()**3)+' mm³';
    options.append(transparent,volume);summary.append(label,color);card.append(summary,options,transparency);tree.append(card);
  });
  const cases=get('case-list'), combined=select('combined-case'); cases.replaceChildren(); combined.replaceChildren();
  info.cases.forEach(item=>{
    const label=document.createElement('label'); const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.value=String(item.id); checkbox.checked=!item.initial;
    label.append(checkbox,document.createTextNode(item.name+(item.initial?'（初始）':''))); cases.append(label);
    if (!item.initial) combined.add(new Option(item.name,String(item.id)));
  });
  updateSelection(); renderLoads(); welding.refresh();
}

async function openSelectedModel() {
  const root=select('root-file').value;
  const file=files.find(item=>fileName(item)===root);
  if(!file) throw new Error('请选择主模型文件。');
  const format=select('model-format').value;
  const extension=file.name.split('.').pop()!.toLowerCase();
  if(format==='marc' || (!format && extension==='dat')) await importFiles(root);
  else {
    const data=new FormData();data.append('file',file);if(format) data.append('format',format);
    await displayModel(await (await api('background',data)).json());
    if(activeTool.id==='surface-load') {
      activeTool=workspaceTools[0];
      document.querySelectorAll<HTMLElement>('[data-tool]').forEach(panel=>panel.hidden=true);
      document.querySelector('main')!.classList.remove('has-tool-panel');
      workspaceTools.forEach(tool=>get('tool-'+tool.id).setAttribute('aria-pressed',String(tool===activeTool)));
    }
  }
  get<HTMLDialogElement>('model-dialog').close();
  input('file').value='';input('directory').value='';
}

function prepareModelFiles(nextFiles: File[], automatic: boolean) {
  if(!nextFiles.length) return;
  files=nextFiles;
  const roots=files.filter(file=>/\.(dat|inp|bdf|nas|stp|step)$/i.test(file.name));
  const picker=select('root-file');picker.replaceChildren();roots.forEach(file=>picker.add(new Option(fileName(file),fileName(file))));
  get('root-picker').hidden=!roots.length;
  if(!roots.length) {message('所选文件中没有支持的模型格式。');return;}
  if(automatic && roots.length===1) void run('正在读取模型，请稍候…',openSelectedModel);
}
get('open-model').onclick=()=>{
  if(info) void run('正在删除模型…',async()=>{
    await api('model/clear',{});info=null;mesh=null;selected=[];loads.length=0;editing=null;lastSeed=null;
    viewer.clearModel();resetExplosion();updateSelection();renderLoads();welding.refresh();
    setIcon(get('open-model'),'upload','打开模型');get('model-info-wrap').hidden=true;infoPinned=false;showInfo(false);
    get('body-list').replaceChildren();get('body-count').textContent='0';get<HTMLButtonElement>('explode').disabled=true;
    get('empty').hidden=activeTool.id==='welding' && welding.hasPaths;
    get<HTMLDialogElement>('model-dialog').showModal();
  });
  else {get('model-import-error').hidden=true;get<HTMLDialogElement>('model-dialog').showModal();}
};
get('choose-model-files').onclick=()=>input('file').click();
get('choose-model-directory').onclick=()=>input('directory').click();
get('close-model-dialog').onclick=()=>get<HTMLDialogElement>('model-dialog').close();
get<HTMLDialogElement>('model-dialog').oncancel=event=>{if(busy) event.preventDefault();};
input('file').onchange=()=>prepareModelFiles(Array.from(input('file').files || []),true);
input('directory').onchange=()=>prepareModelFiles(Array.from(input('directory').files || []),false);
get('import-directory').onclick=()=>{void run('正在读取模型，请稍候…',openSelectedModel);};
get('fit').onclick=()=>viewer.recenter();
document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button=>button.onclick=()=>viewer.fit(button.dataset.view!));
select('projection').onchange=()=>viewer.setProjection(select('projection').value as 'perspective'|'orthographic');
select('render-mode').onchange=()=>viewer.setRenderMode(select('render-mode').value as 'solid'|'wireframe'|'solid-edges');
function resetExplosion() {
  input('explosion').value='0';get('explosion-value').textContent='0%';viewer.setExplosion(0);
  get('explosion-control').hidden=true;get('explode').setAttribute('aria-expanded','false');
}
get('explode').onclick=()=>{const opening=get('explosion-control').hidden;get('explosion-control').hidden=!opening;get('explode').setAttribute('aria-expanded',String(opening));if(!opening) resetExplosion();};
input('explosion').oninput=()=>{get('explosion-value').textContent=input('explosion').value+'%';viewer.setExplosion(number('explosion')/100);};
let infoPinned=false;
const showInfo=(visible:boolean)=>{get('model-info').hidden=!visible;get('model-info-button').setAttribute('aria-expanded',String(visible));};
get('model-info-button').onclick=()=>{infoPinned=!infoPinned;showInfo(infoPinned);};
get('model-info-wrap').onmouseenter=()=>showInfo(true);
get('model-info-wrap').onmouseleave=()=>{if(!infoPinned) showInfo(false);};
get('model-info-button').onfocus=()=>showInfo(true);
get('model-info-button').onblur=()=>{if(!infoPinned) showInfo(false);};
document.addEventListener('pointerdown',event=>{if(!get('model-info-wrap').contains(event.target as Node)){infoPinned=false;showInfo(false);}});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){infoPinned=false;showInfo(false);}});
setupSidebarResize(document.querySelector('main')!);
input('angle').oninput=()=>{get('angle-value').textContent=input('angle').value+'°';};
input('angle').onchange=()=>{if(lastSeed!==null) void run('正在更新选择…',pick);};
get('clear').onclick=()=>{if(!busy){selected=[];lastSeed=null;editing=null;updateSelection();}};
function refreshLoadPreview() {
  if(busy || !selected.length || !info) return;
  displayedLoads.clear();updateLoadChecks();
  const generation=previewGeneration+1;
  void showPreview().catch(error=>{if(generation===previewGeneration) message((error as Error).message);});
}
select('mode').onchange=()=>{get('direction').hidden=select('mode').value!=='manual';refreshLoadPreview();};
for(const id of ['magnitude','pressure-unit','dx','dy','dz']) input(id).onchange=refreshLoadPreview;
select('case-mode').onchange=()=>{get('case-list').hidden=select('case-mode').value!=='custom';};
select('combined-case').onchange=updateCombined;
get('show-all-loads').onclick=()=>{const all=displayedLoads.size===loads.length;displayedLoads.clear();if(!all) loads.forEach((_,index)=>displayedLoads.add(index));drawSavedLoads();};
get('save-load').onclick=()=>{void run('正在保存载荷…',async()=>{
  const spec=currentLoad(); const result:Preview=await (await api('preview',spec)).json();
  const index=editing??loads.length;
  if(editing!==null) loads[editing]={spec,result}; else loads.push({spec,result});
  editing=null;displayedLoads.clear();displayedLoads.add(index); renderLoads();drawSavedLoads();
});};
get('export').onclick=()=>{void run('正在生成新文件…',async()=>{
  if(activeTool.id==='welding') {await welding.export();return;}
  const response=await api('export',{loads:loads.map(load=>load.spec)});
  const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;
  link.download=info!.filename.split('/').pop()!.replace(/\.dat$/i,'')+'_loads.zip';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),10000);message('已生成文件组，原文件保持不变。');
});};
if(!token) message('请通过启动程序打开工具。');
