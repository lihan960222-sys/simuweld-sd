import type {Viewer, TrialOverlay} from './viewer';
import {iconButton} from './ui';
import './localTrial.css';

interface Path {id:number;label:string;points_mm:number[][];length_mm:number;}
interface Meta {model_id:string;source_process:string;paths:Path[];components:{name:string;keep_whole:boolean}[];nodes:{id:number;xyz_mm:number[];component:string}[];bounds_mm:number[][];}
interface Spec {model_id:string;path_id:number;start_mm:number;end_mm:number;wrap:boolean;tolerance_mm:number;box:{min:number[];max:number[]};keep_whole:string[];trim_start_mm:number;trim_end_mm:number;included_path_ids?:number[];}
interface Group extends Spec {name:string;}
interface Preview {matches:{path_id:number;label:string;points_mm:number[][];matched:boolean;reason:string;endpoint_adjustment_mm:number[]}[];crop:{node_ids:number[];points_mm:number[][];node_count:number;element_count:number;cut_faces:{node_ids:number[]}[];faces?:{node_ids:number[]}[]};selection:{points_mm:number[][];length_mm:number;extraction_points_mm:number[][]};warnings:string[];}
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
  private pickTarget='start';
  private cropNodes:TrialOverlay['nodes']=[];
  constructor(private viewer:Viewer,private services:Services){
    this.panel.className='right local-trial-panel';this.panel.dataset.tool='local-trial';this.panel.hidden=true;
    this.panel.innerHTML=`<div class="tool-heading"><h1>局部试算</h1><p id="lt-source" class="hint">上传瞬态计算 DAT 后读取焊路。</p></div>
      <section><h2>焊路显示</h2><label class="check"><input id="lt-all" type="checkbox" checked>显示全部焊路</label><div id="lt-paths"></div></section>
      <section><h2>选取范围（mm）</h2><input id="lt-path" type="hidden"><p id="lt-selected-path" class="hint" role="status">点击焊路节点选择范围。</p><label>选取方式<select id="lt-method"><option value="ends">起点与终点</option><option value="center">中心与长度</option></select></label>
      <div id="lt-ends-fields" class="input-row"><label>起点<input id="lt-start" type="number" step="any" value="0"></label><label>终点<input id="lt-end" type="number" step="any" value="1"></label></div>
      <div id="lt-center-fields" class="input-row" hidden><label>中心<input id="lt-center" type="number" step="any" value="0"></label><label>长度<input id="lt-length" type="number" min="0.001" step="any" value="10"></label></div>
      <div id="lt-pick-buttons" class="lt-actions"></div><label class="check"><input id="lt-wrap" type="checkbox">闭合焊路跨越起点</label><label>匹配容差<input id="lt-tolerance" type="number" min="0.000001" step="any" value="0.01"></label></section>
      <section><h2>模型裁剪（mm）</h2><div id="lt-box"></div><p class="hint">拖动坐标滑条调整包围盒，视窗同步显示。</p><div id="lt-components"></div></section>
      <section><h2>焊路匹配</h2><div id="lt-matches"></div><p id="lt-status" class="hint" role="status"></p></section>
      <section><h2>温度提取范围</h2><div id="lt-trim-track" class="lt-dual-range"><div class="lt-range-base"></div><div id="lt-trim-fill" class="lt-range-fill"></div><input id="lt-trim-start" aria-label="提取起点百分比" type="range" min="0" max="100" step="0.1" value="0"><input id="lt-trim-end" aria-label="提取终点百分比" type="range" min="0" max="100" step="0.1" value="100"></div><div class="lt-range-labels"><output id="lt-trim-start-value">0%</output><output id="lt-trim-end-value">100%</output></div><p id="lt-trim-length" class="hint"></p><p class="hint">拖动两个端点，深黄色部分为保留的提取范围。</p></section>
      <section><h2>试算分组</h2><label>分组名称<input id="lt-name" value="局部试算 1"></label><p id="lt-dirty" class="hint"></p><div class="lt-actions" id="lt-group-actions"></div><div id="lt-groups"></div></section>
      <section><h2>导出</h2><p class="hint">导出局部网格和焊路脚本，在 Simufact 中手动设置计算模型。</p><button id="lt-export" class="primary full">导出网格与焊路 ZIP</button></section>`;
    document.querySelector('main')!.append(this.panel);
    for(const [target,label] of [['start','选取起点'],['end','选取终点'],['center','选取中心']]){const button=iconButton(target==='end'?'end':'start',label);button.dataset.pick=target;button.onclick=()=>{this.pickTarget=target;this.selectionMode();this.updateMethod();this.services.message(`请在视窗点击焊路节点：${label}`);};this.get('pick-buttons').append(button);}
    for(const end of ['min','max'])for(let axis=0;axis<3;axis++){
      const label=document.createElement('label');label.textContent=`${end==='min'?'最小':'最大'} ${'XYZ'[axis]}`;
      const row=document.createElement('div');row.className='lt-box-row';
      const number=document.createElement('input');number.type='number';number.step='any';number.id=`lt-box-${end}-${axis}`;
      const range=document.createElement('input');range.type='range';range.step='any';range.id=`lt-range-${end}-${axis}`;
      number.oninput=()=>{range.value=number.value;this.changed();};range.oninput=()=>{number.value=range.value;this.changed();};row.append(number,range);label.append(row);this.get('box').append(label);
    }
    this.get('all').onchange=()=>{this.shown=new Set(this.field('all').checked?this.meta?.paths.map(p=>p.id):[]);this.renderPaths();this.draw();};
    this.get('method').onchange=()=>{this.pickTarget=this.field('method').value==='center'?'center':'start';this.updateMethod();this.changed();};
    for(const id of ['start','end','center','length','wrap','tolerance'])this.get(id).oninput=()=>this.changed();
    for(const end of ['start','end'])this.get(`trim-${end}`).oninput=()=>{
      const start=this.value('trim-start'),finish=this.value('trim-end');
      if(start>=finish)this.field(`trim-${end}`).value=String(end==='start'?finish-.1:start+.1);
      this.markDirty();this.updateTrim();this.draw();
    };
    this.get('name').oninput=()=>this.markDirty();
    const save=iconButton('save','保存试算分组');save.onclick=()=>{void this.services.run('正在保存试算分组…',async()=>{await this.preview();if(!this.result)throw Error('需要有效的范围预览。');const group={...this.spec(),name:this.field('name').value.trim()};if(!group.name)throw Error('请输入分组名称。');if(this.editing<0){this.groups.push(group);this.editing=this.groups.length-1;}else this.groups[this.editing]=group;this.dirty=false;this.renderGroups();this.services.changed();});};
    const add=iconButton('add','新增独立试算分组');add.onclick=()=>{this.editing=-1;this.field('name').value=`局部试算 ${this.groups.length+1}`;this.resetRange();};this.get('group-actions').append(save,add);
    this.get('export').onclick=()=>{void this.services.run('正在导出局部试算…',async()=>{
      if(!this.groups.length)throw Error('请保存至少一个试算分组。');if(this.dirty)throw Error('当前参数尚未保存，请保存分组后导出。');
      const response=await this.services.api('local-trial/export',{model_id:this.meta!.model_id,groups:this.groups});
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='local_trial.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });};
    this.updateMethod();this.selectionMode();
  }
  private get(id:string){return this.panel.querySelector<HTMLElement>(`#lt-${id}`)!;}
  private field(id:string){return this.get(id) as HTMLInputElement;}
  private value(id:string){const value=Number(this.field(id).value);if(!Number.isFinite(value)||this.field(id).value==='')throw Error('请输入有效数值。');return value;}
  private markDirty(){this.dirty=true;this.get('dirty').textContent='当前参数尚未保存';this.services.changed();}
  activate(active:boolean){this.active=active;this.viewer.showLocalTrial(active);if(active&&this.available&&!this.meta)void this.services.run('正在读取局部试算焊路…',()=>this.load());if(active)this.draw();}
  async modelChanged(available:boolean){this.revision++;window.clearTimeout(this.timer);this.available=available;this.meta=null;this.result=null;this.cropNodes=[];this.groups=[];this.excluded.clear();this.editing=-1;this.dirty=false;this.renderGroups();this.get('paths').replaceChildren();this.get('matches').replaceChildren();this.get('source').textContent=available?'进入局部试算后读取 DAT 焊路。':'局部试算需要瞬态计算 Marc DAT。';this.get('selected-path').textContent='点击焊路节点选择范围。';this.draw();if(this.active&&available)await this.load();}
  private async load(){const revision=++this.revision;const meta:Meta=await(await this.services.api('local-trial',{length_unit:this.services.unit()})).json();if(revision!==this.revision)return;this.meta=meta;this.shown=new Set(meta.paths.map(p=>p.id));this.get('source').textContent=meta.source_process;this.field('path').value=String(meta.paths[0].id);this.renderPaths();this.get('components').replaceChildren();for(const component of meta.components){const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=component.name==='Base'||component.keep_whole;check.dataset.component=component.name;check.onchange=()=>this.changed();label.append(check,document.createTextNode(`完整保留 ${component.name}`));this.get('components').append(label);}this.resetRange();}
  private renderPaths(){this.get('paths').replaceChildren();for(const path of this.meta?.paths??[]){const row=document.createElement('div');row.className='lt-path-row';const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=this.shown.has(path.id);check.onchange=()=>{if(check.checked)this.shown.add(path.id);else this.shown.delete(path.id);this.field('all').checked=this.shown.size===this.meta!.paths.length;this.draw();};label.append(check,document.createTextNode(path.label));const only=iconButton('eye',`单独显示 ${path.label}`);only.onclick=()=>{this.shown=new Set([path.id]);this.field('all').checked=this.meta!.paths.length===1;this.renderPaths();this.draw();};row.append(label,only);this.get('paths').append(row);}}
  private path(){const path=this.meta?.paths.find(p=>p.id===Number(this.field('path').value));if(!path)throw Error('请选择有效焊路。');return path;}
  private resetRange(){
    if(!this.meta)return;const path=this.path();this.field('start').value='0';this.field('end').value=String(path.length_mm);
    this.field('center').value=String(path.length_mm/2);this.field('length').value=String(path.length_mm);this.field('wrap').checked=false;
    this.field('wrap').disabled=Math.hypot(...path.points_mm[0].map((v,i)=>v-path.points_mm.at(-1)![i]))>1e-8;
    this.field('trim-start').value='0';this.field('trim-end').value='100';this.excluded.clear();
    for(let axis=0;axis<3;axis++)for(const [index,end] of ['min','max'].entries()){
      const min=this.meta.bounds_mm[0][axis],max=this.meta.bounds_mm[1][axis],span=max-min;
      const range=this.field(`range-${end}-${axis}`);range.min=String(min-span*.1);range.max=String(max+span*.1);range.disabled=span===0;
      range.value=String(this.meta.bounds_mm[index][axis]);this.field(`box-${end}-${axis}`).value=String(this.meta.bounds_mm[index][axis]);
    }
    this.updateMethod();this.changed();
  }
  private interval(){
    const path=this.path();let start=this.field('start').valueAsNumber,end=this.field('end').valueAsNumber;
    if(this.field('method').value==='center'){
      start=this.field('center').valueAsNumber-this.field('length').valueAsNumber/2;end=this.field('center').valueAsNumber+this.field('length').valueAsNumber/2;
      if(this.field('wrap').checked){start=(start%path.length_mm+path.length_mm)%path.length_mm;end=(end%path.length_mm+path.length_mm)%path.length_mm;}
    }
    const length=this.field('wrap').checked?path.length_mm-start+end:end-start;
    return {start,end,length};
  }
  private spec():Spec {const path=this.path(),{start,end,length}=this.interval();
    if(![start,end,length].every(Number.isFinite))throw Error('请输入有效的焊路范围。');
    return {model_id:this.meta!.model_id,path_id:path.id,start_mm:start,end_mm:end,wrap:this.field('wrap').checked,tolerance_mm:this.value('tolerance'),box:{min:[0,1,2].map(i=>this.value(`box-min-${i}`)),max:[0,1,2].map(i=>this.value(`box-max-${i}`))},keep_whole:Array.from(this.panel.querySelectorAll<HTMLInputElement>('[data-component]:checked')).map(e=>e.dataset.component!),trim_start_mm:length*this.value('trim-start')/100,trim_end_mm:length*(100-this.value('trim-end'))/100,included_path_ids:this.meta!.paths.filter(p=>!this.excluded.has(p.id)).map(p=>p.id)};
  }
  private changed(){if(!this.meta)return;this.markDirty();this.revision++;this.result=null;this.cropNodes=[];this.get('status').textContent='正在更新预览…';window.clearTimeout(this.timer);this.updateTrim();this.draw();this.timer=window.setTimeout(()=>{const revision=this.revision+1;void this.preview().catch(error=>{if(revision===this.revision)this.get('status').textContent=error instanceof Error?error.message:String(error);});},250);}
  private async preview(){
    window.clearTimeout(this.timer);const revision=++this.revision;const spec=this.spec();
    const result:Preview=await(await this.services.api('local-trial/preview',spec)).json();if(revision!==this.revision)return;
    this.result=result;this.cropNodes=result.crop.node_ids.map((id,i)=>({id,xyz_mm:result.crop.points_mm[i]}));
    const warnings=result.warnings;
    this.get('status').textContent=`${result.crop.node_count} 节点 · ${result.crop.element_count} 单元${warnings.length?'\n'+warnings.join('\n'):''}`;
    this.renderMatches();this.updateTrim();this.draw();
  }
  private updateMethod(){
    const center=this.field('method').value==='center';
    this.get('ends-fields').hidden=center;this.get('center-fields').hidden=!center;
    this.get('pick-buttons').querySelectorAll<HTMLButtonElement>('[data-pick]').forEach(button=>{
      button.hidden=center?button.dataset.pick!=='center':button.dataset.pick==='center';
      button.setAttribute('aria-pressed',String(button.dataset.pick===this.pickTarget));
    });
    if(this.meta)this.get('selected-path').textContent=`当前焊路：${this.path().label} · ${this.pickTarget==='start'?'点击起点':this.pickTarget==='end'?'点击终点':'点击中心点'}`;
  }
  private updateTrim(){
    const start=this.value('trim-start'),end=this.value('trim-end');
    this.get('trim-start-value').textContent=`${start.toFixed(1)}%`;this.get('trim-end-value').textContent=`${end.toFixed(1)}%`;
    this.get('trim-fill').style.left=`${start}%`;this.get('trim-fill').style.width=`${end-start}%`;
    const length=this.meta?this.interval().length:0;
    this.get('trim-length').textContent=Number.isFinite(length)&&length>0?`保留 ${(length*(end-start)/100).toFixed(3)} mm · 起点去除 ${(length*start/100).toFixed(3)} mm · 终点去除 ${(length*(100-end)/100).toFixed(3)} mm`:'';
  }
  private renderMatches(){this.get('matches').replaceChildren();for(const path of this.meta?.paths??[]){const matches=this.result?.matches.filter(match=>match.path_id===path.id)??[];const excluded=this.excluded.has(path.id);if(!matches.length&&!excluded)continue;const matched=matches.length>0&&matches.every(match=>match.matched);const label=document.createElement('label');label.className='check';const check=document.createElement('input');check.type='checkbox';check.checked=matched&&!excluded;check.disabled=path.id===Number(this.field('path').value)||(!matched&&!excluded);check.onchange=()=>{if(check.checked)this.excluded.delete(path.id);else this.excluded.add(path.id);this.changed();};const adjustment=matches.flatMap(match=>match.endpoint_adjustment_mm);label.append(check,document.createTextNode(`${path.label} · ${excluded?'未纳入':matched?'匹配成功':matches.map(match=>match.reason).join('；')}${adjustment.some(value=>value>0)?` · 端点调整 ${adjustment.map(value=>value.toPrecision(4)).join(' / ')} mm`:''}`));this.get('matches').append(label);}}
  private selectionMode(){
    this.viewer.localTrialSelection('path',(_ids,pathId,point)=>{
      if(pathId!==undefined&&point!==undefined){
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
    const length=group.wrap?path.length_mm-group.start_mm+group.end_mm:group.end_mm-group.start_mm;
    this.field('trim-start').value=String(group.trim_start_mm/length*100);this.field('trim-end').value=String(100-group.trim_end_mm/length*100);
    for(const end of ['min','max'] as const)for(let axis=0;axis<3;axis++){
      this.field(`box-${end}-${axis}`).value=String(group.box[end][axis]);this.field(`range-${end}-${axis}`).value=String(group.box[end][axis]);
    }
    this.panel.querySelectorAll<HTMLInputElement>('[data-component]').forEach(e=>e.checked=group.keep_whole.includes(e.dataset.component!));
    this.excluded=new Set(this.meta!.paths.filter(p=>group.included_path_ids&&!group.included_path_ids.includes(p.id)).map(p=>p.id));
    this.pickTarget='start';this.updateMethod();this.changed();this.dirty=false;this.renderGroups();
  }
  private draw(){
    const pathId=Number(this.field('path').value);
    const paths:TrialOverlay['paths']=(this.meta?.paths??[]).filter(p=>this.shown.has(p.id)).map(p=>({id:p.id,points_mm:p.points_mm,color:p.id===pathId?0xe0ae35:0x226f98,width:p.id===pathId?4:2,selected:p.id===pathId}));
    if(this.meta&&this.shown.has(pathId)){
      const path=this.path(),{start,end,length}=this.interval();
      if([start,end,length].every(Number.isFinite)&&length>0&&start>=0&&end>=0&&start<=path.length_mm&&end<=path.length_mm){
        const points=this.field('wrap').checked?[...slicePath(path.points_mm,start,path.length_mm),...slicePath(path.points_mm,0,end).slice(1)]:slicePath(path.points_mm,start,end);
        paths.push({id:-1,points_mm:points,color:0xb3bac2,width:6});
        paths.push({id:-2,points_mm:slicePath(points,length*this.value('trim-start')/100,length*this.value('trim-end')/100),color:0xb57600,width:8});
      }
    }
    let box:TrialOverlay['box']=null;if(this.meta)box={min:[0,1,2].map(i=>Number(this.field(`box-min-${i}`).value)),max:[0,1,2].map(i=>Number(this.field(`box-max-${i}`).value))};
    this.viewer.setLocalTrial({paths,nodes:this.cropNodes,selected:[],faces:this.result?.crop.faces??this.result?.crop.cut_faces??[],box,showNodes:false,previewReady:this.result!==null});
    this.viewer.showLocalTrial(this.active);
  }
}
