"""Resource reservations and personal notifications; no inference or lifecycle calls."""
import hashlib
import json
import time
from typing import Literal

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .store import Fault, canonical, uid

ACTIVE = ('pending', 'approved')
TRANSITIONS = {'pending': {'pending','approved','rejected','cancelled'},
               'approved': {'approved','closed','cancelled'}}


class ReservationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    model: str = Field(min_length=1,max_length=100,pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')
    purpose: str = Field(min_length=4,max_length=300)
    preferred_start: float | None = Field(default=None,allow_inf_nan=False)
    estimated_minutes: int = Field(default=30,ge=5,le=240)
    client_ack: bool
    client_request_id: str = Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')


class ReservationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    status: Literal['pending','approved','rejected','cancelled','closed']
    priority: int = Field(default=100,ge=0,le=9999)
    scheduled_start: float | None = Field(default=None,allow_inf_nan=False)
    estimated_minutes: int = Field(ge=5,le=240)
    reason: str = Field(min_length=4,max_length=300)
    expected_revision: int = Field(ge=1)


def current_actor(db,who):
    key=db.execute('SELECT * FROM keys WHERE id=?',(who['key']['id'],)).fetchone()
    user=db.execute('SELECT * FROM users WHERE id=?',(who['user']['id'],)).fetchone()
    if not key or key['revoked'] or key['expires']<=time.time() or not user or not user['active']:
        raise Fault('invalid_or_expired_key',401)
    return user,key


def notify(db,user_id,title,body,reference):
    db.execute('INSERT INTO notifications(id,user_id,title,body,reference,created) VALUES(?,?,?,?,?,?)',
               (uid('notice_'),user_id,title,body,reference,time.time()))


def public_request(row,viewer):
    own=row['user_id']==viewer['user']['id']
    fields=('model','status','priority','preferred_start','scheduled_start','estimated_minutes','created')
    result={name:row[name] for name in fields}
    result.update(queue_ref='R-'+row['id'][-8:],is_owner=own)
    if own or viewer['user']['role']=='admin':
        result.update(dict(row))
    return result


def mount_scheduling(app,store,identity,registered_models=None):
    @app.get('/api/model-requests')
    def requests(who=Depends(identity)):
        rows=store.rows("SELECT * FROM model_requests WHERE status IN ('pending','approved') ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,priority,COALESCE(scheduled_start,preferred_start,created),created LIMIT 100")
        queue=[dict(public_request(row,who),position=i+1) for i,row in enumerate(rows)]
        mine=store.rows('SELECT * FROM model_requests WHERE user_id=? ORDER BY created DESC LIMIT 30',(who['user']['id'],))
        manage=store.rows('SELECT * FROM model_requests ORDER BY created DESC LIMIT 100') if who['user']['role']=='admin' else []
        allowed=sorted(set(json.loads(who['user']['models'])) & set(json.loads(who['key']['models']))) if who['user']['role']!='auditor' else []
        if registered_models:
            known={m['id'] for m in registered_models()}
            allowed=[model for model in allowed if model in known]
        return dict(queue=queue,mine=mine,manage=manage,allowed_models=allowed,
                    can_manage=who['user']['role']=='admin',can_request=bool(allowed),
                    automatic_execution=False,updated_at=time.time(),timezone='Asia/Shanghai')

    @app.post('/api/model-requests',status_code=202)
    def submit(body: ReservationInput,who=Depends(identity)):
        if registered_models and body.model not in {m['id'] for m in registered_models()}:
            raise Fault('model_not_registered',400)
        body_hash=hashlib.sha256(canonical(body.model_dump()).encode()).hexdigest()
        now=time.time()
        with store.tx() as db:
            user,key=current_actor(db,who)
            if user['role']=='auditor' or body.model not in set(json.loads(user['models'])) & set(json.loads(key['models'])):
                raise Fault('model_forbidden',403)
            if not body.client_ack:
                raise Fault('client_runtime_ack_required',400)
            old=db.execute('SELECT * FROM model_requests WHERE user_id=? AND client_request_id=?',(user['id'],body.client_request_id)).fetchone()
            if old:
                if old['body_hash']!=body_hash:
                    raise Fault('idempotency_conflict',409)
                return dict(old)
            if body.preferred_start is not None and not now-60<=body.preferred_start<=now+90*86400:
                raise Fault('reservation_time_out_of_range',400)
            if db.execute("SELECT COUNT(*) FROM model_requests WHERE user_id=? AND status IN ('pending','approved')",(user['id'],)).fetchone()[0]>=3:
                raise Fault('reservation_limit_reached',429)
            if db.execute('SELECT COUNT(*) FROM model_requests WHERE user_id=? AND created>?',(user['id'],now-86400)).fetchone()[0]>=20:
                raise Fault('reservation_daily_limit',429)
            if db.execute("SELECT COUNT(*) FROM model_requests WHERE status IN ('pending','approved')").fetchone()[0]>=100:
                raise Fault('reservation_queue_full',429)
            request_id=uid('reservation_')
            db.execute('INSERT INTO model_requests(id,user_id,client_request_id,body_hash,model,purpose,preferred_start,estimated_minutes,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (request_id,user['id'],body.client_request_id,body_hash,body.model,body.purpose,body.preferred_start,body.estimated_minutes,now,now))
            notify(db,user['id'],'预约申请已登记','申请等待管理员审批；尚未执行推理或预留调用额度。',request_id)
            for admin in db.execute("SELECT id FROM users WHERE role='admin' AND active=1"):
                notify(db,admin['id'],'有新的模型预约',body.model+' 有新申请，请到模型排队中审核。',request_id)
            store.audit(db,user['id'],'reservation.submit',request_id,dict(model=body.model,execution=False))
            return dict(db.execute('SELECT * FROM model_requests WHERE id=?',(request_id,)).fetchone())

    @app.patch('/api/model-requests/{request_id}')
    def update(request_id: str,body: ReservationUpdate,who=Depends(identity)):
        with store.tx() as db:
            user,key=current_actor(db,who)
            if user['role']!='admin':
                raise Fault('role_forbidden',403)
            row=db.execute('SELECT * FROM model_requests WHERE id=?',(request_id,)).fetchone()
            if not row:
                raise Fault('reservation_not_found',404)
            if row['revision']!=body.expected_revision:
                raise Fault('reservation_version_conflict',409)
            if body.status not in TRANSITIONS.get(row['status'],set()):
                raise Fault('reservation_transition_forbidden',409)
            now=time.time()
            if body.status=='approved':
                if body.scheduled_start is None:
                    raise Fault('reservation_schedule_required',400)
                unchanged=row['status']=='approved' and row['scheduled_start']==body.scheduled_start
                if not unchanged and not now-60<=body.scheduled_start<=now+90*86400:
                    raise Fault('reservation_time_out_of_range',400)
                overlap=db.execute("SELECT id FROM model_requests WHERE id!=? AND status='approved' AND scheduled_start<? AND scheduled_start+estimated_minutes*60>? LIMIT 1",
                                   (request_id,body.scheduled_start+body.estimated_minutes*60,body.scheduled_start)).fetchone()
                if overlap:
                    raise Fault('reservation_window_conflict',409)
            scheduled=body.scheduled_start if body.status=='approved' else row['scheduled_start']
            db.execute('UPDATE model_requests SET status=?,priority=?,scheduled_start=?,estimated_minutes=?,reason=?,revision=revision+1,updated=? WHERE id=?',
                       (body.status,body.priority,scheduled,body.estimated_minutes,body.reason,now,request_id))
            notify(db,row['user_id'],'模型预约已更新',f"状态：{body.status}。管理员说明：{body.reason}",request_id)
            store.audit(db,user['id'],'reservation.update',request_id,dict(before_status=row['status'],status=body.status,priority=body.priority,scheduled_start=scheduled,reason_hash=hashlib.sha256(body.reason.encode()).hexdigest(),execution=False))
            return dict(db.execute('SELECT * FROM model_requests WHERE id=?',(request_id,)).fetchone())

    @app.post('/api/model-requests/{request_id}/cancel')
    def cancel(request_id: str,who=Depends(identity)):
        with store.tx() as db:
            user,key=current_actor(db,who)
            row=db.execute('SELECT * FROM model_requests WHERE id=? AND user_id=?',(request_id,user['id'])).fetchone()
            if not row:
                raise Fault('reservation_not_found',404)
            if row['status']=='cancelled':
                return dict(id=request_id,status='cancelled')
            if row['status'] not in ACTIVE:
                raise Fault('reservation_transition_forbidden',409)
            db.execute("UPDATE model_requests SET status='cancelled',revision=revision+1,updated=? WHERE id=?",(time.time(),request_id))
            notify(db,user['id'],'预约已取消','取消的是资源预约，不会停止已经运行的模型或请求。',request_id)
            for admin in db.execute("SELECT id FROM users WHERE role='admin' AND active=1"):
                notify(db,admin['id'],'成员取消了预约',row['model']+' 的预约已取消。',request_id)
            store.audit(db,user['id'],'reservation.cancel',request_id,dict(execution=False))
            return dict(id=request_id,status='cancelled')

    @app.get('/api/notifications')
    def notifications(who=Depends(identity),limit: int=Query(default=40,ge=1,le=100)):
        items=store.rows('SELECT * FROM notifications WHERE user_id=? ORDER BY created DESC LIMIT ?',(who['user']['id'],limit))
        count=store.one('SELECT COUNT(*) AS count FROM notifications WHERE user_id=? AND read_at IS NULL',(who['user']['id'],))['count']
        return dict(items=items,unread=count)

    @app.post('/api/notifications/read-all')
    def read_all(who=Depends(identity)):
        with store.tx() as db:
            user,key=current_actor(db,who)
            db.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL',(time.time(),user['id']))
        return {'read':True}

    @app.post('/api/notifications/{notification_id}/read')
    def read_notification(notification_id: str,who=Depends(identity)):
        with store.tx() as db:
            user,key=current_actor(db,who)
            row=db.execute('SELECT id FROM notifications WHERE id=? AND user_id=?',(notification_id,user['id'])).fetchone()
            if not row:
                raise Fault('notification_not_found',404)
            db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?',(time.time(),notification_id))
        return {'read':True}
