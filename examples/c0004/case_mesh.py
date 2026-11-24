"""Face-connected adaptive finite volumes derived from the 0.1 mm assembly."""
from pathlib import Path
import sys,json
import os
import numpy as np
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'src'))
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from sfmarc_workbench.plate_material import Material
from dataclasses import replace
out=Path(__file__).resolve().parent/'两板等厚底板'
def result_directory(res):
    return out/os.environ.get('C0004_RESULT_DIR',f'整板分层_{res}')

def initial_kelvin(dst):
    timing=dst/'thermal_timing.json'
    if timing.exists():
        return json.loads(timing.read_text(encoding='utf8'))['initial_C']+273.15
    assert dst.name in ('整板分层_2','整板分层_1')
    return 523.15
g=np.load(out/'两板共节点壳.npz')
plan=json.loads((out/'装配后焊路及工艺.json').read_text(encoding='utf8'))
material_file=Path(os.environ.get('C0004_MATERIAL',Path(__file__).parent/'C19000-TM00-sw.xmt'))
mat=replace(Material.from_xmt(material_file.read_bytes()),thermal_extrapolation=True)
paths=[]
for p in plan['paths']:
    pts=np.array(p['points_mm'])
    arc=np.r_[0,np.cumsum(np.linalg.norm(np.diff(pts,axis=0),axis=1))]
    paths.append((p,arc,pts))
def path_points(pitch):
    points=[]
    for p,arc,pts in paths:
        s=np.unique(np.r_[np.arange(0,arc[-1],pitch),arc])
        points.append(np.array([np.interp(s,arc,pts[:,i]) for i in (0,1)]).T)
    return np.vstack(points)

def build(minimum):
    x,y=g['x'],g['y']; X,Y=np.meshgrid(x,y)
    t,b=g['top'],g['bottom']
    mask=np.isfinite(t)
    distance=cKDTree(path_points(.05)).query(np.c_[X.ravel(),Y.ravel()])[0].reshape(X.shape)
    ids=np.full((2,*X.shape),-1,int)
    data=[]
    def block(j,i,k):
        sl=(slice(j,j+k),slice(i,i+k)); m=mask[(slice(None),*sl)]
        if not m.any():return
        dist=distance[sl].min()
        target=minimum if dist<.35 else max(minimum,4) if dist<1.1 else 8 if dist<2.5 else 16
        if k>target:
            for dj,di in [(0,0),(0,k//2),(k//2,0),(k//2,k//2)]:
                block(j+dj,i+di,k//2)
            return
        for layer in (0,1):
            if not m[layer].any():continue
            hit=m[layer];idx=len(data);ids[layer][sl][hit]=idx
            data.append([layer,X[sl][hit].mean(),Y[sl][hit].mean(),hit.sum()*.01,
                         (t[layer][sl][hit]-b[layer][sl][hit]).mean(),k*.1])
    for j in range(0,len(y),16):
        for i in range(0,len(x),16):block(j,i,16)
    cells=np.array(data);n=len(cells)
    def pairs(a,b,weights):
        valid=(a>=0)&(b>=0)&(a!=b)
        lo=np.minimum(a[valid],b[valid]);hi=np.maximum(a[valid],b[valid])
        enc,inv=np.unique(lo*n+hi,return_inverse=True)
        return enc//n,enc%n,np.bincount(inv,weights=weights[valid])
    aa=[];bb=[];ww=[]
    for axis in (1,2):
        a=np.take(ids,np.arange(ids.shape[axis]-1),axis=axis)
        b2=np.take(ids,np.arange(1,ids.shape[axis]),axis=axis)
        a,b2,w=pairs(a,b2,np.full(a.shape,.1))
        aa.extend(a);bb.extend(b2);ww.extend(w)
    a=np.array(aa);b2=np.array(bb)
    # Centroid distances projected on a shared face normal in full cells.
    scale=(cells[a,5]+cells[b2,5])/2
    geom=np.array(ww)/scale*2*cells[a,4]*cells[b2,4]/(cells[a,4]+cells[b2,4])*.001
    touch=(g['bottom'][1]-g['top'][0])<.001
    v0,v1,area=pairs(ids[0],ids[1],touch.astype(float)*1e-8)
    valid=area>0
    return cells,ids,a,b2,geom,v0[valid],v1[valid],area[valid]
