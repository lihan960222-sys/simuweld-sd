import type { Viewer } from './viewer';
import { setIcon } from './ui';
import './thermal.css';

interface ThermalPath {
  id: number; points_mm: number[][]; length_mm: number; speed_mm_s: number;
  start_s: number; end_s: number; cooling_end_s: number; representative_node: number;
  node_count: number; missing_count: number;
}
interface Sampling { spacing_mm:number; offsets_mm:number[]; surface:'both'|'upper'|'lower'; search_mm:number; include_turns:boolean; turn_angle_deg:number|null; }
interface ThermalInfo {
  source_process: string; source_sha256: string; room_c: number | null; end_s: number;
  paths: ThermalPath[]; nodes: {node_id: number; component: string; xyz_mm: number[]}[];
  sampling:Sampling; sampling_id:string; missing:{xyz_mm:number[];component:string;path_id:number}[];
  angle_definition:string;surface_definition:string;
}
interface TemperatureInfo {
  rows: number; increments: number; nodes: number; end_s: number; initial_c: number; digest_verified: boolean;
  coverage: {id: number; complete: boolean; weld_complete: boolean}[];
  curves: {id: number; node_id: number; peak_c: number; error_c: number; curve: number[][]}[];
}
interface Services {
  api: (path: string, body?: object | FormData)=>Promise<Response>;
  run: (text: string, action: ()=>Promise<void>)=>Promise<void>;
  message: (text: string)=>void;
  unit: ()=>string;
  changed: ()=>void;
}

export class ThermalTool {
  private panel=document.createElement('aside');
  private info: ThermalInfo | null=null;
  private temperatures: TemperatureInfo | null=null;
  private active=false;
  private available=false;
  private exported=false;
  private revision=0;
  private samplingBusyModel:number|null=null;
  private samplingTimer=0;
  private samplingId='';
  private modelRevision=0;
  private previewCurrent=false;

  constructor(private viewer: Viewer, private services: Services) {
    this.panel.className='right thermal-panel';this.panel.dataset.tool='thermal-cycle';this.panel.hidden=true;
    this.panel.innerHTML=`<div class="tool-heading"><h1>转换至热循环模式</h1></div>
      <section><h2>焊路与采样节点</h2>
        <p id="tc-source" class="hint">上传瞬态计算 DAT 后自动读取。</p>
        <div id="tc-sampling" hidden>
          <div class="input-row"><label>沿焊路采样间距（mm）<input id="tc-spacing" type="number" min="0.001" step="any" value="2" required></label><label>节点搜索距离上限（mm）<input id="tc-search" type="number" min="0.001" step="any" value="0.25" required></label></div>
          <label>横向采样距离（mm）<input id="tc-offsets" type="text" value="0, -0.25, 0.25, -0.75, 0.75" aria-describedby="tc-offset-help"></label>
          <p id="tc-offset-help" class="hint">使用逗号分隔；正负数表示焊路两侧。起点和终点自动保留。</p>
          <label>采样表面<select id="tc-surface"><option value="both">上下表面</option><option value="upper">上表面</option><option value="lower">下表面</option></select></label>
          <label class="check"><input id="tc-turns" type="checkbox" checked>转弯位置补充采样</label>
          <label>转弯识别角度（°）<input id="tc-angle" type="number" min="0.001" max="180" step="any" placeholder="根据当前焊路自动确定"></label>
          <details class="tc-sampling-help"><summary>采样方向与转弯识别</summary><p id="tc-angle-help" class="hint"></p></details>
        </div>
        <p id="tc-sampling-status" class="hint" role="status" aria-live="polite"></p>
        <div id="tc-display" class="tc-display" hidden>
          <label class="check"><input id="tc-paths-visible" type="checkbox" checked><span class="tc-path-key">显示焊路</span></label>
          <label class="check"><input id="tc-nodes-visible" type="checkbox" checked><span class="tc-node-key">显示采样节点</span></label>
          <label class="tc-size">节点大小<input id="tc-node-size" type="range" min="2" max="16" value="6" aria-label="节点显示大小"></label>
          <span class="tc-missing-key">红色：未匹配目标</span>
        </div>
        <div id="tc-path-list" class="tc-path-list"></div>
        <button id="tc-export" class="primary full" disabled>生成温度导出脚本</button>
        <p id="tc-export-help" class="hint" hidden>在原 Simufact 项目中运行导出脚本，随后导入生成的温度 CSV。</p>
        <label id="tc-import-label" class="file-button" hidden>导入温度数据<input id="tc-temperatures" type="file" accept=".csv"></label>
        <p id="tc-temperature-summary" class="hint" role="status" hidden></p>
      </section>
      <section id="tc-settings" hidden><h2>热循环设置</h2>
        <label class="file-button">读取材料温度<input id="tc-material" type="file" accept=".xmt,.umt,.xml"></label>
        <p id="tc-material-source" class="hint">导入材料文件，或填写材料熔化温度。</p>
        <div class="input-row"><label>初始温度（°C）<input id="tc-room" type="number" step="any" required></label><label>熔化温度（°C）<input id="tc-melting" type="number" step="any" required></label></div>
        <div class="input-row"><label>最大分段长度（mm）<input id="tc-maximum" type="number" min="0.001" max="10000" value="2" step="any" required></label><label>曲线误差（°C）<input id="tc-tolerance" type="number" min="0.001" max="100" value="5" step="any" required></label></div>
        <p id="tc-segments" class="hint"></p>
        <div id="tc-coverage" class="tc-coverage" hidden><p id="tc-coverage-note"></p><label class="check"><input id="tc-allow-partial" type="checkbox">仅使用已记录的代表曲线</label></div>
        <details class="tc-curve-group"><summary>温度曲线</summary><div id="tc-curves"></div></details>
        <button id="tc-create" class="primary full" disabled>生成热循环创建脚本</button>
        <p class="hint">在原项目中运行，创建独立的 _TC 过程并生成求解输入。</p>
      </section>`;
    document.querySelector('main')!.append(this.panel);
    this.get('tc-export').onclick=()=>{void this.services.run('正在生成温度导出脚本…',async()=>{
      await this.download('thermal/export','export_node_temperatures.py');
      this.exported=true;this.get('tc-import-label').hidden=false;this.get('tc-export-help').hidden=false;
      this.services.changed();
    });};
    this.field('tc-temperatures').onchange=()=>{void this.services.run('正在核对温度数据…',async()=>{
      const file=this.field('tc-temperatures').files?.[0];if(!file) return;
      this.field('tc-temperatures').value='';this.temperatures=null;this.get('tc-settings').hidden=true;
      this.get('tc-temperature-summary').hidden=true;
      const data=new FormData();data.append('file',file);data.append('sampling_id',this.samplingId);
      this.temperatures=await (await this.services.api('thermal/temperatures',data)).json();
      const result=this.temperatures!;
      this.field('tc-room').value=String(Number(result.initial_c.toFixed(4)));
      this.get('tc-temperature-summary').textContent=`${file.name}\n${result.increments.toLocaleString()} 个采样时刻 · ${result.nodes} 个节点 · 记录至 ${result.end_s.toFixed(6)} s`;
      this.get('tc-temperature-summary').hidden=false;this.get('tc-settings').hidden=false;
      const missing=result.coverage.filter(item=>!item.complete);
      this.get('tc-coverage').hidden=!missing.length;this.field('tc-allow-partial').checked=false;
      this.get('tc-coverage-note').textContent=missing.length?`焊路 ${missing.map(item=>item.id).join('、')} 的温度记录未覆盖全部焊程或计划冷却。当前曲线使用已有记录，未补充温度数据。`:'';
      this.renderCurves();this.updateCreate();this.services.changed();
    });};
    this.field('tc-material').onchange=()=>{void this.services.run('正在读取材料温度…',async()=>{
      const file=this.field('tc-material').files?.[0];if(!file) return;
      this.field('tc-material').value='';const data=new FormData();data.append('file',file);
      const result=await (await this.services.api('thermal/material',data)).json();
      this.field('tc-melting').value=String(result.melting_c);
      this.get('tc-material-source').textContent=`${result.material} · ${file.name}`;this.updateCreate();
    });};
    this.get('tc-create').onclick=()=>{void this.services.run('正在生成热循环创建脚本…',async()=>{
      await this.download('thermal/create','create_thermal_cycle.py',{
        room_c:this.field('tc-room').valueAsNumber,melting_c:this.field('tc-melting').valueAsNumber,
        maximum_mm:this.field('tc-maximum').valueAsNumber,tolerance_c:this.field('tc-tolerance').valueAsNumber,
        allow_partial:this.field('tc-allow-partial').checked,
      });this.services.changed();
    });};
    for(const id of ['tc-room','tc-melting','tc-maximum','tc-tolerance','tc-allow-partial']) this.field(id).oninput=()=>this.updateCreate();
    for(const id of ['tc-paths-visible','tc-nodes-visible']) this.field(id).onchange=()=>this.show();
    this.field('tc-node-size').oninput=()=>this.viewer.setConversionNodeSize(this.field('tc-node-size').valueAsNumber);
    for(const id of ['tc-spacing','tc-search','tc-offsets','tc-surface','tc-turns','tc-angle']) this.field(id).oninput=()=>this.scheduleSampling();
    for(const id of ['tc-export','tc-create']) {
      const button=this.get(id),label=button.textContent!;setIcon(button,'download',label);
      button.classList.remove('icon-button');button.append(document.createTextNode(label));
    }
  }

  private get<T extends HTMLElement=HTMLElement>(id:string) { return this.panel.querySelector<T>('#'+id)!; }
  private field(id:string) { return this.get<HTMLInputElement>(id); }

  async modelChanged(available:boolean) {
    this.modelRevision++;
    this.previewCurrent=false;
    this.revision++;window.clearTimeout(this.samplingTimer);this.samplingId='';
    this.available=available;this.info=null;this.temperatures=null;this.exported=false;
    this.viewer.setConversion([],[]);this.viewer.showConversion(false);
    this.get('tc-source').textContent=available?'进入模块后自动读取焊路。':'上传瞬态计算 DAT 后自动读取。';
    this.get('tc-path-list').replaceChildren();this.get<HTMLButtonElement>('tc-export').disabled=true;
    for(const id of ['tc-sampling','tc-display','tc-import-label','tc-export-help','tc-temperature-summary','tc-settings']) this.get(id).hidden=true;
    this.get('tc-sampling-status').textContent='';
    this.field('tc-melting').value='';this.get('tc-material-source').textContent='导入材料文件，或填写材料熔化温度。';
    this.field('tc-paths-visible').checked=true;this.field('tc-nodes-visible').checked=true;
    if(this.active && available) await this.prepare();
  }

  activate(active:boolean) {
    this.active=active;this.show();
    if(active && this.available && !this.info) void this.read();
  }

  private async read() {
    await this.services.run('正在识别焊路与真实节点…',()=>this.prepare());
  }

  private async prepare() {
      const revision=this.revision;
      const info:ThermalInfo=await (await this.services.api('thermal',{length_unit:this.services.unit()})).json();
      if(revision!==this.revision)return;
      this.samplingId=info.sampling_id;
      this.field('tc-spacing').value=String(info.sampling.spacing_mm);this.field('tc-search').value=String(info.sampling.search_mm);
      this.field('tc-offsets').value=info.sampling.offsets_mm.join(', ');this.field('tc-surface').value=info.sampling.surface;
      this.field('tc-turns').checked=info.sampling.include_turns;this.field('tc-angle').value='';
      this.get('tc-sampling').hidden=false;this.renderSampling(info);
  }

  private renderSampling(info:ThermalInfo) {
      this.info=info;this.previewCurrent=true;
      this.viewer.setConversion(info.paths.map(path=>path.points_mm),info.nodes.map(node=>node.xyz_mm),info.nodes,info.missing);
      this.viewer.setConversionNodeSize(this.field('tc-node-size').valueAsNumber);
      this.get('tc-source').textContent=`${info.source_process} · ${info.paths.length} 条焊路 · ${info.nodes.length} 个真实节点`;
      this.field('tc-angle').placeholder=`自动：${info.sampling.turn_angle_deg?.toFixed(2) ?? '—'}°`;
      this.get('tc-angle-help').textContent=`${info.surface_definition}\n${info.angle_definition} 角度留空时使用自动阈值。`;
      this.get('tc-sampling-status').textContent=`预览已更新 · ${info.nodes.length} 个真实节点 · ${info.missing.length} 个未匹配目标`;
      const list=this.get('tc-path-list');list.hidden=false;list.replaceChildren();
      for(const path of info.paths) {
        const row=document.createElement('div');row.className='tc-path-row';
        const name=document.createElement('strong'),detail=document.createElement('small');
        name.textContent=String(path.id);detail.textContent=`${path.node_count} 个节点 · ${path.missing_count} 个未匹配目标\n${path.length_mm.toFixed(2)} mm · ${path.speed_mm_s.toFixed(2)} mm/s`;
        row.append(name,detail);list.append(row);
      }
      this.get('tc-display').hidden=false;this.get<HTMLButtonElement>('tc-export').disabled=!info.nodes.length;this.show();
  }

  private sampling():Sampling {
    for(const id of ['tc-spacing','tc-search','tc-angle']) if(!this.field(id).checkValidity())throw new Error('请填写有效的采样间距、搜索距离和角度。');
    const entries=this.field('tc-offsets').value.trim().split(/[,，;；\s]+/);
    const offsets=entries.map(Number);
    if(!entries[0] || offsets.some(value=>!Number.isFinite(value)))throw new Error('横向距离需要填写以逗号分隔的数字。');
    return {spacing_mm:this.field('tc-spacing').valueAsNumber,search_mm:this.field('tc-search').valueAsNumber,
      offsets_mm:[...new Set(offsets)],surface:this.field('tc-surface').value as Sampling['surface'],include_turns:this.field('tc-turns').checked,
      turn_angle_deg:this.field('tc-angle').value?this.field('tc-angle').valueAsNumber:null};
  }

  private scheduleSampling() {
    this.revision++;this.exported=false;this.temperatures=null;this.previewCurrent=false;this.show();
    this.get('tc-path-list').hidden=true;
    if(this.info)this.get('tc-source').textContent=`${this.info.source_process} · ${this.info.paths.length} 条焊路`;
    this.get<HTMLButtonElement>('tc-export').disabled=true;this.get<HTMLButtonElement>('tc-create').disabled=true;
    for(const id of ['tc-import-label','tc-export-help','tc-temperature-summary','tc-settings'])this.get(id).hidden=true;
    this.get('tc-sampling-status').textContent='参数已改变，正在等待更新预览…';
    this.services.changed();window.clearTimeout(this.samplingTimer);
    this.samplingTimer=window.setTimeout(()=>{this.samplingTimer=0;void this.resample();},400);
  }

  private async resample() {
    if(this.samplingBusyModel===this.modelRevision || !this.available)return;
    const revision=this.revision,modelRevision=this.modelRevision;this.samplingBusyModel=modelRevision;
    this.get('tc-sampling-status').textContent='正在计算真实采样节点…';
    try {
      const sampling=this.sampling();
      const info:ThermalInfo=await (await this.services.api('thermal/resample',{sampling,sampling_id:this.samplingId})).json();
      if(!this.available || modelRevision!==this.modelRevision)return;
      this.samplingId=info.sampling_id;
      if(revision===this.revision)this.renderSampling(info);
    } catch(error) {
      if(revision===this.revision)this.get('tc-sampling-status').textContent=error instanceof Error?error.message:String(error);
    } finally {
      if(this.samplingBusyModel===modelRevision)this.samplingBusyModel=null;
      if(modelRevision===this.modelRevision && revision!==this.revision && this.available && !this.samplingTimer)void this.resample();
    }
  }

  private show() {
    this.viewer.showConversion(this.active && !!this.info,this.field('tc-paths-visible').checked,this.previewCurrent && this.field('tc-nodes-visible').checked);
  }

  private updateCreate() {
    const fields=['tc-room','tc-melting','tc-maximum','tc-tolerance'].map(id=>this.field(id));
    const valid=fields.every(field=>!!field.value && field.checkValidity() && Number.isFinite(field.valueAsNumber));
    const partial=this.temperatures?.coverage.some(item=>!item.complete);
    this.get<HTMLButtonElement>('tc-create').disabled=!this.exported || !this.temperatures || !valid
      || this.field('tc-room').valueAsNumber>=this.field('tc-melting').valueAsNumber || (!!partial && !this.field('tc-allow-partial').checked);
    const maximum=this.field('tc-maximum').valueAsNumber;
    this.get('tc-segments').textContent=Number.isFinite(maximum) && maximum>0 && this.info
      ?`请求分段：${this.info.paths.map(path=>Math.ceil(path.length_mm/maximum)).join(' / ')}；实际数量由生成的 DAT 核验。`:'';
  }

  private renderCurves() {
    const holder=this.get('tc-curves');holder.replaceChildren();
    for(const item of this.temperatures!.curves) {
      const section=document.createElement('div'),label=document.createElement('p');
      label.textContent=`${item.id} · 节点 ${item.node_id} · 峰值 ${item.peak_c.toFixed(1)} °C`;
      const times=item.curve.map(point=>point[0]),values=item.curve.map(point=>point[1]);
      const xmin=Math.min(...times),xmax=Math.max(...times),ymin=Math.min(...values),ymax=Math.max(...values);
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 300 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',label.textContent);
      const line=document.createElementNS(svg.namespaceURI,'polyline');
      line.setAttribute('points',item.curve.map(([x,y])=>`${8+284*(x-xmin)/(xmax-xmin)},${92-84*(y-ymin)/Math.max(ymax-ymin,1)}`).join(' '));
      svg.append(line);section.append(label,svg);holder.append(section);
    }
  }

  private async download(path:string,filename:string,body:object={}) {
    const response=await this.services.api(path,{...body,sampling_id:this.samplingId}),url=URL.createObjectURL(await response.blob());
    const link=document.createElement('a');link.href=url;link.download=filename;link.click();
    setTimeout(()=>URL.revokeObjectURL(url),10000);
  }
}
