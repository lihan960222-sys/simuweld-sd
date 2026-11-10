import type {Viewer, MeshData} from './viewer';
import {iconButton, setIcon} from './ui';
import './faceLoad.css';

interface ModelInfo {
  id: string; filename: string;
  cases: {id: number; name: string; initial: boolean}[];
}
interface Load {
  name: string; faces: number[]; magnitude: number; mode: string; direction: number[]; cases: number[];
  length_unit: string; force_unit: string; pressure_unit: string;
}
interface Preview { area: number; force: number[]; face_count: number; face_ids: number[]; centers: number[][]; directions: number[][]; area_unit: string; force_unit: string; }
interface Context {
  api: (path: string, body?: object | FormData)=>Promise<Response>;
  run: (text: string, action: ()=>Promise<void>, showOverlay?: boolean)=>Promise<void>;
  message: (text: string)=>void;
  info: ()=>ModelInfo | null;
  unit: ()=>string;
  busy: ()=>boolean;
  active: ()=>boolean;
  changed: ()=>void;
}

export const faceLoadSelection = `<div class="selection-bar" data-tool="surface-load" hidden><div><strong id="selected-count">0</strong><span>已选面</span></div><div><strong id="area-info">—</strong><span>表面积</span></div><button id="clear">清空选择</button></div>`;
export const faceLoadPanel = `<aside class="right" data-tool="surface-load" aria-label="面载荷参数" hidden>
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
</aside>`;

export function setupFaceLoad(viewer: Viewer, context: Context) {
  const {api, run, message} = context;
  const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  const input = (id: string) => get<HTMLInputElement>(id);
  const select = (id: string) => get<HTMLSelectElement>(id);
  const number = (id: string) => Number(input(id).value);
  const format = (value: number) => new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 5}).format(value);
  const modelScale = () => context.unit()==='m'?1000:1;
  let selected: number[] = [];
  let lastSeed: number | null = null;
  let baseSelection: number[] = [];
  let editing: number | null = null;
  const loads: {spec: Load; result: Preview}[] = [];
  const displayedLoads = new Set<number>();
  let currentPreview: Preview | null = null;
  let previewGeneration = 0;

  function updateSelection() {
    previewGeneration++;
    displayedLoads.clear();updateLoadChecks();
    viewer.highlight(context.active()?selected:[]);
    currentPreview=null;
    get('selected-count').textContent=String(selected.length);
    get('area-info').textContent='—';get('force-info').textContent='选面后显示合力';viewer.clearArrows();
  }
  async function pick() {
    if(lastSeed==null)return;
    const result=await (await api('select',{seed:lastSeed,angle:number('angle')})).json();
    const region:number[]=result.faces;
    const mode=select('selection-mode').value;
    selected=mode==='add'?[...new Set([...baseSelection,...region])]:mode==='remove'?baseSelection.filter(face=>!region.includes(face)):region;
    updateSelection();
    if(selected.length)await showPreview();
  }
  function currentLoad():Load {
    const info=context.info();
    if(!info)throw new Error('请导入模型');
    const cases=select('case-mode').value==='all'?info.cases.filter(c=>!c.initial).map(c=>c.id):Array.from(get('case-list').querySelectorAll<HTMLInputElement>('input:checked')).map(c=>Number(c.value));
    return {name:input('load-name').value.trim()||'面载荷',faces:[...selected],magnitude:number('magnitude'),mode:select('mode').value,
      direction:[number('dx'),number('dy'),number('dz')],cases,length_unit:context.unit(),force_unit:'N',pressure_unit:select('pressure-unit').value};
  }
  async function showPreview():Promise<Preview> {
    const generation=++previewGeneration,modelId=context.info()?.id;
    const result:Preview=await (await api('preview',currentLoad())).json();
    if(generation!==previewGeneration||modelId!==context.info()?.id)return result;
    currentPreview=result;drawPreview();
    if(context.active())viewer.highlight(selected);
    get('selected-count').textContent=String(selected.length);
    get('area-info').textContent=format(result.area*modelScale()**2)+' mm²';
    get('force-info').textContent=`合力 (${result.force.map(format).join(', ')}) N`;
    return result;
  }
  function drawPreview() {
    if(context.active()&&currentPreview)viewer.showArrows(currentPreview.centers.map(point=>point.map(value=>value*modelScale())),currentPreview.directions,currentPreview.area*modelScale()**2,currentPreview.face_ids);
  }
  function updateLoadChecks() {
    get('load-list').querySelectorAll<HTMLInputElement>('[data-load-visible]').forEach(box=>box.checked=displayedLoads.has(Number(box.dataset.loadVisible)));
    get('load-list').querySelectorAll<HTMLButtonElement>('.load-select').forEach(button=>button.setAttribute('aria-pressed',String(displayedLoads.has(Number(button.dataset.loadIndex)))));
    get('show-all-loads').setAttribute('aria-pressed',String(!!loads.length&&displayedLoads.size===loads.length));
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
  function renderLoads() {
    const list=get('load-list');list.replaceChildren();
    loads.forEach(({spec},index)=>{
      const row=document.createElement('div');row.className='load-card';
      const title=document.createElement('button');title.className='load-select';title.textContent=spec.name;title.dataset.loadIndex=String(index);title.title='显示 '+spec.name;
      title.onclick=()=>{displayedLoads.clear();displayedLoads.add(index);drawSavedLoads();};
      const check=document.createElement('input');check.type='checkbox';check.dataset.loadVisible=String(index);check.setAttribute('aria-label','显示 '+spec.name);
      check.onchange=()=>{if(check.checked)displayedLoads.add(index);else displayedLoads.delete(index);drawSavedLoads();};
      const detail=document.createElement('small');detail.textContent=`${format(spec.magnitude)} ${spec.pressure_unit} · ${spec.faces.length.toLocaleString()} 个面`;
      const edit=iconButton('edit','编辑 '+spec.name);edit.onclick=()=>editLoad(index);
      const remove=iconButton('delete','删除 '+spec.name);remove.onclick=()=>{
        if(context.busy())return;loads.splice(index,1);editing=null;displayedLoads.clear();renderLoads();drawSavedLoads();
      };
      const heading=document.createElement('div');heading.className='load-row';heading.append(check,title,edit,remove);
      row.append(heading,detail);list.append(row);
    });
    get('load-count').textContent=String(loads.length);get('combined').hidden=!loads.length;
    input('load-name').value='载荷 '+(loads.length+1);
    get('load-heading').textContent='设置面载荷';updateCombined();context.changed();updateLoadChecks();
  }
  function editLoad(index:number) {
    if(context.busy())return;
    editing=index;const spec=loads[index].spec;
    selected=[...spec.faces];lastSeed=null;updateSelection();
    for(const [id,value] of Object.entries({'load-name':spec.name,magnitude:spec.magnitude,mode:spec.mode,dx:spec.direction[0],dy:spec.direction[1],dz:spec.direction[2],'pressure-unit':spec.pressure_unit}))input(id).value=String(value);
    select('case-mode').value='custom';get('case-list').hidden=false;
    get('case-list').querySelectorAll<HTMLInputElement>('input').forEach(element=>element.checked=spec.cases.includes(Number(element.value)));
    get('direction').hidden=spec.mode!=='manual';get('load-heading').textContent='编辑：'+spec.name;
    void run('正在预览载荷…',async()=>{await showPreview();});
  }
  function updateCombined() {
    const id=Number(select('combined-case').value),force=[0,0,0];
    for(const {spec,result} of loads)if(spec.cases.includes(id))result.force.forEach((value,i)=>force[i]+=value*(spec.force_unit==='kN'?1000:1));
    get('combined-force').textContent=`合力 (${force.map(format).join(', ')}) N`;
  }
  function refreshLoadPreview() {
    if(context.busy()||!selected.length||!context.info())return;
    displayedLoads.clear();updateLoadChecks();
    const generation=previewGeneration+1;
    void showPreview().catch(error=>{if(generation===previewGeneration)message((error as Error).message);});
  }
  setIcon(get('save-load'),'save','保存载荷');setIcon(get('clear'),'close','清空选择');
  setIcon(get('show-all-loads'),'all','显示全部载荷');
  input('angle').oninput=()=>{get('angle-value').textContent=input('angle').value+'°';};
  input('angle').onchange=()=>{if(lastSeed!==null)void run('正在更新选择…',pick,false);};
  get('clear').onclick=()=>{if(!context.busy()){selected=[];lastSeed=null;editing=null;updateSelection();}};
  select('mode').onchange=()=>{get('direction').hidden=select('mode').value!=='manual';refreshLoadPreview();};
  for(const id of ['magnitude','pressure-unit','dx','dy','dz'])input(id).onchange=refreshLoadPreview;
  select('case-mode').onchange=()=>{get('case-list').hidden=select('case-mode').value!=='custom';};
  select('combined-case').onchange=updateCombined;
  get('show-all-loads').onclick=()=>{const all=displayedLoads.size===loads.length;displayedLoads.clear();if(!all)loads.forEach((_,index)=>displayedLoads.add(index));drawSavedLoads();};
  get('save-load').onclick=()=>{void run('正在保存载荷…',async()=>{
    const spec=currentLoad(),result:Preview=await (await api('preview',spec)).json();
    const index=editing??loads.length;
    if(editing!==null)loads[editing]={spec,result};else loads.push({spec,result});
    editing=null;displayedLoads.clear();displayedLoads.add(index);renderLoads();drawSavedLoads();
  });};
  return {
    activate() {if(displayedLoads.size)drawSavedLoads();else {viewer.highlight(selected);drawPreview();}},
    pick(face:number) {void run('正在选择表面…',async()=>{baseSelection=[...selected];lastSeed=face;await pick();},false);},
    get canExport() {return !!loads.length&&loads.every(({spec})=>spec.length_unit===context.unit()&&spec.force_unit==='N');},
    reset() {
      selected=[];loads.length=0;editing=null;lastSeed=null;
      const cases=get('case-list'),combined=select('combined-case');cases.replaceChildren();combined.replaceChildren();
      context.info()!.cases.forEach(item=>{
        const label=document.createElement('label'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=String(item.id);checkbox.checked=!item.initial;
        label.append(checkbox,document.createTextNode(item.name+(item.initial?'（初始）':'')));cases.append(label);
        if(!item.initial)combined.add(new Option(item.name,String(item.id)));
      });
      updateSelection();renderLoads();
    },
    hideBody(mesh:MeshData,bodyId?:number) {selected=bodyId===undefined?[]:selected.filter(face=>mesh.body_ids[face]!==bodyId);lastSeed=null;updateSelection();},
    async export() {
      const response=await api('export',{loads:loads.map(load=>load.spec)});
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;
      link.download=context.info()!.filename.split('/').pop()!.replace(/\.dat$/i,'')+'_loads.zip';link.click();
      setTimeout(()=>URL.revokeObjectURL(url),10000);message('已生成文件组，原文件保持不变。');
    },
  };
}
