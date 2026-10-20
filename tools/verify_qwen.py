"""Opt-in live Qwen verification through an existing localhost tunnel.

Never launches SSH, Docker, a GPU model or a public listener. Does not print keys.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from gateway.store import Store


def main():
    state = BASE / ".state" / "qwen-validation"
    store = Store(state / "gateway.sqlite")
    existing = store.rows("SELECT id,status,actual,input_tokens,output_tokens FROM requests ORDER BY created")
    if existing:
        print(json.dumps(dict(not_resubmitted=True, reason="已有验证请求；避免重复提交，先核对原记录", requests=existing), ensure_ascii=False))
        return
    admin_id = store.create_user("validation-admin", "admin", [], [], 10000)
    caller_id = store.create_user("validation-caller", "caller", ["qwen3-32b"], [], 10000)
    admin = store.issue_key(admin_id, [], 10000)["key"]
    caller = store.issue_key(caller_id, ["qwen3-32b"], 10000)["key"]
    env = dict(os.environ, QWEN_API_BASE="http://127.0.0.1:18000/v1", LITELLM_LOCAL_MODEL_COST_MAP="True")
    # No unaudited upstream key is needed for the observed local service; if chat
    # requires authentication, fail and reconcile rather than requesting a secret.
    env.pop("QWEN_API_KEY", None)
    log_path = state / "process.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "gateway", "serve", "--state", str(state), "--port", "8766", "--qwen"],
                                   cwd=BASE, env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8766", trust_env=False, timeout=150) as client:
                for _ in range(60):
                    if process.poll() is not None:
                        raise RuntimeError("local_validation_gateway_failed")
                    try:
                        if client.get("/health", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError("local_validation_gateway_timeout")
                probe = client.post("/api/qwen/probe", json={}, headers={"Authorization": "Bearer "+admin})
                if probe.status_code != 200:
                    raise RuntimeError("qwen_read_only_probe_failed")
                headers = {"Authorization": "Bearer "+caller, "Idempotency-Key": "live-qwen-minimal-v1"}
                body = dict(model="qwen3-32b", messages=[dict(role="user", content="/no_think\n请仅回复OK。")], max_tokens=16, temperature=0.0)
                first = client.post("/v1/chat/completions", json=body, headers=headers)
                if first.status_code != 200:
                    raise RuntimeError("qwen_request_failed_or_unknown_check_ledger")
                second = client.post("/v1/chat/completions", json=body, headers=headers)
                assert second.status_code == 200 and second.headers.get("X-Idempotent-Replay") == "true"
                assert first.json() == second.json()
                req = store.request(first.headers["X-Request-ID"])
                user = store.one("SELECT * FROM users WHERE id=?", (caller_id,))
                ledger = store.rows("SELECT event,points FROM ledger WHERE request_id=? ORDER BY id", (req["id"],))
                assert req["status"] == "succeeded" and req["actual"] == first.json()["usage"]["total_tokens"]
                assert user["reserved"] == 0 and user["spent"] == req["actual"]
                assert sum(l["event"] == "settle" for l in ledger) == 1
                result = dict(source="real_qwen_via_user_local_ssh_tunnel", model="qwen3-32b", request_id=req["id"],
                              text=first.json()["choices"][0]["message"]["content"], usage=first.json()["usage"],
                              ledger=ledger, actual_points=req["actual"], remaining_reserved=user["reserved"],
                              idempotent_replay=True, server_lifecycle_changes=False, gpu_benchmark=False)
                (BASE / "docs" / "qwen-live-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False))
        finally:
            try:
                httpx.post("http://127.0.0.1:8766/api/local/shutdown", headers={"Authorization": "Bearer "+admin}, timeout=2, trust_env=False)
            except httpx.HTTPError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    # PID is the exact Popen child owned by this verification, never a model.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                else:
                    process.terminate()
                process.wait()


if __name__ == "__main__":
    main()
