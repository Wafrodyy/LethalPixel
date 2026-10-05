r"""Collect room-generation YAML and referenced prefabs, without mesh/texture files.
Usage: py collect_translator_sources.py --assets "..\ExportedProject\Assets"
Python standard library only. Does not modify the source export.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import re
import zipfile

GUID = re.compile(r'guid:\s*([0-9a-fA-F]{32})')
REF = re.compile(r'\{\s*fileID:\s*(-?\d+)\s*,\s*guid:\s*([0-9a-fA-F]{32})')


def collect(root, output):
    index = {}
    print('Indexing GameObject, MonoBehaviour and Scripts...', flush=True)
    for folder in ['GameObject', 'MonoBehaviour', 'Scripts']:
        for meta in (root / folder).rglob('*.meta'):
            match = GUID.search(meta.read_text(encoding='utf-8-sig', errors='replace'))
            if match:
                path = meta.with_suffix('')
                if path.is_file():
                    key = match.group(1).lower()
                    if key in index and index[key] != path:
                        raise ValueError('Duplicate GUID: ' + key)
                    index[key] = path
    starts = sorted(p for p in (root / 'MonoBehaviour').rglob('*.asset')
                    if re.fullmatch(r'Level[123]Flow.*', p.stem))
    if not starts:
        raise ValueError('No Level1/2/3Flow asset found in MonoBehaviour')
    queue = deque(starts)
    visited = set()
    missing = {}
    included = []
    skipped_binary = []
    included_scripts = set()
    while queue:
        path = queue.popleft()
        if path in visited:
            continue
        visited.add(path)
        raw = path.read_bytes()
        if not raw.lstrip(b'\xef\xbb\xbf').startswith(b'%YAML'):
            skipped_binary.append(str(path.relative_to(root)))
            continue
        included.append(path)
        text = raw.decode('utf-8-sig')
        for file_id, guid in REF.findall(text):
            if int(file_id) == 0:
                continue
            guid = guid.lower()
            target = index.get(guid)
            if target is None:
                # Only serialized GameObject/MonoBehaviour refs are needed;
                # mesh/material/texture/animation/audio refs are excluded.
                if abs(int(file_id)) in (100000, 100100000, 11400000):
                    missing.setdefault(guid, set()).add(str(path.relative_to(root)))
                continue
            folder = target.relative_to(root).parts[0]
            if folder in ('MonoBehaviour', 'GameObject') and target.suffix in ('.asset', '.prefab'):
                queue.append(target)
            elif folder == 'Scripts' and target.suffix == '.cs':
                included_scripts.add(target)
    included.extend(sorted(included_scripts))
    report = {
        'roots': [str(p.relative_to(root)) for p in starts],
        'included_files': [str(p.relative_to(root)) for p in included],
        'missing_references': {g: sorted(paths) for g, paths in sorted(missing.items())},
        'skipped_non_yaml': skipped_binary,
        'note': 'Dependency bundle only. No classification or Translator naming files generated yet.'
    }
    output = output.resolve()
    if output.exists():
        raise ValueError('Output already exists; choose another --out filename: ' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included:
            archive.write(path, 'Assets/' + path.relative_to(root).as_posix())
            meta = Path(str(path) + '.meta')
            if meta.is_file():
                archive.write(meta, 'Assets/' + meta.relative_to(root).as_posix())
        archive.writestr('collection_report.json', json.dumps(report, indent=2, ensure_ascii=False))
    print(f'{len(included)} source files collected; {len(missing)} unresolved GUIDs.')
    print(f'{output} ({output.stat().st_size / 1024 / 1024:.2f} MiB)')
    if output.stat().st_size > 32 * 1024 * 1024:
        print('Archive exceeds 32 MiB. Share the size before uploading; it can be split.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--out', default='V73_translator_sources.zip')
    args = parser.parse_args()
    root = Path(args.assets).resolve()
    if not root.is_dir():
        parser.error('Assets directory does not exist: ' + str(root))
    try:
        collect(root, Path(args.out))
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
