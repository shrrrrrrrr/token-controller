"""Copy an operator-selected public gallery into ignored state; never modify originals."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


def install(source, state, apply=False):
    source, state = Path(source), Path(state)
    if source.is_symlink() or state.is_symlink() or not source.is_dir() or not state.is_dir():
        raise ValueError('expected real source and existing state directories')
    directory = state / 'backgrounds'
    if directory.is_symlink():
        raise ValueError('linked gallery directory')
    labels = {'春':'春日校园','夏':'夏日校园','秋':'金秋校园','冬':'冬日校园','夜':'校园夜景'}
    images, blobs, targets = [], {}, {}
    for path in sorted(source.iterdir()):
        if path.suffix.lower() not in ('.jpg','.jpeg','.png'):
            continue
        entry = path.lstat()
        if not stat.S_ISREG(entry.st_mode) or not 0 < entry.st_size <= 12*1024*1024:
            raise ValueError('expected bounded regular source image')
        raw = path.read_bytes()
        suffix = '.png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if raw.startswith(b'\xff\xd8\xff') else None
        if suffix is None:
            raise ValueError('unsupported image signature')
        image_id = hashlib.sha256(raw).hexdigest()
        if image_id in blobs:
            continue
        label = labels.get(path.stem, '校园风景 '+str(len(images)+1).zfill(2))
        images.append({'id':image_id,'label':label,'file':image_id+suffix})
        blobs[image_id] = raw
        targets[image_id+suffix] = raw
        thumbnail = source / 'thumbnails' / path.name
        if thumbnail.exists():
            entry = thumbnail.lstat()
            if not stat.S_ISREG(entry.st_mode) or not 0 < entry.st_size <= 2097152:
                raise ValueError('invalid thumbnail file')
            thumb_raw = thumbnail.read_bytes()
            if not thumb_raw.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')):
                raise ValueError('invalid thumbnail signature')
            thumb_suffix = '.png' if thumb_raw.startswith(b'\x89PNG') else '.jpg'
            thumb_id = hashlib.sha256(thumb_raw).hexdigest()
            targets[thumb_id+thumb_suffix] = thumb_raw
            images[-1]['thumbnail'] = {'id':thumb_id,'file':thumb_id+thumb_suffix}
    if not images or len(images)>100:
        raise ValueError('expected between 1 and 100 images')
    default = next((item['id'] for item in images if item['label']=='夏日校园'), images[0]['id'])
    manifest = {'version':1,'default_id':default,'images':images}
    if apply:
        directory.mkdir(exist_ok=True)
        manifest_path = directory / 'manifest.json'
        if manifest_path.is_symlink() or manifest_path.exists() and not manifest_path.is_file():
            raise ValueError('unsafe manifest target')
        # Validate every pre-existing target before copying anything.
        for filename, raw in targets.items():
            target = directory / filename
            if target.exists() or target.is_symlink():
                entry = target.lstat()
                if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                        or hashlib.sha256(target.read_bytes()).hexdigest()!=hashlib.sha256(raw).hexdigest()):
                    raise ValueError('existing image differs; preserved')
        for filename, raw in targets.items():
            target = directory / filename
            if not target.exists():
                with target.open('xb') as stream:
                    stream.write(raw)
        fd, temporary = tempfile.mkstemp(prefix='.gallery-', dir=directory)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(manifest,stream,ensure_ascii=False,indent=2)
        os.replace(temporary, manifest_path)
    return {'images':len(images),'total_bytes':sum(map(len,blobs.values())),
            'thumbnail_bytes':sum(len(raw) for filename,raw in targets.items() if filename.split('.')[0] not in blobs),'applied':apply,'originals_modified':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--state',required=True)
    parser.add_argument('--apply',action='store_true')
    arguments=parser.parse_args()
    print(json.dumps(install(arguments.source,arguments.state,arguments.apply)))
