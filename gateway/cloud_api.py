"""Restricted cloud pilot routes; no runtime controller or video worker."""
import asyncio
import json

from fastapi import Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr

from .store import Fault, uid


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: SecretStr


def mount_cloud_api(app, store, provider, identity, registered_models, chat_schema, api_identity=None):
    def caller(who=Depends(api_identity or identity)):
        if who["user"]["role"] != "caller":
            raise Fault("member_calling_key_required", 403)
        return who

    @app.get("/v1/models")
    async def models(who=Depends(caller)):
        allowed = set(json.loads(who["user"]["models"])) & set(json.loads(who["key"]["models"]))
        registered = {item["id"] for item in registered_models()}
        if provider.alias not in allowed or provider.alias not in registered:
            return {"object": "list", "data": []}
        if not provider.ready:
            await provider.probe()
        return {"object": "list", "data": [{"id": provider.alias, "object": "model", "owned_by": "project-gateway"}]}

    @app.post("/api/upstream/credential")
    async def provision(body: Credential, who=Depends(identity)):
        if who["user"]["role"] != "admin":
            raise Fault("role_forbidden", 403)
        if provider.rotating:
            raise Fault("upstream_configuration_busy", 409)
        provider.rotating = True
        try:
            with store.tx() as db:
                store.check_admin(db, who)
                if db.execute("SELECT 1 FROM requests WHERE kind='chat' AND status IN ('reserved','dispatched','reconciliation') LIMIT 1").fetchone():
                    raise Fault("upstream_configuration_inflight", 409)
            await provider.provision(body.value.get_secret_value())
            with store.tx() as db:
                store.audit(db, who["user"]["id"], "upstream.credential_provisioned", provider.alias,
                            {"read_only_probe_verified": True, "credential_printed": False})
            return {"configured": True, "model": provider.alias, "read_only_probe_verified": True}
        finally:
            provider.rotating = False

    async def chat(body, who=Depends(caller), idempotency_key: str = Header(default="", max_length=120)):
        allowed = set(json.loads(who["user"]["models"])) & set(json.loads(who["key"]["models"]))
        if body.model not in allowed:
            raise Fault("model_forbidden", 403)
        if body.model != provider.alias or body.model not in {item["id"] for item in registered_models()}:
            raise Fault("model_not_ready_or_draining", 503)
        if body.stream:
            raise Fault("streaming_not_supported_in_mvp", 501)
        if provider.rotating:
            raise Fault("upstream_configuration_busy", 503)
        points = sum(len(message.content.encode("utf-8")) + 256 for message in body.messages) + body.max_tokens + 512
        if points > 16384:
            raise Fault("conservative_context_limit", 400)
        data = body.model_dump()
        request, fresh = store.reserve(who, idempotency_key or uid("auto_"), data, "chat", body.model, points,
            provider.profile, provider.profile_hash, source=provider.source, runtime_required=False, global_concurrency=1,
            required_role="caller")
        if not fresh:
            if request["status"] == "succeeded" and request["response"]:
                return JSONResponse(json.loads(request["response"]), headers={"X-Request-ID": request["id"], "X-Idempotent-Replay": "true"})
            raise Fault("request_" + request["status"], 409)
        dispatched = False
        try:
            if not provider.ready:
                await provider.probe()
            store.mark_dispatched(request["id"])
            dispatched = True
            result = await provider.complete(data)
            usage = result["usage"]
            result["gateway"] = {"request_id": request["id"], "source": provider.source, "profile_hash": provider.profile_hash}
            if not store.settle(request["id"], usage["total_tokens"], usage["prompt_tokens"], usage["completion_tokens"], result):
                raise Fault("usage_exceeds_reservation_reconciliation", 502)
            return JSONResponse(result, headers={"X-Request-ID": request["id"]})
        except BaseException as error:
            code = error.code if isinstance(error, Fault) else "upstream_outcome_unknown_reconciliation"
            if dispatched:
                store.unknown(request["id"], code)
            else:
                store.settle(request["id"], 0, status="failed", reason="not_dispatched:" + code)
            if isinstance(error, asyncio.CancelledError):
                raise
            return JSONResponse({"error": {"code": code}, "request_id": request["id"], "submitted_to_model": dispatched},
                                status_code=502 if dispatched else 503, headers={"X-Request-ID": request["id"]})

    # Assign the concrete schema before FastAPI inspects the endpoint signature.
    chat.__annotations__["body"] = chat_schema
    app.post("/v1/chat/completions")(chat)
