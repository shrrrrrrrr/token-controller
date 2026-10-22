import json

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.deployment import validate_public_origin
from gateway.store import Fault, Store

ORIGIN = "https://models.example.test"
MODEL = {"id": "research-text", "display_name": "研究模型 · 待部署", "kind": "text", "deployment_status": "planned"}


@pytest.fixture
def preview(tmp_path):
    store = Store(tmp_path / "cloud.sqlite", seed_demo=False)
    catalog = tmp_path / "model-catalog.local.json"
    catalog.write_text(json.dumps({"models": [dict(MODEL, upstream_model="private-engine-id")]}), encoding="utf-8")
    ids, headers = {}, {}
    for role in ("admin", "caller", "operator"):
        ids[role] = store.create_user(role, role, [] if role == "admin" else [MODEL["id"]], [], 10000)
        token = store.issue_key(ids[role], [] if role == "admin" else [MODEL["id"]], 10000)["key"]
        headers[role] = {"Authorization": "Bearer " + token}
    app = create_app(store, cloud_preview=True, public_origin=ORIGIN, model_catalog_path=catalog)
    with TestClient(app, base_url=ORIGIN) as client:
        yield store, catalog, ids, headers, client


def test_preview_never_executes_or_seeds_ready_models(preview):
    store, _, _, headers, client = preview
    assert client.get("/health").json() == dict(status="ok", mode="cloud-preview", text_source="unavailable", runtime_source="disabled")
    assert not store.rows("SELECT * FROM runtimes")
    assert not store.rows("SELECT * FROM routes")
    for path in ("/v1/chat/completions", "/v1/videos", "/api/qwen/probe", "/api/local/shutdown", "/api/runtime/operations"):
        for suffix in ("", "/"):
            response = client.post(path + suffix, headers=headers["admin"], json={})
            assert response.status_code == 503
    assert client.patch("/api/runtime/policy", headers=headers["admin"], json={}).status_code == 503
    assert client.get("/v1/models", headers=headers["admin"]).status_code == 503
    for table in ("requests", "tasks", "ledger", "operations"):
        assert not store.rows("SELECT * FROM " + table)
    runtime = client.get("/api/runtime", headers=headers["admin"]).json()
    assert runtime["source"] == "disabled" and runtime["instances"] == []
    inv = client.get("/api/inventory", headers=headers["admin"]).json()
    assert inv["assets"][0]["id"] == MODEL["id"] and not inv["gpus"]
    assert "private-engine-id" not in json.dumps(inv)


def test_preview_dynamic_model_registration_and_grants(preview):
    store, catalog, ids, headers, client = preview
    assert client.get("/api/model-catalog").status_code == 401
    admin_view = client.get("/api/model-catalog", headers=headers["admin"]).json()
    assert admin_view["models"][0]["authorized"] is False
    caller_view = client.get("/api/model-catalog", headers=headers["caller"]).json()
    assert caller_view["models"][0]["authorized"] is True
    assert caller_view["models"][0]["callable"] is False
    assert "private-engine-id" not in json.dumps(caller_view)
    user = client.post("/api/users", headers=headers["admin"], json=dict(name="new-member", role="caller", models=[MODEL["id"]], budget=10))
    assert user.status_code == 201
    token = client.post("/api/keys", headers=headers["admin"], json=dict(user_id=user.json()["id"], models=[MODEL["id"]], budget=10))
    assert token.status_code == 201
    bad_user = client.post("/api/users", headers=headers["admin"], json=dict(name="unknown-model", role="caller", models=["not-registered"], budget=10))
    assert bad_user.status_code == 400
    assert client.post("/api/users", headers=headers["admin"], json=dict(name="old-role", role="operator", models=[], budget=10)).status_code == 422
    assert client.get("/api/me", headers=headers["operator"]).status_code == 403
    # Replacing the private file changes lists without restart or retaining stale authorization.
    catalog.write_text('{"models":[]}', encoding="utf-8")
    assert client.get("/api/model-catalog", headers=headers["caller"]).json()["models"] == []
    assert client.get("/api/model-requests", headers=headers["caller"]).json()["allowed_models"] == []
    reservation = dict(model=MODEL["id"], purpose="sample project test", estimated_minutes=10, client_ack=True, client_request_id="example-reservation")
    assert client.post("/api/model-requests", headers=headers["caller"], json=reservation).status_code == 400
    catalog.write_text('{broken', encoding="utf-8")
    response = client.get("/api/model-catalog", headers=headers["admin"])
    assert response.status_code == 503 and response.json()["error"]["code"] == "model_catalog_unavailable"


def test_registered_offline_model_can_be_reserved_but_not_called(preview):
    store, _, _, headers, client = preview
    body = dict(model=MODEL["id"], purpose="sample project test", estimated_minutes=10, client_ack=True, client_request_id="example-reservation")
    assert client.post("/api/model-requests", headers=headers["caller"], json=body).status_code == 202
    assert not store.rows("SELECT * FROM ledger")
    assert client.post("/v1/chat/completions", headers=headers["caller"], json=dict(model=MODEL["id"], messages=[dict(role="user", content="test")])).status_code == 503
    assert not store.rows("SELECT * FROM requests")


def test_preview_transport_origin_host_and_schema(preview):
    _, _, _, headers, client = preview
    assert client.get("/api/me", headers={**headers["admin"], "Origin": "https://other.example.test"}).status_code == 403
    assert client.get("/api/me", headers={**headers["admin"], "Origin": "null"}).status_code == 403
    assert client.get("/api/me", headers={**headers["admin"], "Origin": ORIGIN}).status_code == 200
    assert client.get("/api/me", headers={**headers["admin"], "Host": "evil.example.test"}).status_code == 400
    assert client.get("http://127.0.0.1/api/me", headers={**headers["admin"], "X-Forwarded-Proto": "https"}).status_code == 403
    assert client.get("http://127.0.0.1/health").status_code == 200
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/api/me", headers={"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 401
    assert client.post("/api/users", headers={**headers["admin"], "Content-Length": "262145"}, content=b"x").status_code == 413


@pytest.mark.parametrize("origin", [None, "http://models.example.test", "https://user:pass@models.example.test", "https://models.example.test/path", "https://models.example.test?secret=x", "https://*.example.test", "https://localhost", "https://models.example.test:8000"])
def test_reject_unsafe_preview_origin(origin):
    with pytest.raises(ValueError):
        validate_public_origin(origin)


def test_preview_cannot_attach_provider_or_controller(tmp_path):
    store = Store(tmp_path / "cloud.sqlite", seed_demo=False)
    for kwargs in (dict(provider=object()), dict(control_url="http://127.0.0.1:9000"), dict(control_token="anything")):
        with pytest.raises(ValueError):
            create_app(store, cloud_preview=True, public_origin=ORIGIN, **kwargs)


def test_admin_revocation_is_rechecked_in_create_transactions(tmp_path):
    store = Store(tmp_path / "cloud.sqlite", seed_demo=False)
    admin = store.create_user("admin", "admin", [], [], 100)
    token = store.issue_key(admin, [], 100)
    who = store.identity(token["key"])
    with store.tx() as db:
        db.execute("UPDATE keys SET revoked=1 WHERE id=?", (token["id"],))
    with pytest.raises(Fault, match="invalid_or_expired_key"):
        store.create_user("blocked", "caller", [], [], 10, authorization=who)
    with pytest.raises(Fault, match="invalid_or_expired_key"):
        store.issue_key(admin, [], 10, authorization=who)


def test_cli_cloud_mode_has_no_control_child_or_demo_fallback(tmp_path, monkeypatch, capsys):
    import gateway.__main__ as cli
    import uvicorn
    state = tmp_path / "state"
    args = ["gateway", "bootstrap", "--state", str(state), "--cloud-preview", "--public-origin", ORIGIN]
    monkeypatch.setattr(cli.sys, "argv", args)
    cli.main()
    capsys.readouterr()  # Do not print the test-only bootstrap credential.
    observed = []

    def fake_run(app, **kwargs):
        observed.append(kwargs)
        assert app.state.service.provider.source == "unavailable"
        assert app.state.service.control_url is None

    def forbidden_child(*args, **kwargs):
        raise AssertionError("cloud mode must not spawn any control process")

    monkeypatch.setattr(cli.subprocess, "Popen", forbidden_child)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(cli.sys, "argv", [*args[:1], "serve", *args[2:]])
    cli.main()
    assert observed[0]["host"] == "127.0.0.1"
    assert observed[0]["forwarded_allow_ips"] == "127.0.0.1"
    assert observed[0]["workers"] == 1
    monkeypatch.setattr(cli.sys, "argv", ["gateway", "serve", "--state", str(state)])
    with pytest.raises(SystemExit):
        cli.main()
