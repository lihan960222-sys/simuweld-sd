"""Two distinct MITC4 shells, welded interface, sliding support and removable pads."""
from case_mesh import *
from sfmarc_workbench.plate_shell import MITC4Shell,ElasticSection
from scipy.sparse import diags,csr_matrix
from scipy.sparse.linalg import spsolve
from scipy.ndimage import distance_transform_edt
import time

res,k=map(int,sys.argv[1:3]); step=k*.1;dst=result_directory(res)
data=np.load(dst/'mesh.npz');ids=data['ids'];cells=data['cells']
assembly=np.load(out/'两板共节点壳.npz'); top=assembly['top'];bot=assembly['bottom']
pad=np.load(out/'两压头接触区.npz');clamp=pad['cover1']|pad['cover2']
nu=mat.poisson;C=np.array([[1,nu,0],[nu,1,0],[0,0,(1-nu)/2]])/(1-nu**2)
pe=np.zeros((len(cells),5,3)); temp=np.zeros((len(cells),5))
gauss,weight=np.polynomial.legendre.leggauss(5);weight/=2
interp=np.stack([np.interp((gauss+1)/2,(np.arange(5)+.5)/5,e) for e in np.eye(5)],axis=1)
for side in (0,1):
    state=np.load(dst/f'plastic_{side}.npz');sel=state['idx']
    pe[sel]=state['p'].transpose(1,0,2);temp[sel]=(interp@state['Tend']).T
pe[:,:,:2]+=mat.thermal_strain(temp,reference=initial_kelvin(dst))[:,:,None]
x0,y0=g['x'][0]-.05,g['y'][0]-.05
ny,nx=ids.shape[1:]; Y,X=np.indices((ny,nx)); nxq=(nx+k-1)//k; nyq=(ny+k-1)//k
tile=(Y//k)*nxq+X//k
xy=[];con=[];hs=[];offs=[];fractions=[];sides=[];areas=[];ls=[];maps=[]
abds=[];loads=[];energies=[];shears=[];padnodes=[];nodeareas=[];anchors=[]
surface=[];rt=mat.elastic_modulus(298.15)
for side in (0,1):
    mask=ids[side]>=0
    tiles,inv=np.unique(tile[mask],return_inverse=True);n=len(tiles)
    counts=np.bincount(inv); area=counts*1e-8
    mapping=np.full(nxq*nyq,-1,int); mapping[tiles]=np.arange(n)+len(con);maps.append(mapping)
    j,i=np.divmod(tiles,nxq)
    c4=(j*(nxq+1)+i)[:,None]+[0,1,nxq+2,nxq+1]
    nodes,q4=np.unique(c4,return_inverse=True);q4=q4.reshape(-1,4)
    N=len(nodes);lookup=np.full((nyq+1)*(nxq+1),-1,int);lookup[nodes]=np.arange(N)+len(xy);ls.append(lookup)
    jj,ii=np.divmod(nodes,nxq+1);nodexy=np.c_[x0+step*ii,y0+step*jj]*.001
    h=(top[side]-bot[side])[mask]*.001
    ref=float(np.nanmean((top[side]+bot[side])/2));off=((top[side][mask]+bot[side][mask])/2-ref)*.001
    def avg(v):return np.bincount(inv,weights=v,minlength=n)/counts
    def nodal(v):
        return np.bincount(q4.ravel(),weights=np.repeat(v,4),minlength=N)
    padarea=avg(clamp[side][mask].astype(float))*area
    # Node belongs to a pad if a majority of its supported footprint is pressed.
    frac=nodal(padarea)/nodal(area)
    padnodes.extend((np.flatnonzero(frac>=.5)+len(xy)).tolist())
    ni=nodal(area)/4
    nodeareas.extend(ni)
    # Retain per-cell surface elevations for contact and welded-face offsets.
    surface.append((ref,avg(bot[side][mask]),avg(top[side][mask])))
    if side==0:
        a=np.argmin(ii+jj);b=np.argmax(ii-jj)
        anchors=[5*a,5*a+1,5*b+1]
    xy.extend(nodexy);con.extend(q4+len(xy)-N);hs.extend(avg(h));offs.extend(avg(off));fractions.extend(counts/k**2)
    areas.extend(area);sides.extend([side]*n)
    abd=np.zeros((n,3,3,3));load=np.zeros((n,2,3));energy=np.zeros(n);sh=np.zeros(n)
    ids0=ids[side][mask]
    for l in range(5):
        E=mat.elastic_modulus(temp[ids0,l]);z=off+h*gauss[l]/2;st=weight[l]*h*E
        ee=pe[ids0,l];stress=ee@C.T
        for m in range(3):abd[:,m]+=avg(st*z**m)[:,None,None]*C
        for m in range(2):
            for d in range(3):load[:,m,d]+=avg(st*z**m*stress[:,d])
        energy+=.5*avg(st*(stress*ee).sum(1));sh+=avg(st)/(2*(1+nu))
    abds.extend(abd);loads.extend(load);energies.extend(energy);shears.extend(sh)

xy,con,hs,offs,fractions,sides,areas,nodeareas=map(np.asarray,(xy,con,hs,offs,fractions,sides,areas,nodeareas))
shell=MITC4Shell(xy,con,hs,rt,nu,area_fraction=fractions,section_offset=offs)
abd,load,en,sh=map(np.asarray,(abds,loads,energies,shears))
nd=shell.ndof; zero=np.zeros(nd)
gravity=zero.copy()
for c in con.T:np.add.at(gravity,5*c+2,-areas*hs*mat.density*9.80665/4)
def points(side,px,py):
    xx=(px-x0)/step;yy=(py-y0)/step
    i,j=np.floor(xx).astype(int),np.floor(yy).astype(int)
    e=maps[side][j*nxq+i]
    assert np.all(e>=0)
    u,v=xx-i,yy-j
    return e,np.stack([(1-u)*(1-v),u*(1-v),u*v,(1-u)*v],axis=1)
def relation(e0,e1,N,arm0=0.,arm1=0.,direction=2):
    row=np.repeat(np.arange(len(e0)),4)
    d0=con[e0];d1=con[e1];shape=(len(e0),nd)
    a=coo_matrix((N.ravel(),(row,(5*d1+direction).ravel())),shape=shape).tocsr()
    a-=coo_matrix((N.ravel(),(row,(5*d0+direction).ravel())),shape=shape).tocsr()
    if direction<2:
        rot=4 if direction==0 else 3;sgn=1 if direction==0 else -1
        a+=coo_matrix(((N*arm1*sgn).ravel(),(row,(5*d1+rot).ravel())),shape=shape).tocsr()
        a-=coo_matrix(((N*arm0*sgn).ravel(),(row,(5*d0+rot).ravel())),shape=shape).tocsr()
    return a
from scipy.sparse import vstack
# Compression-only plate interface. Aggregate integration points within
# subtiles, retaining the original clearances and contact area.
sample=max(1,k//3);sindex=Y//sample*((nx+sample-1)//sample)+X//sample
padrows=[];pw=[]
for side in (0,1):
    hit=clamp[side]
    if not hit.any():continue
    key,which=np.unique(np.stack((tile[hit],sindex[hit]),axis=1),axis=0,return_inverse=True)
    count=np.bincount(which);px=x0+.1*(.5+np.bincount(which,weights=X[hit])/count)
    py=y0+.1*(.5+np.bincount(which,weights=Y[hit])/count)
    e,N=points(side,px,py)
    padrows.append(coo_matrix((N.ravel(),(np.repeat(np.arange(len(e)),4),(5*con[e]+2).ravel())),shape=(len(e),nd)).tocsr())
    pw.extend(count*1e-8*2e13)
P=vstack(padrows).tocsr()
PK=(P.T@diags(pw)@P).tocsr()
both=np.isfinite(top[0]+bot[1])
keys,inv=np.unique(np.stack((tile[both],sindex[both]),axis=1),axis=0,return_inverse=True)
num=np.bincount(inv)
def av(v):return np.bincount(inv,weights=v[both])/num
px=x0+(av(X)+.5)*.1;py=y0+(av(Y)+.5)*.1
e0,N=points(0,px,py);e1,_=points(1,px,py)
interface=relation(e0,e1,N)
lowernodes=np.unique(con[sides==0])
base=coo_matrix((np.ones(len(lowernodes)),(np.arange(len(lowernodes)),5*lowernodes+2)),shape=(len(lowernodes),nd)).tocsr()
A=vstack([base,interface]).tocsr()
gap=np.r_[np.zeros(len(lowernodes)),np.maximum(av(bot[1]-top[0]),0.)*.001]
penalty=2e12
ka=np.r_[nodeareas[lowernodes],num*1e-8]*penalty
# Weld three offset integration tracks within the cone radius at the interface.
bond=[];bw=[]
for p,arc,pts in paths:
    src=p['sources'][1];ds=.22
    s=np.arange(ds/2,arc[-1],ds)
    seg=np.clip(np.searchsorted(arc,s)-1,0,len(pts)-2)
    tangent=np.diff(pts[:,:2],axis=0)[seg];tangent/=np.linalg.norm(tangent,axis=1)[:,None]
    nxdir,nydir=-tangent[:,1],tangent[:,0]
    xc=np.interp(s,arc,pts[:,0]);yc=np.interp(s,arc,pts[:,1])
    rad=src['upper_radius_mm']+(.08/src['depth_mm'])*(src['lower_radius_mm']-src['upper_radius_mm'])
    for r,w in zip([-np.sqrt(.6),0,np.sqrt(.6)],[5/18,4/9,5/18]):
        px=xc+rad*r*nxdir;py=yc+rad*r*nydir;e0,N=points(0,px,py);e1,_=points(1,px,py)
        weldz=pts[0,2]-.08
        for d in range(3):
            bond.append(relation(e0,e1,N,(weldz-surface[0][0])*.001,(weldz-surface[1][0])*.001,d))
            bw.extend(np.full(len(s),w*2*rad*ds*1e-6*2e14))
B=vstack(bond).tocsr()
BK=(B.T@diags(bw)@B).tocsr()
def stage(f):return ElasticSection(abd,load*f,en*f*f,sh)
def balance(q,section,f,held,external):
    fixed=anchors
    free=np.ones(nd,bool);free[fixed]=False
    def evaluate(v,tangent):
        resp=shell.evaluate(v.reshape(-1,5),section=section,tangent=tangent)
        clearance=A@v+gap
        force=resp.residual+BK@v+A.T@(ka*np.minimum(clearance,0.))-gravity*f-external
        if held:force+=held*(PK@v)
        if not tangent:return force
        active=clearance<1e-13
        kactive=A[active]
        tangent=resp.tangent+BK+kactive.T@diags(ka[active])@kactive
        if held:tangent+=held*PK
        return force,tangent
    for it in range(110):
        force,K=evaluate(q,True);r=force[free]
        if np.max(abs(r))<1e-5:return q,force
        # Backtrack the full potential, including contact, gravity and weld.
        def energy(v):
            return shell.energy(v.reshape(-1,5),section=section)+.5*v@(BK@v)+.5*ka@(np.minimum(A@v+gap,0)**2)-v@(gravity*f+external)+(.5*held*v@(PK@v) if held else 0.)
        old=energy(q);Kf=K[free][:,free].tocsc();diag=np.maximum(abs(Kf.diagonal()),1e-12)
        for damp in (0.,1e-9,1e-7,1e-5,.001,.1,1.):
            delta=np.zeros_like(q)
            delta[free]=spsolve(Kf if damp==0 else Kf+diags(damp*diag),-r)
            slope=force@delta
            if slope>=0 or not np.isfinite(slope):continue
            for l in range(20):
                qn=q+delta*2.**-l
                if energy(qn)<=old+1e-4*slope*2.**-l+1e-16:break
            else:continue
            break
        else:raise RuntimeError(('line search',np.max(abs(r))))
        q=qn
    raise RuntimeError(('Newton',np.max(abs(r))))

q=zero.copy();t0=time.perf_counter()
seed_flag=next((v for v in sys.argv if v.startswith('--seed-grid=')),None)
if seed_flag:
    source_k=int(seed_flag.split('=')[1]);coarse=np.load(dst/f'released_{source_k}_soft.npz')
    for side in (0,1):
        ci=coarse['conn'][coarse['sides']==side]; fine_nodes=np.unique(con[sides==side])
        pos=coarse['xy'][ci].mean(1); nearest=cKDTree(pos).query(xy[fine_nodes])[1]
        elem=ci[nearest];uv=(xy[fine_nodes]-coarse['xy'][elem[:,0]])/(source_k*.0001);u,v=uv.T
        w=np.stack(((1-u)*(1-v),u*(1-v),u*v,(1-u)*v),axis=1)
        q.reshape(-1,5)[fine_nodes]=np.einsum('ni,nij->nj',w,coarse['q'][elem])
    # Translate/rotate both plates together into this grid's in-plane gauge.
    nod=q.reshape(-1,5);a=anchors[0]//5;b=anchors[2]//5
    nod[:,:2]-=nod[a,:2].copy()
    angle=nod[b,1]/(xy[b,0]-xy[a,0]);nod[:,1]-=angle*(xy[:,0]-xy[a,0]);nod[:,0]+=angle*(xy[:,1]-xy[a,1])
    q[anchors]=0
    q,force=balance(q,stage(1.),1.,0.,zero);force[anchors]=0.
    np.savez_compressed(dst/f'released_{k}_seed.npz',xy=xy,conn=con,q=q.reshape(-1,5),sides=sides,area=areas,padnodes=padnodes,
        max_pen_m=max(0.,-(A@q+gap).min()),max_tie_m=abs(B@q).max(),residual_N=np.max(abs(force)))
    print('fine equilibrium from coarse branch',time.perf_counter()-t0,flush=True)
    sys.exit()
for operation in ['held','released']:
    if operation=='held' and '--resume' in sys.argv:
        q=np.load(dst/f'held_{k}.npz')['q'].ravel();reaction=-(PK@q)
        continue
    s=0.;increment=.25 if '--resume' in sys.argv else .1
    while s<1.-1e-12:
        trial=min(1.,s+increment);held=operation=='held'
        f=trial if held else 1.;external=zero if held else (1-trial)*reaction
        stiffness=float(held)
        if '--soft-release' in sys.argv and not held:
            stiffness=10.**(-9*trial) if trial<1. else 0.
            external=zero
        try:qn,force=balance(q.copy(),stage(f),f,stiffness,external)
        except RuntimeError as exc:
            increment*=.5
            print('retry',operation,s,increment,exc,flush=True)
            if increment<.00005:raise
            continue
        q=qn;s=trial;increment=min(.3,increment*1.5)
        print('balance',k,operation,round(s,4),round(time.perf_counter()-t0,1),flush=True)
    if held:
        reaction=-(PK@q)
    # Shell transverse displacement at the reference surface. Kirchhoff/MITC
    # kinematics has the same w throughout thickness.
    pen=max(0.,-(A@q+gap).min())
    free_res=force.copy();free_res[anchors]=0
    assert np.max(abs(free_res))<1e-5 and np.isfinite(q).all()
    suffix='_soft' if '--soft-release' in sys.argv else '_damped' if '--resume' in sys.argv else ''
    np.savez_compressed(dst/f'{operation}_{k}{suffix}.npz',xy=xy,conn=con,q=q.reshape(-1,5),sides=sides,area=areas,
        xyz_ref=surface[0][0],padnodes=padnodes,max_pen_m=pen,max_tie_m=abs(B@q).max(),residual_N=np.max(abs(free_res)))
    print('checks',operation,pen,abs(B@q).max(),flush=True)
