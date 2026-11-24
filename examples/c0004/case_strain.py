"""Free-plate membrane and compatible bending on each irregular plate domain."""
from case_mesh import *
from scipy.sparse import hstack,vstack,bmat,diags
from scipy.sparse.linalg import splu
from sfmarc_workbench.plate_material import MaterialState,plane_stress_update
from sfmarc_workbench.plate_inherent import _check_constant_extension
import time

res,side=map(int,sys.argv[1:3])
dst=result_directory(res); mesh=np.load(dst/'mesh.npz')
cells=mesh['cells'];idx=np.flatnonzero(cells[:,0]==side); c=cells[idx]
history=np.load(dst/'T.npy',mmap_mode='r')
nu=mat.poisson
C=np.array([[1,nu,0],[nu,1,0],[0,0,(1-nu)/2]])/(1-nu**2)
_check_constant_extension(mat)

def operators():
    # A quadratic patch gives gradients and curvatures of the same displacement
    # field. Sample neighbours on the material, use physical coordinates (mm).
    n=len(c); pts=c[:,1:3]
    distance,nb=cKDTree(pts).query(pts,k=12)
    v=pts[nb]-pts[:,None,:]
    length=np.maximum(distance[:,-1],.1)
    u,w=np.moveaxis(v/length[:,None,None],-1,0)
    poly=np.stack([u,w,u*u/2,w*w/2,u*w],axis=2)
    weight=1/(.08+(distance/length[:,None])**2)
    sol=np.linalg.pinv(poly*weight[:,:,None],rcond=1e-9)*weight[:,None,:]
    sol[:,:,0]-=sol.sum(axis=2)
    def op(j):
        coeff=sol[:,j]/length[:,None]**(1 if j<2 else 2)
        return coo_matrix((coeff.ravel(),(np.repeat(np.arange(n),nb.shape[1]),nb.ravel())),shape=(n,n)).tocsr()
    dx,dy=op(0),op(1)
    B=bmat([[dx,None],[None,dy],[dy,dx]],format='csr')
    K=vstack([op(2),op(3),2*op(4)],format='csr')
    p0=np.argmin(pts.sum(1)); p1=np.argmax(pts[:,0]-pts[:,1]);p2=np.argmax(pts[:,1])
    def prep(M,fixed,power):
        free=np.ones(M.shape[1],bool);free[fixed]=False; M=M[:,free]
        cw=c[:,3]*c[:,4]**power
        D=bmat([[diags(cw*C[i,j]) if C[i,j]!=0 else None for j in range(3)] for i in range(3)],format='csr')
        rhs=(M.T@D).tocsr();lhs=(rhs@M).tocsc(); lu=splu(lhs)
        def project(a):
            return (M@lu.solve(rhs@a.T.ravel())).reshape(3,n).T
        return project
    return prep(B,[p0,n+p0,n+p1],1),prep(K,[p0,p1,p2],3)
pm,pk=operators()
assert abs(pm(np.ones((len(c),3)))-1).max()<1e-4
# Last three constraints on w fix only affine displacement, not curvature.
assert abs(pk(np.ones((len(c),3)))-1).max()<1e-3
gauss,weights=np.polynomial.legendre.leggauss(5);weights/=2
stations=(1+gauss)/2
cellz=(np.arange(5)+.5)/5
transform=np.stack([np.interp(stations,cellz,e) for e in np.eye(5)],axis=1)
state=MaterialState.zeros((len(c),));diff=MaterialState.zeros((5,len(c)))
def eigen(state):
    e=state.plastic[...,[0,1,5]].copy();e[...,2]*=np.sqrt(2)
    return e
def update(state,total,t,eth,E):
    ep=eigen(state)
    ep[...,:2]+=eth[...,None]
    stress=((total-ep)@C.T)*E[...,None]
    sx,sy,sh=np.moveaxis(stress,-1,0)
    peak=np.maximum(state.peak_temperature,t)
    active=sx*sx+sy*sy-sx*sy+3*sh*sh>mat.yield_stress(t,state.equivalent,peak)**2
    trial=MaterialState(state.plastic.copy(),state.equivalent.copy(),peak)
    if active.any():
        old=MaterialState(state.plastic[active],state.equivalent[active],peak[active])
        sub=plane_stress_update(mat,total[active],t[active],old)[2]
        trial.plastic[active]=sub.plastic;trial.equivalent[active]=sub.equivalent
    return trial

# Region-wise predictor and corrector. Choose times retaining both heating
# and cooling, including the final recorded temperature.
E0=mat.elastic_modulus(initial_kelvin(dst))
step=int(sys.argv[3]) if len(sys.argv)>3 else 2
frames=np.unique(np.r_[np.arange(0,len(history),step),len(history)-1])
mean=np.zeros((len(c),3));curve=mean.copy()
tstart=time.perf_counter()
for it,frame in enumerate(frames):
    raw=history[frame][:,idx].astype(float)
    if raw.min()<293.:raise ValueError('incomplete thermal history')
    tm=np.clip(mat.temperature(mat.enthalpy(raw).mean(0)),mat.temperature_min,mat.temperature_max)
    T=np.clip(transform@raw,mat.temperature_min,mat.temperature_max)
    eth=mat.thermal_strain(T);free=mat.thermal_strain(np.clip(raw,mat.temperature_min,mat.temperature_max)).mean(0)
    young=mat.elastic_modulus(T);Em=mat.elastic_modulus(tm);stiff=weights[:,None]*young
    z=gauss[:,None];arm=z-(stiff*z).sum(0)/stiff.sum(0);D=(stiff*arm**2).sum(0)
    trial=state;trd=diff
    for correct in range(2):
        target=eigen(trial);target[:,:2]+=free[:,None]
        # Precondition by the free-plate elasticity operator; two projection
        # sweeps per corrector keep hot material soft in the trial stress.
        for _ in range(2):
            mean=pm(mean-(Em/E0)[:,None]*(mean-target))
        total=mean.copy()
        total[:,:2]+=(mat.thermal_strain(tm)-free)[:,None]
        trial=update(state,total,tm,mat.thermal_strain(tm),Em)
        et=eigen(trd);et[...,:2]+=eth[...,None]
        mid=(stiff[...,None]*et).sum(0)/stiff.sum(0)[:,None]
        # Physical curvature uses the actual local half-thickness (mm).
        tgt=((stiff*arm)[...,None]*et).sum(0)/D[:,None]/(c[:,4,None]/2)
        for _ in range(2):
            curve=pk(curve-(3*D/E0)[:,None]*(curve-tgt))
        trd=update(diff,mid+arm[...,None]*c[:,4,None]/2*curve,T,eth,young)
    state,diff=trial,trd
    if it%200==0:print('plastic',side,it,len(frames),round(time.perf_counter()-tstart,1),flush=True)
p=eigen(state)+eigen(diff)-np.einsum('z,znc->nc',weights,eigen(diff))
assert np.allclose(np.einsum('z,znc->nc',weights,p),eigen(state),atol=1e-10)
np.savez_compressed(dst/f'plastic_{side}.npz',p=p,idx=idx,Tend=raw,steps=len(frames),mean_p=eigen(state))
