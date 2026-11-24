from case_mesh import *
from PIL import Image,ImageDraw,ImageFont
from scipy.ndimage import distance_transform_edt

res,k=map(int,sys.argv[1:3]);dst=result_directory(res)
mesh=np.load(dst/'mesh.npz');heat=np.load(dst/'heat.npz')
ass=np.load(out/'两板共节点壳.npz');mask=np.isfinite(ass['top'])
X,Y=np.meshgrid(g['x'],g['y']);x0=X[0,0]-.05;y0=Y[0,0]-.05
seam=cKDTree(path_points(.05)).query(np.c_[X.ravel(),Y.ravel()])[0].reshape(X.shape)
def solution(stage, spacing):
    return next(p for suffix in ('_soft','_seed','_damped','') if (p:=dst/f'{stage}_{spacing}{suffix}.npz').exists())

def read(stage,spacing):
    record=np.load(solution(stage,spacing));xy=record['xy']*1000;con=record['conn'];node=record['q'][:,2]*1000
    fields=[]
    for side in (0,1):
        use=record['sides']==side
        ij=np.rint((xy[con[use,0]]-[x0,y0])/(spacing*.1)).astype(int)
        lookup=np.full((ij[:,1].max()+1,ij[:,0].max()+1,4),-1,int)
        lookup[ij[:,1],ij[:,0]]=con[use]
        px=(X[mask[side]]-x0)/(spacing*.1);py=(Y[mask[side]]-y0)/(spacing*.1)
        i,j=px.astype(int),py.astype(int);u,v=px-i,py-j
        cells=lookup[j,i];assert (cells>=0).all()
        w=(node[cells]*np.stack(((1-u)*(1-v),u*(1-v),u*v,(1-u)*v),axis=1)).sum(1)
        out_field=np.full(X.shape,np.nan);out_field[mask[side]]=w;fields.append(out_field)
    return np.array(fields)

fields={s:read(s,k) for s in ('held','released')}
metrics={}
def fmt(v):return f'{v:+.3f}'
for s,field in fields.items():
    metrics[s]=[]
    for side,Z in enumerate(field):
        jj,ii=np.unravel_index(np.nanargmax(Z),Z.shape)
        jj0,ii0=np.unravel_index(np.nanargmin(Z),Z.shape)
        w=Z[mask[side]];rim=mask[side]&(seam<.5)
        metrics[s].append(dict(z_min_mm=float(w.min()),z_max_mm=float(w.max()),peak_xy_mm=[X[jj,ii],Y[jj,ii]],
            min_xy_mm=[X[jj0,ii0],Y[jj0,ii0]],peak_to_seam_mm=seam[jj,ii],rim_median_mm=float(np.nanmedian(Z[rim])),
            percentiles_mm=np.percentile(w,[5,50,95]).tolist(),above_0p1mm=float((w>.1).mean())))
    stats=np.load(solution(s,k))
    metrics[s+'_equilibrium']={key:float(stats[key]) for key in ['residual_N','max_pen_m','max_tie_m']}
metrics['energy_J']={key:float(heat[key]) for key in ['energy_in','energy_lost','error_J']}
metrics['peak_C']=[float(heat['peak_K'][:,mesh['cells'][:,0]==side].max()-273.15) for side in (0,1)]
def section_height(field):
    best=-np.inf;result={}
    for j,y in enumerate(g['y']):
        crossings=[]
        for _,_,pts in paths:
            a,b=pts[:-1,:2],pts[1:,:2]
            cut=((a[:,1]<=y)&(b[:,1]>y))|((b[:,1]<=y)&(a[:,1]>y))
            a,b=a[cut],b[cut]
            crossings.extend(a[:,0]+(b[:,0]-a[:,0])*(y-a[:,1])/(b[:,1]-a[:,1]))
        if len(crossings)<2:continue
        left,right=min(crossings),max(crossings)
        if right-left<3:continue
        inside=(g['x']>=left)&(g['x']<=right)
        if not np.isfinite(field[j,inside]).all():continue
        valid=np.isfinite(field[j])
        lw,rw=np.interp([left,right],g['x'][valid],field[j,valid])
        d=field[j]-(lw+(rw-lw)*(g['x']-left)/(right-left))
        eligible=(g['x']>left+.5)&(g['x']<right-.5)&inside
        i=np.flatnonzero(eligible)[np.argmax(d[eligible])]
        if d[i]>best:
            best=d[i];result=dict(height_mm=float(best),xy=[g['x'][i],y],weld_x=[left,right],weld_z=[lw,rw],
                                  absolute_z_mm=field[j,i])
    return result
metrics['upper_transverse_bulge']={s:section_height(z[1]) for s,z in fields.items()}
if len(sys.argv)>3:
    other=int(sys.argv[3]);other_fields=read('released',other)
    metrics['mechanical_refinement']=[]
    for z,zo in zip(fields['released'],other_fields):
        ok=np.isfinite(z);metrics['mechanical_refinement'].append(dict(coarse_range=[np.nanmin(zo),np.nanmax(zo)],
            rms_mm=np.sqrt(np.mean((z[ok]-zo[ok])**2)),corr=np.corrcoef(z[ok],zo[ok])[0,1]))

width,height=270,493;left=25;top=142
im=Image.new('RGB',(1210,782),'white');pen=ImageDraw.Draw(im)
def label(x,y,txt,size=18):pen.text((x,y),txt,font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',size),fill='#203945')
label(24,16,'C0004 双板  |  整板 Z 位移',30)
label(24,68,'4.0 s · 同温松夹，底托保留 · 虚线为焊路 · 正值向上 · 四幅图共用色标',19)
bound=max(np.nanmax(abs(f)) for f in fields.values());bound=max(bound,1e-6)
palette=np.array([[43,82,147],[101,153,196],[247,247,242],[236,162,111],[165,45,38]])
def rgb(z):
    img=np.stack([np.interp(np.nan_to_num(z),np.linspace(-bound,bound,5),v) for v in palette.T],axis=-1).astype('uint8')
    img[~np.isfinite(z)]=255
    return img
for col,(s,i) in enumerate([('held',1),('released',1),('held',0),('released',0)]):
    xleft=left+col*297;Z=fields[s][i]
    label(xleft,104,('上板' if i else '底板')+(' · 压紧' if s=='held' else ' · 松夹'),23)
    im.paste(Image.fromarray(rgb(Z[::-1])).resize((width,height)),(xleft,top))
    for _,arc,pts in paths:
        for d in np.arange(.25,arc[-1],2):
            line=[]
            for ss in (d,min(arc[-1],d+.7)):
                a=np.interp(ss,arc,pts[:,0]);b=np.interp(ss,arc,pts[:,1])
                line.append((xleft+(a-x0)/(.1*X.shape[1])*width,top+(1-(b-y0)/(.1*X.shape[0]))*height))
            pen.line(line,fill='#555956',width=1)
    label(xleft,top+height+8,f'{fmt(np.nanmin(Z))} ～ {fmt(np.nanmax(Z))} mm',17)
bar=np.linspace(-bound,bound,400)[None]
im.paste(Image.fromarray(rgb(bar)).resize((400,13)),(380,712))
label(260,700,f'−{bound:.3f} mm',16);label(795,700,f'+{bound:.3f} mm',16)
label(24,748,'位移相对于初始装配位置；结果为壳的横向位移。',15)
im.save(dst/'压紧与松夹_Z.png')
if dst.name.endswith('初温25C'):
    im=Image.new('RGB',(900,754),'white');pen=ImageDraw.Draw(im)
    label(26,18,'25 °C 起焊   /   上板 Z 位移',30)
    label(26,73,'4.0 s   ·   整面底托保留   ·   每幅图单独取色标   ·   红色向上',18)
    for col,(stage,title) in enumerate([('held','压紧'),('released','松开上压头')]):
        z=fields[stage][1]
        lo,hi=min(-1e-12,np.nanmin(z)),max(1e-12,np.nanmax(z))
        def paint(values):
            a=np.stack([np.interp(np.nan_to_num(values),[lo,0,hi],p) for p in palette[[0,2,4]].T],axis=-1).astype('uint8')
            a[~np.isfinite(values)]=255
            return Image.fromarray(a)
        x=34+col*448
        label(x,113,title,25)
        pic=paint(z[::-1]);pic.thumbnail((367,490));im.paste(pic,(x+(374-pic.width)//2,159))
        ramp=np.r_[np.linspace(lo,0,185),np.linspace(0,hi,185)][None]
        im.paste(paint(ramp).resize((370,15)),(x,670))
        label(x,690,f'{lo:+.3f} mm',17);label(x+179,690,'0',17);label(x+278,690,f'{hi:+.3f} mm',17)
    im.save(dst/'上板Z位移_独立色标.png')
section=metrics['upper_transverse_bulge']['released']
l,r=section['weld_x'];j=int(np.argmin(abs(g['y']-section['xy'][1])))
xp=np.linspace(l,r,360);curves=[]
for s in ('held','released'):
    z=fields[s][1,j];ok=np.isfinite(z)
    measured=np.interp(xp,g['x'][ok],z[ok])
    chord=np.linspace(measured[0],measured[-1],len(xp))
    curves.append(measured-chord)
profile=Image.new('RGB',(890,420),'white');pen=ImageDraw.Draw(profile)
def txt(x,y,s,sz=17):pen.text((x,y),s,font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',sz),fill='#233846')
txt(22,12,'上板横截面：扣除两侧焊路连线后的板形',27)
txt(22,63,f'Y = {g["y"][j]:.2f} mm · 松夹后局部隆起最明显的横截面',18)
lo,hi=min(min(v.min() for v in curves),0),max(max(v.max() for v in curves),0)
margin=max(.005,(hi-lo)*.1);lo-=margin;hi+=margin
def coord(x,z):return (115+(x-l)/(r-l)*680,320-(z-lo)/(hi-lo)*190)
for value in np.linspace(lo,hi,5):
    _,yy=coord(l,value);pen.line((115,yy,795,yy),fill='#d8e0e2');txt(20,yy-11,f'{value:+.3f}',15)
pen.line((*coord(l,0),*coord(r,0)),fill='#465964',width=1)
for curve,color in zip(curves,['#37729b','#c15328']):
    pen.line([coord(xx,zz) for xx,zz in zip(xp,curve)],fill=color,width=3)
txt(112,335,f'左侧焊路  X={l:.2f} mm');txt(610,335,f'右侧焊路  X={r:.2f} mm')
txt(23,102,'高度（mm）',16);txt(142,377,'蓝线：压紧');txt(360,377,'橙线：松夹');txt(543,377,'零线：两侧焊路的连线')
profile.save(dst/'上板局部鼓包截面.png')
(dst/'整板结果.json').write_text(json.dumps(metrics,indent=2,ensure_ascii=False),encoding='utf8')
print(json.dumps(metrics,ensure_ascii=False),flush=True)
