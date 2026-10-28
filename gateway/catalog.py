"""Public synthetic catalog; private snapshots stay in ignored .state/ only."""
import hashlib
import json
import re
from pathlib import Path
from .branding import VENDORS, vendor_info

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "qwen-tp2-v1": dict(asset="qwen3-32b", image="example-text-image", gpus=[0, 1], port=8000,
                        container="example-text", engine="vLLM (example)", approved=False, protected=True),
    "h3-single-v1": dict(asset="h3", image="example-video-image", gpus=[2], port=30010,
                         container="example-video-single", engine="video single (example)", approved=False, protected=False),
    "h3-sp2-v1": dict(asset="h3", image="example-video-image", gpus=[2, 3], port=30010,
                      container="example-video-dual", engine="video dual (example)", approved=False, protected=False),
}
for name, profile in PROFILES.items():
    profile["id"] = name
    profile["hash"] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()

FIXTURE_SHA = "e3606ac833065e2388a48448c11b197401cae5d13b3dbb396470b44948601841"

MAX_CATALOG_BYTES = 128 * 1024
MAX_MODELS = 64
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")
UPSTREAM_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}\Z")
CATALOG_FIELDS = {"id", "display_name", "kind", "deployment_status", "description", "upstream_model", "vendor"}


class ModelCatalogError(ValueError):
    """An absent or invalid explicit catalog must not become a simulated service."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ModelCatalogError("model_catalog_duplicate_json_field")
        result[key] = value
    return result


def _catalog_text(value, maximum, *, empty=False):
    return (isinstance(value, str) and len(value) <= maximum
            and (empty or bool(value.strip()))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _validate_catalog(raw):
    if not isinstance(raw, dict) or set(raw) != {"models"} or not isinstance(raw["models"], list):
        raise ModelCatalogError("model_catalog_invalid_document")
    if len(raw["models"]) > MAX_MODELS:
        raise ModelCatalogError("model_catalog_too_many_models")
    result, seen = [], set()
    for item in raw["models"]:
        if not isinstance(item, dict) or set(item) - CATALOG_FIELDS:
            raise ModelCatalogError("model_catalog_invalid_fields")
        model_id = item.get("id")
        if not isinstance(model_id, str) or MODEL_ID.fullmatch(model_id) is None:
            raise ModelCatalogError("model_catalog_invalid_id")
        if model_id in seen:
            raise ModelCatalogError("model_catalog_duplicate_id")
        seen.add(model_id)
        if not _catalog_text(item.get("display_name"), 80):
            raise ModelCatalogError("model_catalog_invalid_display_name")
        if 'vendor' in item and (not isinstance(item['vendor'], str) or item['vendor'] not in VENDORS):
            raise ModelCatalogError('model_catalog_invalid_vendor')
        if item.get("kind") not in ("text", "video"):
            raise ModelCatalogError("model_catalog_invalid_kind")
        # Readiness comes from the runtime health check, never from this file.
        if item.get("deployment_status") not in ("planned", "offline"):
            raise ModelCatalogError("model_catalog_invalid_deployment_status")
        if not _catalog_text(item.get("description", ""), 500, empty=True):
            raise ModelCatalogError("model_catalog_invalid_description")
        if "upstream_model" in item and (
                not isinstance(item["upstream_model"], str)
                or UPSTREAM_MODEL.fullmatch(item["upstream_model"]) is None):
            raise ModelCatalogError("model_catalog_invalid_upstream_model")
        result.append(dict(item, **vendor_info(model_id, item.get('vendor')), description=item.get("description", ""),
                           status=item["deployment_status"], callable=False,
                           real_control_enabled=False, health="unverified",
                           source="private_local_catalog_not_live"))
    return result


def load_model_catalog(path=None, cloud_preview=False):
    """Read bounded local registration metadata without network or control calls.

    Local callers without a path keep the two synthetic demo entries. Cloud
    callers without a path receive an empty catalog. An explicitly supplied
    missing or invalid file raises ModelCatalogError, with no fallback. Call on
    each listing to pick up an operator's atomic replacement of the file.
    """
    if path is None:
        if cloud_preview:
            return []
        return [dict(id=model_id, display_name=label, kind=kind,
                     description="仅用于本地演示，未核验实际模型服务。",
                     deployment_status="simulation", status="simulation", callable=False,
                     real_control_enabled=False, health="unverified", source="synthetic_catalog_only")
                for model_id, label, kind in (("glm5.3-flash", "GLM-5.3-Flash", "text"),
                                             ("qwen3.8-flash-next", "Qwen3.8-Flash-Next", "text"),
                                             ("qwen3-32b", "Qwen3-32B", "text"),
                                             ("h3", "MiniMax-H3", "video"))]
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(MAX_CATALOG_BYTES + 1)
        if len(payload) > MAX_CATALOG_BYTES:
            raise ModelCatalogError("model_catalog_too_large")
        raw = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except ModelCatalogError:
        raise
    except (OSError, ValueError, RecursionError):
        raise ModelCatalogError("model_catalog_unavailable_or_invalid_json") from None
    return _validate_catalog(raw)


def _model_grants(record):
    """Store identities use JSON strings; malformed grants grant nothing."""
    value = record.get("models", [])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return set()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return set()
    return set(value)


def authorized_catalog(who, catalog):
    """Return display-safe registrations, not a claim that inference is ready.

    Administrators can manage every registration, but their invocation grants
    still equal the user/key intersection. Other identities see only that
    intersection; the legacy auditor role cannot invoke models. Upstream
    identifiers and any future routing fields never enter this public view.
    """
    role = who["user"]["role"]
    if role == "auditor":
        return []
    allowed = _model_grants(who["user"]) & _model_grants(who["key"])
    fields = ("id", "display_name", "kind", "description", "deployment_status", "status", "source")
    return [dict({field: item[field] for field in fields}, **vendor_info(item['id'], item.get('vendor')), authorized=item["id"] in allowed,
                 callable=False, real_control_enabled=False, health="unverified")
            for item in catalog if role == "admin" or item["id"] in allowed]

def evidence():
    path = ROOT / "examples" / "inventory.synthetic.json"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(source="synthetic_fixture_hash_check", files={path.name: actual},
                checks=[dict(name=path.name, expected=FIXTURE_SHA, actual=actual, match=actual == FIXTURE_SHA)],
                server_live_checked=False, gpu_live_checked=False)

def inventory():
    private = ROOT / ".state" / "inventory.local.json"
    path = private if private.is_file() else ROOT / "examples" / "inventory.synthetic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    # A file is a snapshot, never a live health claim or a profile authorization.
    data["source"] = "private_local_snapshot_not_live" if path == private else "synthetic_example_only"
    data["stale"] = True
    data["profiles"] = list(PROFILES.values())
    for instance in data["instances"]:
        instance["health"] = "unknown"
        instance["observed_state"] = "unknown"
    return data
