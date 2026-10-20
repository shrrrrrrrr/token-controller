"""Public synthetic catalog; private snapshots stay in ignored .state/ only."""
import hashlib
import json
from pathlib import Path

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
