"""Tiny synthetic PNGs for gallery tests; never use a personal photograph."""
import hashlib
import json
from pathlib import Path
import struct
import zlib


def write_gallery(state):
    def chunk(kind, value):
        return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value)&0xffffffff)
    directory = Path(state)/'backgrounds'
    directory.mkdir(exist_ok=True)
    images=[]
    for name, color in [('测试蓝',(70,130,180,255)),('测试绿',(80,160,100,255))]:
        raw=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',8,8,8,6,0,0,0))
             +chunk(b'IDAT',zlib.compress((b'\0'+bytes(color)*8)*8))+chunk(b'IEND',b''))
        image_id=hashlib.sha256(raw).hexdigest()
        (directory/(image_id+'.png')).write_bytes(raw)
        images.append({'id':image_id,'file':image_id+'.png','label':name})
    (directory/'manifest.json').write_text(json.dumps({'version':1,'default_id':images[0]['id'],'images':images}),encoding='utf-8')
    return images
