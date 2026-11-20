import './inherentStrain.css';
import {Viewer,type InherentGrid} from './viewer';
import {setIcon} from './ui';

interface Services {
  api:(path:string,body?:object|FormData)=>Promise<Response>;message:(text:string)=>void;
  unit:()=>string;modelId:()=>string;changed:()=>void;
}
interface State {
  job_id:string;model_id:string;revision:number;status:string;stage:string;operation:string;phase:string;
  time_s:number;target_s:number;completed:number;total:number|null;progress_unit:string;
  max_temperature_c:number|null;steps:number;frames:number;error:string|null;
  prepare_elapsed_s:number;calculation_elapsed_s:number;can_stop:boolean;stop_requested:boolean;
  grid_parameters?:Record<string,number>;
}
interface Frame {job_id:string;index:number;time_s:number;temperature_c:number[];}
interface MechanicsResult {
  job_id:string;released:boolean;positions_mm:number[];displacement_mm:number[];
  normal_displacement_mm:number[];equivalent_plastic_strain:number[];plastic_strain:number[][];
  summary:Record<string,unknown>;
}
const modelFields=[
  ['spacing_mm','远离焊路的网格尺寸 / mm','0.000001','100'],
  ['weld_spacing_mm','焊路附近网格尺寸 / mm','0.000001','100'],
  ['refine_distance_mm','焊路附近加密距离 / mm','0.000001','100'],
];
const calculationFields=[
  ['dt_s','最大时间步长 / s','0.000000001','1'],['end_s','工艺结束时刻 / s','0.000000001',''],
  ['record_interval_s','结果保存间隔 / s','0.000000001',''],
  ['initial_c','初始温度 / ℃','-273.149',''],['ambient_c','环境温度 / ℃','-273.149',''],
  ['convection_h','外表面对流 / W·m⁻²·K⁻¹','0',''],['emissivity','外表面辐射率','0','1'],
  ['contact_h','接触界面换热 / W·m⁻²·K⁻¹','0',''],['gap_h','间隙换热 / W·m⁻²·K⁻¹','0',''],
  ['weld_h','焊后连接换热 / W·m⁻²·K⁻¹','0',''],
  ['fixture_h','夹具换热 / W·m⁻²·K⁻¹','0',''],['fixture_c','夹具温度 / ℃','-273.149',''],
];
const fields=[...modelFields,...calculationFields];
const field=(entry:string[])=>`<label>${entry[1]}<input id="is-${entry[0]}" type="number" step="any" min="${entry[2]}" ${entry[3]?`max="${entry[3]}"`:''} aria-describedby="is-source-${entry[0]}" required><small id="is-source-${entry[0]}" class="muted"></small></label>`;
const section=(name:string,contents:string,open=false,id='')=>`<details ${id?`id="is-${id}"`:''} class="is-group" ${open?'open':''}><summary>${name}</summary><div class="is-content">${contents}</div></details>`;
const format=(number:number)=>new Intl.NumberFormat('zh-CN',{maximumSignificantDigits:6}).format(number);

export class InherentStrainTool {
  private panel:HTMLElement;
  private supported=false;
  private active=false;
  private revision=0;
  private model='';
  private job='';
  private grid:InherentGrid|null=null;
  private state:State|null=null;
  private material:File|null=null;
  private materialReady=false;
  private materialUploading=false;
  private frame:Frame|null=null;
  private timer=0;
  private pending=false;
  private frameRequest=0;
  private shownFrame=-1;
  private follow=true;
  private peak:number[]|null=null;
  private mechanics:MechanicsResult|null=null;
  private dirty=false;
  private applied:Record<string,number>={};

  constructor(private viewer:Viewer,private services:Services) {
    this.panel=document.createElement('aside');this.panel.className='right inherent-panel';this.panel.dataset.tool='inherent-strain';this.panel.hidden=true;
    this.panel.innerHTML=`<h2>固有应变法</h2><p id="is-support" class="muted">请导入 Marc DAT 模型。</p>
      <div class="is-run">
        <ol id="is-phases" aria-label="计算阶段"><li data-phase="prepare">准备</li><li data-phase="thermal">热过程</li><li data-phase="cooling">冷却</li><li data-phase="structure">结构</li><li data-phase="complete">完成</li></ol>
        <output id="is-status" role="status" aria-live="polite">等待导入 DAT</output>
        <progress id="is-progress" aria-label="当前阶段进度"></progress>
        <output id="is-clocks">计算尚未开始</output>
        <div class="is-actions"><button id="is-start" class="primary">开始计算</button><button id="is-cancel">停止</button></div>
        <p id="is-start-reason" class="muted"></p><button id="is-prepare" class="full">重新准备</button>
      </div>
      ${section('材料与二维模型',`<label class="is-upload">XMT 材料<input id="is-material" type="file" accept=".xmt"></label><p id="is-material-info" class="muted">请选择 XMT 材料。</p><label>显示板层<select id="is-layer" disabled></select></label><div id="is-layers" class="muted"></div><label class="check"><input id="is-grid" type="checkbox" checked>显示独立计算网格</label><label class="check"><input id="is-show-paths" type="checkbox" checked>显示焊路</label><label class="check"><input id="is-show-fixtures" type="checkbox" checked>显示夹具区域</label><p class="muted">温度采用固定二维视图。鼠标滚轮缩放，鼠标右键平移，顶部按钮适应窗口。</p>`,true)}
      ${section('工艺详情','<p id="is-process" class="muted">导入后读取 DAT 热源、焊路、阶段时间与夹具位置。</p><div id="is-contacts" class="muted"></div><div id="is-paths"></div>')}
      ${section('高级参数（可选）',`<p class="muted">参数自动填入。修改后点击应用参数；恢复默认值后同样需要应用。最大时间步长为上限，实际步长随热源运动和温度变化自动调整。</p>${fields.map(field).join('')}<div class="is-actions"><button id="is-apply">应用参数</button><button id="is-defaults">恢复默认值</button></div><output id="is-parameters" role="status">等待生成默认参数</output>`,false,'advanced')}
      ${section('结果',`<label>温度显示<select id="is-view"><option value="frame">当前温度帧</option><option value="peak">历史最高温度</option></select></label><label>温度帧<input id="is-frame" aria-label="温度帧" type="range" min="0" max="0" value="0" disabled></label><output id="is-frame-time">尚无温度帧</output><label class="check"><input id="is-follow" type="checkbox" checked>跟随最新温度帧</label><output id="is-mechanical-status" role="status">尚无结构结果</output><label>显示结果<select id="is-mechanical-view" disabled><option value="temperature">温度</option><option value="displacement">法向位移 / mm</option><option value="peeq">等效塑性应变 PEEQ</option><option value="plastic-x">板面 X 方向塑性应变</option><option value="plastic-y">板面 Y 方向塑性应变</option></select></label><label>变形显示倍率<input id="is-deformation-scale" type="number" min="0" step="any" value="1" disabled></label><div class="is-legend"></div><output id="is-legend-unit">温度 / ℃</output><div class="is-range"><label>图例下限<input id="is-min" type="number" step="any" value="25" aria-describedby="is-legend-error"></label><label>图例上限<input id="is-max" type="number" step="any" value="1500" aria-describedby="is-legend-error"></label></div><output id="is-legend-error" role="status"></output><button id="is-export" class="full">导出计算结果 ZIP</button>`,true)}`;
    document.querySelector('main')!.append(this.panel);
    for(const [id,icon,label] of [['prepare','trial','重新准备'],['start','play','开始计算'],['cancel','close','停止'],['export','download','导出计算结果 ZIP']] as const) {
      const button=this.get<HTMLButtonElement>(id);setIcon(button,icon,label);button.classList.remove('icon-button');const text=document.createElement('span');text.textContent=label;button.append(text);
    }
    this.get('prepare').onclick=()=>void this.action(()=>this.prepare());
    this.get('start').onclick=()=>void this.action(()=>this.start());
    this.get('cancel').onclick=()=>void this.action(async()=>{
      this.get('status').textContent='正在停止，等待当前操作结束';this.get<HTMLButtonElement>('cancel').disabled=true;
      const state=await this.json<State>('cancel',this.identity());if(this.matches(state))this.applyState(state);
    });
    this.get('export').onclick=()=>void this.action(async()=>{
      const job=this.job,response=await this.services.api(`inherent-strain/export?job_id=${encodeURIComponent(job)}`);
      const blob=await response.blob();if(job!==this.job)return;
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='inherent-strain-results.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    for(const [id] of fields)this.get(id).oninput=()=>{this.dirty=true;this.parameterSources();this.refresh();};
    this.get('apply').onclick=()=>void this.action(()=>this.applyParameters());
    this.get('defaults').onclick=()=>{if(this.grid){this.fillParameters(this.grid.defaults);this.dirty=true;this.parameterSources();this.refresh();}};
    this.get<HTMLInputElement>('material').onchange=()=>{
      this.material=this.get<HTMLInputElement>('material').files?.[0]||null;this.materialReady=false;
      this.get('material-info').textContent=this.material?`${this.material.name} · 等待读取`:'请选择 XMT 材料。';
      if(!this.running()&&!this.pending)void this.action(()=>this.uploadMaterial());this.refresh();
    };
    this.get<HTMLSelectElement>('layer').onchange=()=>{const index=Number(this.get<HTMLSelectElement>('layer').value);this.grid?.layers.forEach(layer=>this.viewer.showInherentLayer(layer.index,layer.index===index));this.showLayer(index);};
    this.get<HTMLInputElement>('grid').onchange=()=>this.viewer.showInherentGrid(this.get<HTMLInputElement>('grid').checked);
    this.get<HTMLInputElement>('show-paths').onchange=()=>this.grid?.paths.forEach(path=>this.viewer.showInherentPath(path.id,this.get<HTMLInputElement>('show-paths').checked));
    this.get<HTMLInputElement>('show-fixtures').onchange=()=>this.viewer.showInherentFixtures(this.get<HTMLInputElement>('show-fixtures').checked);
    this.get('mechanical-view').onchange=()=>this.changeMechanicalView();
    this.get('deformation-scale').oninput=()=>void this.action(async()=>this.paint());
    this.get('view').onchange=()=>void this.action(()=>this.loadPeak());
    this.get<HTMLInputElement>('frame').oninput=()=>{this.follow=false;this.get<HTMLInputElement>('follow').checked=false;void this.action(()=>this.loadFrame(Number(this.get<HTMLInputElement>('frame').value)));};
    this.get<HTMLInputElement>('follow').onchange=()=>{this.follow=this.get<HTMLInputElement>('follow').checked;if(this.follow&&this.state?.frames)void this.action(()=>this.loadFrame(this.state!.frames-1));};
    for(const id of ['min','max'])this.get(id).oninput=()=>void this.action(async()=>this.paint());
    this.refresh();
  }

  private get<T extends HTMLElement=HTMLElement>(id:string) {return this.panel.querySelector<T>('#is-'+id)!;}
  private identity() {return {model_id:this.model,revision:this.revision,job_id:this.job};}
  private matches(state:{model_id:string;revision:number;job_id:string}) {return state.model_id===this.model&&state.revision===this.revision&&state.job_id===this.job;}
  private async json<T>(path:string,body?:object|FormData):Promise<T> {return (await this.services.api('inherent-strain/'+path,body)).json();}
  private running() {return this.state?.status==='running'||this.state?.status==='cancelling'||!!this.state?.can_stop;}
  private value(id:string) {
    const input=this.get<HTMLInputElement>(id);
    if(!input.checkValidity()||!Number.isFinite(input.valueAsNumber))throw new Error(`请检查${fields.find(item=>item[0]===id)![1]}。`);
    return input.valueAsNumber;
  }
  private async action(action:()=>Promise<void>) {
    const revision=this.revision,model=this.model;
    try {await action();}catch(error) {if(revision===this.revision&&model===this.model){this.get('status').textContent=(error as Error).message;this.services.message((error as Error).message);if(this.running()||(!this.grid&&this.state?.status==='ready'))this.schedule();}}
    finally {this.refresh();}
  }
  activate(active:boolean) {
    this.active=active;this.viewer.activateInherent(active);
    if(active&&this.supported&&!this.job&&!this.pending)void this.action(()=>this.prepare());
  }
  modelChanged(supported:boolean) {
    this.model=this.services.modelId();this.supported=supported;this.revision=0;this.material=null;this.materialReady=false;
    this.applied={};this.dirty=false;this.get<HTMLInputElement>('material').value='';this.get('material-info').textContent='请选择 XMT 材料。';
    this.clear();for(const [id] of fields)this.get<HTMLInputElement>(id).value='';
    this.get('status').textContent='等待导入 DAT';this.get('support').textContent=supported?'DAT 已导入，自动恢复二维外形并准备独立计算网格。':'请导入 Marc DAT 模型。';
    if(this.active&&supported)void this.action(()=>this.prepare());this.refresh();
  }
  private clearResults() {
    this.frame=null;this.peak=null;this.mechanics=null;this.shownFrame=-1;this.frameRequest++;
    this.get<HTMLSelectElement>('mechanical-view').value='temperature';this.get('mechanical-status').textContent='尚无结构结果';
    this.get<HTMLInputElement>('min').value='25';this.get<HTMLInputElement>('max').value='1500';this.get('legend-unit').textContent='温度 / ℃';
    this.get<HTMLInputElement>('frame').value='0';this.get('frame-time').textContent='尚无温度帧';this.viewer.setInherentDisplacement(null);
  }
  private clear() {
    window.clearTimeout(this.timer);this.clearResults();this.job='';this.grid=null;this.state=null;
    delete this.panel.dataset.status;delete this.panel.dataset.job;
    this.viewer.setInherentGrid(null);this.get('layer').replaceChildren();this.get('layers').replaceChildren();this.get('contacts').replaceChildren();this.get('paths').replaceChildren();
  }
  private fillParameters(values:Record<string,number>) {for(const [id] of fields)if(values[id]!==undefined)this.get<HTMLInputElement>(id).value=String(values[id]);}
  private parameterSources() {
    if(!this.grid)return;
    let changed=0;
    for(const [id] of fields){const modified=this.get<HTMLInputElement>(id).valueAsNumber!==this.grid.defaults[id];if(modified)changed++;this.get('source-'+id).textContent=modified?'用户修改 · 统一应用此数值':this.grid.sources[id];}
    this.get('parameters').textContent=`${changed?`${changed} 项用户修改`:'已使用自动参数'}${this.dirty?' · 点击应用参数后生效':' · 参数已应用'}。`;
  }
  private async applyParameters() {
    const values=Object.fromEntries((this.grid?fields:modelFields).map(([id])=>[id,this.value(id)]));
    const newGrid=modelFields.some(([id])=>values[id]!==this.applied[id]);
    if(newGrid){
      this.pending=true;this.refresh();
      try {await this.json('invalidate',{model_id:this.model,revision:this.revision+1});}
      finally {this.pending=false;this.refresh();}
      this.revision++;this.materialReady=false;this.clear();
    }
    this.applied=values;this.dirty=false;this.services.changed();this.get('parameters').textContent='参数已应用，下次计算使用这些设置。已有结果对应上次计算设置。';
    if(newGrid)await this.prepare();else this.parameterSources();
    this.refresh();
  }
  private async uploadMaterial() {
    if(!this.material||this.materialReady||this.running()||this.materialUploading)return;
    const revision=this.revision,model=this.model,file=this.material;
    const form=new FormData();form.append('model_id',model);form.append('revision',String(revision));form.append('material',file);
    this.materialUploading=true;this.get('material-info').textContent=`${file.name} · 正在读取`;this.refresh();
    try {
      const info=await this.json<{name:string}>('material',form);
      if(revision!==this.revision||model!==this.model||file!==this.material)return;
      this.materialReady=true;this.clearResults();if(this.state){this.state.frames=0;this.state.status=this.grid?'ready':this.state.status;}
      this.get('material-info').textContent=`${info.name} · 材料已读取`;
    } finally {this.materialUploading=false;this.refresh();}
  }
  private async prepare() {
    if(this.pending||this.running())return;
    if(!this.model||!this.supported)throw new Error('请导入 Marc DAT 模型。');
    const revision=this.revision,model=this.model,values=Object.fromEntries(modelFields.filter(([id])=>this.applied[id]!==undefined).map(([id])=>[id,this.applied[id]]));
    this.pending=true;this.get('status').textContent='正在提交网格准备任务';this.refresh();
    try {
      const state=await this.json<State>('prepare',{model_id:model,revision,length_unit:this.services.unit(),...values});
      if(revision!==this.revision||model!==this.model)return;
      this.clearResults();this.grid=null;this.job=state.job_id;this.applyState(state);this.schedule();
    } finally {this.pending=false;this.refresh();}
  }
  private async start() {
    if(this.pending)return;
    if(!this.grid||!this.materialReady||this.dirty)throw new Error('请完成网格、材料与参数准备。');
    const values=Object.fromEntries(calculationFields.filter(([id])=>this.applied[id]!==this.grid!.defaults[id]).map(([id])=>[id,this.applied[id]]));
    this.pending=true;this.refresh();
    try {
      const state=await this.json<State>('start',{...this.identity(),...values});
      if(!this.matches(state))return;
      this.clearResults();this.follow=true;this.get<HTMLInputElement>('follow').checked=true;
      this.get('mechanical-status').textContent='温度与冷却完成后自动计算应变和结构变形';this.applyState(state);this.schedule();
    } finally {this.pending=false;this.refresh();}
  }
  private schedule() {window.clearTimeout(this.timer);this.timer=window.setTimeout(()=>void this.action(()=>this.poll()),250);}
  private async poll() {
    const job=this.job;if(!job)return;
    const state=await this.json<State>(`status?job_id=${encodeURIComponent(job)}`);
    if(!this.matches(state))return;
    this.applyState(state);
    if(!this.grid&&state.status==='ready') {
      const grid=await this.json<InherentGrid&{job_id:string;model_id:string;revision:number}>(`grid?job_id=${encodeURIComponent(job)}`);
      if(!this.matches(grid))return;
      this.grid=grid;this.applied={...grid.defaults,...grid.effective_grid,...this.applied};
      if(!this.dirty)this.fillParameters(this.applied);
      this.parameterSources();this.viewer.setInherentGrid(grid);this.renderGrid();
    }
    if(!this.running()&&this.material&&!this.materialReady)await this.uploadMaterial();
    if(state.frames>0&&this.follow&&this.shownFrame!==state.frames-1)await this.loadFrame(state.frames-1);
    if(state.frames>0&&this.get<HTMLSelectElement>('view').value==='peak')await this.loadPeak();
    if(state.status==='complete'&&!this.mechanics) {
      const result=await this.json<MechanicsResult>(`mechanics?job_id=${encodeURIComponent(job)}`);if(job!==this.job)return;
      this.mechanics=result;const values=result.normal_displacement_mm;let low=Infinity,high=-Infinity;for(const value of values){low=Math.min(low,value);high=Math.max(high,value);}
      this.get('mechanical-status').textContent=`${result.released?'Cover 已释放 · Base 支撑保留':'所选工艺时段已完成 · Cover 保持约束'}\n法向位移 ${format(low)}～${format(high)} mm`;
    }
    this.refresh();if(this.running())this.schedule();
  }
  private applyState(state:State) {
    this.state=state;this.panel.dataset.status=state.status;this.panel.dataset.job=state.job_id;
    if(!this.grid&&!this.dirty&&state.grid_parameters)this.fillParameters(state.grid_parameters);
    const temperature=state.max_temperature_c===null?'':`\n最高温度 ${format(state.max_temperature_c)} ℃`;
    this.get('status').textContent=state.stage+(state.operation==='calculate'?`\n模拟时刻 ${format(state.time_s)} s · ${state.steps} 步 · ${state.frames} 帧${temperature}`:'')+(state.error?'\n'+state.error:'');
    this.get('clocks').textContent=`准备耗时 ${format(state.prepare_elapsed_s)} s`+(state.operation==='calculate'?` · 计算耗时 ${format(state.calculation_elapsed_s)} s`:' · 计算尚未开始');
    const progress=this.get<HTMLProgressElement>('progress');
    if(state.total!==null&&state.total>0){progress.max=state.total;progress.value=Math.min(state.total,state.completed);}else progress.removeAttribute('value');
    const phase=state.operation==='prepare'?'prepare':state.phase==='release'?'structure':state.phase;
    this.get('phases').querySelectorAll<HTMLElement>('li').forEach(item=>item.setAttribute('aria-current',item.dataset.phase===phase?'step':'false'));
    this.get<HTMLInputElement>('frame').max=String(Math.max(0,state.frames-1));this.refresh();
  }
  private renderGrid() {
    const grid=this.grid!,select=this.get<HTMLSelectElement>('layer');select.replaceChildren();this.get('paths').replaceChildren();
    for(const layer of grid.layers){const option=document.createElement('option');option.value=String(layer.index);option.textContent=layer.name;select.append(option);}
    const index=grid.layers.at(-1)!.index;select.value=String(index);grid.layers.forEach(layer=>this.viewer.showInherentLayer(layer.index,layer.index===index));this.showLayer(index);
    this.get('contacts').textContent=grid.contacts.map(contact=>`${grid.layers[contact.lower].name} → ${grid.layers[contact.upper].name}：间隙 ${format(contact.gap_mm)} mm`).join('\n');
    this.get('process').textContent=`DAT 工艺终点 ${format(grid.end_s)} s · ${grid.paths.length} 条焊路\n热过程完成后自动冷却至环境温度差 5 ℃，计算固有应变、Shell 平衡和 Cover 释放。\n`+grid.boundaries.bodies.map(body=>`${body.name}：${body.kind===0?`对流 ${body.convection_h} W·m⁻²·K⁻¹ · 辐射率 ${body.emissivity} · 环境 ${body.ambient_c} ℃`:`夹具温度 ${body.temperature_c} ℃`}`).join('\n');
    grid.paths.forEach(path=>{
      const details=document.createElement('details'),summary=document.createElement('summary'),content=document.createElement('p');
      summary.textContent=`${path.label||'焊路 '+path.id} · ${format(path.start_s)}–${format(path.end_s)} s`;
      content.className='muted';content.textContent=path.sources.map((source,index)=>`热源 ${index+1}：${source.power_W} W · 效率 ${source.efficiency}\n${source.kind===5?`上表面半径 ${source.upper_radius_mm} mm · 下表面半径 ${source.lower_radius_mm} mm`:`半径 ${source.radius_mm} mm`} · 深度 ${source.depth_mm} mm`).join('\n');
      details.append(summary,content);this.get('paths').append(details);
    });
    this.viewer.showInherentGrid(this.get<HTMLInputElement>('grid').checked);
    grid.paths.forEach(path=>this.viewer.showInherentPath(path.id,this.get<HTMLInputElement>('show-paths').checked));
    this.viewer.showInherentFixtures(this.get<HTMLInputElement>('show-fixtures').checked);
  }
  private showLayer(index:number) {const layer=this.grid!.layers.find(layer=>layer.index===index)!;this.get('layers').textContent=`厚度 ${layer.thickness_mm.map(format).join('–')} mm · 面积 ${format(layer.area_mm2)} mm²\n${layer.cell_count} 个计算单元`;}
  private async loadFrame(index:number) {
    const job=this.job,request=++this.frameRequest;const frame=await this.json<Frame>(`frame?job_id=${encodeURIComponent(job)}&index=${index}`);
    if(job!==this.job||request!==this.frameRequest)return;
    this.frame=frame;this.shownFrame=index;this.get<HTMLInputElement>('frame').value=String(index);this.get('frame-time').textContent=`${format(frame.time_s)} s · 第 ${index+1} 帧`;this.paint();
  }
  private async loadPeak() {
    if(this.get<HTMLSelectElement>('view').value==='frame'){this.paint();return;}if(!this.state?.frames)return;
    const job=this.job,peak=await this.json<{temperature_c:number[]}>(`peak?job_id=${encodeURIComponent(job)}`);if(job!==this.job)return;this.peak=peak.temperature_c;this.paint();
  }
  private paint() {
    const minimum=this.get<HTMLInputElement>('min'),maximum=this.get<HTMLInputElement>('max'),low=minimum.valueAsNumber,high=maximum.valueAsNumber;
    const error=!Number.isFinite(low)||!Number.isFinite(high)?'请输入有效的图例数值。':high<=low?'图例上限必须大于下限。':'';
    this.get('legend-error').textContent=error;minimum.setAttribute('aria-invalid',String(Boolean(error)));maximum.setAttribute('aria-invalid',String(Boolean(error)));
    const values=this.mechanicalValues();
    if(values){if(error)return;this.viewer.setInherentDisplacement(this.mechanics!.displacement_mm,this.get<HTMLInputElement>('deformation-scale').valueAsNumber);this.viewer.setInherentTemperature(values,low,high);return;}
    this.viewer.setInherentDisplacement(null);const temperature=this.get<HTMLSelectElement>('view').value==='frame'?this.frame?.temperature_c:this.peak;
    if(temperature&&!error)this.viewer.setInherentTemperature(temperature,low,high);
  }
  private mechanicalValues() {
    if(!this.mechanics)return null;
    switch(this.get<HTMLSelectElement>('mechanical-view').value){case 'displacement':return this.mechanics.normal_displacement_mm;case 'peeq':return this.mechanics.equivalent_plastic_strain;case 'plastic-x':return this.mechanics.plastic_strain.map(value=>value[0]);case 'plastic-y':return this.mechanics.plastic_strain.map(value=>value[1]);default:return null;}
  }
  private changeMechanicalView() {
    const values=this.mechanicalValues(),mode=this.get<HTMLSelectElement>('mechanical-view').value;let low=25,high=1500;
    if(values){low=Infinity;high=-Infinity;for(const value of values){low=Math.min(low,value);high=Math.max(high,value);}if(high===low){const padding=Math.max(Math.abs(low)*.01,1e-9);low-=padding;high+=padding;}}
    this.get<HTMLInputElement>('min').value=String(low);this.get<HTMLInputElement>('max').value=String(high);this.get('legend-unit').textContent=mode==='temperature'?'温度 / ℃':mode==='displacement'?'法向位移 / mm':'应变 / 无量纲';this.paint();this.refresh();
  }
  private refresh() {
    const running=this.running(),busy=this.pending||this.materialUploading||running;
    this.get<HTMLButtonElement>('prepare').hidden=!!this.grid||busy||!this.supported;this.get<HTMLButtonElement>('prepare').disabled=busy;
    const reason=!this.supported?'请导入 Marc DAT 模型。':this.materialUploading?'正在读取 XMT 材料。':busy?(this.state?.stop_requested?'正在停止，等待当前操作结束。':this.state?.operation==='calculate'?'正在计算，可以点击停止。':'正在准备二维计算网格，可以点击停止。'):!this.grid?'网格尚未准备完成，请点击重新准备。':!this.materialReady?'网格已准备完成，请选择 XMT 材料。':this.dirty?'请应用高级参数中的修改。':'准备完成，点击开始计算。';
    this.get('start-reason').textContent=reason;this.get<HTMLButtonElement>('start').disabled=busy||!this.grid||!this.materialReady||this.dirty;
    this.get<HTMLButtonElement>('cancel').disabled=!this.state?.can_stop||this.pending;
    this.get<HTMLButtonElement>('export').disabled=!this.state?.frames||busy;
    this.get<HTMLButtonElement>('apply').disabled=busy||!this.supported||!this.dirty;
    this.get<HTMLButtonElement>('defaults').disabled=busy||!this.grid;
    for(const [id] of modelFields)this.get<HTMLInputElement>(id).disabled=busy||!this.supported;
    for(const [id] of calculationFields)this.get<HTMLInputElement>(id).disabled=busy||!this.grid;
    this.get<HTMLInputElement>('material').disabled=this.materialUploading||(busy&&this.state?.operation==='calculate');this.get<HTMLSelectElement>('layer').disabled=!this.grid;
    const mechanical=this.get<HTMLSelectElement>('mechanical-view').value!=='temperature';
    this.get<HTMLInputElement>('frame').disabled=!this.state?.frames||mechanical;this.get<HTMLInputElement>('follow').disabled=mechanical;this.get<HTMLSelectElement>('view').disabled=mechanical;
    this.get<HTMLSelectElement>('mechanical-view').disabled=!this.mechanics;this.get<HTMLInputElement>('deformation-scale').disabled=!this.mechanics||!mechanical;
  }
}
