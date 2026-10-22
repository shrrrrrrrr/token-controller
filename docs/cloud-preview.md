# 隔离的云端预发布

本阶段将管理界面部署到一个新子域名，供少量受邀测试人员验证账户、模型目录和页面流程。校内模型服务器可以暂时离线；云端不执行推理或模型启停。**这不是面向全体成员的正式生产开放。** 现有 API Key 兼作页面登录凭证，正式 Web 会话与调用 Key 的分离、完整客户端协议和真实推理链路仍需后续工作。

本文全部使用合成域名 `models.example.test`。实际域名、IP、证书、用户和资产配置只在服务器或忽略目录中维护。下列操作均在**云服务器 Bash**中进行，除第一轮盘点外会创建新项目资源；先逐段核查，再执行。不要在 GPU 服务器上执行这些部署命令。

## 部署边界

```text
浏览器 ── HTTPS ── 现有 Nginx 的新 server 块
                       │
                       └── HTTP 127.0.0.1:18765
                             独立 systemd 服务 / 单个 worker
                             /opt/token-controller       程序与虚拟环境
                             /var/lib/token-controller   私有数据库和配置
```

静态页面由 Nginx Basic Auth 保护，登录页面后再输入平台 Key。`/api/` 使用应用 Bearer 鉴权，因此该路径关闭 Basic Auth：二者都占用 `Authorization`，不能要求同一次请求同时发送两种凭证。`/v1/` 返回 503，模型操作和本地关闭接口不可用。HTTPS 在 Nginx 终止，回环连接不经过外网。[Nginx 鉴权说明](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)

程序使用独立的无登录系统账户，不加入 Docker 组，也不持有 Docker socket。systemd 限制到 1 个 CPU 核的配额、1 GiB 内存和 128 个任务；内存接近上限时先调查，不能直接挤占其他站点资源。程序只能写自己的状态目录和私有临时目录，预发布网络限制为 loopback。上述隔离降低影响范围；它仍与已有网站共享 Linux 内核和 Nginx，不能声称绝对互不影响。[systemd 隔离说明](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)

## 1. 只读盘点

从已审阅的源码目录运行：

```bash
bash tools/cloud_preflight.sh
```

脚本读取操作系统、Python/uv/Nginx 版本、内存、磁盘、监听端口和新服务路径是否已存在。不读取环境变量、凭证、完整 Nginx 配置或进程命令行，不安装软件、不修改服务。

继续前确认：Python 3.12+ 可用；18765 未被占用；已有站点正常；磁盘与内存足够容纳新进程及依赖安装。已有同名账户、目录或服务时先检查归属，不覆盖。若选择其他端口，同时修改 systemd 和 Nginx 模板。确认 Nginx 的 include 结构确实支持 `sites-enabled`，以及工作进程账户；以下权限示例使用 Ubuntu 默认的 `www-data`。

## 2. 安装锁定源码与环境

先通过已核实的渠道安装 Python 3.12 和 uv。不要修改其他网站的 Python/Node 环境。将**审阅过的固定 Git commit**检出到新建的 `/opt/token-controller`；程序目录保持部署管理员所有，服务账户仅可读取。记录 commit、Python/uv 版本和安装时间到服务器的私有运维记录。不要把本机 `.state`、日志、虚拟环境或管理员 Key 一起上传。

首次创建专用账户与状态目录：

```bash
sudo useradd --system --user-group --home-dir /var/lib/token-controller --no-create-home --shell /usr/sbin/nologin token-controller
sudo install -d -m 0700 -o token-controller -g token-controller /var/lib/token-controller
```

`--system` 创建服务账户，`nologin` 禁止交互登录，状态目录仅该服务可访问。这两条只用于已确认不存在同名资源的首次部署。

进入新程序目录，由拥有该目录写权限的部署管理员使用已安装的 uv 执行：

```bash
cd /opt/token-controller
uv sync --locked --no-dev --python /usr/bin/python3.12 --no-managed-python
```

`--locked` 禁止重算锁文件，`--no-dev` 不安装测试依赖，`--python` 明确解释器，`--no-managed-python` 防止选到管理员家目录中被服务隔离策略屏蔽的解释器。如果 Python 实际位置不同，先核查再替换；确保 `.venv/bin/python` 的链接目标不在个人家目录。命令只创建本项目 `.venv`，不会修改全局 Python 依赖。安装完成后程序和虚拟环境应由部署管理员所有，服务账户没有写权限。[uv 项目环境说明](https://docs.astral.sh/uv/guides/projects/)

在**可见、未录屏的交互终端**首次初始化云状态目录：

```bash
sudo -u token-controller /opt/token-controller/.venv/bin/python -m gateway bootstrap --state /var/lib/token-controller --cloud-preview --public-origin https://models.example.test
```

执行前把示例域名替换为实际新子域名。命令只在终端显示一次管理员 Key；保存到密码管理器，不使用 `tee`、重定向、systemd 启动脚本或 CI 日志接收它，不把 Key 发到聊天中。初始化创建独立状态，不复制本机演示账户/余额，模型目录最初为空。模型由管理员登记后再授权给成员。

### 登记模型与前端名称

本版由运维管理员维护私有文件 `/var/lib/token-controller/model-catalog.local.json`，尚无网页新增模型表单。可以参考 `examples/model-catalog.synthetic.json` 的结构。显示名与稳定 API 标识分开：

```json
{"models":[{"id":"research-text","display_name":"研究用文本模型","kind":"text","deployment_status":"planned","description":"已选型，尚未连接推理服务。"}]}
```

先写到同一状态目录的临时文件，用 `gateway.catalog.load_model_catalog` 验证，再在服务器本机原子替换正式文件；保持服务账户所有、权限 `0600`。替换会更新整份登记表，务必保留仍需使用的旧条目；已有文件先备份。不能把密码、上游 Key、服务器地址或任意命令放进这份模型目录。当前只接受 `planned`（待部署）和 `offline`（已登记但离线），不能手填“就绪”来开启推理。

页面每次请求 `/api/model-catalog` 都重新读取文件，刷新后无需重建前端或重启服务。管理员能查看全部条目，并在成员与 Key 表单中分配权限；成员只看到“成员授权 ∩ 当前 Key 授权”内的模型。已发放的 Key 不会因新增模型自动扩大权限。删除目录条目不会删除成员或历史数据，但该项不能继续被申请预约。

模型文件、推理服务、健康检查和路由尚未因登记而建立；目录中的名字不能被当成 API 已可用。后续接通真实模型时，再核对上游模型 ID 与这个稳定 ID 的映射。

## 3. 启动独立服务

在服务器私有副本中编辑 `deploy/token-controller.service` 的域名，再安装：

```bash
sudo install -m 0644 deploy/token-controller.service /etc/systemd/system/token-controller.service
sudo systemd-analyze verify /etc/systemd/system/token-controller.service
```

第一条只安装新服务，第二条检查 unit 配置。确认不与其他服务同名后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now token-controller
```

`daemon-reload` 重读 unit 定义，不重启已有 Node 服务；`enable --now` 只启动并设置新服务开机启动。只运行一个 worker；多个 worker 与当前进程锁和 SQLite 状态管理不兼容。

```bash
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18765/health
sudo ss -lntp 'sport = :18765'
```

健康检查应显示云预发布模式；监听只能是 `127.0.0.1:18765`。若启动失败，先在云服务器本机查看该服务日志，分享时只提供脱敏错误。预发布不可出现模拟推理成功、真实模型“运行中”或控制代理启动。

## 4. 新子域名、证书与 Nginx

先记录现有两个站点的 HTTPS 状态和关键页面，备份现有 Nginx 配置到**仅 root 可读的服务器私有目录**。备份可能包含内部路径和凭证，不下载到公开仓库。DNS 仅新增新子域名的 A 记录；IPv6 未配置时不要误加 AAAA。不修改根域名和其他子域名记录。

创建 ACME challenge 目录及受限预览凭证：

```bash
sudo install -d -m 0755 /var/www/token-controller-acme
sudo install -d -m 0750 -o root -g www-data /etc/token-controller
```

确认已安装 `htpasswd` 后，首次创建站点访问密码：

```bash
sudo htpasswd -c /etc/token-controller/preview.htpasswd preview-admin
sudo chown root:www-data /etc/token-controller/preview.htpasswd
sudo chmod 0640 /etc/token-controller/preview.htpasswd
```

`htpasswd` 交互提示输入密码；不加 `-b`，避免密码进入命令行和历史。`-c` 只用于首次创建，之后添加用户必须去掉它。此密码与平台 API Key 不同。不要为获取该工具安装或启动另一套 Web 服务。

若新域名还没有证书，先将 `deploy/nginx/token-controller-http.conf` 替换成实际域名并安装为 `/etc/nginx/sites-available/token-controller`，仅在该路径尚未使用时创建对应 `sites-enabled` 符号链接。这个临时配置只提供 ACME challenge，其余请求 404。执行 `sudo nginx -t` 成功后再 `sudo systemctl reload nginx`，立刻复查旧站点。

使用云主机已有证书工具。若已使用 Certbot，采用 **webroot** 模式申请新证书，避免自动改写其他 server 块或占用正在使用的 80 端口：

```bash
sudo certbot certonly --webroot -w /var/www/token-controller-acme -d models.example.test
```

真实域名必须先正确解析。证书路径需与 Certbot 实际输出一致；若已有同名证书产生 `-0001` 目录，调整模板路径。不要同时使用 HTTP 引导模板和正式 HTTPS 模板处理同一个 `server_name`。

将 `deploy/nginx/token-controller.conf` 的全部示例域名和证书路径替换好，覆盖**刚刚新建的本项目配置文件**，保留其他文件。再次执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

只有第一条成功才能执行第二条。模板不使用 `default_server`，不替换全局配置，不开放后端 18765。已有 80/443 入口足够，不需要修改其他站点端口。Nginx 按实际连接来源限流，并覆写转发头；若未来增加 CDN 或另一层代理，必须先配置可信代理地址，不能信任客户端自行传入的 `X-Forwarded-For`。[Nginx 限流说明](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)

确认现有证书自动续期机制覆盖这个 webroot，并在续期后执行已验证的 Nginx reload；不要覆盖原有续期任务。两个旧站与新站均需回归。TLS 和 DNS 必须实际核验后才能记录“已上线”。

## 5. 验收与回退

先用浏览器访问实际新域名：应使用可信 HTTPS 证书，先要求预览用户名/密码，再进入平台 Key 登录页。Key 只存在页面内存，刷新后需重新登录。

不带凭证的只读检查：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://models.example.test/
curl -sS -o /dev/null -w '%{http_code}\n' https://models.example.test/api/me
```

应分别为 401（Nginx Basic）与 401（平台 Bearer）；登录后 `/api/me` 成功。再验证 `/v1/models` 为 503、`/api/local/shutdown` 为 404、18765 无公网监听。管理员登记的模型应显示真实准备状态，不应出现假成功生成。测试创建成员、分配模型授权、吊销 Key、跨用户数据拒绝访问，并确认原两个网站、CPU/内存占用和磁盘均正常。API Key 不得放 URL 查询串或命令历史。

若新服务异常，先只停止 `token-controller`；恢复或禁用本项目新增 Nginx 配置，`nginx -t` 成功后 reload，再检查旧站。保留 `/var/lib/token-controller` 和私有配置；不要删除数据库或回滚其他站点。更新代码前停止本项目服务并完整备份 SQLite 数据库及 WAL/SHM 文件（或用 SQLite 备份接口），记录旧 commit；不要只复制仍被写入的主数据库文件。

模型接通另行执行：校内部署并验证权重/引擎 → 建立受控加密链路 → 管理员登记显示名称、稳定模型 ID 和批准的运行配置 → 授权给成员 → 验证健康与调用 → 开放实际路由。仅在前端增加一个名字不会自动下载模型、占用 GPU 或切换 SimCourt。正式开放前还要完成 Web 会话隔离、真实调用安全测试与客户端兼容性验证。
