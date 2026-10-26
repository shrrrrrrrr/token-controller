# v0.7.0：账户、API Key 与功能分区

状态：2026-10-26 用户已确认按下列分区实现第一版；注册后由管理员审批。功能已实现，本地测试结果见交付记录；不表示现有云端部署已切换。

## 功能分层

| 身份 | 一级分区 | 二级入口及保留的功能 |
| --- | --- | --- |
| 管理员 | 总览 | 平台状态、模型可用性、快捷入口 |
| 管理员 | 模型服务 | 模型目录与资产证据；调用检查；运行管理与原有本地演练；预约队列、审批、时间与顺序 |
| 管理员 | 成员与权限 | 注册审批；成员角色、启停、模型授权与累计额度；历史成员认领；API Key 安全审计与吊销 |
| 管理员 | 用量与审计 | 请求记录；预留、结算和释放账本；未知请求人工对账；管理审计 |
| 管理员 | 项目工具 | 现有工具入口与原站独立登录 |
| 管理员 | 帮助与设置 | 接入指南；随版本更新的角色引导；个人用户名和密码 |
| 成员 | 模型服务 | 已授权模型与调用验证；预约申请、自己的申请和脱敏队列 |
| 成员 | 我的 API | 自己创建、查看、调整与吊销 Key；接入说明 |
| 成员 | 我的用量 | 自己的输入、输出、总 token 与请求状态 |
| 成员 | 项目工具 | 已有工具入口；不转发平台凭证 |
| 成员 | 账户与帮助 | 用户名与密码修改；审批状态；版本使用引导 |

通知继续放在顶部，原通知页、未读标记、批量已读功能保留。一级菜单可展开，实际页面 ID 和功能页签保持可追踪，避免重新排布时遗漏功能。角色导航只是展示；接口继续单独鉴权。

云端未开放的运行控制、视频执行、自动切换、SSE、完整工具协议和服务端 harness 不会因为重新排布而被启用。本地演练入口保留其原有边界。

## 账号与凭证的主线

1. 新成员自行注册用户名、密码与显示名称，角色固定为普通成员，不能自行指定模型、额度或管理员身份。
2. 未批准账户只能查看自己的待审批状态和账户资料。管理员审批时决定模型与额度；默认全部当前登记模型、不限累计用量，但继续统计真实 token。
3. 批准后通过用户名和密码登录网页。会话保存在安全 Cookie 中；网页不再靠调用 Key 登录。
4. 成员自行创建 API Key，默认不超过账户的模型范围。服务端只保存摘要，完整 Key 仅创建时显示一次。可查看、修改自己 Key 的名称/授权/限制或吊销；不能越过账户限制或管理其他人的 Key。
5. 外部客户端继续使用 HTTPS Base URL、API Key、模型 ID。网页会话不能直接替代 API Key，API Key 也不能取得网页管理权限。
6. 管理员负责注册审批、账户启停、角色、模型授权、额度与凭证审计；不需要设置或知道成员密码。历史的管理员代建成员保留为待认领记录，正常路径改为成员注册；不再默认替成员生成调用 Key。

网页里的调用验证使用成员自己创建的调用 Key，可在本页内存中临时填写或使用刚生成的 Key；不保存到 localStorage，也不让 Cookie 自动访问推理接口。

## 保留已有数据

采用增加账户、会话、一次性认领/恢复凭证及 Key 描述表的扩展迁移；不改原用户 ID，不删除旧 Key、请求、账本或审计。

首个管理员的网页登录由服务器本地交互工具初始化，密码不进入命令参数、日志或聊天。已有成员通过管理员签发的短期一次性认领凭证绑定账密；旧共享 Key 不作为认领身份的充分证明。不得公开一个任何人可抢占的“首管理员注册”接口。

原 Key 模式保留为明确的兼容启动模式。新账密模式经过本地验收并完成管理员初始化后才切换；云端迁移另行提供可审核、备份和回退步骤，不自动修改当前部署。

## 认证安全边界

截至2026-10-26，采用 NIST SP800-63B-4 的单因素密码长度方向和 OWASP 的密码、会话、CSRF 建议：至少15字符、支持长口令、不强迫字符组合；scrypt 保存随机盐和参数，使用 OWASP 列出的 `n=2^14,r=8,p=5` 组合并明确内存上限。选择标准库是为了沿用现有 Python/OpenSSL 环境，仍需实测而非声称等价于所有 Argon2id 部署。

浏览器使用随机 opaque 会话，服务端仅存会话凭证摘要；HTTPS Cookie 设置 Secure、HttpOnly、SameSite 和 host-only 范围，登录轮换、退出/改密/禁用后撤销。使用预会话 CSRF token、精确 Origin 校验、自定义 CSRF 头；兄弟子域同站并不能代替这些检查。登录与注册设置分开的 IP、用户名和全局限流及密码哈希并发限制。近期鉴权绕过与 Cookie 漏洞说明不能把客户端声明或单一 URL 中间件当作身份凭证；具体依赖是否受影响仍按实际版本核对，不把其他产品漏洞当成本项目已遭攻击。

尚未实现或宣称：Passkey/MFA、邮箱验证、邮件找回密码、OAuth、完整企业 SSO。密码恢复采用管理员辅助的一次性流程，需要单独审计。

来源：[NIST 正式版（2025-07-31）](https://csrc.nist.gov/pubs/sp/800/63/b/4/final)、[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)、[Python scrypt](https://docs.python.org/3.12/library/hashlib.html#hashlib.scrypt)。以上持续维护页面没有经核实的统一发布日期，不沿用技能文件中的猜测日期。

## 验收

- 原管理员/成员功能有逐项映射，所有导航可达，桌面及窄屏表单不裁剪、输入不丢失。
- 注册不能自授管理员/额度，未审批不能发 Key 或调用；审批和禁用立即生效。
- 正确登录、错误登录、限流、退出、改账密、过期、CSRF、跨 Origin 均测试。
- Key 仅创建时回显，不能跨账户读取、修改或吊销，不能超出账户模型范围；API Key 不能替代网页登录。
- 现有 ID、Key、请求、用量和账本迁移前后保持；回退不覆盖活数据库。
- 真实模型可用性须用独立短合成请求核对模型、回复、usage 和同一请求的账本，不用“网页已打开”替代推理验收。

## 本地审核与启动

**仓库目录的 PowerShell**，已有锁定依赖虚拟环境时运行：

```powershell
.\.venv\Scripts\python.exe tools/run_accounts_demo.py
```

该命令使用独立 `.state/accounts-demo`，绑定 `127.0.0.1:8766`，管理员和普通成员的随机演示账密保存在忽略的 `demo-accounts.local.json`。终端只显示文件位置，完整凭证不写公开源码。只使用合成文本与原有模拟控制，不连接云端或 GPU。可在不同浏览器窗口分别登录两种身份；Ctrl+C 停止本地进程。

如果要给既有本地数据启用新登录，先停止对应网关，备份数据库，再在**交互式终端**运行：

```powershell
.\.venv\Scripts\python.exe tools/manage_web_accounts.py --state .state --username project-admin
.\.venv\Scripts\python.exe -m gateway serve --web-accounts --state .state
```

第一条为已有唯一启用管理员绑定网页登录，密码由隐藏输入提示读取。存在多名管理员时必须提供 `--user-id`；已设置账密不会覆盖，明确恢复才使用 `--reset`。第二条显式启用新模式。用户名、状态目录是命令参数，密码不是。不要把云端状态复制到本地演示目录。

## 云端迁移顺序

这属于已有服务的认证变更，不能直接重新初始化数据库，也不能把新模板直接覆盖三个站点的全局配置。

1. 先核对当前提交、服务用户、数据库及目录权限、Nginx 项目 vhost、证书、隧道和在途请求。记录旧版本、用户/Key/请求/账本数量及聚合用量；避免输出正文或凭证。
2. 在维护窗口停止网关派发并确认无在途/未知请求；使用 SQLite backup API 做一致性私有备份，保留旧 release、unit 与单项目 vhost。不要复制仍在写入的单个 `.sqlite` 文件当作完整备份。
3. 将审核后的新 release 放独立目录，复用既有锁定虚拟环境与状态目录。新增表由 `Store` 扩展创建，原表数据保持。以**状态目录的服务用户**运行 `tools/manage_web_accounts.py`，交互设置管理员账密。不要以 root 创建属于服务用户的 SQLite WAL/SHM。
4. 原 unit 仅切换 release 路径并增加 `--web-accounts`；保留原 `--cloud-api`、固定上游、origin 和资源隔离。Nginx 只替换当前项目 vhost，采用 `deploy/nginx/token-controller-accounts.conf` 的差异，取消该站静态页的预览 Basic 门禁，开放注册登录页。不能同时启用旧、新模板的重复 zone/server。先 `nginx -t`，再 reload。
5. 验证 HTTPS 首页、无 Cookie 的 `/api/me` 为401、无 Bearer 的 `/v1/models` 为401；网页登录成功后 `/v1` 仍必须调用 Key。分别检查注册待审批、审批、成员发 Key、退出和角色导航。核对旧数据数量及总用量保持；其他两个站点状态不变。真实模型生成另行显式验收，不在启动脚本里自动发起。
6. 出现登录边界或旧站回归异常，恢复旧 unit/vhost 并重启网关、reload Nginx；**不覆盖已发生新用量的活数据库**。原 Key 模式可继续读新增表，保留原 ID 与 Key。若回退到早期版本，必须确认支持已有无限额度 `-1` 语义。

账号模式下，上游凭证更新仍需管理员权限，但 `/api/upstream/credential` 改用管理员会话及 CSRF；旧的 Bearer 管理脚本只适用于兼容模式，不能直接重复使用。外部客户端 `/v1` Bearer 用法保持。

## 本地验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.state/pytest-accounts-review
node --test tests/test_cloud_ui.mjs
.\.venv\Scripts\python.exe tools/check_guide_sync.py
.\.venv\Scripts\python.exe tools/check_public_tree.py
```

已安装 Playwright/Chromium 时，可设置 `PLAYWRIGHT_MODULE`（模块目录）和需要时的 `PLAYWRIGHT_BROWSERS_PATH`，运行 `node --test tests/test_accounts_browser.mjs tests/test_membership_browser.mjs`。缺少浏览器运行时会明确 skip；不会把接口测试当成浏览器验收。真实浏览器测试绑定临时 loopback 服务，使用合成成员与模拟响应。

2026-10-26 本地验证：Python 全套112项通过；前端契约与两条真实 Chromium 流程共7项通过。浏览器验证了注册→待审批→管理员批准→成员创建/修改/吊销 Key→模拟调用→自己的用量→退出，检查1440×1000、1366×768及390×844视口；旧 Key 登录表单也保留回归。迁移测试核对已有用户、Key、请求、账本和审计原始数据不变。引导同步与公开源码扫描通过。锁定依赖的 TestClient 有一条上游弃用提示，此次未因此升级依赖。

这些证据仅覆盖本地实现及合成数据；新 Nginx 模板仍须目标主机执行 `nginx -t` 和认证边界检查，新账密模式尚无生产迁移验收。
