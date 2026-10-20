"""Local launch only. No command in this CLI starts a GPU model."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

from .catalog import evidence
from .store import Store

BASE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="本地模型网关；真实服务器启停始终禁用")
    parser.add_argument("command", choices=["bootstrap", "serve", "control", "evidence", "recover-admin", "shutdown"])
    parser.add_argument("--state", type=Path, default=BASE / ".state")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--qwen", action="store_true", help="显式启用QWEN_API_BASE；不会启停服务器")
    args = parser.parse_args()
    args.state = args.state.resolve()
    store = Store(args.state / "gateway.sqlite")
    if args.command == "shutdown":
        import httpx
        admin = store.one("SELECT * FROM users WHERE role='admin' AND active=1 LIMIT 1")
        if not admin:
            raise SystemExit("没有本地管理员，无法发送关闭请求。")
        issued = store.issue_key(admin["id"], [], 0, actor="local-cli-shutdown")
        try:
            result = httpx.post(f"http://127.0.0.1:{args.port}/api/local/shutdown",
                               headers={"Authorization": "Bearer "+issued["key"]}, timeout=5, trust_env=False)
            if result.status_code != 200:
                raise SystemExit("本地关闭请求失败；请在启动窗口按Ctrl+C。")
            print("本地网关正在关闭，服务器模型不受影响。")
        except httpx.HTTPError:
            raise SystemExit("本地网关不可达；未执行服务器操作。") from None
        finally:
            with store.tx() as db:
                db.execute("UPDATE keys SET revoked=1 WHERE id=?", (issued["id"],))
                store.audit(db, "local-cli", "key.revoke", issued["id"], dict(temporary_shutdown_credential=True))
        return
    if args.command == "evidence":
        report = evidence()
        (args.state / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"checks": len(report["checks"]), "matched": sum(s["match"] for s in report["checks"]), "server_live_checked": False}, ensure_ascii=False))
        return
    if args.command in ("bootstrap", "recover-admin"):
        admin = store.one("SELECT * FROM users WHERE role='admin' AND active=1 ORDER BY name LIMIT 1")
        if admin and args.command == "bootstrap":
            print("管理员已存在；如Key遗失，在本机终端运行 recover-admin 创建新的Key。")
            return
        if not admin:
            admin_id = store.create_user("local-admin", "admin", ["qwen3-32b", "h3"], ["h3-single-v1", "h3-sp2-v1"], 1000000)
        else:
            admin_id = admin["id"]
        issued = store.issue_key(admin_id, ["qwen3-32b", "h3"], 1000000, actor="local-cli")
        print("管理员Key仅显示一次；页面登录时粘贴，不要提交到仓库：")
        print(issued["key"])
        return
    import uvicorn
    if args.command == "control":
        from .control import create_control
        token = os.environ.get("GATEWAY_CONTROL_TOKEN", "")
        if len(token) < 32:
            raise SystemExit("control requires a random process-scoped token")
        uvicorn.run(create_control(store, token), host="127.0.0.1", port=args.port, access_log=False)
        return
    if not store.one("SELECT id FROM users WHERE role='admin'"):
        raise SystemExit("先运行 python -m gateway bootstrap，保存管理员Key。")
    # Single-writer process ownership, retained across crashes via OS lock release.
    lockfile = (args.state / "process.lock").open("a+b")
    lockfile.seek(0)
    lockfile.write(b"0")
    lockfile.flush()
    lockfile.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("此状态目录的网关已运行；不要启动第二个worker。") from None
    from .app import create_app
    from .providers import DemoText, LiteLLMText
    if args.qwen:
        base = os.environ.get("QWEN_API_BASE")
        if not base:
            raise SystemExit("--qwen requires QWEN_API_BASE，例如 http://127.0.0.1:18000/v1")
        provider = LiteLLMText(base, os.environ.get("QWEN_API_KEY", ""))
    else:
        provider = DemoText()
    report = evidence()
    (args.state / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        agent_port = sock.getsockname()[1]
    token = secrets.token_urlsafe(48)
    child_env = dict(os.environ, GATEWAY_CONTROL_TOKEN=token)
    # Do not pass upstream credentials into the control process.
    child_env.pop("QWEN_API_KEY", None)
    child_env.pop("QWEN_API_BASE", None)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    child = subprocess.Popen([sys.executable, "-m", "gateway", "control", "--state", str(args.state), "--port", str(agent_port)],
                             cwd=BASE, env=child_env, creationflags=creationflags)
    try:
        import httpx
        for attempt in range(30):
            if child.poll() is not None:
                raise SystemExit("模拟控制代理未能启动")
            try:
                result = httpx.get(f"http://127.0.0.1:{agent_port}/health", headers={"Authorization": "Bearer "+token}, timeout=0.5, trust_env=False)
                if result.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise SystemExit("模拟控制代理健康检查超时")
        app = create_app(store, provider, f"http://127.0.0.1:{agent_port}", token)
        print(f"Local: http://127.0.0.1:{args.port}  | runtime=SIMULATION | text={provider.source}", flush=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1, access_log=False))
        app.state.shutdown = lambda: setattr(server, "should_exit", True)
        server.run()
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        lockfile.close()


if __name__ == "__main__":
    main()
