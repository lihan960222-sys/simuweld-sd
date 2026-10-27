import { type Viewer } from './viewer';
import { HeatSettings } from './heatSettings';
import { WeldTimeline } from './weldTimeline';

interface PathPoint { order: number; node_id: string; xyz: number[]; }
interface SourcePath { name: string; points: PathPoint[]; length_mm: number; }
interface WeldEntry { source: number; start: number; end: number; name: string; pause_after: number; }
interface Services {
  api: (path: string, body?: object | FormData)=>Promise<Response>;
  run: (text: string, action: ()=>Promise<void>)=>Promise<void>;
  message: (text: string)=>void;
  changed: ()=>void;
  unit: ()=>string;
  hasModel: ()=>boolean;
}

export class WeldingTool {
  private sources: SourcePath[]=[];
  private entries: WeldEntry[]=[];
  private editing: number | null=null;
  private active=false;
  private pickEnd: 'start' | 'end'='start';
  private panel=document.createElement('aside');
  private heat: HeatSettings;
  private timeline: WeldTimeline | null=null;
  private elapsed=0;
  private animation=0;
  private previousTime=0;

  constructor(private viewer: Viewer, private services: Services) {
    this.panel.className='right'; this.panel.dataset.tool='welding'; this.panel.hidden=true;
    this.panel.setAttribute('aria-label','生成焊路参数');
    this.panel.innerHTML=`
      <div class="tool-heading"><h1>生成焊路</h1><p class="hint">按列表顺序生成焊路与焊后停止时间。</p></div>
      <section><h2>路径点</h2>
        <label>CSV 坐标单位<select id="weld-unit"><option>mm</option><option>m</option></select></label>
        <label class="file-button">导入 CSV<input id="weld-files" type="file" accept=".csv" multiple></label>
        <p class="hint">列：PathID、Order、X、Y、Z；兼容 WeldID、PointID 或 Index 点序，NodeID 可选。</p>
        <div id="weld-source-info" class="model-info">尚未导入路径</div>
        <label>当前路径<select id="weld-source" disabled></select></label>
        <button id="weld-locate" class="full" disabled>定位路径</button>
      </section>
      <section><h2 id="weld-heading">添加焊路</h2>
        <div class="input-row"><label>起点<select id="weld-start" disabled></select></label><label>终点<select id="weld-end" disabled></select></label></div>
        <div class="row-buttons weld-range"><button id="weld-pick-start" aria-pressed="true">拾取起点</button><button id="weld-pick-end" aria-pressed="false">拾取终点</button><button id="weld-full">整条路径</button><button id="weld-reverse">反向</button></div>
        <p id="weld-range-info" class="hint">起终点之间包含全部路径点。</p>
        <label>焊路名称<input id="weld-name" value="焊路 1" maxlength="80"></label>
        <label>焊后停止时间（s）<input id="weld-pause" type="number" min="0" step="any" value="0" required></label>
        <button id="weld-add" class="primary full" disabled>添加到顺序列表</button><button id="weld-cancel" class="full" hidden>取消编辑</button>
      </section>
      <section><div class="section-title"><h2>焊接顺序</h2><span id="weld-count" class="badge">0</span></div><div id="weld-list"></div></section>
      <section><h2>焊接预览</h2><div class="row-buttons"><button id="weld-play" disabled>播放</button><button id="weld-reset" disabled>回到起点</button></div>
        <label>播放速度<select id="weld-rate"><option value="0.1">0.1×</option><option value="1" selected>1×</option><option value="5">5×</option><option value="20">20×</option></select></label>
        <label>进度<input id="weld-progress" type="range" min="0" max="1" step="any" value="0" disabled></label>
        <output id="weld-time">添加焊路后预览</output><p class="hint">按速度与停止时间播放。光源表示焊接位置，温度场需在 Simufact 中计算。</p>
      </section>
      <section><h2>Simufact 设置</h2>
        <label>Process<input id="weld-process" value="Process-1" maxlength="80"></label>
        <label>Robot<input id="weld-robot" value="Robot-1" maxlength="80"></label>
        <label>新热源名称<input id="weld-heat" value="SD-Laser-1" maxlength="80"></label>
        <label>方向向量（local-vector）</label><div class="input-row"><label>X<input id="weld-dx" type="number" step="any" value="0"></label><label>Y<input id="weld-dy" type="number" step="any" value="0"></label><label>Z<input id="weld-dz" type="number" step="any" value="1"></label></div>
        <p class="hint">方向向量表示焊枪朝向外部的一侧，圆锥沿反方向进入模型。Process 需已存在，Robot 使用空对象或新名称。脚本创建新热源。</p>
      </section>`;
    this.heat=new HeatSettings(()=>{this.invalidatePreview();this.services.changed();});
    this.panel.querySelector('.tool-heading')!.after(this.heat.section);
    document.querySelector('main')!.append(this.panel);
    this.field('weld-files').onchange=()=>{void this.services.run('正在读取路径点…',async()=>{
      const files=Array.from(this.field('weld-files').files || []);
      if(!files.length) return;
      const data=new FormData(); data.append('unit',this.field('weld-unit').value);
      files.forEach(file=>data.append('files',file,file.name));
      const result:{paths: SourcePath[]}=await (await this.services.api('weld-paths',data)).json();
      const count=this.sources.reduce((total,path)=>total+path.points.length,0)+result.paths.reduce((total,path)=>total+path.points.length,0);
      if(count>100000 || this.sources.length+result.paths.length>1000) throw new Error('当前页面最多导入 1000 条路径、10 万个点。');
      const first=this.sources.length; this.sources.push(...result.paths);
      this.sources.slice(first).forEach((path,index)=>this.select('weld-source').add(new Option(path.name,String(first+index))));
      this.select('weld-source').value=String(first); this.select('weld-source').disabled=false;
      this.element('weld-source-info').textContent=`${this.sources.length} 条路径 · ${count.toLocaleString()} 个点`;
      this.element<HTMLButtonElement>('weld-locate').disabled=false;
      this.cancelEdit(); this.chooseSource(); this.viewer.fitPaths(); this.services.changed();
      this.field('weld-files').value='';
    });};
    this.select('weld-source').onchange=()=>{this.cancelEdit();this.chooseSource();};
    for(const id of ['weld-start','weld-end']) this.select(id).onchange=()=>this.draw();
    this.element('weld-full').onclick=()=>{if(this.sources.length) this.chooseSource();};
    this.element('weld-reverse').onclick=()=>{
      if(!this.sources.length) return;
      const start=this.select('weld-start').value;
      this.select('weld-start').value=this.select('weld-end').value; this.select('weld-end').value=start; this.draw();
    };
    for(const target of ['start','end'] as const) this.element('weld-pick-'+target).onclick=()=>this.setPickEnd(target);
    this.element('weld-locate').onclick=()=>this.viewer.fitPaths();
    this.element('weld-cancel').onclick=()=>this.cancelEdit();
    this.element('weld-add').onclick=()=>{void this.services.run('正在添加焊路…',async()=>{
      const source=Number(this.select('weld-source').value),start=Number(this.select('weld-start').value),end=Number(this.select('weld-end').value);
      const name=this.field('weld-name').value.trim(), pause=this.field('weld-pause');
      if(!name || /[\u0000-\u001f\u007f]/.test(name)) throw new Error('请输入有效的焊路名称。');
      if(!pause.value || !pause.checkValidity() || !Number.isFinite(pause.valueAsNumber)) throw new Error('焊后停止时间应为大于或等于零的秒数。');
      if(start===end) throw new Error('起点和终点必须不同。');
      if(this.entries.some((entry,index)=>entry.name===name && index!==this.editing)) throw new Error('焊路名称不能重复。');
      const entry={source,start,end,name,pause_after:pause.valueAsNumber};
      if(this.editing===null) this.entries.push(entry); else this.entries[this.editing]=entry;
      this.cancelEdit(); this.renderList(); this.services.changed();
    });};
    for(const id of ['weld-dx','weld-dy','weld-dz']) this.field(id).oninput=()=>this.invalidatePreview();
    this.element('weld-play').onclick=()=>{void this.services.run('正在准备焊接预览…',async()=>{
      if(this.animation) {this.pause();return;}
      this.preparePreview();
      if(this.elapsed>=this.timeline!.duration) this.elapsed=0;
      this.previousTime=performance.now();this.element('weld-play').textContent='暂停';
      this.animation=requestAnimationFrame(time=>this.tick(time));
    });};
    this.element('weld-reset').onclick=()=>{void this.services.run('正在定位焊接起点…',async()=>{
      this.pause();this.preparePreview();this.elapsed=0;this.displayFrame();
    });};
    this.field('weld-progress').oninput=()=>{void this.services.run('正在定位焊接进度…',async()=>{
      this.pause();this.preparePreview();this.elapsed=Number(this.field('weld-progress').value)*this.timeline!.duration;this.displayFrame();
    });};
    this.renderList();
  }

  private element<T extends HTMLElement>(id: string) { return this.panel.querySelector<T>('#'+id)!; }
  private field(id: string) { return this.element<HTMLInputElement>(id); }
  private select(id: string) { return this.element<HTMLSelectElement>(id); }
  get hasPaths() { return !!this.sources.length; }
  get canExport() { return !!this.entries.length && this.editing===null && this.heat.valid; }

  activate(active: boolean) { this.active=active;this.invalidatePreview(); this.viewer.showPaths(active); if(active) this.refresh(); }
  refresh() { this.invalidatePreview();this.draw(); if(this.active && !this.services.hasModel()) this.viewer.fitPaths(); }

  private pause() {
    cancelAnimationFrame(this.animation);this.animation=0;this.element('weld-play').textContent='播放';
  }
  private invalidatePreview() {
    this.pause();this.timeline=null;this.elapsed=0;this.viewer.hideWeldPreview();
    this.field('weld-progress').value='0';this.element('weld-time').textContent='添加或修改焊路后，点击播放';
    for(const id of ['weld-play','weld-reset','weld-progress']) this.element<HTMLButtonElement>(id).disabled=!this.entries.length || !this.heat.valid || this.editing!==null;
  }
  private direction() {
    const values=['weld-dx','weld-dy','weld-dz'].map(id=>this.field(id).valueAsNumber);
    if(!values.every(Number.isFinite) || !Math.hypot(...values)) throw new Error('方向向量必须为有效数值且不能为零。');
    return values;
  }
  private preparePreview() {
    if(this.timeline) return;
    const heat=this.heat.value, direction=this.direction();
    this.timeline=new WeldTimeline(this.entries.map(entry=>({name:entry.name,points:this.coordinates(entry),pause_after:entry.pause_after})),heat.velocity);
    this.viewer.configureWeldPreview(heat,direction,this.services.unit()==='m'?0.001:1);
  }
  private displayFrame() {
    const frame=this.timeline!.at(this.elapsed);
    this.viewer.showWeldFrame(frame.point,this.services.unit()==='m'?0.001:1,frame.welding);
    this.field('weld-progress').value=String(this.elapsed/this.timeline!.duration);
    this.field('weld-progress').setAttribute('aria-valuetext',`${this.elapsed.toFixed(3)} / ${this.timeline!.duration.toFixed(3)} s`);
    this.element('weld-time').textContent=`${this.elapsed.toFixed(3)} / ${this.timeline!.duration.toFixed(3)} s · ${frame.name} · ${this.elapsed>=this.timeline!.duration?'完成':frame.welding?'焊接':'焊后停止'}`;
  }
  private tick(time: number) {
    this.elapsed=Math.min(this.timeline!.duration,this.elapsed+(time-this.previousTime)/1000*Number(this.select('weld-rate').value));
    this.previousTime=time;this.displayFrame();
    if(this.elapsed>=this.timeline!.duration) this.pause();else this.animation=requestAnimationFrame(next=>this.tick(next));
  }

  private chooseSource() {
    const source=this.sources[Number(this.select('weld-source').value)];
    for(const id of ['weld-start','weld-end']) {
      const select=this.select(id); select.replaceChildren(); select.disabled=false;
      source.points.forEach((point,index)=>select.add(new Option(String(point.order)+(point.node_id?' · '+point.node_id:''),String(index))));
    }
    this.select('weld-end').value=String(source.points.length-1);
    this.element<HTMLButtonElement>('weld-add').disabled=false;
    this.draw();
  }

  private setPickEnd(target: 'start' | 'end') {
    this.pickEnd=target;
    for(const item of ['start','end']) this.element('weld-pick-'+item).setAttribute('aria-pressed',String(item===target));
  }

  private coordinates(entry: Pick<WeldEntry,'source' | 'start' | 'end'>) {
    const points=this.sources[entry.source].points.slice(Math.min(entry.start,entry.end),Math.max(entry.start,entry.end)+1).map(point=>point.xyz);
    return entry.start>entry.end?points.reverse():points;
  }

  private draw() {
    if(!this.sources.length) return;
    const source=Number(this.select('weld-source').value),start=Number(this.select('weld-start').value),end=Number(this.select('weld-end').value);
    const coordinates=this.coordinates({source,start,end});
    const length=coordinates.slice(1).reduce((sum,point,index)=>sum+Math.hypot(...point.map((value,axis)=>value-coordinates[index][axis])),0);
    this.element('weld-range-info').textContent=`${coordinates.length} 个点 · ${length.toLocaleString('zh-CN',{maximumFractionDigits:3})} mm · 起点绿色，终点红色`;
    if(!this.active) return;
    const scale=this.services.unit()==='m'?0.001:1;
    this.viewer.setPaths(this.sources.map((path,id)=>({id,points:path.points.map(point=>point.xyz.map(value=>value*scale))})),source,[start,end],(path,index)=>{
      if(this.panel.inert) return;
      if(path!==Number(this.select('weld-source').value)) {this.cancelEdit();this.select('weld-source').value=String(path);this.chooseSource();}
      this.select('weld-'+this.pickEnd).value=String(index);
      if(this.pickEnd==='start') this.setPickEnd('end');
      this.draw(); this.services.changed();
    });
    this.viewer.showPaths(true);
  }

  private cancelEdit() {
    this.editing=null; this.element('weld-heading').textContent='添加焊路';
    this.element('weld-add').textContent='添加到顺序列表'; this.element('weld-cancel').hidden=true;
    let number=this.entries.length+1;
    while(this.entries.some(entry=>entry.name==='焊路 '+number)) number++;
    this.field('weld-name').value='焊路 '+number;
    this.invalidatePreview();this.services.changed();
  }

  private renderList() {
    this.invalidatePreview();
    const list=this.element('weld-list'); list.replaceChildren();
    this.element('weld-count').textContent=String(this.entries.length);
    this.entries.forEach((entry,index)=>{
      const card=document.createElement('div'); card.className='weld-card load-card';
      const title=document.createElement('strong'); title.textContent=`${index+1}. ${entry.name}`;
      const detail=document.createElement('small'); detail.textContent=`${Math.abs(entry.end-entry.start)+1} 个点 · 焊后停止 ${entry.pause_after} s`;
      const buttons=document.createElement('div'); buttons.className='row-buttons';
      const button=(label: string, action: ()=>void)=>{const element=document.createElement('button');element.textContent=label;element.onclick=action;buttons.append(element);return element;};
      button('编辑',()=>{
        this.editing=index; this.select('weld-source').value=String(entry.source); this.chooseSource();
        this.invalidatePreview();
        this.select('weld-start').value=String(entry.start); this.select('weld-end').value=String(entry.end);
        this.field('weld-name').value=entry.name; this.field('weld-pause').value=String(entry.pause_after);
        this.element('weld-heading').textContent='编辑焊路'; this.element('weld-add').textContent='保存修改'; this.element('weld-cancel').hidden=false;
        this.draw(); this.viewer.fitPaths(); this.services.changed();
      });
      for(const [label,delta] of [['上移',-1],['下移',1]] as const) button(label,()=>{
        [this.entries[index],this.entries[index+delta]]=[this.entries[index+delta],this.entries[index]];
        this.cancelEdit(); this.renderList(); this.services.changed();
      }).disabled=index+delta<0 || index+delta>=this.entries.length;
      button('删除',()=>{this.entries.splice(index,1);this.cancelEdit();this.renderList();this.services.changed();});
      card.append(title,detail,buttons);list.append(card);
    });
    if(!this.entries.length) {const empty=document.createElement('p');empty.className='muted';empty.textContent='添加焊路后，按此列表的顺序执行。';list.append(empty);}
  }

  async export() {
    const orientation=this.direction();
    const response=await this.services.api('weld-script',{
      process:this.field('weld-process').value.trim(),robot:this.field('weld-robot').value.trim(),heat_source:this.field('weld-heat').value.trim(),orientation,heat:this.heat.value,
      paths:this.entries.map(entry=>({name:entry.name,points:this.coordinates(entry),pause_after:entry.pause_after})),
    });
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
    link.href=url;link.download='generate_weld_paths.py';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
    this.services.message('已生成 Python 脚本，在 Simufact Welding 中执行。');
  }
}
