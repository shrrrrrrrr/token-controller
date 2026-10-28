"""One site-wide background choice; media are operator-installed public assets."""
import json
from pathlib import Path
import re
import stat
import time

from fastapi import Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .store import Fault

IMAGE_ID = re.compile(r'[a-f0-9]{64}\Z')
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def regular(path, maximum):
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or not 0 < entry.st_size <= maximum:
        raise ValueError('not a bounded regular file')
    return entry


def load_gallery(state):
    directory = Path(state) / 'backgrounds'
    if directory.is_symlink():
        raise ValueError('linked gallery directory')
    manifest = directory / 'manifest.json'
    if not manifest.exists():
        return {'images': [], 'default_id': None}
    regular(manifest, 131072)
    raw = json.loads(manifest.read_text(encoding='utf-8'))
    if (not isinstance(raw, dict) or raw.get('version') != 1
            or not isinstance(raw.get('images'), list) or len(raw['images']) > 100):
        raise ValueError('invalid gallery')
    images, seen = [], set()
    for item in raw['images']:
        image_id, label, filename = item.get('id'), item.get('label'), item.get('file')
        if (not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id) or image_id in seen
                or not isinstance(label, str) or not 1 <= len(label.strip()) <= 80
                or any(ord(c) < 32 for c in label)
                or filename not in (image_id+'.jpg', image_id+'.png')):
            raise ValueError('invalid image registration')
        seen.add(image_id)
        # Missing media cannot become a public selection.
        path = directory / filename
        regular(path, MAX_IMAGE_BYTES)
        with path.open('rb') as stream:
            magic = stream.read(8)
        if not (filename.endswith('.jpg') and magic.startswith(b'\xff\xd8\xff')
                or filename.endswith('.png') and magic == b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid image type')
        thumbnail = item.get('thumbnail')
        registered = {'id': image_id, 'label': label, 'file': filename,
                      'url': '/background-assets/'+image_id,
                      'thumbnail_url': '/background-assets/'+image_id}
        if thumbnail is not None:
            thumb_id, thumb_file = thumbnail.get('id'), thumbnail.get('file')
            if (not isinstance(thumb_id, str) or not IMAGE_ID.fullmatch(thumb_id)
                    or thumb_file not in (thumb_id+'.jpg',thumb_id+'.png')):
                raise ValueError('invalid thumbnail registration')
            thumb_path = directory / thumb_file
            regular(thumb_path, 2097152)
            with thumb_path.open('rb') as stream:
                magic = stream.read(8)
            if not (thumb_file.endswith('.jpg') and magic.startswith(b'\xff\xd8\xff')
                    or thumb_file.endswith('.png') and magic == b'\x89PNG\r\n\x1a\n'):
                raise ValueError('invalid thumbnail type')
            registered.update(thumbnail_id=thumb_id, thumbnail_file=thumb_file,
                              thumbnail_url='/background-assets/'+thumb_id)
        images.append(registered)
    if raw.get('default_id') not in seen:
        raise ValueError('default image missing')
    return {'images': images, 'default_id': raw['default_id']}


class AppearanceChange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    background_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    revision: int = Field(ge=0)


def mount_appearance(app, store, identity):
    def gallery():
        try:
            return load_gallery(store.path.parent)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            raise Fault('background_gallery_unavailable', 503) from None

    def current(data=None):
        data = data or gallery()
        row = store.one('SELECT * FROM site_appearance WHERE id=1')
        selected = row['background_id'] or data['default_id']
        image = next((item for item in data['images'] if item['id'] == selected), None)
        if not image:
            image = next((item for item in data['images'] if item['id'] == data['default_id']), None)
        return {'revision': row['revision'], 'background':
                {key: image[key] for key in ('id','label','url')} if image else
                {'id': None, 'label': '默认背景', 'url': '/site-assets/campus.jpg'}}

    def admin(who=Depends(identity)):
        if who['user']['role'] != 'admin':
            raise Fault('role_forbidden', 403)
        return who

    @app.get('/api/appearance')
    def public_appearance():
        return current()

    @app.get('/api/admin/appearance')
    def admin_appearance(who=Depends(admin)):
        data = gallery()
        return {**current(data), 'images': [{key: image[key] for key in ('id','label','url','thumbnail_url')}
                                           for image in data['images']]}

    @app.post('/api/admin/appearance')
    def change_appearance(body: AppearanceChange, who=Depends(admin)):
        data = gallery()
        if body.background_id not in {image['id'] for image in data['images']}:
            raise Fault('background_not_registered', 400)
        with store.tx() as db:
            store.check_admin(db, who)
            row = db.execute('SELECT revision FROM site_appearance WHERE id=1').fetchone()
            if row['revision'] != body.revision:
                raise Fault('appearance_changed', 409)
            db.execute('UPDATE site_appearance SET background_id=?,revision=revision+1,updated=?,updated_by=? WHERE id=1',
                       (body.background_id, time.time(), who['user']['id']))
            store.audit(db, who['user']['id'], 'site.appearance_change', 'global',
                        {'background_id': body.background_id, 'revision': body.revision+1})
        return current(data)

    @app.get('/background-assets/{image_id}')
    def background_asset(image_id: str):
        if not IMAGE_ID.fullmatch(image_id):
            raise Fault('asset_not_found', 404)
        data = gallery()
        filename = next((item['file'] for item in data['images'] if item['id'] == image_id), None)
        if not filename:
            filename = next((item['thumbnail_file'] for item in data['images'] if item.get('thumbnail_id') == image_id), None)
        if not filename:
            raise Fault('asset_not_found', 404)
        return FileResponse(store.path.parent / 'backgrounds' / filename,
                            media_type='image/jpeg' if filename.endswith('.jpg') else 'image/png')
