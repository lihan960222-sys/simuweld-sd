import { type Viewer } from './viewer';
import { HeatSettings, type HeatProfile } from './heatSettings';
import { WeldTimeline } from './weldTimeline';
import { iconButton, setIcon } from './ui';
import './welding.css';

interface PathPoint { order: number; node_id: string; xyz: number[]; }
interface SourcePath { name: string; points: PathPoint[]; length_mm: number; }
interface WeldEntry { source: number; start: number; end: number; name: string; pause_after: number; heat_source: string; }
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
  private currentSource=0;
  private sourceDigest='';
  private sourceFilename='';
  private entries: WeldEntry[]=[];
  private editing: number | null=null;
  private active=false;
  private pickEnd: 'start' | 'end'='start';
  private panel=document.createElement('aside');
  private pathPanel=document.createElement('section');
  private heat: HeatSettings;
  private heats: HeatProfile[]=[];
  private heatEditing: number | null=null;
  private heatDirty=true;
  private previewHeat: string | null=null;
  private framePathsOnActivate=false;
  private timeline: WeldTimeline | null=null;
  private elapsed=0;
  private animation=0;
  private previousTime=0;
  private looping: number | null=null;
  private dragging: number | null=null;

  constructor(private viewer: Viewer, private services: Services) {
    this.panel.className='right'; this.panel.dataset.tool='welding'; this.panel.hidden=true;
    this.panel.setAttribute('aria-label','生成焊路参数');
    this.pathPanel.dataset.tool='welding';this.pathPanel.hidden=true;
    this.pathPanel.innerHTML=`<div class="section-title"><h2>路径点</h2></div>
      <label>CSV 坐标单位<select id="weld-unit"><option>mm</option><option>m</option></select></label>
      <label class="file-button" title="列：PathID、Order、X、Y、Z；兼容 WeldID、PointID 或 Index 点序，NodeID 可选。">导入 CSV<input id="weld-files" type="file" accept=".csv"></label>
      <label class="path-visibility" hidden><input id="weld-visible" type="checkbox" aria-label="显示 CSV 路径" checked><span id="weld-source-info"></span></label>`;
    document.querySelector('aside.left')!.append(this.pathPanel);
    this.panel.innerHTML=`
      <div class="tool-heading"><h1>生成焊路</h1></div>
      <section><div class="section-title"><h2>焊路</h2><span id="weld-count" class="badge">0</span></div>
        <div id="weld-heading" class="weld-editor-heading">添加焊路</div>
        <div class="input-row"><label>起点<select id="weld-start" disabled></select></label><label>终点<select id="weld-end" disabled></select></label></div>
        <div class="row-buttons weld-range"><button id="weld-pick-start" aria-pressed="true">拾取起点</button><button id="weld-pick-end" aria-pressed="false">拾取终点</button><button id="weld-full">整条路径</button><button id="weld-reverse">反向</button></div>
        <div class="weld-legend"><span class="weld-unselected">未选</span><span class="weld-selected">已选</span><span id="weld-range-info"></span></div>
        <label>焊路名称<input id="weld-name" value="焊路 1" maxlength="80"></label>
        <label>使用热源<select id="weld-entry-heat" disabled></select></label>
        <label>焊后停止时间（s）<input id="weld-pause" type="number" min="0" step="any" value="0" required></label>
        <button id="weld-add" class="primary full" disabled>添加到顺序列表</button><button id="weld-cancel" class="full" hidden>取消编辑</button>
        <div id="weld-list"></div>
        <div class="weld-player"><div class="row-buttons weld-preview-controls"><button id="weld-all" disabled>按顺序播放全部焊路</button><button id="weld-play" disabled>播放</button><button id="weld-reset" disabled>回到起点</button></div>
        <label>播放速度 <output id="weld-rate-value">1×</output><input id="weld-rate" type="range" min="-50" max="50" step="1" value="0" aria-label="播放速度" aria-valuetext="1×"><span class="weld-rate-marks" aria-hidden="true"><span>0.1×</span><span>1×</span><span>20×</span></span></label>
        <label>进度<input id="weld-progress" type="range" min="0" max="1" step="any" value="0" disabled></label>
        <output id="weld-time" title="按速度与停止时间播放。光源表示焊接位置，温度场需在 Simufact 中计算。">添加焊路后预览</output></div>
      </section>
      <section><h2>Simufact 设置</h2>
        <label>Process<input id="weld-process" value="Process-1" maxlength="80"></label>
        <label>Robot<input id="weld-robot" value="Robot-1" maxlength="80"></label>
        <label>方向向量（local-vector）</label><div class="input-row"><label>X<input id="weld-dx" type="number" step="any" value="0"></label><label>Y<input id="weld-dy" type="number" step="any" value="0"></label><label>Z<input id="weld-dz" type="number" step="any" value="1"></label></div>
        <p class="hint">方向向量表示焊枪朝向外部的一侧，圆锥沿反方向进入模型。Process 需已存在，Robot 使用空对象或新名称。脚本创建新热源。</p>
      </section>`;
    this.heat=new HeatSettings(()=>this.heatChanged());
    const heatControls=document.createElement('div');
    heatControls.innerHTML=`<label>已创建热源<select id="weld-heat-profile" disabled></select></label>
      <label>热源名称<input id="weld-heat-name" value="SD-Laser-1" maxlength="80" required></label>
      <div class="row-buttons"><button id="weld-heat-save" class="primary">创建热源</button><button id="weld-heat-new" disabled>新增热源</button><button id="weld-heat-cancel" hidden>取消修改</button></div>
      <p id="weld-heat-status" class="hint">填写参数后创建热源，再分配给焊路。</p>`;
    this.heat.section.querySelector('h2')!.after(heatControls);
    this.panel.querySelector('.tool-heading')!.after(this.heat.section);
    document.querySelector('main')!.append(this.panel);
    for(const [id,icon,label] of [
      ['weld-pick-start','start','拾取起点'],['weld-pick-end','end','拾取终点'],['weld-full','path','整条路径'],
      ['weld-reverse','reverse','反向'],['weld-add','add','添加焊路'],['weld-cancel','close','取消编辑'],
      ['weld-all','all','按顺序播放全部焊路'],['weld-play','play','播放'],['weld-reset','reset','回到起点'],
      ['weld-heat-save','save','创建热源'],['weld-heat-new','add','新增热源'],['weld-heat-cancel','close','取消修改'],
    ] as const) setIcon(this.element(id),icon,label);
    this.panel.addEventListener('input',event=>{
      const target=event.target as HTMLElement;
      if(target.id!=='weld-rate' && target.id!=='weld-progress') this.invalidatePreview();
    });
    this.field('weld-heat-name').oninput=()=>this.heatChanged();
    this.element('weld-heat-save').onclick=()=>{void this.services.run('正在保存热源…',async()=>this.saveHeat());};
    this.element('weld-heat-new').onclick=()=>{
      this.heatEditing=null;this.heat.setValue();
      let index=this.heats.length+1;while(this.heats.some(heat=>heat.name==='SD-Laser-'+index)) index++;
      this.field('weld-heat-name').value='SD-Laser-'+index;
      setIcon(this.element('weld-heat-save'),'save','创建热源');this.heatChanged();
    };
    this.element('weld-heat-cancel').onclick=()=>this.loadHeat(this.heatEditing ?? Number(this.select('weld-heat-profile').value));
    this.select('weld-heat-profile').onchange=()=>this.loadHeat(Number(this.select('weld-heat-profile').value));
    this.field('weld-files').onchange=()=>{void this.services.run('正在读取路径点…',async()=>{
      const file=this.field('weld-files').files?.[0];
      if(!file) return;
      this.field('weld-files').value='';
      if(file.size>20*1024*1024) throw new Error('CSV 文件不能超过 20 MB。');
      const unit=this.field('weld-unit').value;
      const hash=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
      const digest=unit+':'+Array.from(new Uint8Array(hash),value=>value.toString(16).padStart(2,'0')).join('');
      if(digest===this.sourceDigest) return;
      const data=new FormData();data.append('unit',unit);data.append('files',file,file.name);
      const result:{paths: SourcePath[]}=await (await this.services.api('weld-paths',data)).json();
      const count=result.paths.reduce((total,path)=>total+path.points.length,0);
      if(count>100000 || result.paths.length>1000) throw new Error('当前页面最多导入 1000 条路径、10 万个点。');
      if(this.entries.length && !confirm('更换 CSV 将清空当前焊路列表，是否继续？')) return;
      this.sources=result.paths;this.currentSource=0;this.sourceDigest=digest;this.sourceFilename=file.name;this.entries=[];
      this.field('weld-visible').checked=true;this.field('weld-visible').parentElement!.hidden=false;
      this.element('weld-source-info').textContent=`${file.name}\n${this.sources.length} 条路径 · ${count.toLocaleString()} 个点`;
      this.setPickEnd('start');this.renderList();
      this.cancelEdit(); this.chooseSource();
      if(!this.services.hasModel()) this.viewer.fitPaths();
      this.services.changed();
    });};
    this.field('weld-visible').onchange=()=>{if(!this.field('weld-visible').checked) this.invalidatePreview();this.viewer.showPaths(this.active && this.field('weld-visible').checked);};
    this.field('weld-rate').oninput=()=>{const value=this.playbackRate()+'×';this.element('weld-rate-value').textContent=value;this.field('weld-rate').setAttribute('aria-valuetext',value);};
    for(const id of ['weld-start','weld-end']) this.select(id).onchange=()=>this.draw();
    this.element('weld-full').onclick=()=>{if(this.sources.length) this.chooseSource();};
    this.element('weld-reverse').onclick=()=>{
      if(!this.sources.length) return;
      const start=this.select('weld-start').value;
      this.select('weld-start').value=this.select('weld-end').value; this.select('weld-end').value=start; this.draw();
    };
    for(const target of ['start','end'] as const) this.element('weld-pick-'+target).onclick=()=>{this.invalidatePreview();this.setPickEnd(target);};
    this.element('weld-cancel').onclick=()=>this.cancelEdit();
    this.element('weld-add').onclick=()=>{void this.services.run('正在添加焊路…',async()=>{
      const source=this.currentSource,start=Number(this.select('weld-start').value),end=Number(this.select('weld-end').value);
      const name=this.field('weld-name').value.trim(), pause=this.field('weld-pause');
      if(!name || /[\u0000-\u001f\u007f]/.test(name)) throw new Error('请输入有效的焊路名称。');
      if(!pause.value || !pause.checkValidity() || !Number.isFinite(pause.valueAsNumber)) throw new Error('焊后停止时间应为大于或等于零的秒数。');
      if(start===end) throw new Error('起点和终点必须不同。');
      if(this.entries.some((entry,index)=>entry.name===name && index!==this.editing)) throw new Error('焊路名称不能重复。');
      const heat_source=this.select('weld-entry-heat').value;
      if(!this.heats.some(heat=>heat.name===heat_source)) throw new Error('请创建并选择焊路使用的热源。');
      const entry={source,start,end,name,pause_after:pause.valueAsNumber,heat_source};
      if(this.editing===null) this.entries.push(entry); else this.entries[this.editing]=entry;
      this.cancelEdit(); this.renderList(); this.services.changed();
    });};
    for(const id of ['weld-dx','weld-dy','weld-dz']) this.field(id).oninput=()=>this.invalidatePreview();
    this.element('weld-play').onclick=()=>{void this.services.run('正在准备焊接预览…',async()=>{
      if(this.animation) {this.pause();return;}
      this.preparePreview();
      if(this.elapsed>=this.timeline!.duration) this.elapsed=0;
      this.startPlayback();
    });};
    this.element('weld-all').onclick=()=>{void this.services.run('正在准备焊接预览…',async()=>{
      this.invalidatePreview();this.preparePreview();this.startPlayback();
    });};
    this.element('weld-reset').onclick=()=>{void this.services.run('正在定位焊接起点…',async()=>{
      this.pause();this.preparePreview();this.elapsed=0;this.displayFrame();
    });};
    this.field('weld-progress').oninput=()=>{void this.services.run('正在定位焊接进度…',async()=>{
      this.pause();this.preparePreview();this.elapsed=Number(this.field('weld-progress').value)*this.timeline!.duration;this.displayFrame();
    });};
    this.renderList();
  }

  private element<T extends HTMLElement>(id: string) { return (this.panel.querySelector<T>('#'+id) || this.pathPanel.querySelector<T>('#'+id))!; }
  private field(id: string) { return this.element<HTMLInputElement>(id); }
  private select(id: string) { return this.element<HTMLSelectElement>(id); }
  private playbackRate() {
    const position=Number(this.field('weld-rate').value);
    return Number((position<0?10**(position/50):20**(position/50)).toFixed(2));
  }
  get hasPaths() { return !!this.sources.length; }
  get canExport() { return !!this.entries.length && this.editing===null && !this.heatDirty; }

  activate(active: boolean) {
    this.active=active;this.invalidatePreview();this.viewer.showPaths(active && this.field('weld-visible').checked);
    if(active) {this.draw();if(this.framePathsOnActivate) {this.viewer.fitPaths();this.framePathsOnActivate=false;this.viewer.showPaths(this.field('weld-visible').checked);}}
  }
  refresh() {
    this.invalidatePreview();this.draw();
    this.framePathsOnActivate=!this.services.hasModel() && !!this.sources.length;
    if(this.active && this.framePathsOnActivate) {this.viewer.fitPaths();this.framePathsOnActivate=false;this.viewer.showPaths(this.field('weld-visible').checked);}
  }

  private heatChanged() {
    this.heatDirty=true;
    this.select('weld-heat-profile').disabled=true;this.element<HTMLButtonElement>('weld-heat-new').disabled=true;
    this.element('weld-heat-cancel').hidden=!this.heats.length;
    this.element('weld-heat-status').textContent='热源参数尚未保存。';
    this.invalidatePreview();this.services.changed();
  }
  private loadHeat(index: number) {
    const profile=this.heats[index];this.heatEditing=index;this.heatDirty=false;
    this.heat.setValue(profile.parameters);this.field('weld-heat-name').value=profile.name;
    this.select('weld-heat-profile').value=String(index);this.select('weld-heat-profile').disabled=false;
    this.element<HTMLButtonElement>('weld-heat-new').disabled=false;
    this.element('weld-heat-cancel').hidden=true;setIcon(this.element('weld-heat-save'),'save','保存热源修改');
    this.element('weld-heat-status').textContent=`已保存 ${this.heats.length} 组热源。`;
    this.invalidatePreview();this.services.changed();
  }
  private saveHeat() {
    const name=this.field('weld-heat-name').value.trim(),parameters=this.heat.value;
    if(!name || /[\u0000-\u001f\u007f]/.test(name)) throw new Error('请输入有效的热源名称。');
    if(this.heats.some((profile,index)=>profile.name===name && index!==this.heatEditing)) throw new Error('热源名称不能重复。');
    let selected=this.select('weld-entry-heat').value;
    if(this.heatEditing===null) {
      if(this.heats.length>=1000) throw new Error('最多创建 1000 组热源。');
      this.heats.push({name,parameters});this.heatEditing=this.heats.length-1;selected=name;
    }
    else {
      const previous=this.heats[this.heatEditing].name;
      this.entries.forEach(entry=>{if(entry.heat_source===previous) entry.heat_source=name;});
      this.heats[this.heatEditing]={name,parameters};
      if(selected===previous) selected=name;
    }
    const choices=this.select('weld-heat-profile');choices.replaceChildren();
    this.heats.forEach((profile,index)=>choices.add(new Option(`${profile.name} · ${profile.parameters.power_w} W · ${profile.parameters.velocity} mm/s`,String(index))));
    this.renderHeatChoices(selected);this.loadHeat(this.heatEditing);this.renderList();
  }
  private renderHeatChoices(selected: string) {
    const select=this.select('weld-entry-heat');select.replaceChildren();select.disabled=!this.heats.length;
    this.heats.forEach(profile=>select.add(new Option(profile.name,profile.name)));select.value=selected;
  }

  private pause() {
    cancelAnimationFrame(this.animation);this.animation=0;setIcon(this.element('weld-play'),'play','播放');
  }
  private startPlayback() {
    this.field('weld-visible').checked=true;this.viewer.showPaths(this.active);
    this.previousTime=performance.now();setIcon(this.element('weld-play'),'pause','暂停');this.displayFrame();
    this.animation=requestAnimationFrame(time=>this.tick(time));
  }
  private invalidatePreview() {
    this.pause();this.looping=null;this.timeline=null;this.previewHeat=null;this.elapsed=0;this.viewer.hideWeldPreview();this.viewer.clearWeldTrails();
    this.panel.querySelectorAll<HTMLButtonElement>('.weld-entry-name').forEach(button=>{button.setAttribute('aria-pressed','false');button.disabled=!this.canExport;});
    this.viewer.showPathSelection(true);
    this.field('weld-progress').value='0';this.element('weld-time').textContent='添加或修改焊路后，点击播放';
    for(const id of ['weld-all','weld-play','weld-reset','weld-progress']) this.element<HTMLButtonElement>(id).disabled=!this.canExport;
  }
  private direction() {
    const values=['weld-dx','weld-dy','weld-dz'].map(id=>this.field(id).valueAsNumber);
    if(!values.every(Number.isFinite) || !Math.hypot(...values)) throw new Error('方向向量必须为有效数值且不能为零。');
    return values;
  }
  private preparePreview() {
    if(this.timeline) return;
    const direction=this.direction();
    const entries=this.looping===null?this.entries:[this.entries[this.looping]];
    const paths=entries.map(entry=>{
      const heat=this.heats.find(profile=>profile.name===entry.heat_source)!.parameters;
      return {name:entry.name,points:this.coordinates(entry),pause_after:entry.pause_after,heat_source:entry.heat_source,
        velocity:heat.velocity,radius:Math.max(heat.conical_upper_radius,heat.disc_radius),direction};
    });
    const timeline=new WeldTimeline(paths);
    this.viewer.setWeldTrails(paths,this.services.unit()==='m'?0.001:1);this.timeline=timeline;
  }
  private displayFrame() {
    const frame=this.timeline!.at(this.elapsed);
    this.viewer.showPathSelection(false);
    if(this.previewHeat!==frame.heat_source) {
      const heat=this.heats.find(profile=>profile.name===frame.heat_source)!;
      this.viewer.configureWeldPreview(heat.parameters,this.direction(),this.services.unit()==='m'?0.001:1);this.previewHeat=frame.heat_source;
    }
    this.viewer.showWeldFrame(frame.point,this.services.unit()==='m'?0.001:1,frame.welding);
    this.viewer.showWeldProgress(frame.path_index,frame.segment_index,frame.point);
    this.field('weld-progress').value=String(this.elapsed/this.timeline!.duration);
    this.field('weld-progress').setAttribute('aria-valuetext',`${this.elapsed.toFixed(3)} / ${this.timeline!.duration.toFixed(3)} s`);
    this.element('weld-time').textContent=`${this.elapsed.toFixed(3)} / ${this.timeline!.duration.toFixed(3)} s · ${frame.name} · ${frame.heat_source} · ${this.elapsed>=this.timeline!.duration?'完成':frame.welding?'焊接':'焊后停止'}`;
  }
  private tick(time: number) {
    const elapsed=this.elapsed+(time-this.previousTime)/1000*this.playbackRate();
    this.elapsed=this.looping===null?Math.min(this.timeline!.duration,elapsed):elapsed%this.timeline!.duration;
    this.previousTime=time;this.displayFrame();
    if(this.elapsed>=this.timeline!.duration) this.pause();else this.animation=requestAnimationFrame(next=>this.tick(next));
  }

  private chooseSource() {
    const source=this.sources[this.currentSource];
    for(const id of ['weld-start','weld-end']) {
      const select=this.select(id); select.replaceChildren(); select.disabled=false;
      source.points.forEach((point,index)=>select.add(new Option(String(point.order)+(point.node_id?' · '+point.node_id:''),String(index))));
    }
    this.select('weld-end').value=String(source.points.length-1);
    const count=this.sources.reduce((total,path)=>total+path.points.length,0);
    this.element('weld-source-info').textContent=`${this.sourceFilename}\n${this.sources.length} 条路径 · ${count.toLocaleString()} 个点`+(this.sources.length>1?'\n'+source.name:'');
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
    const source=this.currentSource,start=Number(this.select('weld-start').value),end=Number(this.select('weld-end').value);
    const coordinates=this.coordinates({source,start,end});
    const length=coordinates.slice(1).reduce((sum,point,index)=>sum+Math.hypot(...point.map((value,axis)=>value-coordinates[index][axis])),0);
    this.element('weld-range-info').textContent=`${coordinates.length} 个点 · ${length.toLocaleString('zh-CN',{maximumFractionDigits:3})} mm`;
    if(!this.active) return;
    this.invalidatePreview();
    const scale=this.services.unit()==='m'?0.001:1;
    this.viewer.setPaths(this.sources.map((path,id)=>({id,points:path.points.map(point=>point.xyz.map(value=>value*scale))})),source,[start,end],(path,index)=>{
      if(this.panel.inert) return;
      if(path!==this.currentSource) {this.cancelEdit();this.currentSource=path;this.chooseSource();}
      this.select('weld-'+this.pickEnd).value=String(index);
      if(this.pickEnd==='start') this.setPickEnd('end');
      this.draw(); this.services.changed();
    });
    this.viewer.showPaths(this.field('weld-visible').checked);
  }

  private cancelEdit() {
    this.editing=null; this.element('weld-heading').textContent='添加焊路';
    setIcon(this.element('weld-add'),'add','添加焊路'); this.element('weld-cancel').hidden=true;this.setPickEnd('start');
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
      const title=document.createElement('button');title.className='weld-entry-name';title.textContent=`${index+1}. ${entry.name}`;
      title.title='循环播放 '+entry.name;title.setAttribute('aria-pressed','false');title.disabled=!this.canExport;
      title.onclick=()=>{void this.services.run('正在准备焊接预览…',async()=>{
        this.invalidatePreview();this.looping=index;this.preparePreview();title.setAttribute('aria-pressed','true');this.startPlayback();
      });};
      const grip=iconButton('grip','拖动调整 '+entry.name+' 的顺序；方向键调整顺序');grip.classList.add('weld-grip');grip.draggable=true;
      grip.ondragstart=event=>{this.dragging=index;event.dataTransfer!.effectAllowed='move';event.dataTransfer!.setData('text/plain',String(index));card.classList.add('dragging');};
      grip.ondragend=()=>{this.dragging=null;card.classList.remove('dragging');list.querySelectorAll('.drag-over').forEach(item=>item.classList.remove('drag-over'));};
      card.ondragover=event=>{if(this.dragging!==null) {event.preventDefault();event.dataTransfer!.dropEffect='move';card.classList.add('drag-over');}};
      card.ondragleave=()=>card.classList.remove('drag-over');
      card.ondrop=event=>{event.preventDefault();if(this.dragging!==null) this.moveEntry(this.dragging,index);this.dragging=null;};
      grip.onkeydown=event=>{
        if(event.key!=='ArrowUp' && event.key!=='ArrowDown') return;
        event.preventDefault();this.moveEntry(index,index+(event.key==='ArrowUp'?-1:1));
      };
      const detail=document.createElement('small'); detail.textContent=`${Math.abs(entry.end-entry.start)+1} 个点 · 焊后停止 ${entry.pause_after} s`;
      const heatLabel=document.createElement('label');heatLabel.className='weld-heat-choice';heatLabel.title='使用热源';
      const heatSelect=document.createElement('select');heatSelect.setAttribute('aria-label',entry.name+' 热源');
      this.heats.forEach(profile=>heatSelect.add(new Option(profile.name,profile.name)));heatSelect.value=entry.heat_source;
      heatSelect.onchange=()=>{
        entry.heat_source=heatSelect.value;
        if(this.editing===index) this.select('weld-entry-heat').value=entry.heat_source;
        this.invalidatePreview();this.services.changed();
      };heatLabel.append(heatSelect);
      const buttons=document.createElement('div'); buttons.className='row-buttons';
      const edit=iconButton('edit','编辑 '+entry.name);edit.onclick=()=>{
        this.editing=index; this.currentSource=entry.source; this.chooseSource();
        this.invalidatePreview();
        this.select('weld-start').value=String(entry.start); this.select('weld-end').value=String(entry.end);
        this.field('weld-name').value=entry.name; this.field('weld-pause').value=String(entry.pause_after);
        this.select('weld-entry-heat').value=entry.heat_source;
        this.element('weld-heading').textContent='编辑焊路';setIcon(this.element('weld-add'),'save','保存焊路修改');this.element('weld-cancel').hidden=false;this.setPickEnd('start');
        this.draw(); this.services.changed();
      };
      const remove=iconButton('delete','删除 '+entry.name);remove.onclick=()=>{this.entries.splice(index,1);this.cancelEdit();this.renderList();this.services.changed();};
      buttons.append(edit,remove);
      const heading=document.createElement('div');heading.className='weld-entry-heading';heading.append(grip,title,buttons);
      const metadata=document.createElement('div');metadata.className='weld-entry-meta';metadata.append(detail,heatLabel);
      card.append(heading,metadata);list.append(card);
    });
    if(!this.entries.length) {const empty=document.createElement('p');empty.className='muted';empty.textContent='添加焊路后，按此列表的顺序执行。';list.append(empty);}
  }

  private moveEntry(from: number,to: number) {
    if(from===to || to<0 || to>=this.entries.length) return;
    const [entry]=this.entries.splice(from,1);this.entries.splice(to,0,entry);
    this.cancelEdit();this.renderList();this.services.changed();
    this.element('weld-list').querySelectorAll<HTMLButtonElement>('.weld-grip')[to].focus();
  }

  async export() {
    const orientation=this.direction();
    const response=await this.services.api('weld-script',{
      process:this.field('weld-process').value.trim(),robot:this.field('weld-robot').value.trim(),orientation,heats:this.heats,
      paths:this.entries.map(entry=>({name:entry.name,points:this.coordinates(entry),pause_after:entry.pause_after,heat_source:entry.heat_source})),
    });
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
    link.href=url;link.download='generate_weld_paths.py';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
    this.services.message('已生成 Python 脚本，在 Simufact Welding 中执行。');
  }
}
