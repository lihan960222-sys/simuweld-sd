"""Conservative split FV: horizontal conduction, local implicit thickness transport."""
from case_mesh import *
from scipy.sparse import diags,eye
from scipy.sparse.linalg import splu
from numpy.polynomial.legendre import leggauss
import time
import json

run_start=time.perf_counter()
res=int(sys.argv[1]);cells,ids,a,b,G,lower,upper,contact=build(res)
initial_C=float(sys.argv[2]) if len(sys.argv)>2 else 25.
initial_K=initial_C+273.15
suffix='' if initial_C==250 else f'_初温{initial_C:g}C'
dst=out/f'整板分层_{res}{suffix}';dst.mkdir(exist_ok=True)
n=len(cells); shape=(5,n);th=cells[:,4]*.001; A=cells[:,3]*1e-6
mass=A*th*mat.density/5
lay=cells[:,0].astype(int)
temp_grid=np.unique(np.r_[np.arange(293.15,5000.,1.),mat.solidus,mat.liquidus,mat.temperature_max])
enthalpy=mat.enthalpy(temp_grid); kap=mat.conductivity(temp_grid)
cp_end=float(mat.capacity(mat.temperature_max))
def H(T):return np.interp(T,temp_grid,enthalpy)+cp_end*np.maximum(0.,T-temp_grid[-1])
def inv(h):
    if h.min()<enthalpy[0]-.001:raise ValueError(('thermal range',h.min(),h.max()))
    return np.interp(h,enthalpy,temp_grid)+np.maximum(0.,h-enthalpy[-1])/cp_end
T=np.full(shape,initial_K);h=H(T)
Tmax=T.copy()
# Flux diffusion split with constant reference coefficients for horizontal transport.
# Conservatively propagate enthalpy; alpha is evaluated at preheat temperature.
alpha=mat.conductivity(initial_K)/(mat.density*mat.capacity(initial_K))
edge=G*mat.density*alpha/5
L=coo_matrix((np.r_[edge,edge,-edge,-edge],(np.r_[a,b,a,b],np.r_[a,b,b,a])),shape=(n,n)).tocsr()
mats={}
def horizontal(h,dt):
    key=round(dt,9)
    if key not in mats:mats[key]=splu((diags(mass)+key*L).tocsc())
    return mats[key].solve((h*mass).T).T

# Five equal-volume slabs per plate, ordered from bottom to top. The two plates
# share the same spatial tiles on the seam; contacts there are one-to-one.
assert len(np.unique(lower))==len(lower) and len(np.unique(upper))==len(upper)
col=np.arange(5*n).reshape(5,n)
allcols=[list(np.r_[col[:,i],col[:,j]]) for i,j in zip(lower,upper)]
single=np.setdiff1d(np.arange(n),np.r_[lower,upper])
groups=[np.array(allcols).T,col[:,single]]
xt,wt=leggauss(4);rad=(xt+1)/2;theta=(np.arange(16)+.5)*np.pi/8
def rays(source):
    cone=source['kind']==5
    depth=(xt+1)*source['depth_mm']/2 if cone else np.array([0.])
    r=source['upper_radius_mm']+(source['lower_radius_mm']-source['upper_radius_mm'])*depth/source['depth_mm'] if cone else np.array([source['radius_mm']])
    az,rr,tt=np.meshgrid(np.arange(len(depth)),np.arange(len(rad)),np.arange(len(theta)),indexing='ij')
    dist=r[az]*rad[rr];w=r[az]**2*wt[rr]*rad[rr]*np.exp(-source['gaussian']*rad[rr]**2)*(wt[az] if cone else 1.)
    return (dist*np.cos(theta[tt])).ravel(),(dist*np.sin(theta[tt])).ravel(),depth[az].ravel(),(w/w.sum()).ravel()
ray_data=[[rays(src) for src in p['sources']] for p,_,_ in paths]
energy_in=energy_lost=0.
def source_q(start,dt):
    global energy_in
    q=np.zeros(shape)
    for k,(p,arc,pts) in enumerate(paths):
        t0=max(start,p['start_s']);t1=min(start+dt,p['end_s'])
        if t0>=t1:continue
        count=max(1,int(np.ceil((t1-t0)*p['speed_mm_s']/.035)))
        ts=t0+(np.arange(count)+.5)*(t1-t0)/count
        s=(ts-p['start_s'])*p['speed_mm_s']
        for src,(rx,ry,d,w) in zip(p['sources'],ray_data[k]):
            px=np.interp(s,arc,pts[:,0])[:,None]+rx
            py=np.interp(s,arc,pts[:,1])[:,None]+ry
            ix=np.rint((px-g['x'][0])/.1).astype(int);iy=np.rint((py-g['y'][0])/.1).astype(int)
            z=pts[0,2]-d
            ramp=np.array(src['power_table'])
            ej=src['power_W']*src['efficiency']*np.interp(ts,ramp[:,0],ramp[:,1])*(t1-t0)/count
            vals=ej[:,None]*w
            for side in (0,1):
                idx=ids[side,iy,ix]
                cap=top[side,iy,ix];base=bot[side,iy,ix]
                valid=(idx>=0)&(z>base+1e-8)&(z<=cap+1e-8)
                level=(np.broadcast_to(z,valid.shape)[valid]-base[valid])/(cap[valid]-base[valid])
                bins=np.clip((5*level).astype(int),0,4)
                np.add.at(q,(bins,idx[valid]),vals[valid])
    energy_in+=q.sum()
    return q
top,bot=g['top'],g['bottom']
Gbase=np.zeros(shape);Gbase[0,lay==0]=1e4*A[lay==0]
Gb=Gbase.ravel()
massall=np.tile(mass,5)
contact_area=contact
def vertical(h,q,dt):
    global energy_lost
    new=h.copy().ravel();original=h.ravel();k=np.interp(inv(h),temp_grid,kap)
    for group in groups:
        if group.size==0:continue
        nid=group%n;M=massall[group]
        # Per-column normal conductance using half-cell resistances.
        half=th[nid]/10/k.ravel()[group]
        Aedge=np.minimum(A[nid[1:]],A[nid[:-1]])
        R=half[1:]+half[:-1]
        if len(group)==10:
            mean=(T.ravel()[group[4]]+T.ravel()[group[5]])*.5
            hgap=np.interp(mean,[273.15,371.76,1311.15],[1e4,1e4,2e6])
            R[4]+=1/hgap
            Aedge[4]=contact_area
        conduct=dt*Aedge/R
        env=Gb[group].copy()
        env[-1]+=(20.+.5*5.670374419e-8*(T.ravel()[group[-1]]+298.15)*(T.ravel()[group[-1]]**2+298.15**2))*A[nid[-1]]
        if len(group)==5 and np.any(lay[nid[0]]==1):
            env[0]+=20*A[nid[0]]*(lay[nid[0]]==1)
        env*=dt
        goal=original[group]+q.ravel()[group]/M
        u=inv(original[group]);hom=conduct
        for iteration in range(55):
            res=M*(H(u)-goal)+env*(u-298.15)
            flux=hom*(u[1:]-u[:-1]);res[1:]+=flux;res[:-1]-=flux
            if abs(res).max()<2e-10:break
            cp=(H(u+.025)-H(u-.025))/.05
            diag=M*cp+env;diag[1:]+=hom;diag[:-1]+=hom
            rhs=-res
            for z in range(1,len(group)):
                ratio=hom[z-1]/diag[z-1]
                diag[z]-=ratio*hom[z-1];rhs[z]+=ratio*rhs[z-1]
            step=rhs
            step[-1]/=diag[-1]
            for z in range(len(group)-2,-1,-1):step[z]=(step[z]+hom[z]*step[z+1])/diag[z]
            # Stop at phase boundaries before relinearizing a latent-heat jump.
            v=u+step
            for boundary in (mat.solidus,mat.liquidus):
                v=np.where((u<boundary-.01)&(v>boundary+.01),boundary+.01,v)
                v=np.where((u>boundary+.01)&(v<boundary-.01),boundary-.01,v)
            u=v
        else:raise RuntimeError(('vertical equilibrium',abs(res).max(),u.min(),u.max()))
        new[group]=H(u);energy_lost+=np.sum(env*(u-298.15))
    return new.reshape(shape)

dt=.0008 if res>=2 else .0004
times=np.r_[np.arange(dt,2.528,dt),2.528,np.arange(2.536,4.0001,.008)]
times=np.unique(np.round(times,9))
hist=np.lib.format.open_memmap(dst/'T.npy',mode='w+',dtype='float32',shape=(len(times),*shape))
np.savez_compressed(dst/'mesh.npz',cells=cells,ids=ids,times=times)
start=time.perf_counter();t0=0.
for i,t1 in enumerate(times):
    step=t1-t0; q=source_q(t0,step)
    h=vertical(horizontal(h+.5*q/mass,step),.5*q,step)
    T=inv(h);Tmax=np.maximum(Tmax,T);hist[i]=T;t0=t1
    if i%500==0:print('thermal',n,i,len(times),round(time.perf_counter()-start,1),Tmax.max()-273.15,flush=True)
hist.flush()
stored=np.sum((h-H(initial_K))*mass)
err=stored+energy_lost-energy_in
print('balance J',energy_in,energy_lost,stored,err,flush=True)
assert abs(err)<.005*energy_in
np.savez_compressed(dst/'heat.npz',peak_K=Tmax,end_K=T,energy_in=energy_in,energy_lost=energy_lost,error_J=err)
(dst/'thermal_timing.json').write_text(json.dumps(dict(initial_C=initial_C,wall_s=time.perf_counter()-run_start,marching_and_save_s=time.perf_counter()-start,steps=len(times),cells=n,energy_error_J=err)),encoding='utf8')
