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

from .catalog import evidence, load_model_catalog, ModelCatalogError
from .deployment import validate_public_origin
from .store import Store

BASE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="本地模型网关；真实服务器启停始终禁用")
    parser.add_argument("command", choices=["bootstrap", "serve", "control", "evidence", "recover-admin", "shutdown"])
    parser.add_argument("--state", type=Path, default=BASE / ".state")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--qwen", action="store_true", help="显式启用QWEN_API_BASE；不会启停服务器")
    parser.add_argument("--cloud-preview", action="store_true", help="受限云预发布：禁用所有推理和模拟控制")
    parser.add_argument("--public-origin", help="预发布站点的唯一 HTTPS origin")
    args = parser.parse_args()
    args.state = args.state.resolve()
    marker = args.state / "cloud-preview.json"
    if marker.exists() and not args.cloud_preview:
        raise SystemExit("此目录属于云预发布，必须显式使用 --cloud-preview；禁止回退到模拟执行。")
    if args.cloud_preview:
        if args.qwen or args.command in ("control", "shutdown"):
            raise SystemExit("云预发布禁止连接上游、控制代理或通过网页关闭进程。")
        try:
            args.public_origin, _ = validate_public_origin(args.public_origin)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        if not marker.exists() and (args.state / "gateway.sqlite").exists():
            raise SystemExit("请为云预发布指定全新状态目录，不得直接复用本地演示数据库。")
        args.state.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            with marker.open("x", encoding="utf-8") as handle:
                json.dump({"mode": "cloud-preview"}, handle)
        catalog_path = args.state / "model-catalog.local.json"
        if not catalog_path.exists():
            with catalog_path.open("x", encoding="utf-8") as handle:
                json.dump({"models": []}, handle)
        try:
            load_model_catalog(catalog_path, cloud_preview=True)
        except ModelCatalogError:
            raise SystemExit("模型登记文件无效；未启动服务。") from None
    elif args.public_origin:
        raise SystemExit("--public-origin 只能与 --cloud-preview 一起使用。")
    store = Store(args.state / "gateway.sqlite", seed_demo=not args.cloud_preview)
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
            admin_id = store.create_user("preview-admin" if args.cloud_preview else "local-admin", "admin",
                                         [] if args.cloud_preview else ["qwen3-32b", "h3"],
                                         [] if args.cloud_preview else ["h3-single-v1", "h3-sp2-v1"], 1000000)
        else:
            admin_id = admin["id"]
        grants = json.loads(store.one("SELECT models FROM users WHERE id=?", (admin_id,))["models"])
        issued = store.issue_key(admin_id, grants, 1000000, actor="local-cli")
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
    if args.cloud_preview:
        try:
            app = create_app(store, cloud_preview=True, public_origin=args.public_origin,
                             model_catalog_path=catalog_path,
                             project_tools_path=args.state / "project-tools.local.json")
            print(f"Cloud preview: loopback:{args.port} | model execution disabled", flush=True)
            # Only the local reverse proxy is trusted. The port never binds publicly.
            uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False,
                        proxy_headers=True, forwarded_allow_ips="127.0.0.1", limit_concurrency=64,
                        timeout_keep_alive=5)
        finally:
            lockfile.close()
        return
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
