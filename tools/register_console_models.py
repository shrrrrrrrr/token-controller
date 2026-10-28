"""Append the four console assets to a private catalog; never starts a model."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateway.catalog import load_model_catalog
from gateway.console import MODEL_SHELF


def register(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('expected existing regular catalog')
    item=path.stat()
    if item.st_nlink!=1 or item.st_uid!=os.getuid():raise ValueError('run as the catalog owner')
    models=load_model_catalog(path)
    current=json.loads(path.read_text(encoding='utf-8-sig'))
    ids={m['id'] for m in models}
    extra=[{k:m[k] for k in ('id','display_name','kind')}|{'deployment_status':'offline','description':''}
           for m in MODEL_SHELF if m['id'] not in ids]
    if not extra:return {'changed':False}
    backup=path.with_name('.catalog-before-console-'+str(time.time_ns())+'.json')
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(path.read_bytes());f.flush();os.fsync(f.fileno())
    fd,name=tempfile.mkstemp(prefix='.catalog-console-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            os.chmod(name,stat.S_IMODE(item.st_mode));json.dump({'models':current['models']+extra},f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        load_model_catalog(name)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    return {'changed':True,'added':[m['id'] for m in extra],'backup':str(backup),'models_started':False,'grants_changed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--catalog',type=Path,required=True)
    print(json.dumps(register(parser.parse_args().catalog)))
