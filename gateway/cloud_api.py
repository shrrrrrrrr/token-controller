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
