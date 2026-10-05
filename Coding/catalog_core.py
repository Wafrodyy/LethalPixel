"""Build editable naming dictionaries from AssetRipper V73 YAML."""
import argparse
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re
import tempfile
import os
import yaml

LOADER=getattr(yaml,'CSafeLoader',yaml.SafeLoader)
CATEGORIES=['Permanent','Decorator','Blocker']
FLOWS={'Facility':'Level1Flow','Mansion':'Level2Flow','Mineshaft':'Level3Flow'}


def refs(value):
    if isinstance(value,dict):
        if 'fileID' in value:
            if value['fileID']:
                yield value
        else:
            for v in value.values():
                yield from refs(v)
    elif isinstance(value,list):
        for v in value:
            yield from refs(v)


@lru_cache(maxsize=12)
def docs(path):
    t=Path(path).read_text(encoding='utf-8-sig')
    t=re.sub(r'^%.*\n','',t,flags=re.M)
    t=re.sub(r'((?:m_Indexes|m_IndexBuffer|_typelessdata):)[ \t]*([0-9a-fA-F]+)[ \t]*$',r'\1 "\2"',t,flags=re.M)
    t=re.sub(r'^--- !u!(\d+) &(-?\d+).*$',r'---\n__class: \1\n__id: \2',t,flags=re.M)
    return [d for d in yaml.load_all(t,Loader=LOADER) if d]


def safe_name(name):
    # Folder names use the original name except characters illegal on Windows.
    out=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).rstrip(' .') or 'Unnamed'
    if out.upper() in ['CON','PRN','AUX','NUL']+[f'{p}{i}' for p in ['COM','LPT'] for i in range(1,10)]:
        out='_'+out
    return out


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    # Atomic write keeps a failed update from truncating a naming file.
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,indent=2,ensure_ascii=False)
            f.write('\n')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_names(path):
    if not path.exists():
        return {}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in data.items()):
        raise ValueError(f'{path}: expected an object mapping strings to strings; file not modified')
    return data


class Export:
    def __init__(self,root):
        self.root=root
        self.index={}
        self.scripts={}
        self.warnings=[]
        for folder in ['MonoBehaviour','GameObject','Scripts']:
            for meta in sorted((root/folder).rglob('*.meta')):
                m=re.search(r'^guid:\s*([0-9a-f]{32})',meta.read_text(errors='replace'),re.M)
                if not m:
                    continue
                p=meta.with_suffix('')
                if not p.is_file():
                    continue
                guid=m.group(1)
                if guid in self.index and self.index[guid]!=p:
                    raise ValueError('Duplicate GUID '+guid)
                self.index[guid]=p
                if p.suffix=='.cs':
                    self.scripts[guid]=p.stem

    def resolve(self,ref):
        p=self.index.get(ref.get('guid'))
        if not p:
            self.warnings.append('Missing reference '+str(ref))
        return p

    def room_list(self,prefix):
        roots=sorted(p for p in (self.root/'MonoBehaviour').rglob('*.asset') if p.stem.startswith(prefix))
        if not roots:
            raise ValueError('No flow found: '+prefix)
        visited=set();rooms={};stack=roots[:]
        while stack:
            p=stack.pop()
            if p in visited:
                continue
            visited.add(p)
            for d in docs(p):
                m=d.get('MonoBehaviour',{})
                if 'TileWeights' in m:
                    for entry in m['TileWeights'].get('Weights',[]):
                        ref=entry.get('Value',{})
                        if ref.get('guid'):
                            target=self.resolve(ref)
                            if target:
                                rooms[(ref['guid'],ref['fileID'])]=(target,ref['fileID'])
                else:
                    for key in ['Nodes','Lines','TileSets','BranchCapTileSets','DungeonArchetypes','TileInjectionRules']:
                        for ref in refs(m.get(key,[])):
                            if not ref.get('guid'):
                                continue
                            target=self.resolve(ref)
                            if target and target.suffix=='.asset':
                                stack.append(target)
        return sorted(rooms.values(),key=lambda x:(x[0].name,x[1]))

    def root_name(self,path,fid):
        for d in docs(path):
            if int(d['__id'])==int(fid) and 'GameObject' in d:
                return d['GameObject']['m_Name']
        raise ValueError(f'GameObject {fid} missing in {path}')

    def classify(self,path,root_id):
        data=docs(path)
        go={int(d['__id']):d['GameObject'] for d in data if 'GameObject' in d}
        components=[d['MonoBehaviour'] for d in data if 'MonoBehaviour' in d]
        transforms={int(d['__id']):d['Transform'] for d in data if 'Transform' in d}
        parents={t['m_GameObject']['fileID']:transforms[t['m_Father']['fileID']]['m_GameObject']['fileID']
                 for t in transforms.values() if t.get('m_Father',{}).get('fileID') in transforms}
        children=defaultdict(list)
        for child,parent in parents.items():
            children[parent].append(child)
        categories={c:set() for c in CATEGORIES}
        tagged={};evidence=[]
        scope=set()
        def descendants(obj):
            result={obj};todo=[obj]
            while todo:
                for child in children[todo.pop()]:
                    if child not in result:
                        result.add(child);todo.append(child)
            return result
        scope=descendants(root_id)

        def technical(obj):
            seen=set()
            while obj in go and obj not in seen:
                seen.add(obj)
                label=go[obj]['m_Name'].lower()
                if label in ('navmesh','mapradar') or re.fullmatch(r'(?:navmesh|mapradar) \(\d+\)',label):
                    return True
                obj=parents.get(obj)
            return False

        def active(obj):
            seen=set()
            while obj in go and obj not in seen:
                seen.add(obj)
                if not go[obj].get('m_IsActive',1):
                    return False
                obj=parents.get(obj)
            return True

        def add(ref,category,reason):
            if not ref.get('fileID'):
                return
            if ref.get('guid'):
                target=self.resolve(ref)
                if target:
                    try:
                        name=self.root_name(target,ref['fileID'])
                        categories[category].add(name)
                        evidence.append({'name':name,'category':category,'reason':reason,'source':str(target.relative_to(self.root)), 'fileID':int(ref['fileID'])})
                    except ValueError as error:
                        self.warnings.append(str(error))
            else:
                obj=ref['fileID']
                if obj in go:
                    # Blocker takes precedence over general variable props.
                    for descendant in descendants(obj):
                        if tagged.get(descendant)!='Blocker':
                            tagged[descendant]=category
                    categories[category].add(go[obj]['m_Name'])
                    evidence.append({'name':go[obj]['m_Name'],'category':category,'reason':reason,'fileID':obj})

        for m in components:
            obj=m.get('m_GameObject',{}).get('fileID')
            if obj not in scope:
                continue
            script=self.scripts.get(m.get('m_Script',{}).get('guid'),'')
            if script=='LocalPropSet':
                for entry in m.get('Props',{}).get('Weights',[]):
                    add(entry.get('Value',{}),'Decorator','LocalPropSet selection')
            elif script=='GlobalProp':
                add({'fileID':obj},'Decorator','GlobalProp selection')
            elif script=='ConditionalOnTwoObjects':
                add({'fileID':obj},'Decorator','ConditionalOnTwoObjects')
            elif script=='RandomMapObject':
                for ref in m.get('spawnablePrefabs',[]):
                    add(ref,'Decorator','RandomMapObject spawnable prefab')
            elif script=='Doorway':
                for key in ['BlockerPrefabWeights','BlockerSceneObjects','blockerPrefabs_obsolete']:
                    for ref in refs(m.get(key,[])):
                        add(ref,'Blocker','Doorway '+key)
                for key in ['ConnectorPrefabWeights','ConnectorSceneObjects','doorPrefabs_obsolete']:
                    for ref in refs(m.get(key,[])):
                        add(ref,'Decorator','Doorway conditional connector: '+key)

        def under_props(obj):
            seen=set()
            while obj in go and obj not in seen:
                seen.add(obj)
                if re.fullmatch(r'Props(?: \(\d+\))?',go[obj]['m_Name'],re.I):
                    return True
                obj=parents.get(obj)
            return False

        def permanent_root(obj):
            # Keep a logical decoration name instead of its anonymous mesh
            # child when it sits under a grouping object such as Props/Lights.
            current=obj;seen=set()
            while current in go and current not in seen:
                seen.add(current)
                parent=parents.get(current)
                if parent not in go or parent==root_id:
                    return current
                if re.fullmatch(r'(?:Props|Lights|Decorations)(?: \(\d+\))?',go[parent]['m_Name'],re.I):
                    return current
                current=parent
            return obj

        # Include visible mesh objects, including inactive variant children.
        # The naming catalog describes possible contents, not one seed's scene.
        for d in data:
            renderer=d.get('MeshRenderer',d.get('SkinnedMeshRenderer'))
            if renderer is None:
                continue
            obj=renderer['m_GameObject']['fileID']
            if obj not in scope or obj==root_id:
                continue
            if technical(obj):
                evidence.append({'name':go[obj]['m_Name'],'category':'Excluded',
                                 'reason':'technical NavMesh or MapRadar subtree', 'fileID':obj})
                continue
            category=tagged.get(obj,'Permanent')
            # Avoid listing every mesh child of a variable prop; the named
            # selection root is the logical decorator/blocker already listed.
            if obj in tagged:
                continue
            if not active(obj) or not renderer.get('m_Enabled',1):
                evidence.append({'name':go[obj]['m_Name'],'category':'Excluded',
                                 'reason':'inactive object or disabled renderer with no known selection rule',
                                 'fileID':obj})
                continue
            logical_obj=permanent_root(obj) if category=='Permanent' and under_props(obj) else obj
            categories[category].add(go[logical_obj]['m_Name'])
            evidence.append({'name':go[logical_obj]['m_Name'],'category':category,
                             'reason':'active renderer outside every known selection/blocker subtree',
                             'fileID':logical_obj})
        for m in components:
            obj=m.get('m_GameObject',{}).get('fileID')
            if obj not in scope:
                continue
            script=self.scripts.get(m.get('m_Script',{}).get('guid'),'')
            if script=='SpawnSyncedObject':
                category=tagged.get(obj,'Permanent' if active(obj) else 'Decorator')
                add(m.get('spawnPrefab',{}),category,'SpawnSyncedObject reference; inherits selection category')
        if any('PrefabInstance' in d for d in data):
            self.warnings.append(str(path)+': nested PrefabInstance not expanded')
        return categories,evidence


def validate_catalog(data):
    if not isinstance(data,dict):
        raise ValueError('Translator must be a JSON object')
    for interior,rooms in data.items():
        if not isinstance(rooms,dict):
            raise ValueError(interior+': expected a room dictionary')
        for name,entry in rooms.items():
            if not isinstance(entry,dict) or not isinstance(entry.get('Name',name),str):
                raise ValueError(interior+'/'+name+': expected a room object with Name string')
            for category in CATEGORIES:
                rows=entry.get(category,[])
                if not isinstance(rows,list):
                    raise ValueError(name+'/'+category+': expected an array')
                seen=set()
                for row in rows:
                    if not isinstance(row,dict) or len(row)!=1 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in row.items()):
                        raise ValueError(name+'/'+category+': each entry must map one in-game name to one string')
                    key=next(iter(row))
                    if key in seen:
                        raise ValueError(name+'/'+category+': duplicate name '+key)
                    seen.add(key)


def generate(root,out,legacy=None):
    export=Export(root)
    if out.exists():
        catalog=json.loads(out.read_text(encoding='utf-8-sig'))
        validate_catalog(catalog)
    else:
        catalog={}
    room_report={};new=0
    for interior,prefix in FLOWS.items():
        room_report[interior]=[]
        family=catalog.setdefault(interior,{})
        for path,fid in export.room_list(prefix):
            name=export.root_name(path,fid)
            print(f'{interior}: {name}',flush=True)
            categories,evidence=export.classify(path,fid)
            room=family.setdefault(name,{'Name':name})
            room.setdefault('Name',name)
            technical_names={e['name'] for e in evidence if e.get('reason')=='technical NavMesh or MapRadar subtree'}
            valid_names=set().union(*categories.values())
            for category in CATEGORIES:
                if category in room:
                    room[category]=[row for row in room[category]
                                    if next(iter(row)) not in technical_names or next(iter(row)) in valid_names]
            legacy_room=legacy/interior/safe_name(name) if legacy else None
            old_name=read_names(legacy_room/'base.json') if legacy_room else {}
            if room['Name']==name and name in old_name:
                room['Name']=old_name[name]
            for category in CATEGORIES:
                rows=room.setdefault(category,[])
                known={next(iter(row)) for row in rows}
                previous={}
                if legacy_room:
                    legacy_categories={'Permanent':['Objects','Permanent_Decorators'],
                                       'Decorator':['Decorators'],'Blocker':['Blockers']}
                    for old_category in legacy_categories[category]:
                        previous.update(read_names(legacy_room/old_category/'names.json'))
                for obj in sorted(categories[category]):
                    if obj not in known:
                        rows.append({obj:previous.get(obj,obj)});new+=1
            room_report[interior].append({'name':name,'prefab':str(path.relative_to(root)),
                                         'counts':{c:len(categories[c]) for c in CATEGORIES},'classification':evidence})
    report={'rooms':room_report,'warnings':sorted(set(export.warnings)),
            'classification_note':'Permanent means an active fixed renderer or synced spawn outside the known variable/blocker subtrees. Static presence is checked in the prefab; unrelated custom runtime scripts may still change it.',
            'mapping':'Level1Flow* -> Facility, Level2Flow* -> Mansion, Level3Flow* -> Mineshaft',
            'new_names':new}
    validate_catalog(catalog)
    write_json(out,catalog)
    write_json(out.with_name(out.stem+'_report.json'),report)
    print('Rooms:',{k:len(v) for k,v in room_report.items()},'new naming entries:',new)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',required=True)
    parser.add_argument('--out',default='Translator.json')
    parser.add_argument('--legacy',help='Optional old Translator directory; migrate names matching the new catalog')
    args=parser.parse_args()
    root=Path(args.assets).resolve()
    if not root.is_dir():
        parser.error('Assets directory does not exist: '+str(root))
    try:
        generate(root,Path(args.out),Path(args.legacy) if args.legacy else None)
    except (ValueError,OSError,yaml.YAMLError) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':
    main()
