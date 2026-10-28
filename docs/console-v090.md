# v0.9.0 控制台

本次是本地开发版本，尚未覆盖云端部署或接管 GPU 服务器

## 功能分区

| 用户端主分区 | 内容 |
| --- | --- |
| 仪表台 | 本人请求数、输入/输出 Token、14 天趋势、余额、有效 Key、模型状态 |
| API 配置 | Key 创建/调整/吊销、创建时复制、Base URL、模型名、JSON/Python/cURL 模板 |
| 模型中心 | 四项模型资产、运行/常驻标记、参数、模型名复制、调用测试、任务与结果、申请占位 |
| 我的额度 | 不限量或剩余 Token、真实用量、调用明细 |
| 项目工具 | 原有独立工具入口 |

右上角为 API 快捷配置、通知铃铛、使用引导和个人账户，成员侧栏保持五项，二级入口移到内容区

管理员分为总览、模型管理、成员与权限、用量与审计、通知与公告、工具与设置

保留的项目功能包括原任务列表/产物入口、历史运行策略与演练、工具入口、注册审批、旧成员认领、密码恢复、管理员授权、Key 审计、管理审计、异常对账、公告草稿/发布/撤回、已读状态、首次引导和版本说明

模型申请暂未上线；历史申请与管理员排程数据保留，不再接受新的成员申请

## 计量

新请求使用 `Store.admit`，不增加成员/Key 的 reserved，不产生 reserve/release 事件，成功后只记一次实际结算

默认累计不限量，输入、输出 Token 继续记账，单独保留请求大小、速率与并发保护

这里统计经过平台 API 网关的调用；SimCourt 直连模型时的用量仍由其自身记录，不自动归入成员平台账本

有限额度在每次调用开始前检查，调用完成后按真实 usage 结算，额度耗尽后拒绝下一次调用；当前非流式上游没有经过验证的中途 Token 计量和取消协议，因此不声称能在单次生成中精确停机，断开客户端也不视为已停止模型计算

旧请求通过新增 `metering` 字段区分，原有预留账本与待对账记录继续按旧规则处理，不直接清零或重写历史

## 运行控制

网页只接受已登记模型、固定动作和操作 ID，不接受容器命令、镜像地址、GPU 编号或宿主路径

云网关通过独立凭证访问固定 loopback 代理，校园服务器代理独立安装；公网 Web 进程不持有 Docker socket

流程：停止派发 → 排空平台及 SimCourt 在途任务 → 检查引擎空闲 → 停旧模型 → 核对 GPU 与入口 → 启动批准的容器 → 鉴权健康检查 → 更新路由和 SimCourt 配置

操作 ID 和阶段持久化；超时、重启或提交结果未知时不重放启停命令，保留维护状态和容器，交由管理员核对恢复

代理只管理运营者在私有清单中批准的、已存在的固定容器与固定镜像，核对 Config/HostConfig/Mounts 指纹、GPU、loopback 入口和非 AutoRemove 设置，不下载镜像、不创建容器、不强制杀进程、不删除权重

当前真实执行适配的是共享 18080 入口的文本服务；视频服务需要独立的在途任务及健康适配，H3 资产可以展示，不能把它当作已经兼容聊天接口或已完成真实切换

代理未安装、启动配置未核验时，真实切换按钮禁用；本地预览明确使用模拟切换

私有 manifest 字段：`profiles`、`resident_model`、`credential_file`、`simcourt_config`、`simcourt_container`

每个批准 profile 包含 `container_id`、`image_id`、`configuration_sha256`、`gpus`、`minimum_free_mib`；所有 ID、路径、指纹和凭证由运营者在服务器私有文件填写，不进入公开仓库

代理入口为 `python -m gateway.runtime_agent --manifest <private.json> --state <private-state> --token-file <private-token.json>`，只绑定 loopback；需通过受限隧道转发，不能直接开放代理端口

网关增加 `--runtime-agent-config <private-connection.json>`，仅可配合 `--cloud-api` 使用；连接文件为 `url` 与 `token`，必须由服务账户持有、权限 0600，URL 仅允许固定 IPv4 loopback

## 账户整理

`tools/prepare_console_accounts.py --state <state> --keep-admin <username>` 默认只显示计划；`--apply` 在备份后保留所选管理员，撤销其他账户的登录与 Key，把旧成员归档到历史记录，创建三个默认全部登记模型、不限累计额度的测试成员

先停止网关派发并核清在途请求，再执行该工具；不改模型、SimCourt、隧道或其他网站

测试密码写入服务器私有文件，不输出管理员密码或 API Key；保留旧用量、请求、账本和审计，避免删除外键链条造成历史丢失

四模型目录可通过 `tools/register_console_models.py --catalog <private-catalog>` 补齐，先备份、只追加缺少的登记，不改变旧授权或运行模型；需要清理并重建测试成员时，应先登记目录

## 本地预览与验证

`python tools/run_console_demo.py --port 8767` 使用独立的忽略状态目录，沿用原演示管理员，创建三个测试成员，只生成合成回复

界面保留 VR 徽章与像素细节，统一侧栏、圆角与动效；桌面采用页签和局部滚动，手机保留完整内容与键盘操作，尊重减少动效设置

参考入口：https://fengchao-api.com/ ，本环境外部浏览器访问连接关闭，尚未登录核对其内部截图，不把当前实现称为逐像素复刻

发布前运行 Python 回归、Node 视图测试、真实浏览器交互测试、`tools/check_guide_sync.py` 与 `tools/check_public_tree.py`
