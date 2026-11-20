import './style.css';
import './import.css';
import { Viewer, type MeshData, type RenderMode } from './viewer';
import { WeldingTool } from './welding';
import { ThermalTool } from './thermal';
import { LocalTrialTool } from './localTrial';
import { InherentStrainTool } from './inherentStrain';
import { setupSidebarResize } from './sidebarResize';
import { setIcon, makeCollapsible, type IconName } from './ui';
import { setupFaceLoad, faceLoadPanel, faceLoadSelection } from './faceLoad';
import './design.css';

interface ModelInfo {
  supports_loads: boolean; length_unit?: string;
  id: string; filename: string; nodes: number; elements: number; faces: number;
  bodies: {id: number; name: string; volume: number | null}[]; cases: {id: number; name: string; initial: boolean}[];
}
interface WorkspaceTool { id: string; label: string; activate?: ()=>void; pick?: (face: number)=>void; }

const planeIcon = (plane: string) => `<svg class="plane-axis" viewBox="0 0 36 36" aria-hidden="true" focusable="false"><g class="axis-${plane[0]}"><path d="M10 26h14m-3-3 3 3-3 3"/><text x="26" y="32">${plane[0].toUpperCase()}</text></g><g class="axis-${plane[1]}"><path d="M10 26V12m-3 3 3-3 3 3"/><text x="7" y="9">${plane[1].toUpperCase()}</text></g><circle cx="10" cy="26" r="1.5" fill="currentColor"/></svg>`;

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header><div class="brand"><img class="brand-icon" src="/icon.svg" alt="激光焊接标识" width="36" height="36"><strong>SIMUWELD SD</strong></div><nav id="tools" aria-label="功能"></nav><button id="export" class="primary" disabled>导出 DAT 文件组 ↗</button></header>
  <main>
    <aside class="left">
      <section class="model-entry">
        <div class="model-actions"><button id="open-model" class="primary full">上传模型</button><div id="model-info-wrap" class="info-wrap" hidden><button id="model-info-button" aria-label="模型信息" aria-expanded="false" aria-controls="model-info">!</button><div id="model-info" class="model-info" role="tooltip" hidden></div></div></div>
        <div class="import-toolbar"><select id="import-units" aria-label="模型单位" title="模型文件单位"><option value="m">m · kg · s · N · Pa</option><option value="mm">mm · t · s · N · MPa</option></select><details class="import-options"><summary id="import-settings" aria-label="读取设置" title="读取设置"></summary><div class="import-settings-panel"><label>输入格式<select id="model-format"><option value="">自动识别扩展名</option><option value="marc">Marc / sfMarc DAT</option><option value="inp">Abaqus INP</option><option value="bdf">Nastran BDF / NAS / DAT</option><option value="step">CAD STP / STEP</option></select></label></div></details></div>
      </section>
      <section><div class="section-title"><h2>实体</h2><span id="body-count" class="badge">0</span></div><div id="body-list"><p class="muted">导入后可以分别显示实体。</p></div></section>
    </aside>
    <div id="resize-left" class="sidebar-resizer" role="separator" aria-label="调整左侧栏宽度" aria-orientation="vertical" tabindex="0"></div>
    <div class="workspace"><div class="view-tools"><button id="fit" aria-label="回到中心" title="回到中心"><svg viewBox="0 0 36 36" aria-hidden="true" focusable="false"><path class="fit-corners" d="M11 4H4v7m21-7h7v7M4 25v7h7m21-7v7h-7"/><path class="fit-model" d="M18 11v14m-7-7h14"/><circle class="fit-model" cx="18" cy="18" r="4"/></svg></button><button data-view="xy" aria-label="XY 平面" title="XY 平面">${planeIcon('xy')}</button><button data-view="xz" aria-label="XZ 平面" title="XZ 平面">${planeIcon('xz')}</button><button data-view="yz" aria-label="YZ 平面" title="YZ 平面">${planeIcon('yz')}</button><select id="projection" aria-label="投影方式"><option value="perspective">透视</option><option value="orthographic">正交</option></select><select id="render-mode" aria-label="渲染模式"><option value="solid">实体</option><option value="wireframe" disabled>网格</option><option value="edges">边线</option><option value="solid-edges">实体与边线</option></select><button id="explode" aria-label="爆炸图" title="爆炸图" aria-expanded="false" disabled>爆炸图</button><label id="explosion-control" hidden>展开程度<input id="explosion" aria-label="爆炸图展开程度" type="range" min="0" max="100" value="0"><output id="explosion-value">0%</output></label></div>
      <div id="viewport"></div><div id="empty"><img class="empty-shape" src="/icon.svg" alt="SIMUWELD SD" width="64" height="64"><h1>Empowering Welding Simulation</h1></div>
      <div id="busy" hidden><span class="spinner"></span><div class="busy-content"><span id="busy-text" role="status" aria-live="polite">正在读取模型…</span><progress id="import-progress" aria-label="模型读取进度" hidden></progress></div></div>
      ${faceLoadSelection}
      <div id="message" role="status" hidden></div>
    </div>
    <div id="resize-right" class="sidebar-resizer" role="separator" aria-label="调整右侧栏宽度" aria-orientation="vertical" tabindex="0"></div>
    ${faceLoadPanel}
  </main><footer>copyright LH</footer>
  <input id="file" type="file" accept=".dat,.inp,.bdf,.nas,.stp,.step" hidden />`;

const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const input = (id: string) => get<HTMLInputElement>(id);
const select = (id: string) => get<HTMLSelectElement>(id);
const number = (id: string) => Number(input(id).value);
const format = (value: number) => new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 5}).format(value);
const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let info: ModelInfo | null = null;
let mesh: MeshData | null = null;
let busy = false;
let files: File[] = [];
let activeTool: WorkspaceTool;
let modelLengthUnit='m';
const modelScale=()=>modelLengthUnit==='m'?1000:1;
let importProgressActive=false;
let importProgressLocal=false;

function showImportProgress(stage:string,completed:number|null=null,total:number|null=null,unit='') {
  if(!importProgressActive)return;
  const counted=completed!==null && total!==null && total>0;
  const text=counted?`${stage} · 已完成 ${completed.toLocaleString()} / ${total.toLocaleString()} ${unit}`:`${stage}…`;
  get('busy-text').textContent=text;
  const progress=get<HTMLProgressElement>('import-progress');progress.hidden=false;progress.setAttribute('aria-valuetext',text);
  if(counted){progress.max=total;progress.value=completed;}else progress.removeAttribute('value');
}

function watchImportProgress() {
  importProgressActive=true;importProgressLocal=false;
  showImportProgress('正在传输模型文件');
  let stopped=false,timer=0;
  const poll=async()=>{
    try {
      const state=await (await api('import-progress')).json();
      if(!stopped && !importProgressLocal && state.active)showImportProgress(state.stage,state.completed,state.total,state.unit);
    } catch(error) {
      if(!stopped && !importProgressLocal)showImportProgress(`无法读取进度：${error instanceof Error?error.message:String(error)}`);
    }
    if(!stopped)timer=window.setTimeout(poll,250);
  };
  timer=window.setTimeout(poll,250);
  return ()=>{stopped=true;window.clearTimeout(timer);importProgressActive=false;
    get('import-progress').hidden=true;
  };
}

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
}
function fadeMessage() {
  const notice=get('message');
  if(notice.hidden || messageAnimation) return;
  const animation=notice.animate([{opacity:1},{opacity:0}],{duration:matchMedia('(prefers-reduced-motion: reduce)').matches?1:300,fill:'forwards'});
  messageAnimation=animation;
  animation.onfinish=()=>{if(messageAnimation===animation) hideMessage();};
}
document.addEventListener('pointerdown',fadeMessage);
async function run(text: string, action: ()=>Promise<void>, showOverlay=true) {
  if (busy) return;
  busy = true; if(showOverlay) get('busy').hidden = false; get('busy-text').textContent = text; fadeMessage();
  get('viewport').setAttribute('aria-busy','true');
  document.querySelectorAll<HTMLElement>('aside, header, .selection-bar').forEach(element=>element.inert=true);
  try { await action(); }
  catch (error) { message((error as Error).message); }
  finally {
    busy = false; if(showOverlay) get('busy').hidden = true;
    get('viewport').setAttribute('aria-busy','false');
    document.querySelectorAll<HTMLElement>('aside, header, .selection-bar').forEach(element=>element.inert=false);
    updateExport();
  }
}

const viewer = new Viewer(get('viewport'), face => activeTool.pick?.(face));
const faceLoad = setupFaceLoad(viewer,{api,run,message,info:()=>info,unit:()=>modelLengthUnit,busy:()=>busy,active:()=>activeTool?.id==='surface-load',changed:updateExport});
const welding = new WeldingTool(viewer, {api,run,message,unit:()=>'mm',hasModel:()=>!!info,changed:()=>{
  window.addEventListener('beforeunload',confirmLeave);
  get('empty').hidden=!!info || welding.hasPaths;
  updateExport();
}});
const thermal = new ThermalTool(viewer,{api,run,message,unit:()=>modelLengthUnit,changed:()=>window.addEventListener('beforeunload',confirmLeave)});
const localTrial = new LocalTrialTool(viewer,{api,run,message,unit:()=>modelLengthUnit,changed:()=>window.addEventListener('beforeunload',confirmLeave)});
const inherentStrain = new InherentStrainTool(viewer,{api,message,unit:()=>modelLengthUnit,modelId:()=>info?.id||'',changed:()=>window.addEventListener('beforeunload',confirmLeave)});
makeCollapsible();
function setModelUploadButton(replace=false) {
  const button=get('open-model');
  setIcon(button,'upload',replace?'重新上传模型':'上传模型');
  button.classList.remove('icon-button');
  const label=document.createElement('span');label.textContent='上传模型';button.append(label);
}
setModelUploadButton();setIcon(get('model-info-button'),'info','模型信息');
setIcon(get('import-settings'),'settings','读取设置');
setIcon(get('explode'),'explode','爆炸图');
setIcon(get('fit'),'focus','回到中心');
const workspaceTools: WorkspaceTool[] = [
  {id:'model', label:'模型查看'},
  {id:'welding', label:'生成焊路'},
  {id:'surface-load', label:'面载荷', activate:faceLoad.activate, pick:faceLoad.pick},
  {id:'local-trial', label:'局部试算'},
  {id:'inherent-strain', label:'固有应变法'},
  {id:'thermal-cycle', label:'转换至热循环模式'},
];
for (const tool of workspaceTools) {
  const button=document.createElement('button'); button.id='tool-'+tool.id;
  const icons:Record<string,IconName>={model:'model',welding:'path','surface-load':'pressure','local-trial':'trial','inherent-strain':'inherent','thermal-cycle':'thermal'};
  setIcon(button,icons[tool.id],tool.label);button.classList.remove('icon-button');
  const label=document.createElement('span');label.textContent=tool.label;button.append(label);
  button.onclick=()=>activateTool(tool); get('tools').append(button);
}
function activateTool(tool: WorkspaceTool) {
  if(busy || activeTool===tool) return;
  if(tool.id==='surface-load' && info?.supports_loads===false) {message('面载荷需要导入 Marc DAT 模型。');return;}
  activeTool=tool;
  if(tool.id==='welding' || tool.id==='thermal-cycle' || tool.id==='local-trial' || tool.id==='inherent-strain') resetExplosion();
  get<HTMLButtonElement>('explode').disabled=!info || tool.id==='welding' || tool.id==='thermal-cycle' || tool.id==='local-trial' || tool.id==='inherent-strain';
  document.querySelectorAll<HTMLElement>('[data-tool]').forEach(panel=>panel.hidden=panel.dataset.tool!==tool.id);
  document.querySelector('main')!.classList.toggle('has-tool-panel',!!document.querySelector('aside.right:not([hidden])'));
  workspaceTools.forEach(item=>get('tool-'+item.id).setAttribute('aria-pressed',String(item===tool)));
  viewer.highlight([]); viewer.clearArrows(); welding.activate(tool.id==='welding'); thermal.activate(tool.id==='thermal-cycle'); localTrial.activate(tool.id==='local-trial'); tool.activate?.(); updateExport();
  inherentStrain.activate(tool.id==='inherent-strain');
  document.querySelectorAll<HTMLElement>('[data-view],#projection,#render-mode,#explode').forEach(control=>control.hidden=tool.id==='inherent-strain');
  if(tool.id!=='inherent-strain')viewer.setProjection(select('projection').value as 'perspective'|'orthographic');
  get('empty').hidden=!!info || (tool.id==='welding' && welding.hasPaths);
}
activateTool(workspaceTools[0]);

function updateExport() {
  get('export').hidden=activeTool?.id==='thermal-cycle' || activeTool?.id==='local-trial' || activeTool?.id==='inherent-strain';
  if(activeTool?.id==='welding') {
    setIcon(get('export'),'download','导出 Python 脚本');
    get<HTMLButtonElement>('export').disabled=busy || !welding.canExport;
    return;
  }
  setIcon(get('export'),'download','导出 DAT 文件组');
  get<HTMLButtonElement>('export').disabled = busy || !faceLoad.canExport;
}

async function importFiles(root: string) {
  const data = new FormData(); data.append('root',root);
  files.forEach(file=>data.append('files',file,file.name));
  const next: ModelInfo = await (await api('model',data)).json();
  await displayModel(next,true);
}

async function displayModel(next: ModelInfo, hasMesh: boolean) {
  info=next;
  select('render-mode').querySelector<HTMLOptionElement>('[value=wireframe]')!.disabled=!hasMesh;
  if(!hasMesh && select('render-mode').value==='wireframe') {
    select('render-mode').value='edges';viewer.setRenderMode('edges');
  }
  modelLengthUnit=info.length_unit || select('import-units').value;
  importProgressLocal=true;showImportProgress('正在传输模型显示数据');
  mesh=await (await api('mesh')).json();
  showImportProgress('正在构建视窗模型');
  await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
  mesh!.points=mesh!.points.map(value=>value*modelScale());
  viewer.load(mesh!); get('empty').hidden=true;
  resetExplosion();get<HTMLButtonElement>('explode').disabled=activeTool.id==='welding' || activeTool.id==='thermal-cycle' || activeTool.id==='local-trial' || activeTool.id==='inherent-strain';
  setModelUploadButton(true);get('model-info-wrap').hidden=false;
  get('model-info').textContent=`${info.filename}\n${info.nodes.toLocaleString()} 节点\n${info.elements.toLocaleString()} 单元 · ${info.faces.toLocaleString()} 外表面`;
  get('body-count').textContent=String(info.bodies.length);
  const bodies=get('body-list'); bodies.replaceChildren();
  const tree=document.createElement('details');tree.className='body-tree';tree.open=true;
  const heading=document.createElement('summary'),all=document.createElement('input'),name=document.createElement('span');
  all.type='checkbox';all.checked=true;all.setAttribute('aria-label','全部实体显示');
  name.textContent=info.filename;name.title=info.filename;heading.append(all,name);tree.append(heading);bodies.append(tree);
  const visibilityInputs:HTMLInputElement[]=[];
  const updateAll=()=>{const count=visibilityInputs.filter(checkbox=>checkbox.checked).length;all.checked=count===visibilityInputs.length;all.indeterminate=count>0 && !all.checked;};
  const setAll=(visible:boolean)=>{for(const checkbox of visibilityInputs){checkbox.checked=visible;viewer.showBody(Number(checkbox.dataset.bodyVisible),visible);} if(!visible)faceLoad.hideBody(mesh!);updateAll();};
  all.onclick=event=>event.stopPropagation();all.onchange=()=>setAll(all.checked);
  [...info.bodies].sort((first,second)=>(second.volume ?? -1)-(first.volume ?? -1) || first.id-second.id).forEach(body=>{
    const card=document.createElement('details'); card.className='body-card';card.dataset.bodyId=String(body.id);
    const summary=document.createElement('summary');
    const label=document.createElement('label'); label.className='body-row';
    const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=true;
    checkbox.dataset.bodyVisible=String(body.id);visibilityInputs.push(checkbox);
    checkbox.onclick=event=>event.stopPropagation();
    checkbox.onchange=()=>{ viewer.showBody(body.id,checkbox.checked);updateAll(); if (!checkbox.checked)faceLoad.hideBody(mesh!,body.id); };
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
  faceLoad.reset(); welding.refresh();
  if(activeTool.id==='thermal-cycle')showImportProgress('正在计算焊路采样节点');
  await localTrial.modelChanged(info.supports_loads);
  inherentStrain.modelChanged(info.supports_loads);
  await thermal.modelChanged(info.supports_loads);
}

async function openSelectedModel() {
  const file=files[0];
  if(!file) throw new Error('请选择模型文件。');
  const root=file.name;
  const format=select('model-format').value;
  const extension=file.name.split('.').pop()!.toLowerCase();
  const stopProgress=watchImportProgress();
  try {
  if(format==='marc' || (!format && extension==='dat')) await importFiles(root);
  else {
    const data=new FormData();data.append('file',file);if(format) data.append('format',format);
    const fileFormat=format || extension;
    await displayModel(await (await api('background',data)).json(),fileFormat!=='stp' && fileFormat!=='step');
    if(activeTool.id==='surface-load') {
      activeTool=workspaceTools[0];
      document.querySelectorAll<HTMLElement>('[data-tool]').forEach(panel=>panel.hidden=true);
      document.querySelector('main')!.classList.remove('has-tool-panel');
      workspaceTools.forEach(tool=>get('tool-'+tool.id).setAttribute('aria-pressed',String(tool===activeTool)));
    }
  }
  input('file').value='';
  } finally {stopProgress();}
}

function prepareModelFiles(nextFiles: File[]) {
  if(!nextFiles.length) return;
  files=[nextFiles[0]];
  void run('正在读取模型，请稍候…',openSelectedModel);
}
get('open-model').onclick=()=>{
  if(!busy) {input('file').value='';input('file').click();}
};
input('file').onchange=()=>prepareModelFiles(Array.from(input('file').files || []));
get('fit').onclick=()=>viewer.recenter();
document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button=>button.onclick=()=>viewer.fit(button.dataset.view!));
select('projection').onchange=()=>viewer.setProjection(select('projection').value as 'perspective'|'orthographic');
select('render-mode').onchange=()=>viewer.setRenderMode(select('render-mode').value as RenderMode);
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
const importOptions=document.querySelector<HTMLDetailsElement>('.import-options')!;
document.addEventListener('pointerdown',event=>{if(!importOptions.contains(event.target as Node))importOptions.open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape' && importOptions.open){importOptions.open=false;get('import-settings').focus();}});
select('model-format').onchange=()=>{importOptions.open=false;};
setupSidebarResize(document.querySelector('main')!);
get('export').onclick=()=>{void run('正在生成新文件…',async()=>{
  if(activeTool.id==='welding') {await welding.export();return;}
  await faceLoad.export();
});};
if(!token) message('请通过启动程序打开工具。');
