"""Export blockers and decorations at 2 and 20 pixels per Unity unit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
import atexit
import numpy as np
from catalog_core import Export, FLOWS
from render_core import Assets, documents, geometry, render
from room_views import frame,reorient,clip_below


def safe(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')
    return (value or 'Object')[:100]


def signature(triangles):
    # Normalize translation; preserve dimensions, rotation, UVs and materials.
    center = np.concatenate([t[0] for t in triangles]).min(axis=0)
    digest = hashlib.sha256()
    for tri, name, material, uv, surface in triangles:
        digest.update(np.round(tri-center, 5).astype('<f8').tobytes())
        digest.update(material.encode())
        if uv is not None:
            digest.update(np.round(uv, 5).astype('<f8').tobytes())
        if surface:
            digest.update(surface['name'].encode())
            digest.update(np.asarray(surface['tint']).tobytes())
    return digest.hexdigest()[:12]


def size_in_units(triangles):
    points=np.concatenate([triangle[0] for triangle in triangles])
    if not np.isfinite(points).all():
        raise ValueError('Non-finite geometry coordinates')
    extent=np.ptp(points,axis=0)
    return {axis:round(float(value),6) for axis,value in zip(('x','y','z'),extent)}


def sizes_by_interior(report):
    grouped={interior:{'Room':[],'Decoratif':[],'Blocker':[]} for interior in FLOWS}
    seen=set()
    for row in report:
        variant=row.get('variant')
        # Unmeasurable references stay distinct; never claim a zero size.
        key=(row['interior'],row['category'],row['name'],row.get('custom_name',row['name']),
             variant or (row['source'],row['fileID']))
        if key in seen:
            continue
        seen.add(key)
        entry={row['name']:row.get('custom_name',row['name']),
               'Size':row.get('size_units')}
        if variant:
            entry['Variant']=variant
        else:
            entry['Error']=row.get('error','Geometry unavailable')
        grouped[row['interior']][row['category']].append(entry)
    for interior in grouped.values():
        for entries in interior.values():
            entries.sort(key=lambda entry:(next(iter(entry)).casefold(),entry.get('Variant','')))
    return grouped


class Progress:
    def __init__(self):
        self.done=0;self.total=0;self.phase='Indexation des assets'
        self.started=time.monotonic();self.stop=threading.Event()
        atexit.register(self.stop.set)
        # Progress is printed only after each object or room completes.
    def run(self):
        while not self.stop.wait(2):
            self.display()
    def display(self):
        ratio=self.done/self.total if self.total else 0
        bar='#'*int(ratio*20)+'-'*(20-int(ratio*20))
        count=f'{self.done}/{self.total}' if self.total else 'preparation'
        elapsed=int(time.monotonic()-self.started)
        print(f'[{bar}] {count} | {elapsed}s | {self.phase}',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--out', default='Objects_PNG')
    parser.add_argument('--translator', help='Optional room-based or grouped naming JSON')
    parser.add_argument('--room', help='Export only this in-game room name')
    parser.add_argument('--interior', choices=['Facility','Mansion','Mineshaft'], help='Export only this interior')
    parser.add_argument('--include-rooms', action='store_true', help='Export room PNGs as well as variable decorations and blockers')
    parser.add_argument('--rooms-only', action='store_true', help='Export room PNGs with fixed decorations only')
    parser.add_argument('--sizes-only', action='store_true', help='Export object and room sizes in game units without regenerating PNGs')
    args = parser.parse_args()
    root = Path(args.assets).resolve()
    if not root.is_dir():
        parser.error('Assets directory does not exist: '+str(root))
    names = json.loads(Path(args.translator).read_text(encoding='utf-8-sig')) if args.translator else {}
    progress=Progress()
    catalog, assets = Export(root), Assets(root)
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    report = []
    generated = {}
    plans=[]
    for interior, flow in FLOWS.items():
        if args.interior and interior!=args.interior:
            continue
        for path, root_id in catalog.room_list(flow):
            room=catalog.root_name(path,root_id)
            if args.room and room != args.room:
                continue
            progress.phase='Analyse '+interior+' / '+room
            _,evidence=catalog.classify(path,root_id)
            plans.append((interior,path,room,evidence))
    progress.total=len({(interior,item['category'],item.get('source',str(path)),item.get('fileID'))
                        for interior,path,room,evidence in plans for item in evidence
                        if item['category'] in ('Decorator','Blocker')})
    room_sizes=[]
    print(f'{len(plans)} salles a exporter.' if args.rooms_only else f'{progress.total} objets a traiter, puis {len(plans)} salles.',flush=True)
    for interior,path,room,evidence in plans:
        for item in (() if args.rooms_only else evidence):
            category = item['category']
            if category not in ('Decorator', 'Blocker'):
                continue
            kind = 'Blocker' if category == 'Blocker' else 'Decoratif'
            name = item['name']
            target = root/item['source'] if 'source' in item else path
            if 'fileID' in item:
                ids = [int(item['fileID'])]
            else:
                ids = [int(d['__id']) for d in documents(target)
                       if d and d.get('GameObject', {}).get('m_Name') == name]
            for fid in ids:
                key = (interior, kind, str(target), fid)
                if key in generated:
                    generated[key]['rooms'].append(room)
                    continue
                row = dict(interior=interior, category=kind, name=name,
                           source=str(target.relative_to(root)), fileID=fid,
                           rooms=[room], issues=[], images={})
                naming=names.get(interior,{})
                maps=naming[room].get(category,[]) if room in naming else naming.get(kind,[])
                custom=next((mapping[name] for mapping in maps if name in mapping),name)
                row['custom_name']=custom
                generated[key] = row
                report.append(row)
                progress.phase=interior+' / '+room+' / '+name+' : geometrie et textures'
                try:
                    triangles = geometry(target, assets, row['issues'], include_props=True,
                                         individual=True, textures=True, all_geometry=True,
                                         selected_root=fid)
                    if not triangles:
                        raise ValueError('No supported enabled mesh in this object subtree')
                    row['materials']=[dict(name=surface['name'],texture_property=surface.get('texture_property'),
                                           shader_source=surface.get('shader_source'),tint=surface['tint'].tolist())
                                      for surface in {id(t[4]):t[4] for t in triangles if t[4] is not None}.values()]
                    row['size_units']=size_in_units(triangles)
                    row['variant']=signature(triangles)
                    basename = safe(custom)+'__'+row['variant']
                    for ppm in (() if args.sizes_only else (2, 20)):
                        progress.phase=name+f" : rendu {ppm} px/unite"
                        folder = output/f'{ppm}px_par_unite'/interior/kind
                        folder.mkdir(parents=True, exist_ok=True)
                        image, meta = render(triangles, interior, ppm, None, None, False)
                        # Very thin objects can fall between pixel centers at low resolution.
                        # Keep the scale exact and report this instead of enlarging geometry.
                        if not image.getchannel('A').getbbox():
                            row['issues'].append(f'Empty top-down projection at {ppm} px/unit')
                            continue
                        destination = folder/(basename+'.png')
                        image.save(destination)
                        row['images'][str(ppm)] = str(destination.relative_to(output))
                        row.setdefault('dimensions', {})[str(ppm)] = [image.width, image.height]
                    row['status'] = ('measured' if args.sizes_only else 'failed' if not row['images'] else
                                     'partial' if row['issues'] or len(row['images']) < 2 else 'generated')
                except Exception as error:
                    row['status'] = 'partial' if row['images'] else 'failed'
                    row['error'] = str(error)
                row['issues'] = list(dict.fromkeys(row['issues']))
                progress.done+=1
                progress.display()
    # Measure room geometry separately: only fixed, active renderers.
    # Variable/blocker subtrees must not change the base room bounds.
    for index,(interior,path,room,evidence) in enumerate(plans,1):
        root_id=next(int(d['__id']) for d in documents(path)
                     if d and d.get('GameObject',{}).get('m_Name')==room)
        row=dict(interior=interior,category='Room',name=room,
                 custom_name=names.get(interior,{}).get(room,{}).get('Name',room)
                 if isinstance(names.get(interior,{}).get(room),dict) else room,
                 source=str(path.relative_to(root)),fileID=root_id,issues=[],images={})
        if 'Room' in names.get(interior,{}):
            row['custom_name']=next((mapping[room] for mapping in names[interior]['Room'] if room in mapping),room)
        try:
            triangles=geometry(path,assets,row['issues'],include_props=True,individual=True,
                               textures=not args.sizes_only,all_geometry=True,selected_root=root_id)
            # Identify all local selection descendants, including mesh children.
            data=documents(path)
            transforms={int(d['__id']):d['Transform'] for d in data if d and 'Transform' in d}
            parents={t['m_GameObject']['fileID']:transforms[t['m_Father']['fileID']]['m_GameObject']['fileID']
                     for t in transforms.values() if t.get('m_Father',{}).get('fileID') in transforms}
            variable={int(e['fileID']) for e in evidence
                      if e['category'] in ('Decorator','Blocker') and 'fileID' in e and 'source' not in e}
            def fixed(triangle):
                obj=int(triangle[1].rsplit(' @',1)[1]);seen=set()
                while obj is not None and obj not in seen:
                    if obj in variable:return False
                    seen.add(obj);obj=parents.get(obj)
                return True
            triangles=[triangle for triangle in triangles if fixed(triangle)]
            if not triangles:raise ValueError('No supported fixed room mesh')
            rotation,door_heights,orientation=frame(path,catalog)
            triangles=reorient(triangles,rotation)
            row['orientation']=orientation
            row['size_units']=size_in_units(triangles)
            row['variant']=signature(triangles)
            row['status']='measured'
            if (args.rooms_only or args.include_rooms) and not args.sizes_only:
                # Remove roofs/ceilings so they do not cover the interior.
                visible=[triangle for triangle in triangles if not re.search(
                    r'ceiling|roof',triangle[1]+' '+triangle[2],re.I)]
                if not visible:raise ValueError('No room surfaces after ceiling removal')
                basename=safe(row['custom_name'])+'__'+row['variant']
                points=np.concatenate([t[0] for t in visible])
                floor_y=min(door_heights) if door_heights else float(points[:,1].min())
                height_above_floor=float(points[:,1].max()-floor_y)
                row.update(floor_y=floor_y,height_above_floor=height_above_floor)
                views=[('sol_5u',clip_below(visible,floor_y+5))]
                if height_above_floor>15+1e-5:
                    views.append(('sol_12u',clip_below(visible,floor_y+12)))
                if height_above_floor>=9-1e-5:
                    views.append(('haut_complet',visible))
                bounds=(points[:,[0,2]].min(axis=0),points[:,[0,2]].max(axis=0))
                for view,parts in views:
                    for ppm in (2,20):
                        folder=output/f'{ppm}px_par_unite'/interior/'Room'
                        folder.mkdir(parents=True,exist_ok=True)
                        image,metadata=render(parts,interior,ppm,None,None,False,bounds_xz=bounds,vertical_lines=False)
                        if not image.getchannel('A').getbbox():
                            raise ValueError(f'Empty room projection: {view} at {ppm} px/unit')
                        destination=folder/(basename+'__'+view+'.png')
                        image.save(destination)
                        key=f'{ppm}_{view}'
                        row['images'][key]=str(destination.relative_to(output))
                        row.setdefault('dimensions',{})[key]=[image.width,image.height]
                        metadata.update(room=room,interior=interior,size_units=row['size_units'],
                                        floor_y=floor_y,view=view,orientation=orientation,
                                        cut_height=floor_y+{'sol_5u':5,'sol_12u':12}[view] if view in ('sol_5u','sol_12u') else None,
                                        composition='fixed room geometry and decorations; variable props and blockers excluded')
                        destination.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
                row['status']='partial' if row['issues'] else 'generated'
        except Exception as error:
            row['status']='partial' if row['images'] else 'failed';row['error']=str(error)
        room_sizes.append(row)
        print(f'Salle [{index}/{len(plans)}] {interior} / {room}: {row["status"]}',flush=True)
    (output/'report.json').write_text(json.dumps(dict(objects=report,rooms=room_sizes, warnings=catalog.warnings),
                                               ensure_ascii=False, indent=2), encoding='utf-8')
    sizes_path=output/'Translator_by_interior_sizes.json'
    sizes_path.write_text(json.dumps(sizes_by_interior(report+room_sizes),ensure_ascii=False,indent=2),encoding='utf-8')
    print('Sizes in game units:',sizes_path,flush=True)
    completed=room_sizes if args.rooms_only else report+room_sizes if args.include_rooms else report
    success = sum('size_units' in row if args.sizes_only else len(row['images']) >= 2 for row in completed)
    operation='measured in game units' if args.sizes_only else 'exported at both resolutions'
    label='rooms' if args.rooms_only else 'rooms and objects' if args.include_rooms else 'objects'
    print(f'{success}/{len(completed)} {label} {operation}. See {output / "report.json"}')
    progress.stop.set()
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
