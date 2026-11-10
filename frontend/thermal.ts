import type { Viewer } from './viewer';
import { setIcon } from './ui';
import './thermal.css';

interface Division { mode:'length'|'count'; value:number; }
interface ThermalSegment {
  path_id:number; index:number; label:string; start_mm:number; end_mm:number; length_mm:number;
  points_mm:number[][]; start_s:number; end_s:number; node_count:number; radius_mm:number;
}
interface ThermalPath {
  id:number; label:string; points_mm:number[][]; length_mm:number; speed_mm_s:number;
  division:Division; segments:ThermalSegment[]; node_count:number; missing_count:number; missing_length_mm:number;
}
interface Sampling { search_mm:number; divisions:Record<string,Division>; }
interface ThermalInfo {
  source_process: string; source_sha256: string; room_c: number | null; end_s: number;
  paths:ThermalPath[]; nodes:{node_id:number;component:string;xyz_mm:number[];paths:number[];segments:{path_id:number;index:number;label:string}[]}[];
  sampling:Sampling; sampling_id:string; missing:{xyz_mm:number[];component:string;path_id:number;label:string;length_mm:number}[];
  exported:boolean; temperatures?:TemperatureInfo|null; segment_count:number; can_export:boolean; can_create:boolean;
}
interface TemperatureInfo {
  rows: number; increments: number; nodes: number; end_s: number; initial_c: number; digest_verified: boolean;
  can_create:boolean;
  coverage:{path_id:number;index:number;label:string;complete:boolean;weld_complete:boolean;reason:string}[];
  curves:{path_id:number;index:number;label:string;node_count:number;peak_c:number;error_c:number;curve:number[][]}[];
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
  private materialLoaded=false;
  private temperatureFilename='';
  private revision=0;
  private samplingBusyModel:number|null=null;
  private samplingTimer=0;
  private samplingId='';
  private modelRevision=0;
  private previewCurrent=false;

  constructor(private viewer: Viewer, private services: Services) {
    this.panel.className='right thermal-panel';this.panel.dataset.tool='thermal-cycle';this.panel.hidden=true;
    this.panel.innerHTML=`<div class="tool-heading"><h1>转换至热循环模式</h1></div>
      <section id="tc-parameters"><h2>设置参数</h2>
        <label id="tc-material-label" class="file-button" aria-disabled="true">导入材料 XMT<input id="tc-material" type="file" accept=".xmt,.umt,.xml" disabled></label>
        <p id="tc-material-source" class="hint">上传 DAT 后导入材料文件。</p>
        <p id="tc-source" class="hint">上传瞬态计算 DAT 后自动读取。</p>
        <div id="tc-sampling">
          <label>搜索半径（mm）<input id="tc-search" type="number" min="0.001" step="any" value="0.25" required disabled></label>
          <div id="tc-path-list" class="tc-path-list"></div>
        </div>
        <p id="tc-sampling-status" class="hint" role="status" aria-live="polite"></p>
        <details id="tc-display" class="tc-display" hidden><summary>预览显示</summary><div>
          <label class="check"><input id="tc-paths-visible" type="checkbox" checked><span class="tc-path-key">显示焊路</span></label>
          <label class="check"><input id="tc-nodes-visible" type="checkbox" checked><span class="tc-node-key">显示采样节点</span></label>
          <label class="tc-size">节点大小<input id="tc-node-size" type="range" min="2" max="16" value="6" aria-label="节点显示大小"></label>
          <span class="tc-missing-key">红色：搜索范围内没有节点</span>
        </div></details>
      </section>
      <section><h2>导出温度脚本</h2>
        <button id="tc-export" class="primary full" disabled>生成温度导出脚本</button>
        <p id="tc-export-help" class="hint">导入材料并完成参数设置后生成脚本。</p>
      </section>
      <section><h2>导入温度 CSV</h2>
        <label id="tc-import-label" class="file-button" aria-disabled="true">导入温度数据<input id="tc-temperatures" type="file" accept=".csv" disabled></label>
        <p id="tc-temperature-summary" class="hint" role="status">尚未导入温度数据。</p>
        <details id="tc-coverage" class="tc-coverage" hidden><summary id="tc-coverage-title"></summary><p id="tc-coverage-note" class="hint"></p></details>
        <div id="tc-curves"></div>
      </section>
      <section id="tc-settings"><h2>生成 TC 脚本</h2>
        <details class="tc-options"><summary>温度基准与曲线精度</summary>
          <div class="input-row"><label>初始温度（°C）<input id="tc-room" type="number" min="-273.14" step="any" required></label><label>熔化温度（°C）<input id="tc-melting" type="number" step="any" required readonly></label></div>
          <label>曲线误差（°C）<input id="tc-tolerance" type="number" min="0.001" max="100" value="5" step="any" required></label>
        </details>
        <p id="tc-segments" class="hint"></p>
        <button id="tc-create" class="primary full" disabled>生成热循环创建脚本</button>
        <p class="hint">每个分段使用平均温度曲线，创建独立轨迹和热源。在原项目中运行脚本，创建 _TC 过程并生成求解输入。</p>
      </section>`;
    document.querySelector('main')!.append(this.panel);
    this.get('tc-export').onclick=()=>{void this.services.run('正在生成温度导出脚本…',async()=>{
      await this.download('thermal/export','export_node_temperatures.py');
      this.exported=true;this.updateWorkflow();
      this.services.changed();
    });};
    this.field('tc-temperatures').onchange=()=>{void this.services.run('正在核对温度数据…',async()=>{
      const file=this.field('tc-temperatures').files?.[0];if(!file) return;
      this.field('tc-temperatures').value='';this.temperatures=null;this.temperatureFilename='';
      this.renderTemperatures();this.updateWorkflow();
      const data=new FormData();data.append('file',file);data.append('sampling_id',this.samplingId);
      this.temperatures=await (await this.services.api('thermal/temperatures',data)).json();
      this.temperatureFilename=file.name;
      if(!this.field('tc-room').value)this.field('tc-room').value=String(Number(this.temperatures!.initial_c.toFixed(4)));
      this.info!.can_create=this.temperatures!.can_create;
      this.renderTemperatures();this.updateWorkflow();this.services.changed();
    });};
    this.field('tc-material').onchange=()=>{void this.services.run('正在读取材料温度…',async()=>{
      const file=this.field('tc-material').files?.[0];if(!file) return;
      this.field('tc-material').value='';this.materialLoaded=false;this.field('tc-melting').value='';
      this.get('tc-material-source').textContent='尚未读取有效材料文件。';this.updateWorkflow();
      const data=new FormData();data.append('file',file);
      const result=await (await this.services.api('thermal/material',data)).json();
      this.field('tc-melting').value=String(result.melting_c);
      this.materialLoaded=true;
      this.get('tc-material-source').textContent=`${result.material} · ${file.name}\n熔化温度 ${result.melting_c.toFixed(2)} °C`;
      this.updateWorkflow();this.services.changed();
    });};
    this.get('tc-create').onclick=()=>{void this.services.run('正在生成热循环创建脚本…',async()=>{
      await this.download('thermal/create','create_thermal_cycle.py',{
        room_c:this.field('tc-room').valueAsNumber,melting_c:this.field('tc-melting').valueAsNumber,
        tolerance_c:this.field('tc-tolerance').valueAsNumber,
      });this.services.changed();
    });};
    for(const id of ['tc-room','tc-tolerance']) this.field(id).oninput=()=>this.updateWorkflow();
    for(const id of ['tc-paths-visible','tc-nodes-visible']) this.field(id).onchange=()=>this.show();
    this.field('tc-node-size').oninput=()=>this.viewer.setConversionNodeSize(this.field('tc-node-size').valueAsNumber);
    this.field('tc-search').oninput=()=>this.scheduleSampling();
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
    this.revision++;window.clearTimeout(this.samplingTimer);this.samplingTimer=0;this.samplingId='';
    this.available=available;this.info=null;this.temperatures=null;this.exported=false;this.materialLoaded=false;this.temperatureFilename='';
    this.viewer.setConversion([],[]);this.viewer.showConversion(false);
    this.get('tc-source').textContent=available?'进入模块后自动读取焊路。':'上传瞬态计算 DAT 后自动读取。';
    this.get('tc-path-list').replaceChildren();this.get('tc-display').hidden=true;
    this.get('tc-sampling-status').textContent='';
    this.field('tc-room').value='';this.field('tc-melting').value='';this.field('tc-search').value='0.25';
    this.get('tc-material-source').textContent='上传 DAT 后导入材料文件。';
    this.field('tc-paths-visible').checked=true;this.field('tc-nodes-visible').checked=true;
    this.renderTemperatures();this.updateWorkflow();
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
      this.field('tc-search').value=String(info.sampling.search_mm);
      if(info.room_c!==null)this.field('tc-room').value=String(info.room_c);
      this.get('tc-material-source').textContent='导入材料文件，读取熔化温度。';
      this.renderSampling(info);
  }

  private renderSampling(info:ThermalInfo) {
      this.info=info;this.previewCurrent=true;this.exported=info.exported;
      this.temperatures=info.temperatures ?? null;if(!this.temperatures)this.temperatureFilename='';
      this.viewer.setConversion(info.paths.map(path=>path.points_mm),info.nodes.map(node=>node.xyz_mm),info.nodes,info.missing);
      this.viewer.setConversionNodeSize(this.field('tc-node-size').valueAsNumber);
      this.get('tc-source').textContent=`${info.source_process} · ${info.paths.length} 条焊路 · ${info.nodes.length} 个真实节点`;
      const missing=info.paths.reduce((total,path)=>total+path.missing_length_mm,0);
      this.get('tc-sampling-status').textContent=`${info.nodes.length.toLocaleString()} 个真实节点 · ${info.segment_count} 个分段${missing>0?`\n${missing.toFixed(3)} mm 焊路在搜索范围内没有节点`:''}`;
      const list=this.get('tc-path-list');
      for(const path of info.paths) {
        let row=list.querySelector<HTMLElement>(`[data-path-id="${path.id}"]`);
        if(!row) {
          row=document.createElement('div');row.className='tc-path-row';row.dataset.pathId=String(path.id);
          row.innerHTML='<div class="tc-path-heading"><strong class="tc-path-name"></strong><small class="tc-path-summary"></small></div><div class="input-row"><label>分段方式<select><option value="length">最大长度（mm）</option><option value="count">分段数量</option></select></label><label><span class="tc-division-label"></span><input type="number" required></label></div><p class="tc-path-warning hint" hidden></p>';
          const mode=row.querySelector('select')!,value=row.querySelector('input')!;
          mode.setAttribute('aria-label',path.label+'分段方式');
          mode.onchange=()=>{
            value.value=mode.value==='count'?String(this.info!.paths.find(item=>item.id===path.id)!.segments.length):'2';
            this.setDivisionInput(row!);this.scheduleSampling();
          };
          value.oninput=()=>this.scheduleSampling();list.append(row);
        }
        row.querySelector('.tc-path-name')!.textContent=path.label;
        row.querySelector('.tc-path-summary')!.textContent=`${path.length_mm.toFixed(2)} mm · ${path.segments.length} 段 · ${path.node_count} 个节点`;
        row.querySelector('select')!.value=path.division.mode;row.querySelector('input')!.value=String(path.division.value);
        row.querySelector('input')!.setAttribute('aria-label',path.label+'分段参数');this.setDivisionInput(row);
        const empty=path.segments.filter(segment=>!segment.node_count),warning=row.querySelector<HTMLElement>('.tc-path-warning')!;
        warning.hidden=!empty.length;warning.textContent=empty.map(segment=>segment.label).join('、')+'没有匹配节点。';
      }
      this.get('tc-display').hidden=false;this.renderTemperatures();this.updateWorkflow();this.show();
  }

  private setDivisionInput(row:HTMLElement) {
    const count=row.querySelector('select')!.value==='count',input=row.querySelector('input')!;
    input.min=count?'1':'0.001';input.step=count?'1':'any';
    row.querySelector('.tc-division-label')!.textContent=count?'段数':'长度（mm）';
  }

  private sampling():Sampling {
    if(!this.field('tc-search').checkValidity())throw new Error('请填写大于零的搜索半径。');
    const divisions:Sampling['divisions']={};
    for(const row of this.get('tc-path-list').querySelectorAll<HTMLElement>('.tc-path-row')) {
      const mode=row.querySelector('select')!.value as Division['mode'],input=row.querySelector('input')!;
      if(!input.checkValidity())throw new Error(`${row.querySelector('.tc-path-name')!.textContent}：${mode==='count'?'段数需要为正整数。':'分段长度需要大于零。'}`);
      divisions[row.dataset.pathId!]={mode,value:input.valueAsNumber};
    }
    return {search_mm:this.field('tc-search').valueAsNumber,divisions};
  }

  private scheduleSampling() {
    this.revision++;this.previewCurrent=false;this.show();this.updateWorkflow();
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

  private updateWorkflow() {
    const current=this.previewCurrent && !!this.info,material=this.materialLoaded && Number.isFinite(this.field('tc-melting').valueAsNumber);
    this.field('tc-material').disabled=!current;this.get('tc-material-label').setAttribute('aria-disabled',String(!current));
    this.field('tc-search').disabled=!this.info;
    this.get<HTMLButtonElement>('tc-export').disabled=!current || !material || !this.info?.can_export;
    this.field('tc-temperatures').disabled=!current || !this.exported || !material;
    this.get('tc-import-label').setAttribute('aria-disabled',String(this.field('tc-temperatures').disabled));
    this.get('tc-export-help').textContent=this.exported
      ?'脚本已生成。在原 Simufact 项目中运行，随后导入温度 CSV。'
      :!material?'导入材料并完成参数设置后生成脚本。':'导出全部焊路共用的真实节点温度，每个节点仅导出一次。';
    const fields=['tc-room','tc-melting','tc-tolerance'].map(id=>this.field(id));
    const valid=fields.every(field=>!!field.value && field.checkValidity() && Number.isFinite(field.valueAsNumber));
    this.get<HTMLButtonElement>('tc-create').disabled=!current || !material || !this.exported || !this.temperatures || !valid
      || !this.info?.can_create || this.field('tc-room').valueAsNumber>=this.field('tc-melting').valueAsNumber;
    this.get('tc-segments').textContent=this.info?`${this.info.segment_count} 个分段将生成 ${this.info.segment_count} 条独立轨迹。`:'';
  }

  private renderTemperatures() {
    const holder=this.get('tc-curves');holder.replaceChildren();
    const result=this.temperatures;
    this.get('tc-temperature-summary').textContent=result
      ?`${this.temperatureFilename}\n${result.increments.toLocaleString()} 个时刻 · ${result.nodes.toLocaleString()} 个节点 · 记录至 ${result.end_s.toFixed(6)} s`
      :'尚未导入温度数据。';
    const missing=result?.coverage.filter(item=>!item.complete) ?? [];
    this.get('tc-coverage').hidden=!missing.length;
    this.get('tc-coverage-title').textContent=`${missing.length} 个分段温度记录不完整`;
    this.get('tc-coverage-note').textContent=missing.map(item=>`${item.label}：${item.reason}`).join('\n');
    if(!result)return;
    for(const path of this.info!.paths) {
      const curves=result.curves.filter(item=>item.path_id===path.id);
      const group=document.createElement('details'),heading=document.createElement('summary');group.className='tc-curve-group';
      heading.textContent=`${path.label} · ${curves.length} 条平均温度曲线`;group.append(heading);
      for(const item of curves) {
        const section=document.createElement('details'),label=document.createElement('summary');section.className='tc-curve';
        label.textContent=`${item.label} · ${item.node_count} 个节点 · ${item.peak_c.toFixed(1)} °C`;section.append(label);
        section.ontoggle=()=>{
          if(!section.open || section.querySelector('svg'))return;
          const times=item.curve.map(point=>point[0]),values=item.curve.map(point=>point[1]);
          const xmin=Math.min(...times),xmax=Math.max(...times),ymin=Math.min(...values),ymax=Math.max(...values);
          const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 300 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',label.textContent!);
          const line=document.createElementNS(svg.namespaceURI,'polyline');
          line.setAttribute('points',item.curve.map(([x,y])=>`${8+284*(x-xmin)/Math.max(xmax-xmin,1e-9)},${92-84*(y-ymin)/Math.max(ymax-ymin,1)}`).join(' '));
          const range=document.createElement('p');range.textContent=`相对时间 ${xmin.toFixed(3)} 至 ${xmax.toFixed(3)} s · 温度 ${ymin.toFixed(1)} 至 ${ymax.toFixed(1)} °C`;
          svg.append(line);section.append(svg,range);
        };
        group.append(section);
      }
      holder.append(group);
    }
  }

  private async download(path:string,filename:string,body:object={}) {
    const response=await this.services.api(path,{...body,sampling_id:this.samplingId}),url=URL.createObjectURL(await response.blob());
    const link=document.createElement('a');link.href=url;link.download=filename;link.click();
    setTimeout(()=>URL.revokeObjectURL(url),10000);
  }
}
