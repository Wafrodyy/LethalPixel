"""AssetRipper Unity YAML -> top-down geometric pixel tiles (prototype).

No Unity installation required. Dependencies: numpy, Pillow, PyYAML.
Optional base-color texture sampling; this is not a Unity shader renderer.
"""
from pathlib import Path
import argparse
import csv
import json
import math
import re
import sys
from functools import lru_cache

import numpy as np
import yaml
from PIL import Image, ImageDraw

TILE_SCRIPT = 'dcf4d9ff07d69c36441b139533cef6ae'
BUILTIN = '0000000000000000e000000000000000'
PALETTES = {
    'Facility': ((91, 105, 101), (38, 48, 47), (139, 151, 131)),
    'Mansion': ((131, 95, 65), (58, 43, 40), (192, 156, 105)),
    'Mineshaft': ((119, 102, 75), (52, 47, 39), (164, 143, 97)),
    'Unclassified': ((105, 111, 104), (43, 48, 44), (163, 169, 151)),
}


def documents(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    text = re.sub(r'^%.*\n', '', text, flags=re.M)
    # Unity's hexadecimal buffers are unquoted; digit-only buffers must not
    # be interpreted as YAML numbers (that would discard leading zeros).
    text = re.sub(r'((?:m_Indexes|m_IndexBuffer|_typelessdata):)[ \t]*([0-9a-fA-F]+)[ \t]*$',
                  r'\1 "\2"', text, flags=re.M)
    text = re.sub(r'^--- !u!(\d+) &(-?\d+).*$',
                  r'---\n__class: \1\n__id: \2', text, flags=re.M)
    return list(yaml.safe_load_all(text))


def vec(v):
    return np.array([v.get(k, 0) for k in 'xyz'], dtype=float)


def transform(t):
    q = t['m_LocalRotation']
    x, y, z, w = (q[k] for k in 'xyzw')
    norm = math.sqrt(x*x + y*y + z*z + w*w)
    if norm == 0:
        raise ValueError('Zero quaternion')
    x, y, z, w = (v/norm for v in (x,y,z,w))
    r = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                  [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                  [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    m = np.eye(4)
    m[:3,:3] = r @ np.diag(vec(t['m_LocalScale']))
    m[:3,3] = vec(t['m_LocalPosition'])
    return m


def hex_bytes(value):
    if not isinstance(value, str):
        raise ValueError('Hex data must be a YAML string')
    return bytes.fromhex(value)


def decode_mesh(mesh, with_uv=False):
    v = mesh['m_VertexData']
    channels = v['m_Channels']
    pos = channels[0]
    count = int(v['m_VertexCount'])
    if mesh.get('m_StreamData', {}).get('size', 0):
        raise ValueError('External streamed mesh data is not supported in this prototype')
    formats = {0: ('<f4', 4), 1: ('<f2', 2), 2: ('u1', 1), 3: ('i1', 1),
               4: ('<u2', 2), 5: ('<i2', 2), 6: ('u1', 1), 7: ('i1', 1),
               8: ('<u2', 2), 9: ('<i2', 2), 10: ('<u4', 4), 11: ('<i4', 4)}
    strides = {}
    for c in channels:
        dim = c['dimension'] & 15
        if dim:
            if c['format'] not in formats:
                raise ValueError('Unsupported vertex channel format')
            end = c['offset'] + dim * formats[c['format']][1]
            strides[c['stream']] = max(strides.get(c['stream'], 0), end)
    offset = 0
    for s in sorted(strides):
        if s == pos['stream']:
            break
        offset += count * strides[s]
        offset = (offset + 15) // 16 * 16
    if pos['format'] not in (0, 1) or (pos['dimension'] & 15) != 3:
        raise ValueError('Only float32/float16 XYZ positions are supported')
    dtype, size = formats[pos['format']]
    verts = np.ndarray((count, 3), dtype=dtype,
                       buffer=hex_bytes(v['_typelessdata']),
                       offset=offset+pos['offset'],
                       strides=(strides[pos['stream']], size)).astype(float)
    raw = hex_bytes(mesh['m_IndexBuffer'])
    dtype = '<u4' if mesh.get('m_IndexFormat', 0) else '<u2'
    size = np.dtype(dtype).itemsize
    groups = []
    for slot, sm in enumerate(mesh['m_SubMeshes']):
        if sm['topology'] != 0:
            raise ValueError('Non-triangle submesh is not supported')
        start = sm['firstByte']
        indices = np.frombuffer(raw[start:start+sm['indexCount']*size], dtype=dtype)
        indices = indices.astype(np.int64) + sm.get('baseVertex', 0)
        if len(indices) % 3 or (len(indices) and indices.max() >= len(verts)):
            raise ValueError('Invalid triangle indices')
        groups.append((slot, indices.reshape(-1,3)))
    uv = None
    if with_uv and len(channels)>4:
        channel=channels[4]
        if channel['dimension'] & 15:
            if channel['format'] not in (0,1):
                raise ValueError('Unsupported UV format')
            uv_offset=0
            for stream in sorted(strides):
                if stream==channel['stream']:
                    break
                uv_offset=(uv_offset+count*strides[stream]+15)//16*16
            uv_dtype,uv_size=formats[channel['format']]
            uv=np.ndarray((count,2),dtype=uv_dtype,buffer=hex_bytes(v['_typelessdata']),
                          offset=uv_offset+channel['offset'],
                          strides=(strides[channel['stream']],uv_size)).astype(float)
    return (verts,groups,uv) if with_uv else (verts,groups)


def cube():
    v = np.array([[-.5,-.5,-.5],[.5,-.5,-.5],[.5,-.5,.5],[-.5,-.5,.5],
                  [-.5,.5,-.5],[.5,.5,-.5],[.5,.5,.5],[-.5,.5,.5]])
    f = np.array([[4,7,6],[4,6,5],[0,1,2],[0,2,3],
                  [0,4,5],[0,5,1],[1,5,6],[1,6,2],
                  [2,6,7],[2,7,3],[3,7,4],[3,4,0]])
    return v, [(0, f)], v[:,[0,2]]+.5


class Assets:
    def __init__(self, root):
        self.root = root
        self.guids = {}
        print('Indexing .meta files...', flush=True)
        for p in root.rglob('*.meta'):
            match = re.search(r'^guid:\s*([0-9a-f]{32})', p.read_text(errors='replace'), re.M)
            if match:
                self.guids[match[1]] = p.with_suffix('')

    @lru_cache(maxsize=128)
    def mesh(self, guid, fid):
        if guid == BUILTIN and fid == 10202:
            return cube()
        path = self.guids.get(guid)
        if not path:
            raise ValueError('Missing mesh GUID ' + str(guid))
        for d in documents(path):
            if d and 'Mesh' in d and int(d['__id']) == int(fid):
                return decode_mesh(d['Mesh'],with_uv=True)
        raise ValueError('Mesh object not found: ' + str(path))

    @lru_cache(maxsize=512)
    def material_name(self, guid):
        path = self.guids.get(guid)
        return path.stem if path else ''

    @lru_cache(maxsize=128)
    def texture(self,guid):
        path=self.guids.get(guid)
        if not path:
            raise ValueError('Missing texture GUID '+str(guid))
        # AssetRipper sometimes places image bytes next to the serialized asset.
        choices=[path]+[path.with_suffix(s) for s in ['.png','.tga','.jpg','.jpeg','.bmp','.dds']]
        pixels=None
        for candidate in choices:
            if candidate.is_file():
                try:
                    with Image.open(candidate) as im:
                        pixels=np.array(im.convert('RGBA'))
                    break
                except Exception:
                    pass
        if pixels is None:
            raise ValueError('Texture has no readable exported image: '+str(path))
        wraps=(0,0)
        meta=Path(str(path)+'.meta')
        if meta.exists():
            try:
                data=yaml.safe_load(meta.read_text(encoding='utf-8-sig'))
                settings=data.get('TextureImporter',{}).get('textureSettings',{})
                wraps=(int(settings.get('wrapU',settings.get('wrapMode',0))),
                       int(settings.get('wrapV',settings.get('wrapMode',0))))
            except Exception:
                pass
        return pixels,wraps

    @lru_cache(maxsize=256)
    def material(self,guid):
        path=self.guids.get(guid)
        if not path:
            raise ValueError('Missing material GUID '+str(guid))
        data=next((d['Material'] for d in documents(path) if d and 'Material' in d),None)
        if data is None:
            raise ValueError('Material YAML not found: '+str(path))
        saved=data.get('m_SavedProperties',{})
        def pairs(key):
            result={}
            entries=saved.get(key,[]) or []
            if isinstance(entries,dict):
                return entries
            for entry in entries:
                if 'first' in entry and 'second' in entry:
                    result[entry['first']]=entry['second']
                else:
                    result.update(entry)
            return result
        texs,colors,floats=pairs('m_TexEnvs'),pairs('m_Colors'),pairs('m_Floats')
        selected=None
        shader_path=self.guids.get(data.get('m_Shader',{}).get('guid'))
        shader_text=shader_path.read_text(errors='replace') if shader_path and shader_path.suffix=='.shader' else ''
        candidates=['_BaseColorMap','_BaseMap','_MainTex','_AlbedoMap','_DiffuseMap']
        declared=[key for key in candidates if re.search(r'\b'+re.escape(key)+r'\s*\(',shader_text)]
        if declared:
            candidates=declared
        for key in candidates:
            if texs.get(key,{}).get('m_Texture',{}).get('fileID'):
                selected=key
                break
        texture=None;scale=(1,1);offset=(0,0)
        if selected:
            t=texs[selected]
            texture=self.texture(t['m_Texture'].get('guid'))
            scale=tuple(t.get('m_Scale',{'x':1,'y':1}).get(k,1) for k in 'xy')
            offset=tuple(t.get('m_Offset',{}).get(k,0) for k in 'xy')
        elif any(t.get('m_Texture',{}).get('fileID') for t in texs.values()):
            raise ValueError('No recognized base-color texture property; material sample needed: '+str(path))
        color=colors.get('_BaseColor',colors.get('_Color',{'r':1,'g':1,'b':1,'a':1}))
        if selected == '_MainTex':
            color=colors.get('_Color',color)
        tint=np.array([color.get(k,1) for k in 'rgba'],dtype=float)
        material_notes=[]
        keywords=str(data.get('m_ShaderKeywords',''))+' '+str(data.get('m_ValidKeywords',''))
        cutout=('ALPHATEST' in keywords or floats.get('_AlphaClip',0)>0 or floats.get('_AlphaCutoffEnable',0)>0)
        cutoff=float(floats.get('_Cutoff',floats.get('_AlphaCutoff',.5))) if cutout else 0
        # RGB is used as exported (unlit), without reproducing shader lighting.
        unsupported=[]
        for key,value in texs.items():
            if key!=selected and value.get('m_Texture',{}).get('fileID'):
                unsupported.append(key)
        return {'texture':texture,'scale':scale,'offset':offset,'tint':tint,
                'cutoff':cutoff,'name':data.get('m_Name',path.stem),
                'texture_property':selected,'shader_source':str(shader_path) if shader_path else None,
                'ignored_texture_slots':unsupported,'notes':material_notes,
                'transparent':floats.get('_Surface',floats.get('_SurfaceType',0))>0 or floats.get('_Mode',0)>=2}


def geometry(path, assets, issues, include_props=False, individual=False, textures=False, all_geometry=False, selected_root=None):
    docs = documents(path)
    objects, transforms, filters, renderers, builders = {}, {}, {}, {}, {}
    tile_root = None
    collider_objects=set()
    for d in docs:
        if not d:
            continue
        for kind, dest in [('GameObject',objects),('Transform',transforms)]:
            if kind in d:
                dest[int(d['__id'])] = d[kind]
        for kind, dest in [('MeshFilter',filters),('MeshRenderer',renderers)]:
            if kind in d:
                data = d[kind]
                dest[data['m_GameObject']['fileID']] = data
        for collider_kind in ('BoxCollider','MeshCollider','CapsuleCollider','SphereCollider'):
            if collider_kind in d:
                collider_objects.add(d[collider_kind]['m_GameObject']['fileID'])
        m = d.get('MonoBehaviour', {})
        if 'm_Positions' in m and 'm_Faces' in m:
            builders[m['m_GameObject']['fileID']] = m
        if m.get('m_Script',{}).get('guid') == TILE_SCRIPT:
            tile_root = m['m_GameObject']['fileID']
        if 'PrefabInstance' in d:
            issues.append('Nested PrefabInstance unsupported; its geometry may be missing')
    if selected_root is not None:
        tile_root = int(selected_root)
    if tile_root is None:
        return None
    by_go = {t['m_GameObject']['fileID']: k for k,t in transforms.items()}
    root_tid = by_go[tile_root]

    @lru_cache(None)
    def state(tid):
        t = transforms[tid]
        go = objects[t['m_GameObject']['fileID']]
        name = go['m_Name']
        active = bool(go.get('m_IsActive',1))
        if tid == root_tid:
            if selected_root is not None:
                matrix = transform(t)
                parent=t.get('m_Father',{}).get('fileID')
                seen={tid}
                while parent in transforms and parent not in seen:
                    seen.add(parent)
                    matrix=transform(transforms[parent]) @ matrix
                    parent=transforms[parent].get('m_Father',{}).get('fileID')
                matrix[:3,3] = 0
                return matrix, True, [name]
            return np.eye(4), active, [name]
        parent = t['m_Father']['fileID']
        if parent and parent in transforms:
            m, a, names = state(parent)
            return m @ transform(t), active and a, names+[name]
        return transform(t), active, [name]

    triangles = []
    for goid, renderer in renderers.items():
        if not renderer.get('m_Enabled',1) or goid not in by_go:
            continue
        if selected_root is not None:
            ancestor = by_go[goid]
            seen = set()
            while ancestor in transforms and ancestor not in seen and ancestor != root_tid:
                seen.add(ancestor)
                ancestor = transforms[ancestor].get("m_Father", {}).get("fileID")
            if ancestor != root_tid:
                continue
        mesh_ref=filters.get(goid,{}).get('m_Mesh',{})
        materials=renderer.get('m_Materials',[])
        if (selected_root is not None and goid in collider_objects
                and mesh_ref.get('guid')==BUILTIN and mesh_ref.get('fileID')==10202
                and materials and all(m.get('guid')=='fa4e1aa64020e9d4e8bed6e2a7efe4ab' for m in materials)):
            issues.append(objects[goid]['m_Name']+': default-material primitive collider excluded')
            continue
        matrix, active, names = state(by_go[goid])
        if not active:
            continue
        if selected_root is not None and any(re.fullmatch(
                r'(?:nav|navmesh|navigation|mapradar|radarbox|colliders?|collision|killtrigger)(?: \(\d+\))?',n,re.I) for n in names):
            issues.append('/'.join(names)+': technical geometry excluded')
            continue
        if selected_root is not None and any(re.search(r'collider|trigger|radarbox',n,re.I) for n in names):
            issues.append('/'.join(names)+': technical collider/trigger/radar geometry excluded')
            continue
        label = '/'.join(names).lower()
        excluded = ['mapradar', 'killtrigger', 'colliders', 'ceiling', 'roof',
                    'particle', 'volumetric', 'fog', 'steam', 'gascloud']
        if not include_props:
            excluded += ['hanginglight','lamponbars','pipes','storageshelf']
        if not individual and not all_geometry and any(n in label for n in excluded):
            continue
        if individual:
            label = '/'.join(names) + ' @' + str(goid)
        try:
            if goid in builders:
                b = builders[goid]
                vertices = np.array([vec(p) for p in b['m_Positions']])
                uv_data=b.get('m_Textures0',[])
                uv=np.array([[p.get('x',0),p.get('y',0)] for p in uv_data]) if len(uv_data)==len(vertices) else None
                groups = [(f.get('m_SubmeshIndex',0), np.frombuffer(
                    hex_bytes(f['m_Indexes']),dtype='<i4').reshape(-1,3)) for f in b['m_Faces']]
            else:
                ref = filters.get(goid,{}).get('m_Mesh',{})
                if not ref.get('fileID'):
                    continue
                vertices, groups, uv = assets.mesh(ref.get('guid'), ref['fileID'])
            world = vertices @ matrix[:3,:3].T + matrix[:3,3]
            mats = renderer.get('m_Materials',[])
            for slot, faces in groups:
                if np.linalg.det(matrix[:3,:3]) < 0:
                    faces = faces[:,[0,2,1]]
                mat = assets.material_name(mats[slot].get('guid')) if slot < len(mats) else ''
                material_guid=mats[slot].get('guid') if slot<len(mats) else None
                if selected_root is not None and (material_guid=='10d929035e8e45449bf86a0a55edb0fe'
                        or re.search(r'trigger|collider|navmesh|radar',mat,re.I)):
                    issues.append('/'.join(names)+': technical material excluded: '+(mat or str(material_guid)))
                    continue

                # Also skip ceiling faces within combined ProBuilder meshes.
                if not individual and not all_geometry and re.search(r'ceiling|roof|fog|steam|particle',mat,re.I):
                    continue
                surface=None
                if textures:
                    try:
                        if slot>=len(mats) or not mats[slot].get('fileID'):
                            raise ValueError('No material in slot '+str(slot))
                        surface=assets.material(mats[slot].get('guid'))
                        if surface['texture'] is not None and uv is None:
                            raise ValueError('Mesh has no UV0 coordinates')
                        for note in surface.get('notes',[]):
                            issues.append('/'.join(names)+': '+note)
                        if surface['ignored_texture_slots']:
                            issues.append('/'.join(names)+': only base-color texture rendered; ignored '+','.join(surface['ignored_texture_slots']))
                        if surface['transparent']:
                            issues.append('/'.join(names)+': translucent shader approximated as alpha cutout')
                    except Exception as e:
                        issues.append('/'.join(names)+': texture fallback: '+str(e))
                        surface=None
                for face in faces:
                    triangles.append((world[face], label, mat.lower(),uv[face] if uv is not None else None,surface))
        except Exception as e:
            issues.append('/'.join(names) + ': ' + str(e))
    return triangles


def render(triangles, palette, ppm, max_y, min_y, outline, bounds_xz=None, vertical_lines=True):
    selected = []
    vertical = []
    floor, wall, detail = PALETTES[palette]
    for item in triangles:
        tri,name,mat=item[:3]
        uv,surface=item[3:] if len(item)>3 else (None,None)
        if max_y is not None and np.max(tri[:,1]) > max_y:
            continue
        if min_y is not None and np.min(tri[:,1]) < min_y:
            continue
        normal = np.cross(tri[1]-tri[0],tri[2]-tri[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-10:
            continue
        normal /= norm
        # Discard downward-facing surfaces. Mirrored transform winding is
        # corrected during geometry extraction.
        if normal[1] < -.05:
            continue
        color = floor
        if re.search(r'wall|railing|bars',(mat+' '+name).lower()):
            color = wall
        elif re.search(r'stair|catwalk|wood|metal|grate',(mat+' '+name).lower()):
            color = detail
        if abs(normal[1]) < .05:
            if vertical_lines:
                vertical.append((tri,color,normal[1],uv,surface))
            elif normal[1]>1e-6:
                selected.append((tri,color,normal[1],uv,surface))
        else:
            selected.append((tri, color, normal[1],uv,surface))
    if not selected and not vertical:
        raise ValueError('No visible triangles; check missing meshes and height limits')
    points = np.concatenate([t[0] for t in selected+vertical])
    bounds=bounds_xz if bounds_xz is not None else (points[:,[0,2]].min(axis=0),points[:,[0,2]].max(axis=0))
    lo = np.floor(np.asarray(bounds[0])*ppm).astype(int)-1
    hi = np.ceil(np.asarray(bounds[1])*ppm).astype(int)+1
    width, height = (hi-lo).tolist()
    if width*height > 16000000:
        raise ValueError('Image too large; check scale or accidental geometry')
    pixels = np.zeros((height,width,4),dtype=np.uint8)
    depth = np.full((height,width),-np.inf)
    for tri, color, ny, uv, surface in selected:
        px = tri[:,0]*ppm-lo[0]
        py = hi[1]-tri[:,2]*ppm
        x0,x1 = max(0,math.floor(px.min())),min(width,math.ceil(px.max()))
        y0,y1 = max(0,math.floor(py.min())),min(height,math.ceil(py.max()))
        if x0>=x1 or y0>=y1:
            continue
        xx,yy = np.meshgrid(np.arange(x0,x1)+.5,np.arange(y0,y1)+.5)
        den = (py[1]-py[2])*(px[0]-px[2])+(px[2]-px[1])*(py[0]-py[2])
        if abs(den)<1e-12:
            continue
        a = ((py[1]-py[2])*(xx-px[2])+(px[2]-px[1])*(yy-py[2]))/den
        b = ((py[2]-py[0])*(xx-px[2])+(px[0]-px[2])*(yy-py[2]))/den
        c = 1-a-b
        z = a*tri[0,1]+b*tri[1,1]+c*tri[2,1]
        target = depth[y0:y1,x0:x1]
        hit = (a>=-1e-8)&(b>=-1e-8)&(c>=-1e-8)&(z>target)
        shade = .78+.22*max(0,ny)
        if surface is None:
            rgba=np.empty(xx.shape+(4,),dtype=np.uint8)
            rgba[:]=[int(v*shade) for v in color]+[255]
        else:
            rgba=np.full(xx.shape+(4,),255,dtype=float)
            if surface['texture'] is not None and uv is not None:
                coords=a[:,:,None]*uv[0]+b[:,:,None]*uv[1]+c[:,:,None]*uv[2]
                coords=coords*np.array(surface['scale'])+np.array(surface['offset'])
                tex,wraps=surface['texture']
                for axis,wrap in enumerate(wraps):
                    if wrap==1:
                        coords[:,:,axis]=np.clip(coords[:,:,axis],0,1)
                    elif wrap in (2,3):
                        value=np.abs(coords[:,:,axis]) if wrap==3 else coords[:,:,axis]
                        coords[:,:,axis]=1-np.abs(np.mod(value,2)-1)
                        if wrap==3:
                            coords[:,:,axis]=np.clip(value,0,1)
                    else:
                        coords[:,:,axis]=np.mod(coords[:,:,axis],1)
                tx=np.clip(np.floor(coords[:,:,0]*tex.shape[1]).astype(int),0,tex.shape[1]-1)
                ty=np.clip(tex.shape[0]-1-np.floor(coords[:,:,1]*tex.shape[0]).astype(int),0,tex.shape[0]-1)
                rgba=tex[ty,tx].astype(float)
            rgba=np.clip(rgba*surface['tint'],0,255).astype(np.uint8)
            # Cutout holes do not write depth, allowing the lower floor to show.
            threshold=max(surface['cutoff'],.5 if surface['transparent'] else 0)
            hit &= (rgba[:,:,3]>0)&(rgba[:,:,3]>=threshold*255)
            rgba[:,:,3]=255
        target[hit]=z[hit]
        pixels[y0:y1,x0:x1][hit] = rgba[hit]
    # Vertical surfaces have zero projected area. Draw their longest XZ
    # segment at one pixel minimum thickness at either export resolution.
    for tri,color,ny,uv,surface in vertical:
        xz=tri[:,[0,2]]
        distances=np.sum((xz[:,None]-xz[None,:])**2,axis=2)
        first,last=np.unravel_index(np.argmax(distances),distances.shape)
        if distances[first,last]<1e-12:
            continue
        endpoints=xz[[first,last]].copy()*ppm
        for axis in (0,1):
            if abs(endpoints[0,axis]-endpoints[1,axis])<.01:
                endpoints[:,axis]=endpoints[:,axis].mean()
        coords=[(int(math.floor(q[0]-lo[0])),int(math.floor(hi[1]-q[1]))) for q in endpoints]
        mask_image=Image.new('L',(width,height))
        ImageDraw.Draw(mask_image).line(coords,fill=255,width=1)
        mask=np.asarray(mask_image)>0
        elevation=float(tri[:,1].max())
        mask &= elevation>=depth-1e-8
        rgba=np.array(list(color)+[255],dtype=float)
        if surface is not None:
            rgba=np.full(4,255.,dtype=float)
            if surface['texture'] is not None and uv is not None:
                point=uv.mean(axis=0)*np.asarray(surface['scale'])+np.asarray(surface['offset'])
                tex,wraps=surface['texture']
                for axis,wrap in enumerate(wraps):
                    if wrap==1:
                        point[axis]=np.clip(point[axis],0,1)
                    elif wrap==2:
                        point[axis]=1-abs(point[axis]%2-1)
                    elif wrap==3:
                        point[axis]=np.clip(abs(point[axis]),0,1)
                    else:
                        point[axis]%=1
                tx=int(np.clip(math.floor(point[0]*tex.shape[1]),0,tex.shape[1]-1))
                ty=int(np.clip(tex.shape[0]-1-math.floor(point[1]*tex.shape[0]),0,tex.shape[0]-1))
                rgba=tex[ty,tx].astype(float)
            rgba=np.clip(rgba*surface['tint'],0,255)
            threshold=max(surface['cutoff'],.5 if surface['transparent'] else 0)
            if rgba[3]<=0 or rgba[3]<threshold*255:
                continue
        rgba[3]=255
        pixels[mask]=rgba.astype(np.uint8)
        depth[mask]=elevation
    if outline:
        mask = pixels[:,:,3]>0
        interior = mask.copy()
        for dy,dx in [(0,1),(0,-1),(1,0),(-1,0)]:
            interior &= np.roll(mask,(dy,dx),(0,1))
        pixels[mask & ~interior,:3] = wall
    return Image.fromarray(pixels), {
        'pixels_per_unit':ppm, 'origin_x':lo[0]/ppm, 'top_z':hi[1]/ppm,
        'width_px':width,'height_px':height, 'padding_px':1,
        'orientation':'X right, Z up', 'min_y':min_y, 'max_y':max_y,
        'visible_triangles':len(selected),
        'vertical_triangles_as_lines':len(vertical),
        'textured_triangles':sum(s[4] is not None and s[4]['texture'] is not None for s in selected),
        'material_color_triangles':sum(s[4] is not None for s in selected),
        'rendering':'base color unlit; alpha cutout; nearest texture sampling',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',required=True,help='ExportedProject/Assets directory')
    parser.add_argument('--out',default='generated_tiles')
    parser.add_argument('--tile',help='Prefab filename or name (one tile only)')
    parser.add_argument('--pixels-per-unit',type=float,default=2)
    parser.add_argument('--palette',choices=PALETTES,default='Facility')
    parser.add_argument('--max-y',type=float,help='Drop triangles above this local height (ceiling removal)')
    parser.add_argument('--min-y',type=float)
    parser.add_argument('--include-props',action='store_true')
    parser.add_argument('--objects',action='store_true',help='Export each enabled mesh object separately, including decorations')
    parser.add_argument('--textures',action='store_true',help='Sample exported base-color textures with UV0 coordinates')
    parser.add_argument('--all-geometry',action='store_true',help='Disable name/material exclusions, including ceiling removal')
    parser.add_argument('--no-outline',action='store_true')
    parser.add_argument('--preview-scale',type=int,default=8)
    args = parser.parse_args()
    root = Path(args.assets).resolve()
    if not root.is_dir():
        parser.error('Assets directory does not exist: '+str(root))
    if args.pixels_per_unit<=0 or args.preview_scale<1:
        parser.error('Scale must be positive')
    assets = Assets(root)
    output = Path(args.out)
    output.mkdir(parents=True,exist_ok=True)
    candidates = sorted((root/'GameObject').rglob('*.prefab'))
    if args.tile:
        candidates = [p for p in candidates if p.name==args.tile or p.stem==args.tile]
        if not candidates:
            parser.error('Prefab not found: '+args.tile)
    report=[]
    names=set()
    for i,path in enumerate(candidates,1):
        # Cheap prefilter: avoid fully parsing unrelated prefabs.
        if TILE_SCRIPT not in path.read_text(encoding='utf-8-sig',errors='replace'):
            continue
        print(f'[{i}/{len(candidates)}] {path.name}',flush=True)
        issues=[]
        entry={'prefab':str(path.relative_to(root)),'issues':issues}
        try:
            tris=geometry(path,assets,issues,args.include_props,args.objects,args.textures,args.all_geometry)
            issues[:]=list(dict.fromkeys(issues))
            if tris is None:
                continue
            if args.objects:
                folder=output/path.stem
                folder.mkdir(parents=True,exist_ok=True)
                groups={}
                for triangle in tris:
                    groups.setdefault(triangle[1],[]).append(triangle)
                objects=[]
                for object_path,parts in groups.items():
                    object_name,object_id=object_path.rsplit(' @',1)
                    basename=re.sub(r'[^A-Za-z0-9._-]+','_',object_name.rsplit('/',1)[-1]).strip('_') or 'Object'
                    basename+='_'+object_id
                    obj={'object_path':object_name,'game_object_id':object_id}
                    try:
                        im,metadata=render(parts,args.palette,args.pixels_per_unit,
                                           args.max_y,args.min_y,not args.no_outline)
                        im.save(folder/(basename+'.png'))
                        im.resize((im.width*args.preview_scale,im.height*args.preview_scale),
                                  Image.Resampling.NEAREST).save(folder/(basename+'_preview.png'))
                        metadata.update(obj)
                        metadata['coordinate_space']='tile root; placement and orientation retained'
                        metadata['palette']=args.palette
                        (folder/(basename+'.json')).write_text(json.dumps(metadata,indent=2),encoding='utf-8')
                        obj.update(status='generated',image=basename+'.png',width=im.width,height=im.height)
                    except Exception as e:
                        obj.update(status='failed',error=str(e))
                    objects.append(obj)
                (folder/'objects.json').write_text(json.dumps(objects,indent=2),encoding='utf-8')
                generated=sum(o['status']=='generated' for o in objects)
                entry.update(status=('failed' if not generated else 'partial' if issues or generated<len(objects) else 'generated'),
                             objects_generated=generated,objects_total=len(objects),folder=path.stem)
                report.append(entry)
                print(f'  {generated}/{len(objects)} mesh objects exported',flush=True)
                continue
            image,meta=render(tris,args.palette,args.pixels_per_unit,
                              args.max_y,args.min_y,not args.no_outline)
            name=path.stem
            if name in names:
                name+='_'+str(i)
            names.add(name)
            image.save(output/(name+'.png'))
            image.resize((image.width*args.preview_scale,image.height*args.preview_scale),
                         Image.Resampling.NEAREST).save(output/(name+'_preview.png'))
            meta.update(entry)
            meta['palette']=args.palette
            meta['status']='partial' if issues else 'generated'
            (output/(name+'.json')).write_text(json.dumps(meta,indent=2),encoding='utf-8')
            entry.update(status=meta['status'],image=name+'.png',width=image.width,height=image.height)
        except Exception as e:
            entry.update(status='failed',error=str(e))
            print('  Failed: '+str(e),flush=True)
        report.append(entry)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    ok=sum(r['status']!='failed' for r in report)
    print(f'{ok}/{len(report)} tiles generated. See {output / "report.json"}')
    if not report or not ok:
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
