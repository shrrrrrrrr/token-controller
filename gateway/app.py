import asyncio
import json
import math
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .catalog import PROFILES, evidence, inventory
from .providers import DemoText, LiteLLMText, verified_usage
from .store import Fault, Store, canonical, uid

STATIC = Path(__file__).parent / "static"
MANAGERS = {"admin", "operator", "auditor"}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Message(Input):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)


class Chat(Input):
    model: Literal["qwen3-32b"]
    messages: list[Message] = Field(min_length=1, max_length=64)
    max_tokens: int = Field(default=256, ge=16, le=4096)
    temperature: float = Field(default=0.2, ge=0, le=2)
    stream: bool = False


class Video(Input):
    model: Literal["h3"] = "h3"
    prompt: str = Field(min_length=1, max_length=4000)
    duration_seconds: int = Field(default=5, ge=1, le=10)
    num_inference_steps: int = Field(default=50, ge=2, le=50)


class UserInput(Input):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[\w\-\u4e00-\u9fff ]+$")
    role: Literal["admin", "operator", "caller", "auditor"]
    models: list[Literal["qwen3-32b", "h3"]] = Field(max_length=2)
    profiles: list[Literal["h3-single-v1", "h3-sp2-v1"]] = Field(default_factory=list, max_length=2)
    budget: int = Field(default=10000, ge=0, le=1000000000)


class KeyInput(Input):
    user_id: str = Field(max_length=80)
    models: list[Literal["qwen3-32b", "h3"]] = Field(max_length=2)
    budget: int = Field(default=10000, ge=0, le=1000000000)
    rpm: int = Field(default=30, ge=1, le=1000)
    tpm: int = Field(default=100000, ge=100, le=10000000)
    concurrency: int = Field(default=2, ge=1, le=20)
    days: int = Field(default=30, ge=1, le=365)


class Reconcile(Input):
    outcome: Literal["usage_verified", "not_executed"]
    input_tokens: int = Field(default=0, ge=0, le=10000000)
    output_tokens: int = Field(default=0, ge=0, le=10000000)
    evidence: str = Field(min_length=10, max_length=500)


class RuntimeInput(Input):
    action: Literal["start", "stop", "switch"]
    profile: Literal["qwen-tp2-v1", "h3-single-v1", "h3-sp2-v1"]
    operation_id: str = Field(default_factory=lambda: uid("op_"), pattern=r"^op_[a-f0-9]{32}$")


class PolicyInput(Input):
    model: Literal["qwen3-32b", "h3"]
    policy: Literal["always_on", "on_demand", "manual", "idle_stop"]


class BudgetInput(Input):
    budget: int = Field(ge=0, le=1000000000)


class UserPatch(Input):
    role: Literal["admin", "operator", "caller", "auditor"]
    active: bool
    models: list[Literal["qwen3-32b", "h3"]] = Field(max_length=2)
    profiles: list[Literal["h3-single-v1", "h3-sp2-v1"]] = Field(default_factory=list, max_length=2)


class Service:
    def __init__(self, store, provider, control_url=None, control_token=None, control_transport=None):
        self.store, self.provider = store, provider
        self.control_url, self.control_token = control_url, control_token
        self.control_transport = control_transport

    async def control(self, path, body=None):
        if not self.control_url or not self.control_token:
            raise Fault("simulation_control_not_connected", 503)
        try:
            async with httpx.AsyncClient(base_url=self.control_url, timeout=5, trust_env=False,
                                         transport=self.control_transport) as client:
                response = await client.post(path, json=body or {}, headers={"Authorization": "Bearer "+self.control_token})
        except httpx.HTTPError:
            raise Fault("simulation_control_unreachable", 503) from None
        if response.status_code >= 400:
            raise Fault(response.json().get("error", {}).get("code", "control_failure"), response.status_code)
        return response.json()

    async def video_tick(self):
        # Claim transactionally; default concurrency=1, unknown execution blocks dispatch.
        with self.store.tx() as db:
            running = db.execute("SELECT * FROM tasks WHERE status='running' LIMIT 1").fetchone()
            if running:
                if time.time()-running["started"] < 1:
                    return
                task = dict(running)
            else:
                if db.execute("SELECT 1 FROM tasks WHERE status='reconciliation'").fetchone():
                    return
                row = db.execute("SELECT t.* FROM tasks t LEFT JOIN dispatches d ON t.user_id=d.user_id WHERE t.status='queued' ORDER BY COALESCE(d.last_started,0),t.created LIMIT 1").fetchone()
                if not row:
                    return
                task = dict(row)
                req = db.execute("SELECT * FROM requests WHERE id=?", (task["request_id"],)).fetchone()
                runtime = db.execute("SELECT status FROM runtimes WHERE profile=?", (req["profile"],)).fetchone()
                if runtime[0] != "ready":
                    return
                db.execute("UPDATE tasks SET status='running',started=?,upstream_id=? WHERE id=? AND status='queued'", (time.time(), uid("sim_upstream_"), task["id"]))
                db.execute("UPDATE requests SET status='dispatched' WHERE id=?", (task["request_id"],))
                db.execute("UPDATE scheduler SET last_user=? WHERE id=1", (task["user_id"],))
                db.execute("INSERT INTO dispatches VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_started=excluded.last_started", (task["user_id"], time.time()))
                return
        body = json.loads(task["body"])
        artifact = canonical(dict(source="simulation", notice="模拟任务结果；没有生成视频或调用GPU", duration_seconds=body["duration_seconds"], job_id=task["id"])).encode()
        with self.store.tx() as db:
            current = db.execute("SELECT status FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
            if current != "running":
                return
            self.store.settle(task["request_id"], body["duration_seconds"]*100, response={"job_id": task["id"], "source": "simulation"}, db=db)
            db.execute("UPDATE tasks SET status='completed',artifact=? WHERE id=?", (artifact, task["id"]))

    async def tick(self):
        await self.video_tick()
        await self.control("/tick")


def create_app(store=None, provider=None, control_url=None, control_token=None, control_transport=None, background=True):
    store = store or Store(Path(__file__).parents[1] / ".state" / "gateway.sqlite")
    provider = provider or DemoText()
    service = Service(store, provider, control_url, control_token, control_transport)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()

        async def worker():
            while True:
                try:
                    await service.tick()
                except Exception:
                    # No upstream exception bodies or credentials in logs. Fail closed.
                    pass
                await asyncio.sleep(0.5)

        task = asyncio.create_task(worker()) if background else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="法律+AI 模型网关 · Local MVP", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service, app.state.store = service, store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def security(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": {"code": "origin_forbidden"}}, status_code=403)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 262144):
            return JSONResponse({"error": {"code": "body_too_large"}}, status_code=413)
        if request.method in ("POST", "PATCH", "PUT"):
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 262144:
                    return JSONResponse({"error": {"code": "body_too_large"}}, status_code=413)
            request._body = bytes(raw)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "no-referrer",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.exception_handler(Fault)
    async def fault(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.code}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # FastAPI's default validation body echoes submitted values, including credentials.
        return JSONResponse({"error": {"code": "invalid_request", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}}, status_code=422)

    def identity(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise Fault("bearer_key_required", 401)
        return store.identity(authorization[7:])

    def roles(*allowed):
        def check(who=Depends(identity)):
            if who["user"]["role"] not in allowed:
                raise Fault("role_forbidden", 403)
            return who
        return check

    def owned_task(job_id, who):
        # Admin/auditor do not inherit artifact access.
        task = store.one("SELECT * FROM tasks WHERE id=? AND user_id=?", (job_id, who["user"]["id"]))
        if not task:
            raise Fault("task_not_found", 404)
        return task

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    def script():
        return FileResponse(STATIC / "app.js", media_type="application/javascript")

    @app.get("/style.css")
    def css():
        return FileResponse(STATIC / "style.css", media_type="text/css")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "local-mvp", "text_source": provider.source, "runtime_source": "simulation"}

    @app.get("/api/me")
    def me(who=Depends(identity)):
        user, key = dict(who["user"]), dict(who["key"])
        key.pop("hash")
        return {"user": user, "key": key, "text_source": provider.source}

    @app.post("/api/local/shutdown")
    def shutdown(who=Depends(roles("admin"))):
        if not hasattr(app.state, "shutdown"):
            raise Fault("local_shutdown_unavailable", 501)
        with store.tx() as db:
            store.audit(db, who["user"]["id"], "local_gateway.shutdown", "local-process", dict(server_models_affected=False))
        app.state.shutdown()
        return {"stopping": True, "server_models_affected": False}

    @app.get("/v1/models")
    def models(who=Depends(identity)):
        allowed = set(json.loads(who["user"]["models"])) & set(json.loads(who["key"]["models"]))
        if who["user"]["role"] == "auditor":
            allowed = set()
        return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "local-gateway"} for m in sorted(allowed)]}

    @app.get("/api/inventory")
    def catalog(who=Depends(roles(*MANAGERS))):
        return inventory()

    @app.get("/api/evidence")
    def evidence_api(who=Depends(roles(*MANAGERS))):
        return evidence()

    @app.get("/api/runtime")
    def runtime(who=Depends(roles(*MANAGERS))):
        return {"source": "simulation", "real_control_enabled": False,
                "instances": store.rows("SELECT * FROM runtimes"), "routes": store.rows("SELECT * FROM routes"),
                "profiles": list(PROFILES.values()), "operations": store.rows("SELECT * FROM operations ORDER BY created DESC LIMIT 50")}

    @app.post("/api/runtime/operations", status_code=202)
    async def operate(body: RuntimeInput, who=Depends(roles("admin", "operator"))):
        return await service.control("/operations", dict(operation_id=body.operation_id, actor=who["user"]["id"],
            action=body.action, profile=body.profile, profile_hash=PROFILES[body.profile]["hash"]))

    @app.patch("/api/runtime/policy")
    def policy(body: PolicyInput, who=Depends(roles("admin"))):
        if body.model == "qwen3-32b" and body.policy != "always_on":
            raise Fault("protected_always_on_service", 409)
        with store.tx() as db:
            old = dict(db.execute("SELECT * FROM routes WHERE model=?", (body.model,)).fetchone())
            if old["draining"]:
                raise Fault("resource_operation_locked", 409)
            db.execute("UPDATE routes SET policy=? WHERE model=?", (body.policy, body.model))
            store.audit(db, who["user"]["id"], "policy.change", body.model, dict(before=old, after=body.policy, automation_enabled=False))
        return {"policy": body.policy, "automation_enabled": False, "source": "simulation"}

    @app.post("/api/qwen/probe")
    async def probe(who=Depends(roles("admin", "operator"))):
        if not isinstance(provider, LiteLLMText):
            raise Fault("qwen_not_configured_demo_mode", 409)
        try:
            return await provider.probe()
        except (httpx.HTTPError, ValueError):
            raise Fault("qwen_probe_failed", 503) from None

    @app.post("/v1/chat/completions")
    async def chat(body: Chat, who=Depends(identity), idempotency_key: str = Header(default="", max_length=120)):
        if body.stream:
            raise Fault("streaming_not_supported_in_mvp", 501)
        data = body.model_dump()
        if isinstance(provider, LiteLLMText) and (not provider.verified_at or time.time()-provider.verified_at > 300):
            raise Fault("qwen_probe_required_or_stale", 503)
        route = store.one("SELECT * FROM routes WHERE model=?", (body.model,))
        profile = PROFILES[route["profile"]]
        points = sum(len(m.content.encode("utf-8"))+256 for m in body.messages)+body.max_tokens+512
        req, fresh = store.reserve(who, idempotency_key or uid("auto_"), data, "chat", body.model, points, profile["id"], profile["hash"], source=provider.source)
        if not fresh:
            if req["status"] == "succeeded" and req["response"]:
                return JSONResponse(json.loads(req["response"]), headers={"X-Request-ID": req["id"], "X-Idempotent-Replay": "true"})
            raise Fault("request_"+req["status"], 409)
        store.mark_dispatched(req["id"])
        try:
            result = await provider.complete(data)
            input_tokens, output_tokens, total = verified_usage(result)
            result["gateway"] = dict(request_id=req["id"], source=provider.source, profile_hash=profile["hash"])
            if not store.settle(req["id"], total, input_tokens, output_tokens, result):
                raise Fault("usage_exceeds_reservation_reconciliation", 502)
            return JSONResponse(result, headers={"X-Request-ID": req["id"]})
        except BaseException as exc:
            store.unknown(req["id"], exc.code if isinstance(exc, Fault) else "upstream_outcome_unknown")
            if isinstance(exc, asyncio.CancelledError):
                raise
            code = exc.code if isinstance(exc, Fault) else "upstream_outcome_unknown_reconciliation"
            return JSONResponse({"error": {"code": code}, "request_id": req["id"]}, status_code=502,
                                headers={"X-Request-ID": req["id"]})

    @app.post("/v1/videos", status_code=202)
    def video(body: Video, who=Depends(identity), idempotency_key: str = Header(default="", max_length=120)):
        route = store.one("SELECT * FROM routes WHERE model='h3'")
        profile = PROFILES[route["profile"]]
        req, fresh = store.reserve(who, idempotency_key or uid("auto_"), body.model_dump(), "video", "h3",
                                   body.duration_seconds*100, profile["id"], profile["hash"])
        task = store.one("SELECT id,status,source,created FROM tasks WHERE request_id=?", (req["id"],))
        return {**task, "request_id": req["id"], "replay": not fresh, "notice": "模拟队列；真实H3适配未启用"}

    @app.get("/v1/videos/{job_id}")
    def video_status(job_id: str, who=Depends(identity)):
        task = owned_task(job_id, who)
        return {k: task[k] for k in ("id", "request_id", "status", "source", "created", "started")}

    @app.post("/v1/videos/{job_id}/cancel")
    def video_cancel(job_id: str, who=Depends(identity)):
        task = owned_task(job_id, who)
        with store.tx() as db:
            row = db.execute("SELECT status FROM tasks WHERE id=?", (job_id,)).fetchone()
            if row[0] != "queued":
                raise Fault("only_queued_task_can_be_cancelled", 409)
            db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (job_id,))
            store.settle(task["request_id"], 0, status="cancelled", actor=who["user"]["id"], reason="queued_cancelled", db=db)
        return {"id": job_id, "status": "cancelled"}

    @app.get("/v1/videos/{job_id}/content")
    def content(job_id: str, who=Depends(identity)):
        task = owned_task(job_id, who)
        if task["status"] != "completed" or not task["artifact"]:
            raise Fault("artifact_not_ready", 409)
        return Response(task["artifact"], media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{task["id"]}-simulation.json"'})

    @app.get("/api/tasks")
    def tasks(who=Depends(identity), limit: int = Query(ge=1, le=100, default=50), offset: int = Query(ge=0, default=0)):
        return store.rows("SELECT id,request_id,status,source,created,started FROM tasks WHERE user_id=? ORDER BY created DESC LIMIT ? OFFSET ?", (who["user"]["id"], limit, offset))

    @app.get("/api/usage")
    def usage(who=Depends(identity), limit: int = Query(ge=1, le=100, default=50), offset: int = Query(ge=0, default=0)):
        clause, args = ("", []) if who["user"]["role"] in MANAGERS else (" WHERE user_id=?", [who["user"]["id"]])
        return {"requests": store.rows("SELECT id,user_id,key_id,kind,model,source,profile,profile_hash,status,reserved,actual,input_tokens,output_tokens,created,elapsed_ms,error FROM requests"+clause+" ORDER BY created DESC LIMIT ? OFFSET ?", (*args, limit, offset)),
                "ledger": store.rows("SELECT l.* FROM ledger l JOIN requests r ON l.request_id=r.id"+(" WHERE r.user_id=?" if args else "")+" ORDER BY l.id DESC LIMIT ? OFFSET ?", (*args, limit*3, offset*3))}

    @app.post("/api/requests/{request_id}/reconcile")
    def reconcile(request_id: str, body: Reconcile, who=Depends(roles("admin"))):
        actual = body.input_tokens+body.output_tokens if body.outcome == "usage_verified" else 0
        with store.tx() as db:
            req = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not req or req["status"] != "reconciliation":
                raise Fault("request_not_pending_reconciliation", 409)
            if req["kind"] != "chat" and body.outcome != "not_executed":
                raise Fault("video_requires_upstream_artifact_verification_not_implemented", 409)
            store.settle(request_id, actual, body.input_tokens if actual else 0, body.output_tokens if actual else 0,
                         status="failed", actor=who["user"]["id"], reason=body.evidence, allow_excess=True, db=db)
            db.execute("UPDATE tasks SET status='failed' WHERE request_id=? AND status='reconciliation'", (request_id,))
        return {"request_id": request_id, "actual": actual, "outcome": body.outcome}

    @app.get("/api/users")
    def users(who=Depends(roles(*MANAGERS))):
        return store.rows("SELECT * FROM users ORDER BY name")

    @app.post("/api/users", status_code=201)
    def add_user(body: UserInput, who=Depends(roles("admin"))):
        return {"id": store.create_user(**body.model_dump(), actor=who["user"]["id"])}

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: str, body: UserPatch, who=Depends(roles("admin"))):
        with store.tx() as db:
            current = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not current:
                raise Fault("user_not_found", 404)
            if user_id == who["user"]["id"] and (not body.active or body.role != "admin"):
                raise Fault("cannot_remove_current_admin_access", 409)
            if current["role"] == "admin" and (not body.active or body.role != "admin"):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                    raise Fault("last_admin_protected", 409)
            db.execute("UPDATE users SET role=?,active=?,models=?,profiles=? WHERE id=?", (body.role, int(body.active), canonical(body.models), canonical(body.profiles), user_id))
            store.audit(db, who["user"]["id"], "user.grants.change", user_id, dict(before={k: current[k] for k in ("role", "active", "models", "profiles")}, after=body.model_dump()))
        return {"id": user_id, "updated": True}

    @app.patch("/api/users/{user_id}/budget")
    def budget(user_id: str, body: BudgetInput, who=Depends(roles("admin"))):
        with store.tx() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise Fault("user_not_found", 404)
            if body.budget < user["spent"]+user["reserved"]:
                raise Fault("budget_below_committed_amount", 409)
            db.execute("UPDATE users SET budget=? WHERE id=?", (body.budget, user_id))
            store.audit(db, who["user"]["id"], "budget.change", user_id, dict(before=user["budget"], after=body.budget))
        return {"id": user_id, "budget": body.budget}

    @app.get("/api/keys")
    def keys(who=Depends(roles("admin"))):
        return store.rows("SELECT id,user_id,prefix,models,budget,spent,reserved,rpm,tpm,concurrency,expires,revoked FROM keys ORDER BY expires DESC LIMIT 200")

    @app.post("/api/keys", status_code=201)
    def add_key(body: KeyInput, who=Depends(roles("admin"))):
        return store.issue_key(**body.model_dump(), actor=who["user"]["id"])

    @app.delete("/api/keys/{key_id}")
    def revoke(key_id: str, who=Depends(roles("admin"))):
        with store.tx() as db:
            row = db.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
            if not row:
                raise Fault("key_not_found", 404)
            if key_id == who["key"]["id"]:
                raise Fault("cannot_revoke_current_admin_key_use_cli_recovery", 409)
            db.execute("UPDATE keys SET revoked=1 WHERE id=?", (key_id,))
            store.audit(db, who["user"]["id"], "key.revoke", key_id)
        return {"id": key_id, "revoked": True}

    @app.get("/api/audit")
    def audit(who=Depends(roles(*MANAGERS)), limit: int = Query(ge=1, le=100, default=50), offset: int = Query(ge=0, default=0)):
        return store.rows("SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))

    return app
