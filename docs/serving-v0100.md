# 服务器接入步骤：先核查，再开放并发

本文件是待执行方案。本地开发不会自动改变云端、校园网账号、SSH、Docker或SimCourt。先保持现有服务，逐步收集输出后才生成针对实际环境的安装命令。真实凭证、地址、账号映射和运行结果只放私有状态目录

## 1 · 校内只读检查

在校内GPU服务器普通运维账号的Bash中执行 `bash tools/serving_preflight.sh`（需先把审核后的脚本传到服务器）。脚本只显示资源、拓扑、容器选定字段、监听端口、网关脚本hash/权限及相关服务名；不会显示容器完整命令、环境变量或登录密码

目的：重新确认GLM容器、空闲第四卡、进程/显存、网络脚本是否被修改、隧道实际unit名。已有三路模拟结果不能替代服务器结果。未核查前不停止GLM，不删权重，不创建新容器

云端另外读取部署版本、网关health、隧道health、Nginx语法与目标站配置hash，确认其他两个站点基线。不要输出上游Key或整份带秘密的配置

## 2 · 自动联网

提供的 `ops/network_guard.py` 与 `deploy/token-controller-network.service/.timer` 只用于校内服务器。要求系统Python已有requests；运行身份 `tc-netguard` 独立、无交互登录、无sudo、无Docker组

安装时先检查目标路径未占用并保存配置备份，由管理员把经过SHA256审查的校园网脚本复制到root控制的 `/usr/local/lib/token-controller/`。不能让服务执行仍可由普通用户修改的家目录脚本。脚本、wrapper、service和timer都归root所有，不允许组/其他用户写入

`/etc/token-controller-network/config.json` 为运营者固定配置：

```json
{
  "helper": "/usr/local/lib/token-controller/campus-login.py",
  "helper_sha256": "REPLACE_WITH_REVIEWED_SHA256",
  "probes": ["https://models.example.test/health", "https://www.example.org/"],
  "portal_probe": "http://models.example.test/",
  "gateway_host": "gateway.example.edu"
}
```

这只是字段示例。第一个HTTPS目标必须指向云端平台，第二个用于辅助诊断；均须无凭证、已知可达，辅助站可达不能掩盖云端不可达。网关域名以实际认证系统为准。配置目录root:tc-netguard 0750、普通配置0640，脚本路径所有父目录由root控制。校园网凭证由管理员在服务器终端用getpass输入一次，写入root:root 0600的 `campus-login.json`（字段username/password），不放聊天、命令行、环境变量、Git或journald。`ops/network_credentials.py` 提供仅交互式root可用的首次录入工具，拒绝覆盖已有凭证

服务通过 `LoadCredential` 获得凭证，使用宿主网络，不启用PrivateNetwork。ProtectSystem/ProtectHome/NoNewPrivileges/空Capability限制它访问其他资源。systemd自动创建仅服务可写的0700状态目录

行为：云端主探测收到经过TLS校验的非重定向HTTP响应就不登录，包括500/502/503等服务端错误；这些错误只说明网络传输可达，仍须检查应用。主探测失败且HTTP明确重定向至配置的校园网关时才调用固定脚本。每次调用45秒上限、两次登录至少隔5分钟、每小时最多3次。凭证只送入子进程stdin，脚本stdout/stderr捕获且不写日志。登录退出码0不是联网成功，最后重新检查云端HTTPS。结果区分cloud_https_reachable和cloud_http_server_error，任何一次联网检查都不验证模型可用性

先 `systemd-analyze verify`，再单次启动并查看仅包含阶段/HTTP状态的日志。验证有效后才启用timer。出现持续backoff、密码错误或配置失败时停止自动尝试并通知运维，不无限重试。OnFailure告警接收端需按实际告警服务配置，当前模板未假装提供外部消息告警

不需要把Linux密码提供给该服务，也无需有人保持SSH会话。若共用校园网账号会触发学校网关的设备/会话限制，应使用允许用于此服务器的联网身份，不能通过反复登录挤掉其他设备

## 3 · 受限运维账号

模板：`ops/ssh-dispatch.sh`、`ops/maintenance.py`、`deploy/token-controller-maintenance.sudoers`、`deploy/token-controller-maintenance-sshd.conf`

集中保存的 authorized_keys 公钥文件应为root:tc-maint 0640，由root修改、目标账号只读；不能设成root:root 0600，否则OpenSSH临时使用目标UID读取时会失败。安装前后须实际用目标账号检查可读且不可写，并通过真实SSH公钥登录验收，配置语法通过不能替代认证验收

专用SSH身份 `tc-maint` 只用独立公钥、锁定密码。其shell需能执行受控ForceCommand（不能设成会直接退出的nologin）；所有实际命令由root拥有的强制分发器约束，不提供PTY、SFTP或端口转发。公钥由root放入 `/etc/ssh/authorized_keys/tc-maint`，不覆盖现有管理员或隧道账号

`/etc/token-controller-maintenance/config.json` root:root 0600只含真实 `tunnel_unit` 字段。三个固定命令：

| SSH命令 | 权限 |
|---|---|
| `status` | 查看指定联网/隧道unit的选定状态字段 |
| `network-check` | 启动一次固定联网检查服务 |
| `tunnel-reconnect` | 重启唯一已登记隧道服务 |

不允许任意systemctl、Shell、Python、Docker、文件上传、读取上游Key或修改账号。后续部署权限应另审，不把通用sudo权限附加到这个账号。sudoers只放行root wrapper的这三个精确参数组合

先 `visudo -cf`、`sshd -t`、`sshd -T -C user=tc-maint,host=...,addr=...` 检查最终Match效果；保留原管理员会话，重载而不重启SSH。用第二会话分别验证允许命令和拒绝Shell/SFTP/转发/额外参数，确认后再用于自动维护。不要更改原有SSH安全组或已有隧道身份

## 4 · 云网关更新

先停止新派发，等待真实在途请求与未知项处理，使用SQLite在线备份并保存当前unit、目标站Nginx及版本。新版本只新增表，比较用户、Key、账本及累计用量后再恢复入口

生产先保持 `--inference-slots 1` 和网关单worker，取得同一引擎实例的真实三路资格后才配置3。Nginx的 `/v1/` 保持 `proxy_buffering off`，read timeout设置660秒；上游执行总时限600秒，工作流客户端660秒，普通同步排队最多25秒。工作流心跳独立续租，延长推理时限不放宽失联判定。不要改变其他两个站点的server块。先本机HTTPS核验鉴权、模型列表、一次合成流式工具调用、未知断线对账，再从公网验证。未实现的Responses明确501，不放开裸引擎端口

模型权重和镜像不用重新下载。网关升级不会自动提升真实引擎的parallel。部署后检查新版界面、账号登录、旧Key、用量，以及其他两个站点状态

## 5 · SimCourt 统一接入

在校园服务器读取真实 `worker.py`、`model_adapter.py` 和凭证引用的结构/hash，备份后再改。包裹的是一次庭审worker入口，不是网页服务启动入口；调用 `linked_court_task` 取得任务ID并续租，每次模型调用走统一网关、传同一个X-Task-ID。Base URL不再直接指向原模型端口，使用专属普通成员工具Key

现有SimCourt有独立登录，不能自动推定它与平台同名账号是同一个人。运营者建立私有身份映射，每人一个限制在当前模型的工具Key，或先由用户在受信任配置界面绑定。未映射时拒绝任务，不冒用通用服务身份。映射与Key留在0700/0600私有目录，不进入public repo

确认没有旧直连旁路，所有庭审模型调用出现在同一队列及对应成员用量中。先一庭审短测，再完整庭审；工程验收不等于法律准确性验收

## 6 · 三路推理与第四卡

先保留当前容器及配置快照，以独立候选容器修改已审核参数。不要对原容器原地无记录修改；新增GPU配置意味着重新创建容器，不是给容器加一个“更大”的大小参数。CPU/RAM限制、CUDA可见GPU、权重分配和KV上下文是不同设置

测试顺序：原三卡一路基线 → 三卡两路 → 三卡三路 → 必要时四卡三路。每一步无OOM且恢复路径明确才进入下一步；GPU4方案需要同时记录拓扑、分层/张量切分与运行代理profile指纹。第四卡不保证更快，PCIe跨NUMA通信可能抵消收益

每一路保留目标上下文需要额外KV容量；不要只把parallel从1改为3却把每槽上下文缩成原来的三分之一。核对实际 `/props`、启动日志和每槽限制，验证当前模型/内核支持的KV类型及flash attention。一个请求与三个请求共用同一份权重和显存池，不给每个用户固定分一张卡；不人为塞满显存或强迫GPU始终100%利用率

合成验收：单任务独占可用算力、三任务同时有进展、第四项排队、取消/断线、长上下文、持续运行。混合验收：一个SimCourt加两个harness。记录峰值显存、每任务首字延迟、生成速率、总吞吐、p95与错误率，比较三卡/四卡结果，选择稳定配置。保留计算缓存和故障余量

引擎三路通过后再把网关 `--inference-slots 3` 放开，检查Key并发上限；旧Key限制不会擅自增加。若当前GLM三路长上下文不达标，需要调整配置或更轻模型后重测；一两路加排队必须标明为降级，不声称达到三路

## 完成条件

账号/Key/用量保留；其他两个站点无变化；所有生产文本模型调用遵守同一任务队列；成员只见自己和前方人数；管理员能识别对应成员；网络认证失效能被识别并有界恢复；所有部署与三路GPU结果都有新鲜验收记录。源码/本地模拟不能代替这些生产证据
