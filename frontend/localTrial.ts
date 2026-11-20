import type {Viewer, TrialOverlay} from './viewer';
import {iconButton} from './ui';
import './localTrial.css';

interface Path {id:number;label:string;points_mm:number[][];length_mm:number;}
interface Meta {model_id:string;source_process:string;paths:Path[];components:{name:string;keep_whole:boolean}[];nodes:{id:number;xyz_mm:number[];component:string}[];bounds_mm:number[][];}
interface Spec {model_id:string;path_id:number;start_mm:number;end_mm:number;wrap:boolean;tolerance_mm:number;box:{min:number[];max:number[]};keep_whole:string[];trim_start_mm:number;trim_end_mm:number;included_path_ids?:number[];}
interface Group extends Spec {name:string;}
interface Preview {matches:{path_id:number;label:string;points_mm:number[][];matched:boolean;reason:string;endpoint_adjustment_mm:number[]}[];crop:{node_ids:number[];points_mm:number[][];node_count:number;element_count:number;faces:{node_ids:number[];body:number}[]};selection:{points_mm:number[][];length_mm:number;extraction_points_mm:number[][]};warnings:string[];}
interface Services {api:(path:string,body?:object|FormData)=>Promise<Response>;run:(text:string,action:()=>Promise<void>)=>Promise<void>;message:(text:string)=>void;unit:()=>string;changed:()=>void;}

function slicePath(points:number[][],start:number,end:number){
  const selected:number[][]=[];let distance=0;
  for(let index=1;index<points.length;index++){
    const first=points[index-1],last=points[index],length=Math.hypot(...last.map((v,axis)=>v-first[axis]));
    if(length>0&&distance+length>=start&&distance<=end){
      const a=Math.max(0,(start-distance)/length),b=Math.min(1,(end-distance)/length);
      const point=(t:number)=>first.map((v,axis)=>v+(last[axis]-v)*t);
      if(a<b){if(!selected.length)selected.push(point(a));selected.push(point(b));}
    }
    distance+=length;
  }
  return selected;
}

export class LocalTrialTool {
  private panel=document.createElement('aside');
  private meta:Meta|null=null;
  private result:Preview|null=null;
  private active=false;
  private available=false;
  private revision=0;
  private timer=0;
  private shown=new Set<number>();
  private excluded=new Set<number>();
  private groups:Group[]=[];
  private editing=-1;
  private dirty=false;
  private hasSelection=false;
  private pickTarget='start';
  private cropNodes:TrialOverlay['nodes']=[];
  private tab='model';
  private equivalent:any=null;
  private resultRevision=0;
  private datasetId='';
  private importedFile:File|null=null;
  private analysisTimer=0;
  private analysisBusy=false;
  private analysisQueued=false;
  private abaqusOrder:number[]=[];
  private abaqusExcluded=new Set<number>();
  private abaqusReversed=new Set<number>();
  constructor(private viewer:Viewer,private services:Services){
    this.panel.className='right local-trial-panel';this.panel.dataset.tool='local-trial';this.panel.hidden=true;
    this.panel.innerHTML=`<div class="tool-heading"><h1>局部试算</h1><p id="lt-source" class="hint">上传瞬态计算 DAT 后读取焊路。</p></div>
      <section><h2>焊路显示</h2><label class="check"><input id="lt-all" type="checkbox" checked>显示全部焊路</label><div id="lt-paths"></div></section>
      <section><h2>选取范围（mm）</h2><input id="lt-path" type="hidden"><p id="lt-selected-path" class="hint" role="status">点击焊路节点选择范围。</p><label>选取方式<select id="lt-method"><option value="ends">起点与终点</option><option value="center">中心与长度</option></select></label>
      <div id="lt-ends-fields" class="input-row"><label>起点<input id="lt-start" type="number" step="any" value="0"></label><label>终点<input id="lt-end" type="number" step="any" value="1"></label></div>
      <div id="lt-center-fields" class="input-row" hidden><label>中心<input id="lt-center" type="number" step="any" value="0"></label><label>长度<input id="lt-length" type="number" min="0.001" step="any" value="10"></label></div>
      <div id="lt-pick-buttons" class="lt-actions"></div><input id="lt-wrap" type="checkbox" hidden><label>匹配容差<input id="lt-tolerance" type="number" min="0.000001" step="any" value="0.01"></label></section>
      <section><h2>模型裁剪（mm）</h2><div id="lt-box"></div><p class="hint">拖动坐标滑条调整包围盒，视窗同步显示。</p><div id="lt-components"></div></section>
      <section><h2>试算分组</h2><label>分组名称<input id="lt-name" value="局部试算 1"></label><p id="lt-dirty" class="hint"></p><div class="lt-actions" id="lt-group-actions"></div><div id="lt-groups"></div></section>
      <section><h2>创建局部算例</h2><label>来源 Process<input id="lt-source-process" maxlength="200"></label><label>目标 Process<input id="lt-target-process" placeholder="来源名称-Local" maxlength="200"></label><details><summary>组件对应</summary><div id="lt-component-names"></div></details><div class="input-row"><label>冷却停止温度（℃）<input id="lt-cooling-limit" type="number" value="26" min="25.01" step="0.1"></label><label>最长冷却时间（s）<input id="lt-cooling-max" type="number" value="60" min="0.01"></label></div><p id="lt-status" class="hint" role="status"></p><details class="lt-match-details"><summary>参与计算的焊路</summary><div id="lt-matches"></div></details><button id="lt-export" class="primary full">导出局部算例 ZIP</button></section>
      <section data-workflow="results"><h2>提取计算结果</h2><label>局部 Process<input id="lt-results-process" placeholder="填写局部算例名称" maxlength="200"></label><label>最终提取时刻（s，可留空）<input id="lt-results-final" type="number" min="0.00001" step="any" placeholder="默认最终保存时刻"></label><p class="hint">自动提取初始、各次焊接冷却后的状态。最终状态应位于解除夹具之前。</p><button id="lt-results-export" class="full">导出结果提取脚本</button></section>
      <section data-workflow="results"><h2>代表区间</h2><label>结果 ZIP<input id="lt-result-file" type="file" accept=".zip"></label><details><summary>旧结果包配套文件</summary><label>局部求解 DAT<input id="lt-result-dat" type="file" accept=".dat"></label><label>DAT 长度单位<select id="lt-result-unit"><option value="m">m</option><option value="mm">mm</option></select></label></details><div class="input-row"><label>代表长度（mm）<input id="lt-represent-length" type="number" value="1" min="0.001" step="0.1"></label><label>横向统计区间<input id="lt-represent-bins" type="number" value="16" min="2" max="256"></label></div><button id="lt-analyze" class="primary full">读取并计算代表数据</button><p id="lt-result-status" class="hint" role="status"></p><progress id="lt-result-progress" max="1" value="0" hidden></progress><div id="lt-result-summary"></div><button id="lt-equivalent-export" class="full" disabled>导出代表数据</button></section>
      <section data-workflow="abaqus"><h2>整体 shell 模型</h2><label>网格 INP<input id="lt-abaqus-file" type="file" accept=".inp"></label><label>模型长度单位<select id="lt-abaqus-unit"><option value="mm">mm</option><option value="m">m</option></select></label><button id="lt-abaqus-read" class="full">读取 shell 网格</button><p id="lt-abaqus-status" class="hint" role="status"></p><div id="lt-abaqus-bodies"></div></section>
      <section data-workflow="abaqus"><h2>焊路与加载</h2><p id="lt-abaqus-path-status" class="hint">读取来源 DAT 的焊路后，按代表长度自动分段。</p><div id="lt-abaqus-paths"></div><label>计算名称<input id="lt-abaqus-job" value="SD_Weld" maxlength="60"></label><button id="lt-abaqus-export" class="primary full">导出 Abaqus 计算包</button><p class="hint">材料、截面、板间连接及夹具沿用你的 INP。包内包含官方 Abaqus 模型准备脚本。</p></section>`;
    document.querySelector('main')!.append(this.panel);
    const tabs=document.createElement('nav');tabs.className='lt-tabs';tabs.setAttribute('aria-label','局部试算步骤');
    for(const [id,label] of [['model','局部模型'],['results','结果处理'],['abaqus','Abaqus']]){const button=document.createElement('button');button.textContent=label;button.dataset.tab=id;button.onclick=()=>this.switchTab(id);tabs.append(button);}
    this.panel.querySelector('.tool-heading')!.after(tabs);this.switchTab('model');
    for(const [target,label] of [['start','选取起点'],['end','选取终点'],['center','选取中心']]){const button=iconButton(target==='end'?'end':'start',label);button.dataset.pick=target;button.onclick=()=>{this.pickTarget=target;this.selectionMode();this.updateMethod();this.services.message(`请在视窗点击焊路节点：${label}`);};this.get('pick-buttons').append(button);}
    for(let axis=0;axis<3;axis++){
      const group=document.createElement('div');group.className='lt-axis-group';group.dataset.axis='XYZ'[axis];
      const heading=document.createElement('h3');heading.textContent='XYZ'[axis];group.append(heading);this.get('box').append(group);
      for(const end of ['min','max']){
      const label=document.createElement('label');label.textContent=`${end==='min'?'最小':'最大'} ${'XYZ'[axis]}`;
      const row=document.createElement('div');row.className='lt-box-row';
      const number=document.createElement('input');number.type='number';number.step='any';number.id=`lt-box-${end}-${axis}`;
      const range=document.createElement('input');range.type='range';range.step='any';range.id=`lt-range-${end}-${axis}`;
      number.oninput=()=>{range.value=number.value;this.changed();};range.oninput=()=>{number.value=range.value;this.changed();};row.append(number,range);label.append(row);group.append(label);
      }
    }
    this.get('all').onchange=()=>{this.shown=new Set(this.field('all').checked?this.meta?.paths.map(p=>p.id):[]);this.renderPaths();this.draw();};
    this.get('method').onchange=()=>{this.pickTarget=this.field('method').value==='center'?'center':'start';this.updateMethod();this.changed();};
    for(const id of ['start','end','center','length','tolerance'])this.get(id).oninput=()=>{if(id!=='tolerance')this.hasSelection=true;this.changed();};
    this.get('name').oninput=()=>this.markDirty();
    const save=iconButton('save','保存试算分组');save.onclick=()=>{void this.services.run('正在保存试算分组…',async()=>{await this.preview();if(!this.result)throw Error('需要有效的范围预览。');const group={...this.spec(),name:this.field('name').value.trim()};if(!group.name)throw Error('请输入分组名称。');if(this.editing<0){this.groups.push(group);this.editing=this.groups.length-1;}else this.groups[this.editing]=group;this.dirty=false;this.renderGroups();this.services.changed();});};
    const add=iconButton('add','新增独立试算分组');add.onclick=()=>{this.editing=-1;this.field('name').value=`局部试算 ${this.groups.length+1}`;this.resetRange();};this.get('group-actions').append(save,add);
    this.get('export').onclick=()=>{void this.services.run('正在导出局部试算…',async()=>{
      if(!this.groups.length)throw Error('请保存至少一个试算分组。');if(this.dirty)throw Error('当前参数尚未保存，请保存分组后导出。');
      const component_names=Object.fromEntries(Array.from(this.panel.querySelectorAll<HTMLInputElement>('[data-source-component]')).map(input=>[input.dataset.sourceComponent!,input.value.trim()]));
      const response=await this.services.api('local-trial/export',{model_id:this.meta!.model_id,groups:this.groups,source_process:this.field('source-process').value,target_process:this.field('target-process').value,component_names,cooling_limit_c:this.value('cooling-limit'),max_cooling_s:this.value('cooling-max')});
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='local_trial.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });};
    this.get('results-export').onclick=()=>{void this.services.run('正在生成结果提取脚本…',async()=>{
      const response=await this.services.api('local-trial/results-script',{process_name:this.field('results-process').value,final_time_s:this.field('results-final').value?this.value('results-final'):null,cooling_limit_c:this.value('cooling-limit')});
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='export_local_results.py';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });};
    this.get('analyze').onclick=()=>{void this.services.run('正在读取局部结果…',()=>this.analyzeResults());};
    for(const id of ['represent-length','represent-bins','results-final'])this.get(id).oninput=()=>{this.resultRevision++;this.equivalent=null;this.field('equivalent-export').disabled=true;this.get('result-status').textContent='正在等待输入完成…';window.clearTimeout(this.analysisTimer);if(this.datasetId)this.analysisTimer=window.setTimeout(()=>{void this.analyzeResults().catch(error=>this.services.message(String(error)));},550);};
    for(const id of ['result-file','result-dat','result-unit'])this.get(id).onchange=()=>{this.resultRevision++;this.datasetId='';this.importedFile=null;this.equivalent=null;this.field('equivalent-export').disabled=true;this.get('result-status').textContent='输入已修改，请重新读取结果。';};
    this.get('equivalent-export').onclick=()=>{void this.services.run('正在导出代表数据…',async()=>{const response=await this.services.api('local-trial/equivalent');this.download(await response.blob(),'shell_equivalent.json');});};
    this.get('abaqus-read').onclick=()=>{void this.services.run('正在读取 shell 网格…',()=>this.readAbaqus());};
    const assembly=document.createElement('details');assembly.innerHTML='<summary>装配 INP 预览</summary><p class="hint">包含 Part / Instance 的文件使用 Abaqus 官方接口提取全局坐标。在 INP 所在目录运行预览脚本，再选择生成的 JSON。</p><button id="lt-preview-script" class="full">导出装配预览脚本</button><label>装配预览 JSON<input id="lt-abaqus-preview" type="file" accept=".json"></label>';this.get('abaqus-read').before(assembly);
    this.get('preview-script').onclick=()=>{void this.services.run('正在生成装配预览脚本…',async()=>{const file=this.field('abaqus-file').files?.[0];if(!file)throw Error('请选择 INP。');const response=await this.services.api('local-trial/abaqus-preview-script',{filename:file.name,length_unit:this.field('abaqus-unit').value});this.download(await response.blob(),'export_abaqus_preview.py');});};
    for(const id of ['abaqus-file','abaqus-unit','abaqus-preview'])this.get(id).onchange=()=>{this.abaqus=null;this.get('abaqus-bodies').replaceChildren();this.get('abaqus-status').textContent='输入已修改，请重新读取 shell 网格。';};
    this.get('abaqus-export').onclick=()=>{void this.services.run('正在生成 Abaqus 计算包…',()=>this.exportAbaqus());};
    this.updateMethod();this.selectionMode();
  }
  private switchTab(tab:string){this.tab=tab;this.panel.querySelectorAll<HTMLElement>(':scope > section').forEach(section=>section.hidden=(section.dataset.workflow??'model')!==tab);this.panel.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.tab===tab)));if(tab==='abaqus')this.renderAbaqusPaths();if(tab!=='model')this.viewer.localTrialSelection('none',()=>{});else if(this.meta)this.selectionMode();if(this.meta)this.draw();}
  private renderAbaqusPaths(){
    if(!this.meta)return;if(this.get('abaqus-paths').dataset.model!==this.meta.model_id){this.abaqusOrder=this.meta.paths.map(path=>path.id);this.abaqusExcluded.clear();this.abaqusReversed.clear();this.get('abaqus-paths').dataset.model=this.meta.model_id;}
    this.get('abaqus-paths').replaceChildren();
    for(const id of this.abaqusOrder){const path=this.meta.paths.find(path=>path.id===id)!;const row=document.createElement('div');row.className='lt-path-row';row.draggable=true;row.ondragstart=event=>event.dataTransfer?.setData('text/plain',String(id));row.ondragover=event=>event.preventDefault();row.ondrop=event=>{event.preventDefault();const source=Number(event.dataTransfer?.getData('text/plain'));if(source===id||!this.abaqusOrder.includes(source))return;this.abaqusOrder=this.abaqusOrder.filter(value=>value!==source);this.abaqusOrder.splice(this.abaqusOrder.indexOf(id),0,source);this.renderAbaqusPaths();this.draw();this.services.changed();};const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=!this.abaqusExcluded.has(id);check.onchange=()=>{if(check.checked)this.abaqusExcluded.delete(id);else this.abaqusExcluded.add(id);this.draw();this.services.changed();};label.append(check,document.createTextNode(path.label));const reverse=iconButton('reverse','反向焊接');reverse.setAttribute('aria-pressed',String(this.abaqusReversed.has(id)));reverse.onclick=()=>{if(this.abaqusReversed.has(id))this.abaqusReversed.delete(id);else this.abaqusReversed.add(id);this.renderAbaqusPaths();this.draw();this.services.changed();};row.append(label,reverse);this.get('abaqus-paths').append(row);}
    this.get('abaqus-path-status').textContent=`拖动调整顺序 · 勾选参与焊接 · 代表长度 ${this.equivalent?.representative_length_mm??1} mm`;
  }
  private download(blob:Blob,name:string){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  private async analyzeResults(){
    if(this.analysisBusy){this.analysisQueued=true;return;}this.analysisBusy=true;
    try{await this.calculateResults();}finally{this.analysisBusy=false;if(this.analysisQueued){this.analysisQueued=false;void this.analyzeResults().catch(error=>this.services.message(String(error)));}}
  }
  private async calculateResults(){
    const file=this.field('result-file').files?.[0];if(!file)throw Error('请选择结果 ZIP。');
    const revision=++this.resultRevision,form=new FormData();form.append('results',file);const dat=this.field('result-dat').files?.[0];if(dat)form.append('local_model',dat);
    const settings={length_mm:this.value('represent-length'),bins:this.value('represent-bins'),final_time_s:this.field('results-final').value?this.value('results-final'):null,length_unit:this.field('result-unit').value};form.append('settings',JSON.stringify(settings));
    this.get('result-progress').hidden=false;this.get('result-status').textContent='正在读取积分点并计算…';
    const timer=window.setInterval(()=>{void this.services.api('local-trial/results-progress').then(response=>response.json()).then(state=>{const progress=this.get('result-progress') as HTMLProgressElement;progress.max=Math.max(state.total,1);progress.value=state.completed;this.get('result-status').textContent=`${state.stage??'正在读取'} · ${state.completed}/${state.total||'…'}`;});},700);
    try{const cached=this.datasetId&&this.importedFile===file;const result=await(await this.services.api(cached?'local-trial/results-recompute':'local-trial/results',cached?{...settings,dataset_id:this.datasetId}:form)).json();this.datasetId=result.dataset_id;this.importedFile=file;if(revision!==this.resultRevision)return;this.equivalent=result;this.field('equivalent-export').disabled=false;this.get('result-summary').replaceChildren();for(const state of result.states){if(state.kind==='initial')continue;const section=document.createElement('div');section.className='lt-result-card';const title=document.createElement('strong');title.textContent=`第 ${state.pass} 次焊接 · ${state.time_s.toFixed(6)} s`;section.append(title);for(const component of state.components){const text=document.createElement('p');text.textContent=`${component.name} · 厚度 ${component.profile.thickness_mm.toPrecision(4)} mm · 最高温度 ${component.profile.temperature_max_c.toFixed(1)}℃`;section.append(text);}this.get('result-summary').append(section);}this.get('result-status').textContent=result.final_cooling_complete?'代表数据已计算，保留每次焊接的独立收缩及弯曲增量。':`末态最高温度 ${result.final_temperature_max_c.toFixed(2)}℃，未达到 ${result.cooling_limit_c}℃ 冷却条件。当前数据可检查流程，变形精度需要冷却完整的结果验证。`;this.services.changed();this.draw();}finally{window.clearInterval(timer);this.get('result-progress').hidden=true;}
  }
  private async readAbaqus(){
    const file=this.field('abaqus-file').files?.[0];if(!file)throw Error('请选择 shell INP。');
    const form=new FormData();form.append('model',file);form.append('length_unit',this.field('abaqus-unit').value);
    const preview=this.field('abaqus-preview').files?.[0];if(preview)form.append('preview',preview);
    const value=await(await this.services.api('local-trial/abaqus-model',form)).json();this.abaqus=value;
    this.get('abaqus-status').textContent=`${value.node_count} 节点 · ${value.face_count} shell 单元`;this.get('abaqus-bodies').replaceChildren();
    for(const body of value.bodies){const label=document.createElement('label');label.textContent=body.name;const select=document.createElement('select');select.dataset.abaqusBody=String(body.id);for(const component of this.equivalent?.component_names??[]){const option=document.createElement('option');option.value=component;option.textContent=component;select.append(option);}label.append(select);this.get('abaqus-bodies').append(label);}
    this.get('abaqus-bodies').querySelectorAll<HTMLSelectElement>('select').forEach(select=>{const option=document.createElement('option');option.value='';option.textContent='请选择对应板件';select.prepend(option);select.value='';});
    this.renderAbaqusPaths();this.draw();this.viewer.fit();
  }
  private abaqus:any=null;
  private async exportAbaqus(){
    if(!this.equivalent)throw Error('请在结果处理页面完成代表数据计算。');if(!this.abaqus)throw Error('请读取整体 shell INP。');if(!this.meta)throw Error('请读取来源 DAT 的完整焊路。');
    const body_components=Object.fromEntries(Array.from(this.panel.querySelectorAll<HTMLSelectElement>('[data-abaqus-body]')).map(select=>[select.dataset.abaqusBody!,select.value]));
    const path_order=this.abaqusOrder.filter(id=>!this.abaqusExcluded.has(id));const response=await this.services.api('local-trial/abaqus-export',{job_name:this.field('abaqus-job').value,body_components,model_id:this.meta.model_id,equivalent_id:this.equivalent.equivalent_id,path_order,reverse_path_ids:path_order.filter(id=>this.abaqusReversed.has(id))});this.download(await response.blob(),'abaqus_weld.zip');
  }
  private get(id:string){return this.panel.querySelector<HTMLElement>(`#lt-${id}`)!;}
  private field(id:string){return this.get(id) as HTMLInputElement;}
  private value(id:string){const value=Number(this.field(id).value);if(!Number.isFinite(value)||this.field(id).value==='')throw Error('请输入有效数值。');return value;}
  private markDirty(){this.dirty=true;this.get('dirty').textContent='当前参数尚未保存';this.services.changed();}
  activate(active:boolean){this.active=active;this.viewer.showLocalTrial(active);if(active&&this.available&&!this.meta)void this.services.run('正在读取局部试算焊路…',()=>this.load());if(active)this.draw();}
  async modelChanged(available:boolean){this.revision++;window.clearTimeout(this.timer);this.available=available;this.hasSelection=false;this.meta=null;this.result=null;this.cropNodes=[];this.groups=[];this.excluded.clear();this.editing=-1;this.dirty=false;this.renderGroups();this.get('paths').replaceChildren();this.get('matches').replaceChildren();this.get('source').textContent=available?'进入局部试算后读取 DAT 焊路。':'局部试算需要瞬态计算 Marc DAT。';this.get('selected-path').textContent='点击焊路节点选择范围。';this.draw();if(this.active&&available)await this.load();}
  private async load(){const revision=++this.revision;const meta:Meta=await(await this.services.api('local-trial',{length_unit:this.services.unit()})).json();if(revision!==this.revision)return;this.meta=meta;this.shown=new Set(meta.paths.map(p=>p.id));this.get('source').textContent=meta.source_process;this.field('path').value=String(meta.paths[0].id);this.renderPaths();this.get('components').replaceChildren();for(const component of meta.components){const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=component.name==='Base'||component.keep_whole;check.dataset.component=component.name;check.onchange=()=>this.changed();label.append(check,document.createTextNode(`完整保留 ${component.name}`));this.get('components').append(label);}this.resetRange();}
  private renderPaths(){this.get('paths').replaceChildren();for(const path of this.meta?.paths??[]){const row=document.createElement('div');row.className='lt-path-row';const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=this.shown.has(path.id);check.onchange=()=>{if(check.checked)this.shown.add(path.id);else this.shown.delete(path.id);this.field('all').checked=this.shown.size===this.meta!.paths.length;this.draw();};label.append(check,document.createTextNode(path.label));const only=iconButton('eye',`单独显示 ${path.label}`);only.onclick=()=>{this.shown=new Set([path.id]);this.field('all').checked=this.meta!.paths.length===1;this.renderPaths();this.draw();};row.append(label,only);this.get('paths').append(row);}}
  private path(){const path=this.meta?.paths.find(p=>p.id===Number(this.field('path').value));if(!path)throw Error('请选择有效焊路。');return path;}
  private resetRange(){
    if(!this.meta)return;
    if(this.get('component-names').dataset.model!==this.meta.model_id){this.get('component-names').dataset.model=this.meta.model_id;this.field('source-process').value=this.meta.source_process;
      this.get('component-names').replaceChildren();for(const component of this.meta.components){const label=document.createElement('label');label.textContent=component.name;const input=document.createElement('input');input.dataset.sourceComponent=component.name;input.value=component.name;label.append(input);this.get('component-names').append(label);}}
    const path=this.path();this.field('start').value='0';this.field('end').value=String(path.length_mm);
    this.field('center').value=String(path.length_mm/2);this.field('length').value=String(path.length_mm);this.field('wrap').checked=false;
    this.field('wrap').disabled=Math.hypot(...path.points_mm[0].map((v,i)=>v-path.points_mm.at(-1)![i]))>1e-8;
    this.excluded.clear();
    for(let axis=0;axis<3;axis++)for(const [index,end] of ['min','max'].entries()){
      const min=this.meta.bounds_mm[0][axis],max=this.meta.bounds_mm[1][axis],span=max-min;
      const margin=axis===2?Math.max(span,1):span*.1;
      const range=this.field(`range-${end}-${axis}`);range.min=String(min-margin);range.max=String(max+margin);range.disabled=span===0&&margin===0;
      range.value=String(this.meta.bounds_mm[index][axis]);this.field(`box-${end}-${axis}`).value=String(this.meta.bounds_mm[index][axis]);
    }
    this.updateMethod();this.changed();
  }
  private interval(){
    const path=this.path();let start=this.field('start').valueAsNumber,end=this.field('end').valueAsNumber;
    const closed=Math.hypot(...path.points_mm[0].map((v,i)=>v-path.points_mm.at(-1)![i]))<=1e-8;
    this.field('wrap').checked=closed&&start>end;
    if(this.field('method').value==='center'){
      start=this.field('center').valueAsNumber-this.field('length').valueAsNumber/2;end=this.field('center').valueAsNumber+this.field('length').valueAsNumber/2;
      this.field('wrap').checked=closed&&(start<0||end>path.length_mm);
      if(this.field('wrap').checked){start=(start%path.length_mm+path.length_mm)%path.length_mm;end=(end%path.length_mm+path.length_mm)%path.length_mm;}
    }
    const length=this.field('wrap').checked?path.length_mm-start+end:end-start;
    return {start,end,length};
  }
  private spec():Spec {const path=this.path(),{start,end,length}=this.interval();
    if(![start,end,length].every(Number.isFinite))throw Error('请输入有效的焊路范围。');
    return {model_id:this.meta!.model_id,path_id:path.id,start_mm:start,end_mm:end,wrap:this.field('wrap').checked,tolerance_mm:this.value('tolerance'),box:{min:[0,1,2].map(i=>this.value(`box-min-${i}`)),max:[0,1,2].map(i=>this.value(`box-max-${i}`))},keep_whole:Array.from(this.panel.querySelectorAll<HTMLInputElement>('[data-component]:checked')).map(e=>e.dataset.component!),trim_start_mm:0,trim_end_mm:0,included_path_ids:this.meta!.paths.filter(p=>!this.excluded.has(p.id)).map(p=>p.id)};
  }
  private changed(){if(!this.meta)return;this.markDirty();this.revision++;this.result=null;this.cropNodes=[];this.get('status').textContent='正在更新预览…';window.clearTimeout(this.timer);this.draw();this.timer=window.setTimeout(()=>{const revision=this.revision+1;void this.preview().catch(error=>{if(revision===this.revision)this.get('status').textContent=error instanceof Error?error.message:String(error);});},250);}
  private async preview(){
    window.clearTimeout(this.timer);const revision=++this.revision;const spec=this.spec();
    const result:Preview=await(await this.services.api('local-trial/preview',spec)).json();if(revision!==this.revision)return;
    if(!result.crop.faces?.every(face=>Number.isInteger(face.body)))throw Error('局部试算后台与网页版本不一致，请关闭 SIMUWELD SD 程序后重新启动。');
    this.result=result;this.cropNodes=result.crop.node_ids.map((id,i)=>({id,xyz_mm:result.crop.points_mm[i]}));
    const warnings=result.warnings;
    this.get('status').textContent=`${result.crop.node_count} 节点 · ${result.crop.element_count} 单元${warnings.length?'\n'+warnings.join('\n'):''}`;
    this.renderMatches();this.draw();
  }
  private updateMethod(){
    const center=this.field('method').value==='center';
    this.get('ends-fields').hidden=center;this.get('center-fields').hidden=!center;
    this.get('pick-buttons').querySelectorAll<HTMLButtonElement>('[data-pick]').forEach(button=>{
      button.hidden=center?button.dataset.pick!=='center':button.dataset.pick==='center';
      button.setAttribute('aria-pressed',String(button.dataset.pick===this.pickTarget));
    });
    if(this.meta)this.get('selected-path').textContent=this.hasSelection?`当前焊路：${this.path().label} · ${this.pickTarget==='start'?'点击起点':this.pickTarget==='end'?'点击终点':'点击中心点'}`:'点击焊路节点选择范围。';
  }
  private renderMatches(){this.get('matches').replaceChildren();for(const path of this.meta?.paths??[]){const matches=this.result?.matches.filter(match=>match.path_id===path.id)??[];const excluded=this.excluded.has(path.id);if(!matches.length&&!excluded)continue;const matched=matches.length>0&&matches.every(match=>match.matched);const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=matched&&!excluded;check.disabled=path.id===Number(this.field('path').value)||(!matched&&!excluded);check.onchange=()=>{if(check.checked)this.excluded.delete(path.id);else this.excluded.add(path.id);this.changed();};const adjustment=matches.flatMap(match=>match.endpoint_adjustment_mm);label.append(check,document.createTextNode(`${path.label} · ${excluded?'未纳入':matched?'匹配成功':matches.map(match=>match.reason).join('；')}${adjustment.some(value=>value>0)?` · 端点调整 ${adjustment.map(value=>value.toPrecision(4)).join(' / ')} mm`:''}`));this.get('matches').append(label);}}
  private selectionMode(){
    this.viewer.localTrialSelection('path',(_ids,pathId,point)=>{
      if(pathId!==undefined&&point!==undefined){
        this.hasSelection=true;
        const path=this.meta?.paths.find(p=>p.id===pathId);if(!path)return;
        const different=Number(this.field('path').value)!==pathId;
        this.field('path').value=String(pathId);this.excluded.delete(pathId);
        if(different){this.pickTarget=this.field('method').value==='center'?'center':'start';this.resetRange();}
        this.field('wrap').disabled=Math.hypot(...path.points_mm[0].map((v,i)=>v-path.points_mm.at(-1)![i]))>1e-8;
        if(this.field('wrap').disabled)this.field('wrap').checked=false;
        let distance=0;for(let i=1;i<=point;i++)distance+=Math.hypot(...path.points_mm[i].map((v,k)=>v-path.points_mm[i-1][k]));
        this.field(this.pickTarget).value=String(distance);this.field('method').value=this.pickTarget==='center'?'center':'ends';
        if(this.pickTarget==='start')this.pickTarget='end';
        this.updateMethod();this.changed();
      }
    });
    this.draw();
  }
  private renderGroups(){this.get('dirty').textContent=this.dirty?'当前参数尚未保存':'';this.get('groups').replaceChildren();this.groups.forEach((group,index)=>{const row=document.createElement('div');row.className='lt-path-row';const label=document.createElement('span');label.textContent=group.name;const edit=iconButton('edit','编辑试算分组');edit.onclick=()=>this.restore(group,index);const copy=iconButton('add','复用试算分组');copy.onclick=()=>{this.restore(group,-1);this.field('name').value=group.name+' 副本';this.markDirty();};const remove=iconButton('delete','删除试算分组');remove.onclick=()=>{this.groups.splice(index,1);if(this.editing===index)this.editing=-1;else if(this.editing>index)this.editing--;this.renderGroups();this.services.changed();};row.append(label,edit,copy,remove);this.get('groups').append(row);});}
  private restore(group:Group,index:number){
    this.editing=index;this.field('name').value=group.name;this.field('path').value=String(group.path_id);
    this.field('method').value='ends';this.field('start').value=String(group.start_mm);this.field('end').value=String(group.end_mm);
    this.field('wrap').checked=group.wrap;const path=this.path();
    this.field('wrap').disabled=Math.hypot(...path.points_mm[0].map((v,i)=>v-path.points_mm.at(-1)![i]))>1e-8;
    this.field('tolerance').value=String(group.tolerance_mm);
    this.hasSelection=true;
    for(const end of ['min','max'] as const)for(let axis=0;axis<3;axis++){
      this.field(`box-${end}-${axis}`).value=String(group.box[end][axis]);this.field(`range-${end}-${axis}`).value=String(group.box[end][axis]);
    }
    this.panel.querySelectorAll<HTMLInputElement>('[data-component]').forEach(e=>e.checked=group.keep_whole.includes(e.dataset.component!));
    this.excluded=new Set(this.meta!.paths.filter(p=>group.included_path_ids&&!group.included_path_ids.includes(p.id)).map(p=>p.id));
    this.pickTarget='start';this.updateMethod();this.changed();this.dirty=false;this.renderGroups();
  }
  private draw(){
    if(this.tab==='abaqus'&&this.abaqus){const nodes=this.abaqus.points_mm.map((xyz_mm:number[],index:number)=>({id:index,xyz_mm}));const paths:TrialOverlay['paths']=[];const length=this.equivalent?.representative_length_mm??1;for(const id of this.abaqusOrder){if(this.abaqusExcluded.has(id))continue;const path=this.meta?.paths.find(path=>path.id===id);if(!path)continue;const points=this.abaqusReversed.has(id)?[...path.points_mm].reverse():path.points_mm;for(let start=0;start<path.length_mm-1e-8;start+=length)paths.push({id:-1-paths.length,points_mm:slicePath(points,start,Math.min(start+length,path.length_mm)),color:paths.length%2?0xe5b34d:0xb88425,width:3});}this.viewer.setLocalTrial({nodes,paths,selected:[],faces:this.abaqus.faces,box:null,showNodes:false,previewReady:true,independent:true});this.viewer.showLocalTrial(this.active);return;}
    if(this.tab==='results'&&this.equivalent){const paths=this.equivalent.paths.map((path:any,index:number)=>({id:index,points_mm:path.points_mm,color:0x246f96,width:3}));const reference=this.equivalent.paths[0].points_mm;let length=0;for(let index=1;index<reference.length;index++)length+=Math.hypot(...reference[index].map((value:number,axis:number)=>value-reference[index-1][axis]));paths.push({id:-1,points_mm:slicePath(reference,(length-this.equivalent.representative_length_mm)/2,(length+this.equivalent.representative_length_mm)/2),color:0xc18a22,width:7});this.viewer.setLocalTrial({nodes:[],paths,selected:[],faces:[],box:null,showNodes:false,previewReady:false});this.viewer.showLocalTrial(this.active);return;}
    const pathId=this.hasSelection?Number(this.field('path').value):-1;
    const paths:TrialOverlay['paths']=(this.meta?.paths??[]).filter(p=>this.shown.has(p.id)).map(p=>({id:p.id,points_mm:p.points_mm,color:p.id===pathId?0xe0ae35:0x226f98,width:p.id===pathId?4:2,selected:p.id===pathId}));
    if(this.meta&&this.shown.has(pathId)){
      const path=this.path(),{start,end,length}=this.interval();
      if([start,end,length].every(Number.isFinite)&&length>0&&start>=0&&end>=0&&start<=path.length_mm&&end<=path.length_mm){
        const points=this.field('wrap').checked?[...slicePath(path.points_mm,start,path.length_mm),...slicePath(path.points_mm,0,end).slice(1)]:slicePath(path.points_mm,start,end);
        paths.push({id:-1,points_mm:points,color:0xb57600,width:6});
      }
    }
    let box:TrialOverlay['box']=null;if(this.meta)box={min:[0,1,2].map(i=>Number(this.field(`box-min-${i}`).value)),max:[0,1,2].map(i=>Number(this.field(`box-max-${i}`).value))};
    this.viewer.setLocalTrial({paths,nodes:this.cropNodes,selected:[],faces:this.result?.crop.faces??[],box,showNodes:false,previewReady:this.result!==null});
    this.viewer.showLocalTrial(this.active);
  }
}
