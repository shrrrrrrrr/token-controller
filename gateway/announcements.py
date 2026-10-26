"""Administrator-authored notices with immutable publications and personal receipts."""
import time

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .store import Fault, uid

VISIBLE = " FROM announcement_publications p JOIN announcements a ON a.id=p.announcement_id WHERE a.status='published'"


def inbox(store, user_id, *, limit=40, offset=0, include_announcements=True):
    personal = "SELECT id,title,body,created,read_at,'personal' AS kind FROM notifications WHERE user_id=?"
    args = [user_id]
    combined = personal
    if include_announcements:
        combined += (" UNION ALL SELECT p.id,p.title,p.body,p.created,r.read_at,'announcement' AS kind "
                     "FROM announcement_publications p JOIN announcements a ON a.id=p.announcement_id "
                     "LEFT JOIN announcement_reads r ON r.publication_id=p.id AND r.user_id=? WHERE a.status='published'")
        args.append(user_id)
    items = store.rows('SELECT * FROM ('+combined+') ORDER BY created DESC,id DESC LIMIT ? OFFSET ?', (*args, limit, offset))
    counts = store.one('SELECT COUNT(*) AS total,COALESCE(SUM(read_at IS NULL),0) AS unread FROM ('+combined+')', args)
    unread = []
    if include_announcements:
        unread = store.rows("SELECT p.id,p.title,p.body,p.created,'announcement' AS kind"+VISIBLE+
                            ' AND NOT EXISTS(SELECT 1 FROM announcement_reads r WHERE r.publication_id=p.id AND r.user_id=?) '
                            'ORDER BY p.created DESC,p.id DESC LIMIT 10', (user_id,))
    return dict(items=items, **counts, unread_announcements=unread, limit=limit, offset=offset)


def mark_read(db, user, notification_id=None):
    now = time.time()
    if notification_id is None:
        db.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL', (now, user['id']))
        if user['active']:
            db.execute('INSERT OR IGNORE INTO announcement_reads(publication_id,user_id,read_at) '
                       'SELECT p.id,?,?'+VISIBLE, (user['id'], now))
    elif notification_id.startswith('annpub_'):
        if not user['active'] or not db.execute('SELECT p.id'+VISIBLE+' AND p.id=?', (notification_id,)).fetchone():
            raise Fault('notification_not_found', 404)
        db.execute('INSERT OR IGNORE INTO announcement_reads VALUES(?,?,?)', (notification_id, user['id'], now))
    else:
        if not db.execute('SELECT 1 FROM notifications WHERE id=? AND user_id=?', (notification_id, user['id'])).fetchone():
            raise Fault('notification_not_found', 404)
        db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?', (now, notification_id))


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=6000)


class Edit(Draft):
    expected_revision: int = Field(ge=1)


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)


def mount_announcements(app, store, identity):
    def admin(who=Depends(identity)):
        if who['user']['role'] != 'admin':
            raise Fault('role_forbidden', 403)
        return who

    def existing(db, who, announcement_id, revision):
        store.check_admin(db, who)
        row = db.execute('SELECT * FROM announcements WHERE id=?', (announcement_id,)).fetchone()
        if not row:
            raise Fault('announcement_not_found', 404)
        if row['revision'] != revision:
            raise Fault('announcement_revision_conflict', 409)
        return row

    def content(body):
        title, text = body.title.strip(), body.body.strip()
        if not title or not text:
            raise Fault('announcement_content_required', 400)
        return title, text

    @app.get('/api/announcements')
    def listing(who=Depends(admin), limit: int=Query(default=50, ge=1, le=100), offset: int=Query(default=0, ge=0)):
        return {'items': store.rows('SELECT * FROM announcements ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?', (limit, offset)),
                'total': store.one('SELECT COUNT(*) AS total FROM announcements')['total']}

    @app.post('/api/announcements', status_code=201)
    def create(body: Draft, who=Depends(admin)):
        title, text = content(body)
        announcement_id, now = uid('ann_'), time.time()
        with store.tx() as db:
            store.check_admin(db, who)
            db.execute("INSERT INTO announcements VALUES(?,?,?,'draft',1,0,?,?,?)", (announcement_id, title, text, who['user']['id'], now, now))
            store.audit(db, who['user']['id'], 'announcement.create', announcement_id, {'content_logged': False})
        return {'id': announcement_id, 'revision': 1}

    @app.patch('/api/announcements/{announcement_id}')
    def edit(announcement_id: str, body: Edit, who=Depends(admin)):
        title, text = content(body)
        with store.tx() as db:
            row = existing(db, who, announcement_id, body.expected_revision)
            revision = row['revision']+1
            db.execute('UPDATE announcements SET title=?,body=?,revision=?,updated=? WHERE id=?', (title, text, revision, time.time(), announcement_id))
            store.audit(db, who['user']['id'], 'announcement.edit', announcement_id, {'revision': revision, 'content_logged': False})
        return {'id': announcement_id, 'revision': revision}

    @app.post('/api/announcements/{announcement_id}/publish')
    def publish(announcement_id: str, body: Revision, who=Depends(admin)):
        with store.tx() as db:
            row = existing(db, who, announcement_id, body.expected_revision)
            if row['status']=='published' and row['published_revision']==row['revision']:
                publication = db.execute('SELECT id FROM announcement_publications WHERE announcement_id=? AND revision=?', (announcement_id, row['revision'])).fetchone()
                return {'published': True, 'publication_id': publication['id'], 'replayed': True}
            publication_id, now = uid('annpub_'), time.time()
            db.execute('INSERT INTO announcement_publications VALUES(?,?,?,?,?,?)', (publication_id, announcement_id, row['revision'], row['title'], row['body'], now))
            db.execute("UPDATE announcements SET status='published',published_revision=revision,updated=? WHERE id=?", (now, announcement_id))
            store.audit(db, who['user']['id'], 'announcement.publish', announcement_id, {'revision': row['revision'], 'publication_id': publication_id})
            return {'published': True, 'publication_id': publication_id, 'replayed': False}

    @app.post('/api/announcements/{announcement_id}/withdraw')
    def withdraw(announcement_id: str, body: Revision, who=Depends(admin)):
        with store.tx() as db:
            existing(db, who, announcement_id, body.expected_revision)
            db.execute("UPDATE announcements SET status='withdrawn',revision=revision+1,updated=? WHERE id=?", (time.time(), announcement_id))
            store.audit(db, who['user']['id'], 'announcement.withdraw', announcement_id)
        return {'withdrawn': True}
