export interface HeatParameters {
  power_w: number; velocity: number; efficiency: number;
  conical_upper_radius: number; conical_lower_radius: number; conical_depth: number;
  conical_gaussian_parameter: number; volume_heat_fraction: number;
  disc_radius: number; surface_depth: number; disc_gaussian_parameter: number;
}

const fields: [keyof HeatParameters,string,number,number,number?][]=[
  ['power_w','功率（W）',35,0],['velocity','速度（mm/s）',120,0],['efficiency','效率（0–1）',0.8,0,1],
  ['conical_upper_radius','上部半径（mm）',0.18,0],['conical_lower_radius','下部半径（mm）',0.08,0],
  ['conical_depth','圆锥深度（mm）',0.18,0],['conical_gaussian_parameter','圆锥 Gaussian（0–3）',1,0,3],
  ['volume_heat_fraction','体积热量比例（0–1）',0.8,0,1],['disc_radius','表面圆盘半径（mm）',0.18,0],
  ['surface_depth','表面搜索深度（mm）',0.1,0],['disc_gaussian_parameter','表面 Gaussian（0–3）',1,0,3],
];

export class HeatSettings {
  readonly section=document.createElement('section');
  private svg: SVGSVGElement;

  constructor(changed: ()=>void) {
    this.section.innerHTML='<h2>热源参数</h2><div class="heat-fields"></div><p class="hint">初始数值可编辑；全部焊路使用此组参数。</p><svg id="heat-diagram" viewBox="0 0 300 250" role="img" aria-label="圆锥热源尺寸示意图"></svg><p id="heat-error" role="status"></p>';
    this.svg=this.section.querySelector('svg')!;
    for(const [key,label,value,min,max] of fields) {
      const row=document.createElement('label');row.textContent=label;
      const input=document.createElement('input');input.id='heat-'+key;input.type='number';input.step='any';input.min=String(min);if(max!=null) input.max=String(max);
      input.required=true;input.value=String(value);row.append(input);this.section.querySelector('.heat-fields')!.append(row);
      input.oninput=()=>{this.draw();changed();};
    }
    this.draw();
  }

  get valid() { return [...this.section.querySelectorAll('input')].every(input=>input.checkValidity()); }
  get value(): HeatParameters {
    if(!this.valid) throw new Error('请填写有效的热源参数。');
    return Object.fromEntries(fields.map(([key])=>[key,this.section.querySelector<HTMLInputElement>('#heat-'+key)!.valueAsNumber])) as unknown as HeatParameters;
  }

  private draw() {
    const zeroAllowed=['efficiency','conical_lower_radius','conical_gaussian_parameter','volume_heat_fraction','disc_gaussian_parameter'];
    for(const [key] of fields) {
      const input=this.section.querySelector<HTMLInputElement>('#heat-'+key)!;
      input.setCustomValidity(!zeroAllowed.includes(key) && input.valueAsNumber===0?'数值必须大于零。':'');
    }
    this.svg.replaceChildren();
    this.section.querySelector('#heat-error')!.textContent=this.valid?'':'请填写有效数值以显示热源。';
    if(!this.valid) return;
    const heat=this.value, radius=Math.max(heat.conical_upper_radius,heat.conical_lower_radius,heat.disc_radius);
    const scale=Math.min(76/radius,125/(heat.conical_depth+heat.surface_depth));
    const top=55, x=125, upper=heat.conical_upper_radius*scale, lower=heat.conical_lower_radius*scale, depth=heat.conical_depth*scale;
    const disc=heat.disc_radius*scale, surface=heat.surface_depth*scale, bottom=top+depth;
    this.svg.dataset.scale=String(scale);this.svg.dataset.depth=String(depth);this.svg.dataset.upperRadius=String(upper);
    // 正面尺寸采用相同缩放比例，椭圆表示圆形截面。
    this.svg.innerHTML=`<rect x="${x-disc}" y="${top}" width="${disc*2}" height="${surface}" fill="#ffb347" opacity=".12" stroke="#bd781e" stroke-dasharray="3 3"/>
      <path d="M${x-upper} ${top}L${x-lower} ${bottom}Q${x} ${bottom+lower*.4} ${x+lower} ${bottom}L${x+upper} ${top}Z" fill="#f8ad66" stroke="#bc6328" opacity="${0.3+heat.volume_heat_fraction*.6}"/>
      <ellipse cx="${x}" cy="${top}" rx="${upper}" ry="${upper*.2}" fill="#ffdd9b" stroke="#bc6328"/>
      <ellipse cx="${x}" cy="${top}" rx="${disc}" ry="${disc*.2}" fill="none" stroke="#df9026" stroke-dasharray="3 2"/>
      <line x1="${x}" y1="${top}" x2="${x+upper}" y2="${top}" stroke="#344c5b"/>
      <path d="M230 ${top}H240M235 ${top}V${bottom}M230 ${bottom}H240" fill="none" stroke="#344c5b"/>
      <text x="125" y="18" text-anchor="middle">上部半径 ${heat.conical_upper_radius} mm</text>
      <text x="125" y="222" text-anchor="middle">下部半径 ${heat.conical_lower_radius} mm</text>
      <text x="125" y="241" text-anchor="middle">深度 ${heat.conical_depth} mm</text>
      <text x="${x}" y="${Math.min(205,bottom+lower*.2+20)}" text-anchor="middle">圆盘半径 ${heat.disc_radius} mm</text>`;
  }
}
