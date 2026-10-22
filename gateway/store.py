import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path


class Fault(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def uid(prefix):
    return prefix + uuid.uuid4().hex


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
 models TEXT NOT NULL, profiles TEXT NOT NULL, budget INTEGER NOT NULL,
 spent INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS keys (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), hash TEXT UNIQUE NOT NULL,
 prefix TEXT NOT NULL, models TEXT NOT NULL, budget INTEGER NOT NULL,
 spent INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0,
 rpm INTEGER NOT NULL, tpm INTEGER NOT NULL, concurrency INTEGER NOT NULL,
 expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, key_id TEXT NOT NULL REFERENCES keys(id), user_id TEXT NOT NULL,
 idem TEXT NOT NULL, body_hash TEXT NOT NULL, kind TEXT NOT NULL, model TEXT NOT NULL,
 profile TEXT NOT NULL, profile_hash TEXT NOT NULL, status TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'simulation',
 reserved INTEGER NOT NULL, actual INTEGER, input_tokens INTEGER, output_tokens INTEGER,
 created REAL NOT NULL, elapsed_ms INTEGER, error TEXT, response TEXT,
 UNIQUE(key_id, idem));
CREATE TABLE IF NOT EXISTS ledger (
 id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
 event TEXT NOT NULL, points INTEGER NOT NULL, created REAL NOT NULL,
 UNIQUE(request_id,event));
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
 user_id TEXT NOT NULL, key_id TEXT NOT NULL, body TEXT NOT NULL,
 status TEXT NOT NULL, upstream_id TEXT, artifact BLOB, created REAL NOT NULL,
 started REAL, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtimes (
 profile TEXT PRIMARY KEY, status TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS routes (
 model TEXT PRIMARY KEY, profile TEXT NOT NULL, draining INTEGER NOT NULL DEFAULT 0,
 policy TEXT NOT NULL, desired TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, body_hash TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, old TEXT, status TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scheduler (id INTEGER PRIMARY KEY CHECK(id=1), last_user TEXT);
INSERT OR IGNORE INTO scheduler VALUES(1, NULL);
CREATE TABLE IF NOT EXISTS dispatches (user_id TEXT PRIMARY KEY, last_started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS model_requests (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 client_request_id TEXT NOT NULL, body_hash TEXT NOT NULL, model TEXT NOT NULL,
 purpose TEXT NOT NULL, preferred_start REAL, estimated_minutes INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 100,
 scheduled_start REAL, reason TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(user_id,client_request_id));
CREATE INDEX IF NOT EXISTS model_requests_status ON model_requests(status,priority,scheduled_start);
CREATE INDEX IF NOT EXISTS model_requests_owner ON model_requests(user_id,created);
CREATE TABLE IF NOT EXISTS notifications (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 body TEXT NOT NULL, reference TEXT NOT NULL, created REAL NOT NULL, read_at REAL);
CREATE INDEX IF NOT EXISTS notifications_owner ON notifications(user_id,created);
"""


class Store:
    def __init__(self, path, seed_demo=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            if "source" not in [r[1] for r in db.execute("PRAGMA table_info(requests)")]:
                db.execute("ALTER TABLE requests ADD COLUMN source TEXT NOT NULL DEFAULT 'simulation'")
            db.execute("PRAGMA journal_mode=WAL")
            if seed_demo:
                for profile in ("qwen-tp2-v1", "h3-single-v1", "h3-sp2-v1"):
                    db.execute("INSERT OR IGNORE INTO runtimes VALUES(?,?,?)",
                               (profile, "ready" if profile.startswith("qwen") else "stopped", time.time()))
                db.execute("INSERT OR IGNORE INTO routes VALUES('qwen3-32b','qwen-tp2-v1',0,'always_on','ready')")
                db.execute("INSERT OR IGNORE INTO routes VALUES('h3','h3-sp2-v1',0,'manual','stopped')")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def tx(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    @staticmethod
    def audit(db, actor, action, target, detail=None):
        db.execute("INSERT INTO audit(actor,action,target,detail,created) VALUES(?,?,?,?,?)",
                   (actor, action, target, canonical(detail or {}), time.time()))

    def check_admin(self, db, authorization):
        if authorization is None:
            return  # Internal CLI/test callers; never derived from request body.
        key = db.execute("SELECT * FROM keys WHERE id=?", (authorization["key"]["id"],)).fetchone()
        user = db.execute("SELECT * FROM users WHERE id=?", (authorization["user"]["id"],)).fetchone()
        if not key or key["revoked"] or key["expires"] <= time.time() or not user or not user["active"]:
            raise Fault("invalid_or_expired_key", 401)
        if user["role"] != "admin" or key["user_id"] != user["id"]:
            raise Fault("role_forbidden", 403)

    def create_user(self, name, role, models, profiles, budget, actor="bootstrap", authorization=None):
        user_id = uid("usr_")
        with self.tx() as db:
            self.check_admin(db, authorization)
            try:
                db.execute("INSERT INTO users(id,name,role,models,profiles,budget) VALUES(?,?,?,?,?,?)",
                           (user_id, name, role, canonical(models), canonical(profiles), budget))
            except sqlite3.IntegrityError:
                raise Fault("name_exists", 409) from None
            self.audit(db, actor, "user.create", user_id, dict(name=name, role=role, models=models, profiles=profiles, budget=budget))
        return user_id

    def issue_key(self, user_id, models, budget, rpm=30, tpm=100000, concurrency=2, days=30, actor="bootstrap", authorization=None):
        token, key_id = "lg_" + secrets.token_urlsafe(32), uid("key_")
        with self.tx() as db:
            self.check_admin(db, authorization)
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or not user["active"]:
                raise Fault("user_not_found", 404)
            if not set(models).issubset(json.loads(user["models"])):
                raise Fault("key_models_exceed_user_grant", 403)
            db.execute("INSERT INTO keys(id,user_id,hash,prefix,models,budget,rpm,tpm,concurrency,expires) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (key_id, user_id, digest(token), token[:7] + "…", canonical(models), budget, rpm, tpm, concurrency, time.time()+days*86400))
            self.audit(db, actor, "key.issue", key_id, dict(user_id=user_id, models=models, budget=budget, days=days))
        return dict(id=key_id, key=token, reveal="完整Key仅本次返回；请保存到密码管理器")

    def identity(self, token):
        key = self.one("SELECT * FROM keys WHERE hash=?", (digest(token),))
        if not key or key["revoked"] or key["expires"] <= time.time():
            raise Fault("invalid_or_expired_key", 401)
        user = self.one("SELECT * FROM users WHERE id=?", (key["user_id"],))
        if not user or not user["active"]:
            raise Fault("user_disabled", 401)
        return dict(user=user, key=key)

    def reserve(self, identity, idem, body, kind, model, points, profile, profile_hash, max_queue=30, source="simulation"):
        user_id, key_id = identity["user"]["id"], identity["key"]["id"]
        body_hash = digest(canonical(dict(body=body, source=source)))
        now = time.time()
        with self.tx() as db:
            # Re-check credentials/grants inside the same transaction as the reservation.
            key = db.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if key["revoked"] or key["expires"] <= now or not user["active"]:
                raise Fault("invalid_or_expired_key", 401)
            if user["role"] == "auditor" or model not in json.loads(key["models"]) or model not in json.loads(user["models"]):
                raise Fault("model_forbidden", 403)
            old = db.execute("SELECT * FROM requests WHERE key_id=? AND idem=?", (key_id, idem)).fetchone()
            if old:
                if old["body_hash"] != body_hash or old["kind"] != kind:
                    raise Fault("idempotency_conflict", 409)
                return dict(old), False
            route = db.execute("SELECT * FROM routes WHERE model=?", (model,)).fetchone()
            runtime = db.execute("SELECT * FROM runtimes WHERE profile=?", (profile,)).fetchone()
            if not route or route["draining"] or route["profile"] != profile or not runtime or runtime["status"] != "ready":
                raise Fault("model_not_ready_or_draining", 503)
            recent = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved),0) FROM requests WHERE key_id=? AND created>?",
                                (key_id, now-60)).fetchone()
            active = db.execute("SELECT COUNT(*) FROM requests WHERE key_id=? AND status IN ('reserved','dispatched','reconciliation')", (key_id,)).fetchone()[0]
            text_reserved = db.execute("SELECT COALESCE(SUM(reserved),0) FROM requests WHERE key_id=? AND kind='chat' AND created>?", (key_id, now-60)).fetchone()[0]
            if recent[0] >= key["rpm"] or (kind == "chat" and text_reserved+points > key["tpm"]) or active >= key["concurrency"]:
                raise Fault("rate_or_concurrency_limit", 429)
            if user["spent"]+user["reserved"]+points > user["budget"] or key["spent"]+key["reserved"]+points > key["budget"]:
                raise Fault("insufficient_quota", 402)
            if kind == "video":
                total = db.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued','running','reconciliation')").fetchone()[0]
                own = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status IN ('queued','running','reconciliation')", (user_id,)).fetchone()[0]
                if total >= max_queue or own >= 3:
                    raise Fault("video_queue_full", 429)
            request_id = uid("req_")
            db.execute("INSERT INTO requests(id,key_id,user_id,idem,body_hash,kind,model,profile,profile_hash,status,reserved,created) VALUES(?,?,?,?,?,?,?,?,?,'reserved',?,?)",
                       (request_id, key_id, user_id, idem, body_hash, kind, model, profile, profile_hash, points, now))
            db.execute("UPDATE requests SET source=? WHERE id=?", (source, request_id))
            for table, row_id in [("users", user_id), ("keys", key_id)]:
                db.execute(f"UPDATE {table} SET reserved=reserved+? WHERE id=?", (points, row_id))
            db.execute("INSERT INTO ledger(request_id,event,points,created) VALUES(?,'reserve',?,?)", (request_id, points, now))
            if kind == "video":
                db.execute("INSERT INTO tasks(id,request_id,user_id,key_id,body,status,created,source) VALUES(?,?,?,?,?,'queued',?,'simulation')",
                           (uid("job_"), request_id, user_id, key_id, canonical(body), now))
            return dict(db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()), True

    def mark_dispatched(self, request_id):
        with self.tx() as db:
            db.execute("UPDATE requests SET status='dispatched' WHERE id=? AND status='reserved'", (request_id,))

    def unknown(self, request_id, code):
        with self.tx() as db:
            db.execute("UPDATE requests SET status='reconciliation',error=? WHERE id=? AND status IN ('reserved','dispatched')", (code, request_id))
            db.execute("UPDATE tasks SET status='reconciliation' WHERE request_id=? AND status IN ('queued','running')", (request_id,))

    def settle(self, request_id, actual, input_tokens=None, output_tokens=None, response=None, status="succeeded", actor="worker", reason="", allow_excess=False, db=None):
        with self.tx() if db is None else nullcontext(db) as db:
            req = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not req:
                raise Fault("request_not_found", 404)
            if req["status"] in ("succeeded", "failed", "cancelled"):
                return False
            if actual < 0:
                raise Fault("negative_usage")
            if actual > req["reserved"] and not allow_excess:
                db.execute("UPDATE requests SET status='reconciliation',error='usage_exceeds_reservation' WHERE id=?", (request_id,))
                return False
            extra = actual-req["reserved"]
            for table, row_id in [("users", req["user_id"]), ("keys", req["key_id"])]:
                row = db.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
                if extra > 0 and row["spent"]+row["reserved"]+extra > row["budget"]:
                    raise Fault("reconciliation_requires_budget_increase", 409)
            for table, row_id in [("users", req["user_id"]), ("keys", req["key_id"])]:
                db.execute(f"UPDATE {table} SET reserved=reserved-?,spent=spent+? WHERE id=?", (req["reserved"], actual, row_id))
            db.execute("UPDATE requests SET status=?,actual=?,input_tokens=?,output_tokens=?,response=?,elapsed_ms=?,error=? WHERE id=?",
                       (status, actual, input_tokens, output_tokens, canonical(response) if response else None,
                        int((time.time()-req["created"])*1000), reason or None, request_id))
            db.execute("INSERT INTO ledger(request_id,event,points,created) VALUES(?,'settle',?,?)", (request_id, actual, time.time()))
            db.execute("INSERT INTO ledger(request_id,event,points,created) VALUES(?,'release',?,?)", (request_id, req["reserved"]-actual, time.time()))
            self.audit(db, actor, "usage.settle", request_id, dict(actual=actual, status=status, reason=reason))
            return True

    def recover(self):
        """Called once by the owning process; never re-submit uncertain upstream work."""
        with self.tx() as db:
            db.execute("UPDATE requests SET status='reconciliation',error='process_restart_unknown' WHERE kind='chat' AND status IN ('reserved','dispatched')")
            db.execute("UPDATE requests SET status='reconciliation',error='process_restart_unknown' WHERE id IN (SELECT request_id FROM tasks WHERE status='running')")
            db.execute("UPDATE tasks SET status='reconciliation' WHERE status='running'")

    def request(self, request_id):
        return self.one("SELECT * FROM requests WHERE id=?", (request_id,))
