import json

import pytest

from gateway.catalog import (MAX_CATALOG_BYTES, ModelCatalogError, authorized_catalog,
                             load_model_catalog)
from gateway.store import Store


def model(**updates):
    return dict(id="example-text", display_name="示例文本模型", kind="text",
                deployment_status="planned", description="登记后等待部署。", **updates)


def save(path, entries):
    path.write_text(json.dumps({"models": entries}, ensure_ascii=False), encoding="utf-8")


def test_catalog_registration_never_claims_readiness_and_reloads(tmp_path):
    path = tmp_path / "catalog.json"
    save(path, [model(upstream_model="engine/model-name")])
    rows = load_model_catalog(path, cloud_preview=True)
    assert rows[0]["status"] == "planned"
    assert rows[0]["upstream_model"] == "engine/model-name"
    assert rows[0]["callable"] is False
    assert rows[0]["health"] == "unverified"
    assert rows[0]["real_control_enabled"] is False
    changed = model()
    changed["deployment_status"] = "offline"
    save(path, [changed])
    assert load_model_catalog(path)[0]["status"] == "offline"


def test_cloud_default_is_empty_and_explicit_failures_do_not_fallback(tmp_path):
    assert load_model_catalog(cloud_preview=True) == []
    demo = load_model_catalog()
    assert {row["id"] for row in demo} == {"qwen3-32b", "h3"}
    assert all(row["status"] == "simulation" and not row["callable"] for row in demo)
    path = tmp_path / "missing.json"
    for cloud in (False, True):
        with pytest.raises(ModelCatalogError):
            load_model_catalog(path, cloud_preview=cloud)
    for payload in ("not json", "null", "[]", '{"models":{}}', '{"models":[],"shell":"false"}',
                    '{"models":[],"models":[]}', '{"models":[{"id":"a","id":"b"}]}'):
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ModelCatalogError):
            load_model_catalog(path)
    path.write_bytes(b"x" * (MAX_CATALOG_BYTES + 1))
    with pytest.raises(ModelCatalogError, match="too_large"):
        load_model_catalog(path)


@pytest.mark.parametrize("field,value", [
    ("id", "../../model"), ("id", ""), ("id", 1), ("id", "a" * 97),
    ("display_name", ""), ("display_name", "a" * 81), ("display_name", "hidden\nline"),
    ("kind", "shell"), ("deployment_status", "ready"), ("deployment_status", "simulation"),
    ("description", "a" * 501), ("description", None),
    ("upstream_model", "https://example.test/v1"), ("upstream_model", "$(run)"),
    ("upstream_model", "a" * 161), ("upstream_model", None),
    ("approved", True), ("url", "http://127.0.0.1:1"), ("command", "start"),
])
def test_catalog_rejects_invalid_fields_and_execution_settings(tmp_path, field, value):
    entry = model()
    entry[field] = value
    path = tmp_path / "catalog.json"
    save(path, [entry])
    with pytest.raises(ModelCatalogError):
        load_model_catalog(path)


def test_catalog_rejects_duplicate_ids_and_large_lists(tmp_path):
    path = tmp_path / "catalog.json"
    save(path, [model(), model()])
    with pytest.raises(ModelCatalogError, match="duplicate_id"):
        load_model_catalog(path)
    save(path, [dict(model(), id=f"model-{index}") for index in range(65)])
    with pytest.raises(ModelCatalogError, match="too_many_models"):
        load_model_catalog(path)


def test_catalog_visibility_uses_store_user_key_intersection_and_hides_upstream(tmp_path):
    path = tmp_path / "catalog.json"
    save(path, [model(upstream_model="private-provider-name"),
                dict(model(), id="example-second"), dict(model(), id="example-third")])
    rows = load_model_catalog(path)
    store = Store(tmp_path / "test.sqlite")
    user_id = store.create_user("member", "caller", ["example-text", "example-second"], [], 100)
    key = store.issue_key(user_id, ["example-text"], 100)["key"]
    identity = store.identity(key)
    result = authorized_catalog(identity, rows)
    assert [row["id"] for row in result] == ["example-text"]
    assert result[0]["authorized"] is True
    assert result[0]["callable"] is False
    assert "private-provider-name" not in json.dumps(result)
    assert "upstream_model" not in result[0]
    identity["user"]["role"] = "admin"
    managed = authorized_catalog(identity, rows)
    assert len(managed) == 3
    assert [row["authorized"] for row in managed] == [True, False, False]
    assert all(not row["callable"] and "upstream_model" not in row for row in managed)
    identity["user"]["role"] = "auditor"
    assert authorized_catalog(identity, rows) == []
    identity["user"]["role"] = "caller"
    identity["user"]["models"] = '[]'
    assert authorized_catalog(identity, rows) == []
    for invalid in ('invalid', '{"example-text":true}', '[null]', None):
        identity["user"]["models"] = invalid
        assert authorized_catalog(identity, rows) == []
