# Token Controller · 项目组模型平台

可运行的模型 API 网关、独立 Key/额度账本与模型运行管理演练系统。

面向项目组共享本地模型。法律、科研、代码等应用作为项目工具接入；平台本身不限定业务领域。单个大模型常驻、其他模型预约使用的演进设计见 [共享平台设计](docs/shared-platform.md)。

采用 **FastAPI + SQLite WAL + LiteLLM SDK**。包含管理页面、模型授权、用户/Key 双层预算、原子额度预留、幂等结算、待对账处理、视频模拟队列与独立进程的运行控制模拟器。

前端采用奶油色与海军蓝的简约工作空间，使用 PBL 风格技能提供的寒蝉团圆体及少量像素细节，包含窄屏导航和接入指南。字体许可、来源与设计说明见 [前端设计](docs/frontend-design.md)。

**公开仓库只含合成资产示例。默认不会连接、发现、启动或停止真实模型，也不包含任何实际部署记录。** 运行控制始终是模拟；真实文本接入必须显式启用。

## 启动

先安装 Python 3.12+ 和 uv。**本机 PowerShell**，在仓库目录运行：

```powershell
uv sync --locked --cache-dir .state/uv-cache
.\start.ps1
```

第一条安装锁定依赖到独立 `.venv`；第二条首次创建本地管理员、在终端仅显示一次完整 API Key，然后启动本机网关和隔离的模拟控制进程。打开 [本地管理页](http://127.0.0.1:8765)，使用该 Key 登录。Key 只放当前页面内存，刷新后重新登录。

如果 PowerShell 不允许运行脚本，不用修改执行策略。**仓库目录的 PowerShell** 先运行初始化，再启动：

```powershell
.\.venv\Scripts\python.exe -m gateway bootstrap
.\.venv\Scripts\python.exe -m gateway serve
```

`bootstrap` 生成本地管理员 Key；`serve` 仅绑定 `127.0.0.1:8765`。Ctrl+C 关闭本地进程，远程模型不受影响。丢失管理员 Key 可执行 `python -m gateway recover-admin`（使用项目虚拟环境的 Python），然后吊销旧 Key。

**Linux/macOS 终端**，在仓库目录运行同样的 `uv sync` 后：

```bash
.venv/bin/python -m gateway bootstrap
.venv/bin/python -m gateway serve
```

两条命令分别创建管理员和启动本地服务，语义与 Windows 相同。

## 功能与边界

| 模块 | 当前行为 |
|---|---|
| 资产与证据 | 权重、镜像、profile、实例分别展示；默认合成示例，未知/非实时状态明确标记 |
| 独立 Key | 仅存 SHA256，创建时显示一次；支持到期、吊销、模型范围、RPM、文本预留TPM与并发 |
| 用户与角色 | 管理员、运行操作员、调用者、审计员；角色、模型与 profile grant 都由后端检查 |
| 额度账本 | 用户总预算 + Key 子预算；原子预留、一次结算/释放；内部点数不等于电费 |
| 文本调用 | `/v1/chat/completions`，默认模拟；显式配置后经 LiteLLM SDK 接入 OpenAI-compatible 上游 |
| 幂等请求 | `Idempotency-Key` 绑定 Key、参数与来源；相同请求返回缓存；参数变化返回409 |
| 不确定结果 | 超时、缺失/非法usage、超额usage冻结待对账，不盲目重试或自动退费 |
| 视频队列 | 持久化、有界、按用户调度、并发1；只能取消排队任务；用户只能读自己的任务和产物 |
| 运行管理 | 独立模拟控制代理、白名单profile/hash、防重放、资源互斥、排空、失败恢复、常驻保护 |
| 审计 | 权限/预算/策略/运行操作/结算记录；列表不显示正文或完整Key |
| 项目工具 | 登录后展示本机私有配置中的工具入口；可尝试内嵌，原站独立登录，不转发平台凭证 |

视频结果是明确标注的模拟 JSON；没有真实 H3 MP4 生成、查询或取消能力。`stream=true` 在额度预留前返回501。按需/空闲策略只保存意图，尚无自动启停。没有 Docker socket、任意 Shell/镜像/挂载参数或真实 GPU 执行器。

当前文本接口只验证了非流式聊天，不支持完整工具调用、Responses 或 Anthropic Messages 协议。因此，填入 DSH、Cursor 或其他 Agent 客户端的地址与 Key，并不代表已完成兼容验证。

## 项目工具子界面

管理员在忽略目录 `.state/project-tools.local.json` 配置入口。以下全部为合成示例：

```json
{"tools":[{"id":"legal","title":"法律工作台","description":"现有应用，沿用原站登录。","url":"https://legal.example.test/","embed":true}]}
```

保存后进入“项目工具”，配置会即时读取，无需重启模型。页面不代理目标站点，不自动携带平台 Key，也不验证目标健康度。目标站点的证书、CSP、X-Frame-Options 和浏览器第三方 Cookie 策略可能阻止内嵌，此时使用“在原站打开”；不要关闭 TLS 校验来绕过证书问题。除本机 loopback 外只接受 HTTPS；拒绝 URL 内嵌账号、密码、查询串或片段。没有配置时入口列表为空。

## 接入自己的文本上游

准备一个仅本机可达的上游 API 地址，例如由你自行建立的 SSH 转发。**仓库目录的本机 PowerShell**：

```powershell
$env:QWEN_API_BASE = 'http://127.0.0.1:18000/v1'
.\start.ps1 -Qwen
```

第一条只设置本进程的上游地址；`-Qwen` 显式启用真实文本适配器。别名默认 `qwen3-32b`，上游必须声明同名模型；登录后先点“只读 Qwen 探测”，读取 `/v1/models`，5分钟内再发送合成测试。上游鉴权凭证可放当前进程的 `QWEN_API_KEY`，不要写入代码、文档或截图。

上游请求关闭自动重试，不确定结果保留预留与并发占位。首次 SDK 导入可能需数十秒，不要因等待就换 ID 重发。生产使用前需固定 tokenizer 验证预留上界：本地实现使用 UTF-8 字节数加封装余量预留输入，真实计费用上游 usage；它不是精确的 tokenizer TPM 限流。

`tools/verify_qwen.py` 是**可选的真实调用脚本**，不会在默认测试中运行。它假设你已准备本机 loopback18000 上游，临时启动本地验证网关、发一个最多16输出token的合成请求并核对账本及幂等重放。输出、数据库和日志仅保存在忽略目录；已有请求时不会重新提交。

## 私有资料放在哪里

真实资产快照放在 `.state/inventory.local.json`，结构参考 `examples/inventory.synthetic.json`。这是本机管理员维护的 JSON 快照，不会自动批准 profile、触发模型启停或被解释为实时健康数据。不要把真实主机地址、账号、GPU UUID、模型宿主路径、请求日志或验证报告填回合成示例文件。

`.state`、虚拟环境、数据库、环境变量文件、凭证文件、日志、视频和真实验证输出均被忽略。忽略规则不是保密审查的替代品；提交前检查暂存区并运行检查工具：

```powershell
.\.venv\Scripts\python.exe tools/check_public_tree.py
git diff --cached
```

第一条扫描候选公开文件中的凭证模式、内网IP、个人主目录、GPU UUID等；第二条人工审阅最终暂存区。扫描器只输出文件名和规则名，不回显命中的秘密。不要用 `git add -f` 绕过本地私有文件的忽略规则。

## 验证

**仓库目录的本机 PowerShell**：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.state/pytest
node --check gateway/static/app.js
```

第一条运行权限、额度并发、幂等、对账、队列归属、运行状态机和实际 LiteLLM SDK 到本机假上游的测试；**不会连接真实模型**。pytest 会清理测试专用 `.state/pytest`，勿放个人资料。第二条检查前端语法。

## 选型与后续

[New API](https://github.com/QuantumNous/new-api) 提供中文后台、渠道、用户/Key与额度，采用 AGPL-3.0 并有附加归属条款。[LiteLLM](https://github.com/BerriAI/litellm/tree/v1.106.0) 提供 SDK/Proxy、vLLM 接入、虚拟Key和预算，社区范围与 enterprise 范围许可不同。本项目使用固定版本的社区 SDK 适配文本协议，只保留一套本地点数账本，不串联两个代理计费系统。采用自研小范围身份/账本意味着需要自行维护，扩大部署前应重新比较成熟 Proxy。

本项目是本地 MVP，尚不适合公网直接部署：需要正式账号/会话、数据保留与脱敏、Postgres/Redis迁移、独立控制代理权限边界、真实GPU容量/健康/恢复、H3实机适配、TLS和上游绕过防护。文本响应正文为幂等重放保存在本地数据库，只用合成内容演练。详细权限与流程见 [architecture.md](docs/architecture.md)。
