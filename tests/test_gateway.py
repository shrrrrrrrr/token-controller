import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.catalog import PROFILES, evidence
from gateway.control import Operation, Simulator, create_control
from gateway.providers import DemoText
from gateway.store import Fault, Store, uid


@pytest.fixture
def system(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    credentials = {}
    ids = {}
    for role in ("admin", "operator", "caller", "auditor"):
        ids[role] = store.create_user(role, role, ["qwen3-32b", "h3"], ["h3-single-v1", "h3-sp2-v1"], 100000)
        credentials[role] = store.issue_key(ids[role], ["qwen3-32b", "h3"], 100000, concurrency=10)
    sim = Simulator(store)
    control = create_control(store, "test-control-secret-only", sim)
    app = create_app(store, control_url="http://control", control_token="test-control-secret-only",
                     control_transport=httpx.ASGITransport(app=control), background=False)
    with TestClient(app) as client:
        yield dict(store=store, credentials=credentials, ids=ids, client=client, sim=sim, app=app, control=control)


def auth(system, role="admin", idem=None):
    return {"Authorization": "Bearer "+system["credentials"][role]["key"], **({"Idempotency-Key": idem} if idem else {})}


CHAT = {"model": "qwen3-32b", "messages": [{"role": "user", "content": "测试闭环"}], "max_tokens": 32}


def operation(system, action="start", profile="h3-sp2-v1"):
    return system["client"].post("/api/runtime/operations", json=dict(action=action, profile=profile), headers=auth(system))


def start_h3(system):
    assert operation(system).status_code == 202
    system["sim"].step()


def test_unauthorized_and_backend_permissions(system):
    client = system["client"]
    assert client.post("/v1/chat/completions", json=CHAT).status_code == 401
    for path in ("/api/users", "/api/keys", "/api/runtime", "/api/inventory", "/api/audit"):
        assert client.get(path, headers=auth(system, "caller")).status_code == 403
    for role in ("caller", "auditor"):
        assert client.post("/api/runtime/operations", json=dict(action="start", profile="h3-sp2-v1"), headers=auth(system, role)).status_code == 403
    assert client.post("/api/users", json=dict(name="hacker", role="admin", models=[], budget=1), headers=auth(system, "operator")).status_code == 403
    assert client.post("/v1/chat/completions", json=CHAT, headers=auth(system, "auditor")).status_code == 403
    assert client.get("/api/keys", headers=auth(system, "auditor")).status_code == 403


def test_model_grant_expiry_revocation_and_hash_storage(system):
    s, c = system["store"], system["client"]
    no_qwen = s.issue_key(system["ids"]["caller"], ["h3"], 10000)
    assert c.post("/v1/chat/completions", json=CHAT, headers={"Authorization": "Bearer "+no_qwen["key"]}).status_code == 403
    assert no_qwen["key"] not in json.dumps(s.rows("SELECT * FROM keys"))
    with s.tx() as db:
        db.execute("UPDATE keys SET expires=? WHERE id=?", (time.time()-1, no_qwen["id"]))
    assert c.get("/api/me", headers={"Authorization": "Bearer "+no_qwen["key"]}).status_code == 401
    caller_key = system["credentials"]["caller"]
    assert c.delete("/api/keys/"+caller_key["id"], headers=auth(system)).status_code == 200
    assert c.get("/api/me", headers=auth(system, "caller")).status_code == 401


def test_qwen_demo_usage_and_idempotency(system):
    c, s = system["client"], system["store"]
    first = c.post("/v1/chat/completions", json=CHAT, headers=auth(system, "caller", "same"))
    assert first.status_code == 200, first.text
    req_id = first.headers["X-Request-ID"]
    assert first.json()["usage"]["total_tokens"] == 20
    second = c.post("/v1/chat/completions", json=CHAT, headers=auth(system, "caller", "same"))
    assert second.status_code == 200
    assert second.headers["X-Idempotent-Replay"] == "true"
    assert first.json() == second.json()
    assert len(s.rows("SELECT * FROM ledger WHERE request_id=?", (req_id,))) == 3
    user = s.one("SELECT * FROM users WHERE id=?", (system["ids"]["caller"],))
    assert user["spent"] == 20 and user["reserved"] == 0
    changed = dict(CHAT, max_tokens=64)
    assert c.post("/v1/chat/completions", json=changed, headers=auth(system, "caller", "same")).status_code == 409


def test_atomic_shared_user_quota_across_keys(tmp_path):
    s = Store(tmp_path / "concurrent.sqlite")
    user_id = s.create_user("limited", "caller", ["qwen3-32b"], [], 1500)
    identities = [s.identity(s.issue_key(user_id, ["qwen3-32b"], 100000)["key"]) for _ in range(2)]
    def reserve(i):
        try:
            s.reserve(identities[i], "attempt", CHAT, "chat", "qwen3-32b", 1000, "qwen-tp2-v1", PROFILES["qwen-tp2-v1"]["hash"])
            return "ok"
        except Fault as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert sorted(outcomes) == ["insufficient_quota", "ok"]
    assert s.one("SELECT * FROM users WHERE id=?", (user_id,))["reserved"] == 1000


def test_rate_limits_and_stream_rejected_before_reservation(system):
    s, c = system["store"], system["client"]
    assert c.post("/v1/chat/completions", json=dict(CHAT, stream=True), headers=auth(system, "caller")).status_code == 501
    assert not s.rows("SELECT * FROM requests")
    with s.tx() as db:
        db.execute("UPDATE keys SET rpm=1 WHERE id=?", (system["credentials"]["caller"]["id"],))
    assert c.post("/v1/chat/completions", json=CHAT, headers=auth(system, "caller")).status_code == 200
    assert c.post("/v1/chat/completions", json=CHAT, headers=auth(system, "caller")).status_code == 429


@pytest.mark.parametrize("failure", ["missing", "timeout", "excess", "negative", "bool"])
def test_unknown_usage_freezes_and_reconciles(system, failure):
    class Broken:
        source = "test"
        async def complete(self, body):
            if failure == "timeout":
                raise TimeoutError("secret-upstream-body-must-not-leak")
            if failure == "missing":
                return {"usage": None}
            vals = {"excess": (100000, 1, 100001), "negative": (-1, 1, 0), "bool": (True, 1, 2)}[failure]
            return {"usage": dict(zip(["prompt_tokens", "completion_tokens", "total_tokens"], vals))}
    system["app"].state.service.provider = Broken()
    # create_app closures hold provider; install a separate app using the broken provider.
    app = create_app(system["store"], Broken(), background=False)
    with TestClient(app) as c:
        response = c.post("/v1/chat/completions", json=CHAT, headers=auth(system, "caller", "unknown"))
    assert response.status_code == 502 and "secret-upstream" not in response.text
    req = system["store"].request(response.json()["request_id"])
    assert req["status"] == "reconciliation" and req["actual"] is None
    key = system["store"].one("SELECT * FROM keys WHERE id=?", (system["credentials"]["caller"]["id"],))
    assert key["reserved"] > 0 and key["spent"] == 0
    reconciliation = dict(outcome="not_executed", evidence="已从上游只读记录确认没有执行该请求")
    assert system["client"].post("/api/requests/"+req["id"]+"/reconcile", json=reconciliation, headers=auth(system, "operator")).status_code == 403
    assert system["client"].post("/api/requests/"+req["id"]+"/reconcile", json=reconciliation, headers=auth(system)).status_code == 200
    assert system["store"].one("SELECT * FROM keys WHERE id=?", (key["id"],))["reserved"] == 0


def test_process_restart_never_resubmits(system):
    s = system["store"]
    identity = s.identity(system["credentials"]["caller"]["key"])
    req, _ = s.reserve(identity, "pending", CHAT, "chat", "qwen3-32b", 1000, "qwen-tp2-v1", PROFILES["qwen-tp2-v1"]["hash"])
    s.mark_dispatched(req["id"])
    s.recover()
    assert s.request(req["id"])["status"] == "reconciliation"
    assert s.one("SELECT * FROM keys WHERE id=?", (identity["key"]["id"],))["reserved"] == 1000


def test_video_ownership_cancel_settlement_and_download(system):
    c, s = system["client"], system["store"]
    start_h3(system)
    task = c.post("/v1/videos", json={"prompt": "本地合成内容"}, headers=auth(system, "caller", "video-1")).json()
    assert task["status"] == "queued", task
    for suffix in ("", "/content"):
        assert c.get("/v1/videos/"+task["id"]+suffix, headers=auth(system)).status_code == 404
    assert c.post("/v1/videos/"+task["id"]+"/cancel", headers=auth(system)).status_code == 404
    assert c.post("/v1/videos/"+task["id"]+"/cancel", headers=auth(system, "caller")).status_code == 200
    assert s.request(task["request_id"])["actual"] == 0
    again = c.post("/v1/videos", json={"prompt": "本地合成内容"}, headers=auth(system, "caller", "video-1")).json()
    assert again["id"] == task["id"] and again["status"] == "cancelled"
    task = c.post("/v1/videos", json={"prompt": "另一项合成测试"}, headers=auth(system, "caller")).json()
    asyncio.run(system["app"].state.service.video_tick())
    assert c.post("/v1/videos/"+task["id"]+"/cancel", headers=auth(system, "caller")).status_code == 409
    with s.tx() as db:
        db.execute("UPDATE tasks SET started=0 WHERE id=?", (task["id"],))
    asyncio.run(system["app"].state.service.video_tick())
    download = c.get("/v1/videos/"+task["id"]+"/content", headers=auth(system, "caller"))
    assert download.status_code == 200 and download.json()["source"] == "simulation"
    assert download.headers["content-type"] == "application/json"
    assert s.request(task["request_id"])["actual"] == 500


def test_draining_waits_and_mutual_exclusion_and_rollback(system):
    c, s, sim = system["client"], system["store"], system["sim"]
    start_h3(system)
    task = c.post("/v1/videos", json={"prompt": "在途任务"}, headers=auth(system, "caller")).json()
    op = operation(system, "switch", "h3-single-v1")
    assert op.status_code == 202, op.text
    sim.step()
    assert s.one("SELECT * FROM operations WHERE id=?", (op.json()["id"],))["status"] == "draining"
    assert c.post("/v1/videos", json={"prompt": "不应派发"}, headers=auth(system, "caller")).status_code == 503
    assert c.post("/v1/videos/"+task["id"]+"/cancel", headers=auth(system, "caller")).status_code == 200
    sim.fail_profiles.add("h3-single-v1")
    sim.step()
    assert s.one("SELECT * FROM operations WHERE id=?", (op.json()["id"],))["status"] == "restored"
    assert s.one("SELECT * FROM routes WHERE model='h3'")["profile"] == "h3-sp2-v1"
    ready = s.rows("SELECT * FROM runtimes WHERE status='ready'")
    assert [r["profile"] for r in ready if r["profile"].startswith("h3")] == ["h3-sp2-v1"]
    assert operation(system, "stop", "qwen-tp2-v1").status_code == 409


def test_control_auth_whitelist_replay_profile_grants(system):
    c = TestClient(system["control"])
    op = dict(operation_id=uid("op_"), actor=system["ids"]["operator"], action="start", profile="h3-sp2-v1", profile_hash=PROFILES["h3-sp2-v1"]["hash"])
    headers = {"Authorization": "Bearer test-control-secret-only"}
    assert c.post("/operations", json=op).status_code == 401
    assert c.post("/operations", json=dict(op, command="docker rm"), headers=headers).status_code == 422
    assert c.post("/operations", json=dict(op, profile_hash="0"*64), headers=headers).status_code == 403
    assert c.post("/operations", json=op, headers=headers).status_code == 202
    assert c.post("/operations", json=op, headers=headers).status_code == 202
    assert c.post("/operations", json=dict(op, action="stop"), headers=headers).status_code == 409
    system["sim"].step()
    with system["store"].tx() as db:
        db.execute("UPDATE users SET profiles='[]' WHERE id=?", (system["ids"]["operator"],))
    op["operation_id"] = uid("op_")
    assert c.post("/operations", json=op, headers=headers).status_code == 403


def test_evidence_stale_security_and_no_plain_keys_in_audit(system):
    c = system["client"]
    response = c.get("/api/inventory", headers=auth(system))
    assert response.status_code == 200
    data = response.json()
    assert data["stale"] and len(data["instances"]) == 3
    assert all(i["health"] == "unknown" for i in data["instances"])
    assert all(g["free_mib"] is None for g in data["gpus"])
    assert c.get("/api/me", headers={**auth(system), "Host": "evil.example"}).status_code == 400
    assert c.post("/v1/chat/completions", json=CHAT, headers={**auth(system), "Origin": "http://evil.example"}).status_code == 403
    assert "frame-ancestors 'none'" in c.get("/health").headers["content-security-policy"]
    audit = c.get("/api/audit", headers=auth(system)).text
    assert not any(item["key"] in audit for item in system["credentials"].values())
    report = evidence()
    assert len(report["checks"]) == 1 and all(item["match"] for item in report["checks"])


def test_settlement_is_atomic_and_once(system):
    s = system["store"]
    identity = s.identity(system["credentials"]["caller"]["key"])
    req, _ = s.reserve(identity, "settle-race", CHAT, "chat", "qwen3-32b", 1000, "qwen-tp2-v1", PROFILES["qwen-tp2-v1"]["hash"])
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: s.settle(req["id"], 20), range(4)))
    assert results.count(True) == 1
    assert s.one("SELECT * FROM users WHERE id=?", (identity["user"]["id"],))["spent"] == 20
    assert len(s.rows("SELECT * FROM ledger WHERE request_id=? AND event='settle'", (req["id"],))) == 1


def test_queue_bounds_fairness_and_unknown_restart_blocks(system):
    c, s = system["client"], system["store"]
    start_h3(system)
    for i in range(3):
        assert c.post("/v1/videos", json={"prompt": str(i)}, headers=auth(system, "caller")).status_code == 202
    assert c.post("/v1/videos", json={"prompt": "over"}, headers=auth(system, "caller")).status_code == 429
    assert c.post("/v1/videos", json={"prompt": "admin"}, headers=auth(system)).status_code == 202
    asyncio.run(system["app"].state.service.video_tick())
    first = s.one("SELECT * FROM tasks WHERE status='running'")
    assert first["user_id"] == system["ids"]["caller"]
    with s.tx() as db:
        db.execute("UPDATE tasks SET started=0 WHERE id=?", (first["id"],))
    asyncio.run(system["app"].state.service.video_tick())
    asyncio.run(system["app"].state.service.video_tick())
    second = s.one("SELECT * FROM tasks WHERE status='running'")
    assert second["user_id"] == system["ids"]["admin"]
    s.recover()
    assert s.one("SELECT * FROM tasks WHERE id=?", (second["id"],))["status"] == "reconciliation"
    asyncio.run(system["app"].state.service.video_tick())
    assert not s.one("SELECT * FROM tasks WHERE status='running'")


def test_user_grant_revoke_and_disable_effective_immediately(system):
    c, s = system["client"], system["store"]
    caller_id = system["ids"]["caller"]
    payload = dict(role="caller", active=True, models=["h3"], profiles=[])
    assert c.patch("/api/users/"+caller_id, json=payload, headers=auth(system, "operator")).status_code == 403
    assert c.patch("/api/users/"+caller_id, json=payload, headers=auth(system)).status_code == 200
    assert c.post("/v1/chat/completions", json=CHAT, headers=auth(system, "caller")).status_code == 403
    payload["active"] = False
    assert c.patch("/api/users/"+caller_id, json=payload, headers=auth(system)).status_code == 200
    assert c.get("/api/me", headers=auth(system, "caller")).status_code == 401
    assert c.patch("/api/users/"+system["ids"]["admin"], json=payload, headers=auth(system)).status_code == 409


def test_pending_operation_rechecks_revoked_permission(system):
    c, s = system["client"], system["store"]
    response = c.post("/api/runtime/operations", json=dict(action="start", profile="h3-sp2-v1"), headers=auth(system, "operator"))
    assert response.status_code == 202
    with s.tx() as db:
        db.execute("UPDATE users SET profiles='[]' WHERE id=?", (system["ids"]["operator"],))
    system["sim"].step()
    assert s.one("SELECT * FROM operations WHERE id=?", (response.json()["id"],))["status"] == "failed"
    assert not s.one("SELECT * FROM runtimes WHERE profile='h3-sp2-v1'")["status"] == "ready"


def test_idempotency_cannot_cross_simulation_and_real_source(system):
    s = system["store"]
    who = s.identity(system["credentials"]["caller"]["key"])
    args = (who, "same-source", CHAT, "chat", "qwen3-32b", 1000, "qwen-tp2-v1", PROFILES["qwen-tp2-v1"]["hash"])
    s.reserve(*args, source="simulation")
    with pytest.raises(Fault, match="idempotency_conflict"):
        s.reserve(*args, source="qwen_via_litellm")
