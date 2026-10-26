import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.store import Store
from gateway.upstream import CloudText, load_upstream_config

ORIGIN = "https://models.example.test"
ALIAS = "research-text"
SECRET = "fixture_upstream_credential_only"
CONFIG = {"alias": ALIAS, "served_model": "private-engine-id", "base_url": "http://127.0.0.1:18080/v1"}


@pytest.fixture
def pilot(tmp_path):
    store = Store(tmp_path / "gateway.sqlite", seed_demo=False)
    catalog = tmp_path / "model-catalog.local.json"
    catalog.write_text(json.dumps({"models": [{"id": ALIAS, "display_name": "研究文本模型", "kind": "text", "deployment_status": "offline"}]}))
    headers, users = {}, {}
    for role in ("admin", "caller", "operator"):
        users[role] = store.create_user(role, role, [ALIAS], [], 100000)
        headers[role] = {"Authorization": "Bearer " + store.issue_key(users[role], [ALIAS], 100000)["key"]}
    calls = []
    behavior = {"usage_valid": True, "served_model": CONFIG["served_model"], "post_error": False}

    async def upstream(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + SECRET
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": behavior["served_model"]}]})
        if behavior["post_error"]:
            raise httpx.ReadTimeout("sensitive upstream exception must never be echoed")
        body = json.loads(request.content)
        assert set(body) == {"model", "messages", "max_tokens", "temperature", "stream"}
        assert body["model"] == CONFIG["served_model"] and body["stream"] is False
        return httpx.Response(200, json={"id": "chatcmpl-fixture", "model": CONFIG["served_model"],
            "choices": [{"message": {"role": "assistant", "content": "synthetic reply"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10 if behavior["usage_valid"] else 0}})

    provider = CloudText(CONFIG, tmp_path, transport=httpx.MockTransport(upstream))
    app = create_app(store, provider, cloud_api=True, public_origin=ORIGIN, model_catalog_path=catalog)
    with TestClient(app, base_url=ORIGIN) as client:
        yield store, catalog, users, headers, provider, calls, behavior, client


def provision(pilot):
    return pilot[-1].post("/api/upstream/credential", headers=pilot[3]["admin"], json={"value": SECRET})


def chat(pilot, *, key="same-request", **changes):
    body = {"model": ALIAS, "messages": [{"role": "user", "content": "synthetic request"}], "max_tokens": 32}
    body.update(changes)
    return pilot[-1].post("/v1/chat/completions", headers={**pilot[3]["caller"], "Idempotency-Key": key}, json=body)


def test_explicit_cloud_pilot_keeps_simulation_and_control_disabled(pilot):
    store, _, _, headers, provider, calls, _, client = pilot
    assert client.get("/health").json()["mode"] == "cloud-api"
    assert not store.rows("SELECT * FROM runtimes") and not store.rows("SELECT * FROM routes")
    assert client.get("/api/runtime", headers=headers["admin"]).json()["source"] == "disabled"
    for path in ("/api/local/shutdown", "/api/runtime/operations"):
        assert client.post(path, headers=headers["admin"], json={}).status_code == 503
    assert client.post("/v1/videos", headers=headers["caller"], json={}).status_code == 501
    assert client.get("/api/me", headers=headers["operator"]).status_code == 403
    with pytest.raises(ValueError):
        create_app(store, provider, cloud_api=True, cloud_preview=True, public_origin=ORIGIN)


def test_upstream_key_requires_admin_and_is_never_returned_or_audited(pilot):
    store, _, _, headers, provider, _, _, client = pilot
    assert client.post("/api/upstream/credential", json={"value": SECRET}).status_code == 401
    assert client.post("/api/upstream/credential", headers=headers["caller"], json={"value": SECRET}).status_code == 403
    response = provision(pilot)
    assert response.status_code == 200
    assert SECRET not in response.text and SECRET not in json.dumps(store.rows("SELECT * FROM audit"))
    assert provider.key_path.read_text().strip() == SECRET
    if os.name != "nt":
        assert provider.key_path.stat().st_mode & 0o777 == 0o600
    assert client.get("/api/model-catalog", headers=headers["caller"]).json()["models"][0]["callable"] is True
    assert client.get("/api/model-catalog", headers=headers["admin"]).json()["models"][0]["callable"] is False


def test_catalog_refreshes_expired_health_without_inference(pilot):
    _, _, _, headers, provider, calls, _, client = pilot
    provision(pilot)
    calls.clear()
    provider.verified_at = 0
    result = client.get("/api/model-catalog", headers=headers["admin"])
    assert result.status_code == 200
    assert result.json()["models"][0]["status"] == "ready"
    assert [call.method for call in calls] == ["GET"]
    client.get("/api/model-catalog", headers=headers["admin"])
    assert len(calls) == 1  # Fresh health is cached, not a probe on every render.


def test_catalog_distinguishes_missing_credentials_and_failed_probe(pilot):
    _, _, _, headers, provider, _, behavior, client = pilot
    item = client.get("/api/model-catalog", headers=headers["admin"]).json()["models"][0]
    assert item["status"] == "unconfigured"
    provision(pilot)
    provider.verified_at = 0
    behavior["served_model"] = "wrong-model"
    item = client.get("/api/model-catalog", headers=headers["admin"]).json()["models"][0]
    assert item["status"] == "unavailable" and item["callable"] is False


def test_unlimited_member_defaults_still_record_exact_usage_and_release(pilot):
    store, _, _, headers, _, _, _, client = pilot
    provision(pilot)
    response = client.post("/api/users", headers=headers["admin"], json={
        "name": "unlimited-member", "role": "caller", "models": [ALIAS]})
    assert response.status_code == 201
    member = response.json()["id"]
    issued = client.post("/api/keys", headers=headers["admin"], json={"user_id": member, "models": [ALIAS]})
    assert issued.status_code == 201
    assert store.one("SELECT budget FROM users WHERE id=?", (member,))["budget"] == -1
    assert store.one("SELECT budget FROM keys WHERE id=?", (issued.json()["id"],))["budget"] == -1
    result = client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + issued.json()["key"]},
                         json={"model": ALIAS, "messages": [{"role": "user", "content": "synthetic"}], "max_tokens": 32})
    assert result.status_code == 200
    assert store.one("SELECT spent,reserved FROM users WHERE id=?", (member,)) == {"spent": 10, "reserved": 0}
    assert store.one("SELECT spent,reserved FROM keys WHERE id=?", (issued.json()["id"],)) == {"spent": 10, "reserved": 0}
    assert store.one("SELECT COUNT(*) AS n FROM ledger WHERE event='settle'")["n"] == 1


@pytest.mark.parametrize("user_budget,key_budget", [(-1, 0), (0, -1)])
def test_unlimited_one_side_never_bypasses_other_side_limit(pilot, user_budget, key_budget):
    store, _, users, _, _, _, _, _ = pilot
    provision(pilot)
    with store.tx() as db:
        db.execute("UPDATE users SET budget=? WHERE id=?", (user_budget, users["caller"]))
        db.execute("UPDATE keys SET budget=? WHERE user_id=?", (key_budget, users["caller"]))
    assert chat(pilot).status_code == 402


def test_admin_can_change_budget_to_unlimited_without_resetting_usage(pilot):
    store, _, users, headers, _, _, _, client = pilot
    with store.tx() as db:
        db.execute("UPDATE users SET spent=15 WHERE id=?", (users["caller"],))
    result = client.patch("/api/users/" + users["caller"] + "/budget", headers=headers["admin"], json={"budget": -1})
    assert result.status_code == 200
    assert store.one("SELECT budget,spent FROM users WHERE id=?", (users["caller"],)) == {"budget": -1, "spent": 15}


def test_unlimited_unknown_request_keeps_reservation_and_global_protection(pilot):
    store, _, users, _, _, calls, behavior, _ = pilot
    provision(pilot)
    with store.tx() as db:
        db.execute("UPDATE users SET budget=-1 WHERE id=?", (users["caller"],))
        db.execute("UPDATE keys SET budget=-1 WHERE user_id=?", (users["caller"],))
    behavior["post_error"] = True
    assert chat(pilot).status_code == 502
    row = store.one("SELECT status,reserved FROM requests")
    assert row["status"] == "reconciliation" and row["reserved"] > 0
    assert store.one("SELECT spent,reserved FROM users WHERE id=?", (users["caller"],)) == {"spent": 0, "reserved": row["reserved"]}
    assert chat(pilot, key="another-request").status_code == 429
    assert len([call for call in calls if call.method == "POST"]) == 1


def test_concurrent_cached_health_checks_probe_once(pilot):
    _, _, _, _, provider, _, _, _ = pilot
    provision(pilot)
    provider.verified_at = 0
    reads = []

    async def upstream(request):
        reads.append(request.method)
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"data": [{"id": CONFIG["served_model"]}]})

    provider.transport = httpx.MockTransport(upstream)

    async def check():
        return await asyncio.gather(*(provider.probe(cached=True) for _ in range(5)))

    assert len(asyncio.run(check())) == 5
    assert reads == ["GET"]


def test_member_models_and_one_settlement_with_idempotent_replay(pilot):
    store, _, _, headers, _, calls, _, client = pilot
    assert provision(pilot).status_code == 200
    assert client.get("/v1/models", headers=headers["admin"]).status_code == 403
    assert client.get("/v1/models", headers=headers["caller"]).json()["data"][0]["id"] == ALIAS
    response = chat(pilot)
    assert response.status_code == 200 and response.json()["model"] == ALIAS
    assert response.json()["usage"]["total_tokens"] == 10
    replay = chat(pilot)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert replay.headers["X-Idempotent-Replay"] == "true"
    assert len([call for call in calls if call.method == "POST"]) == 1
    assert len(store.rows("SELECT * FROM ledger WHERE event='settle'")) == 1
    assert store.one("SELECT reserved,spent FROM users WHERE role='caller'") == {"reserved": 0, "spent": 10}
    assert "private-engine-id" not in response.text and SECRET not in response.text


def test_missing_credential_is_not_dispatched_and_releases_reservation(pilot):
    store, _, _, _, _, calls, _, _ = pilot
    result = chat(pilot)
    assert result.status_code == 503 and result.json()["submitted_to_model"] is False
    assert not calls
    assert store.one("SELECT status,actual FROM requests") == {"status": "failed", "actual": 0}
    assert store.one("SELECT reserved,spent FROM users WHERE role='caller'") == {"reserved": 0, "spent": 0}


@pytest.mark.parametrize("mode", ["post_error", "invalid_usage"])
def test_unknown_submission_is_never_retried_and_blocks_new_dispatch(pilot, mode):
    store, _, _, _, _, calls, behavior, _ = pilot
    provision(pilot)
    if mode == "post_error":
        behavior["post_error"] = True
    else:
        behavior["usage_valid"] = False
    result = chat(pilot)
    assert result.status_code == 502 and result.json()["submitted_to_model"] is True
    assert "sensitive upstream" not in result.text and SECRET not in result.text
    assert store.one("SELECT status FROM requests")["status"] == "reconciliation"
    assert store.one("SELECT reserved FROM users WHERE role='caller'")["reserved"] > 0
    assert chat(pilot).status_code == 409
    assert chat(pilot, key="different-request").status_code == 429
    assert len([call for call in calls if call.method == "POST"]) == 1


def test_quota_revocation_and_registration_removal_prevent_dispatch(pilot):
    store, catalog, users, headers, _, calls, _, client = pilot
    provision(pilot)
    with store.tx() as db:
        db.execute("UPDATE users SET budget=1 WHERE id=?", (users["caller"],))
    assert chat(pilot).status_code == 402
    catalog.write_text('{"models":[]}')
    assert chat(pilot).status_code == 503
    with store.tx() as db:
        db.execute("UPDATE keys SET revoked=1 WHERE user_id=?", (users["caller"],))
    assert chat(pilot).status_code == 401
    assert not any(call.method == "POST" for call in calls)


def test_mismatched_upstream_key_provision_never_commits(pilot):
    pilot[6]["served_model"] = "different-model"
    response = provision(pilot)
    assert response.status_code == 503
    assert not pilot[4].key_path.exists() and not pilot[4].rotating


def test_scope_transport_and_unsupported_protocols(pilot):
    _, _, _, headers, _, calls, _, client = pilot
    assert client.get("/api/me", headers={**headers["admin"], "Origin": "https://other.example.test"}).status_code == 403
    assert client.get("/api/me", headers={**headers["admin"], "Host": "other.example.test"}).status_code == 400
    assert client.get("http://models.example.test/api/me", headers=headers["admin"], follow_redirects=False).status_code == 403
    assert chat(pilot, stream=True).status_code == 501
    assert chat(pilot, api_base="https://other.example.test").status_code == 422
    assert chat(pilot, tools=[]).status_code == 422
    assert not calls


@pytest.mark.parametrize("base", ["https://other.example.test/v1", "http://localhost:18080/v1", "http://127.0.0.1:18080/v1?x=1", "http://user:pass@127.0.0.1:18080/v1"])
def test_upstream_config_rejects_non_fixed_targets(tmp_path, base):
    path = tmp_path / "upstream.json"
    path.write_text(json.dumps({**CONFIG, "base_url": base}))
    with pytest.raises(ValueError):
        load_upstream_config(path)


def test_store_global_admission_is_across_distinct_member_keys(pilot):
    store, _, _, headers, provider, _, _, _ = pilot
    first = store.identity(headers["caller"]["Authorization"][7:])
    second_id = store.create_user("second-member", "caller", [ALIAS], [], 100000)
    second = store.identity(store.issue_key(second_id, [ALIAS], 100000)["key"])
    args = ("id", {"model": ALIAS}, "chat", ALIAS, 100, provider.profile, provider.profile_hash)
    store.reserve(first, *args, runtime_required=False, global_concurrency=1)
    from gateway.store import Fault
    with pytest.raises(Fault) as error:
        store.reserve(second, *args, runtime_required=False, global_concurrency=1)
    assert error.value.code == "gateway_capacity_or_reconciliation_limit"


def test_role_changes_are_rechecked_inside_reservation_transaction(pilot):
    store, _, users, headers, provider, _, _, _ = pilot
    stale_identity = store.identity(headers["caller"]["Authorization"][7:])
    with store.tx() as db:
        db.execute("UPDATE users SET role='admin' WHERE id=?", (users["caller"],))
    from gateway.store import Fault
    with pytest.raises(Fault) as error:
        store.reserve(stale_identity, "stale-role", {}, "chat", ALIAS, 100, provider.profile, provider.profile_hash,
                      runtime_required=False, global_concurrency=1, required_role="caller")
    assert error.value.code == "member_calling_key_required"
    assert not store.rows("SELECT * FROM requests")


def test_concurrent_members_dispatch_only_one_upstream_call(pilot):
    store, _, _, headers, provider, _, _, client = pilot
    provision(pilot)
    member_id = store.create_user("parallel-member", "caller", [ALIAS], [], 100000)
    other = {"Authorization": "Bearer " + store.issue_key(member_id, [ALIAS], 100000)["key"]}

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        posts = []

        async def upstream(request):
            posts.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"id": "parallel-fixture", "model": CONFIG["served_model"],
                "choices": [{"message": {"role": "assistant", "content": "synthetic"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        provider.transport = httpx.MockTransport(upstream)
        body = {"model": ALIAS, "messages": [{"role": "user", "content": "synthetic"}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url=ORIGIN) as http:
            first = asyncio.create_task(http.post("/v1/chat/completions", headers=headers["caller"], json=body))
            await asyncio.wait_for(entered.wait(), 3)
            try:
                second = await http.post("/v1/chat/completions", headers=other, json=body)
                assert second.status_code == 429
            finally:
                release.set()
            assert (await first).status_code == 200
        assert len(posts) == 1
    asyncio.run(run())


def test_redirects_do_not_forward_secret_or_retry(pilot):
    provision(pilot)
    provider = pilot[4]
    calls = []

    async def redirect(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example.test/steal"})

    provider.transport = httpx.MockTransport(redirect)
    response = chat(pilot)
    assert response.status_code == 502
    assert len(calls) == 1 and calls[0].url.host == "127.0.0.1"
