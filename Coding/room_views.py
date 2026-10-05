"""Normalize room up direction from doorway transforms and clip horizontal views."""
from functools import lru_cache
import numpy as np
from render_core import documents,transform


def frame(path,catalog):
    data=documents(path)
    transforms={int(d['__id']):d['Transform'] for d in data if 'Transform' in d}
    by_go={t['m_GameObject']['fileID']:fid for fid,t in transforms.items()}
    @lru_cache(None)
    def world(fid):
        t=transforms[fid];parent=t.get('m_Father',{}).get('fileID')
        return (world(parent) if parent in transforms else np.eye(4)) @ transform(t)
    doorways=[]
    for d in data:
        m=d.get('MonoBehaviour',{})
        if catalog.scripts.get(m.get('m_Script',{}).get('guid'))=='Doorway':
            fid=by_go.get(m.get('m_GameObject',{}).get('fileID'))
            if fid is not None:doorways.append(world(fid))
    if not doorways:
        return np.eye(3),[],{'up_source':'fallback Y; no Doorway transforms','original_up':[0,1,0]}
    vectors=np.array([m[:3,1]/np.linalg.norm(m[:3,1]) for m in doorways])
    # Use the consensus among doorway local-up directions, not dimensions.
    seed=vectors[np.argmax((vectors@vectors.T>.95).sum(axis=1))]
    agreeing=vectors[vectors@seed>.95]
    up=agreeing.mean(axis=0);up/=np.linalg.norm(up)
    right=np.array([1.,0,0]);right-=up*np.dot(right,up)
    if np.linalg.norm(right)<.1:
        right=np.array([0.,0,1]);right-=up*np.dot(right,up)
    right/=np.linalg.norm(right);forward=np.cross(right,up)
    rotation=np.stack([right,up,forward])
    # Geometry is translated by its room root position during extraction.
    roots=[fid for fid,t in transforms.items() if not t.get('m_Father',{}).get('fileID')]
    origin=world(roots[0])[:3,3] if roots else np.zeros(3)
    heights=[float((rotation@(m[:3,3]-origin))[1]) for m in doorways]
    return rotation,heights,{'up_source':'Doorway local-up consensus','original_up':up.round(6).tolist()}


def reorient(triangles,rotation):
    return [(t[0]@rotation.T,*t[1:]) for t in triangles]


def clip_below(triangles,height):
    output=[]
    for tri,name,mat,uv,surface in triangles:
        vertices=[(point,None if uv is None else uv[i]) for i,point in enumerate(tri)]
        polygon=[]
        for i,current in enumerate(vertices):
            previous=vertices[i-1];a,b=previous[0],current[0]
            inside_a=a[1]<=height+1e-8;inside_b=b[1]<=height+1e-8
            if inside_a!=inside_b:
                fraction=(height-a[1])/(b[1]-a[1])
                polygon.append((a+(b-a)*fraction,None if uv is None else previous[1]+(current[1]-previous[1])*fraction))
            if inside_b:polygon.append(current)
        for i in range(1,len(polygon)-1):
            indices=[0,i,i+1]
            output.append((np.array([polygon[j][0] for j in indices]),name,mat,
                           None if uv is None else np.array([polygon[j][1] for j in indices]),surface))
    return output
