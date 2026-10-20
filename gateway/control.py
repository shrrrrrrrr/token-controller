"""Separate HTTP control process. Simulation only; no Docker/shell capability."""
import hmac
import json
import time

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .catalog import PROFILES
from .store import Fault, Store, canonical, digest


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^op_[a-f0-9]{32}$")
    actor: str = Field(max_length=80)
    action: str = Field(pattern=r"^(start|stop|switch)$")
    profile: str = Field(max_length=80)
    profile_hash: str = Field(min_length=64, max_length=64)


class Simulator:
    def __init__(self, store, fail_profiles=None):
        self.store = store
        self.fail_profiles = fail_profiles or set()  # Test injection only; not an HTTP field.

    def submit(self, op):
        if op.profile not in PROFILES or op.profile_hash != PROFILES[op.profile]["hash"]:
            raise Fault("profile_not_whitelisted_or_hash_mismatch", 403)
        profile = PROFILES[op.profile]
        if profile["protected"]:
            raise Fault("protected_always_on_service", 409)
        body_hash = digest(canonical(op.model_dump()))
        with self.store.tx() as db:
            old_op = db.execute("SELECT * FROM operations WHERE id=?", (op.operation_id,)).fetchone()
            if old_op:
                if old_op["body_hash"] != body_hash:
                    raise Fault("operation_replay_conflict", 409)
                return dict(old_op)
            actor = db.execute("SELECT * FROM users WHERE id=? AND active=1", (op.actor,)).fetchone()
            if not actor or actor["role"] not in ("admin", "operator"):
                raise Fault("operator_required", 403)
            if actor["role"] != "admin" and op.profile not in json.loads(actor["profiles"]):
                raise Fault("profile_forbidden", 403)
            route = db.execute("SELECT * FROM routes WHERE model=?", (profile["asset"],)).fetchone()
            if route["policy"] == "always_on" and op.action in ("stop", "switch"):
                raise Fault("protected_always_on_service", 409)
            if db.execute("SELECT 1 FROM operations WHERE status IN ('queued','draining','starting','rolling_back')").fetchone():
                raise Fault("resource_operation_locked", 409)
            current = route["profile"]
            if actor["role"] != "admin" and current not in json.loads(actor["profiles"]):
                raise Fault("source_profile_forbidden", 403)
            if op.action == "stop" and current != op.profile:
                raise Fault("stop_requires_current_profile", 409)
            current_state = db.execute("SELECT status FROM runtimes WHERE profile=?", (current,)).fetchone()[0]
            if op.action == "start" and current_state == "ready" and current != op.profile:
                raise Fault("use_switch_for_running_service", 409)
            db.execute("UPDATE routes SET draining=1 WHERE model=?", (profile["asset"],))
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,'draining',?,?)",
                       (op.operation_id, op.actor, body_hash, op.action, op.profile,
                        current if current_state == "ready" else None, "等待在途任务；模拟资源锁已取得", time.time()))
            self.store.audit(db, op.actor, "runtime."+op.action, op.operation_id,
                             dict(target=op.profile, before=current, source="simulation"))
        return self.store.one("SELECT * FROM operations WHERE id=?", (op.operation_id,))

    def step(self):
        with self.store.tx() as db:
            operation = db.execute("SELECT * FROM operations WHERE status='draining' ORDER BY created LIMIT 1").fetchone()
            if not operation:
                return
            target, old = operation["target"], operation["old"]
            asset = PROFILES[target]["asset"]
            actor = db.execute("SELECT * FROM users WHERE id=? AND active=1", (operation["actor"],)).fetchone()
            authorized = actor and (actor["role"] == "admin" or
                (actor["role"] == "operator" and target in json.loads(actor["profiles"]) and
                 (not old or old in json.loads(actor["profiles"]))))
            route = db.execute("SELECT * FROM routes WHERE model=?", (asset,)).fetchone()
            if not authorized or (route["policy"] == "always_on" and operation["action"] in ("stop", "switch")):
                db.execute("UPDATE operations SET status='failed',detail='permission_or_policy_changed' WHERE id=?", (operation["id"],))
                db.execute("UPDATE routes SET draining=0 WHERE model=?", (asset,))
                self.store.audit(db, operation["actor"], "runtime.authorization_changed", operation["id"])
                return
            if db.execute("SELECT 1 FROM requests WHERE model=? AND status IN ('reserved','dispatched','reconciliation')", (asset,)).fetchone():
                return
            target_p = PROFILES[target]
            for runtime in db.execute("SELECT * FROM runtimes WHERE status='ready'").fetchall():
                other = PROFILES[runtime["profile"]]
                if runtime["profile"] not in (old, target) and (set(other["gpus"]) & set(target_p["gpus"]) or other["port"] == target_p["port"]):
                    db.execute("UPDATE operations SET status='failed',detail='resource_conflict' WHERE id=?", (operation["id"],))
                    db.execute("UPDATE routes SET draining=0 WHERE model=?", (asset,))
                    return
            # Simulation transitions are transactionally recorded. A real executor must verify
            # GPU release, health and rollback across RPC leases; none is executed here.
            self.store.audit(db, operation["actor"], "runtime.drained", operation["id"], dict(source="simulation"))
            if old:
                db.execute("UPDATE runtimes SET status='stopped',updated=? WHERE profile=?", (time.time(), old))
            failed = target in self.fail_profiles and operation["action"] != "stop"
            if failed:
                db.execute("UPDATE runtimes SET status='failed',updated=? WHERE profile=?", (time.time(), target))
                if old:
                    db.execute("UPDATE runtimes SET status='ready',updated=? WHERE profile=?", (time.time(), old))
                db.execute("UPDATE operations SET status=?,detail='模拟启动失败；已恢复旧路由和健康状态' WHERE id=?",
                           ("restored" if old else "failed", operation["id"]))
            else:
                state = "stopped" if operation["action"] == "stop" else "ready"
                db.execute("UPDATE runtimes SET status=?,updated=? WHERE profile=?", (state, time.time(), target))
                db.execute("UPDATE routes SET profile=?,desired=? WHERE model=?", (target, state, asset))
                db.execute("UPDATE operations SET status='completed',detail='模拟健康检查通过，路由已提交' WHERE id=?", (operation["id"],))
            db.execute("UPDATE routes SET draining=0 WHERE model=?", (asset,))
            self.store.audit(db, operation["actor"], "runtime.result", operation["id"], dict(failed=failed, source="simulation"))


def create_control(store, token, simulator=None):
    app = FastAPI(title="Isolated simulation control", docs_url=None, redoc_url=None)
    sim = simulator or Simulator(store)

    @app.exception_handler(Fault)
    async def fault_handler(request, exc):
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied, "Bearer "+token):
            return JSONResponse({"error": {"code": "control_unauthorized"}}, status_code=401)
        return await call_next(request)

    @app.post("/operations", status_code=202)
    def submit(op: Operation):
        return sim.submit(op)

    @app.post("/tick")
    def tick():
        sim.step()
        return {"source": "simulation"}

    @app.get("/health")
    def health():
        return {"source": "simulation", "docker_access": False, "real_execution": False}

    return app
