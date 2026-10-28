# v0.10.0 · Harness 与统一任务队列

## 已实现的边界

最多三个活跃工作流，共享一份模型权重。推理并发独立配置：真实服务默认一路，本地模拟三路；只有实测通过才能用 `--inference-slots 2` 或 `--inference-slots 3` 提升网关派发上限。参数不会更改真实引擎或自动添加GPU

成员「模型中心 → 任务与调度」查看自己的任务、子请求状态、前方排队人数并取消任务。管理员看到成员归属和全局状态。顶部桌面状态显示工作流、推理占用与等待数；小屏在任务页查看完整信息。成员接口不返回其他成员 ID、名称、任务标题、提示词或任务ID。前方人数为当前公平调度顺序的用户去重数，等待时间不是完成时间承诺

### 协议矩阵

| 接口/能力 | 状态 |
|---|---|
| OpenAI `/v1/chat/completions` | 文本、SSE、函数工具、多轮 tool 结果、developer 映射为 system |
| `max_tokens` / `max_completion_tokens` | 支持，冲突值拒绝 |
| OpenAI `/v1/models` | 沿用已授权模型目录 |
| Anthropic `/v1/messages` | 文本、函数工具与流式事件适配，支持 `x-api-key` |
| OpenAI Responses、音视频输入、原生托管工具、Anthropic thinking/缓存控制 | 尚不支持，拒绝未知参数，不静默假装实现 |

JSON Schema 是提供给模型的工具说明，网关从不执行工具。工具由调用者的 harness 在自己的执行环境运行。协议兼容不等于模型能稳定调用工具；真实模型质量和完整 DSH 工作流还需单独验收。本地模拟源不代表实际 GLM 结果

输入长度采用保守的 UTF-8 字节上界加输出限制，超过16384时拒绝；这不是精确分词或对完整16K可用输入的承诺。三个槽位时每槽上下文、KV缓存和峰值显存必须在引擎侧另行确认

## 任务接口

所有 `/v1/` 调用需要普通成员调用 Key，不能使用管理员 Key 或网页Cookie提交推理

1. `POST /v1/jobs`，正文 `{"model":"research-text","kind":"harness","label":"编程任务"}`，建议传 `Idempotency-Key`，返回202和任务ID。`kind` 允许 harness/simcourt，不提供优先权限
2. `GET /v1/jobs/{id}` 查看状态；排队期间也要续租
3. `POST /v1/jobs/{id}/heartbeat` 每30秒续租，服务端租约300秒
4. 标准聊天请求增加 `X-Task-ID: work_...`，多个子请求归于同一工作流，任务和创建它的调用 Key 绑定
5. `POST /v1/jobs/{id}/finish` 仅在无未完成子请求时释放名额；`cancel` 取消排队或请求取消执行。运行中的取消若无法确认上游停止/用量则进入对账，不能报告零用量成功

不传任务ID的普通聊天按单请求计任务，不擅自把同一 Key 的所有调用归并成一个任务。普通同步API等待排队最多25秒，超时只取消尚未派发项，返回429和 `Retry-After: 10`；调用方不能对未知提交自动换ID重放。上游执行总时限600秒，建议客户端与代理等待660秒；长输入可能先计算数分钟才返回内容。显式工作流仍需每30秒发送心跳

长时间排队用 `POST /v1/jobs/{id}/calls` 提交聊天（202），`GET /v1/calls/{id}` 读取状态和最终结果。此接口不会返回标准聊天响应，只有任务适配器才可使用，不应让普通OpenAI客户端误把202当完成

网页关闭不取消已接收推理，已开始的流式输出客户端断开后仍可能继续执行并计量；需要取消时使用任务取消接口。整个客户端 harness 的工具执行仍在客户端，客户端进程退出后不能靠网关自动继续；任务租约最终释放。异步 calls 接口的推理由网关执行器负责，不依赖网页在线

## 调度与恢复

- 三个活跃工作流；同一工作流每次最多一个子请求进入引擎，避免单庭审或单harness抢占多路
- 多用户按最近派发时间轮转，队列总量100、单成员20，单任务待处理子请求最多8个
- 现有Key并发上限保留，新成员自管Key默认3；引擎槽位与Key上限取共同约束，不因Key上限为3就承诺三路GPU能力
- 不预留或冻结额度；派发前重新核验身份、模型权限和预算，结算按真实usage，任务ID和调用ID幂等
- 调度器使用独立操作系统锁，网关只运行一个worker；一个异步进程可管理多路上游流，不要用多个worker扩大并发
- 新增 `work_jobs/work_calls/work_fairness` 表，不删除或重建旧表。排队项重启后继续调度；已派发/准备派发的未知项转对账，不自动重发
- 对账项占用保护槽位；只能依据既有请求对账接口及真实证据解除，不能为恢复流量清空状态
- 调用正文和结果默认7天后清除，使用量账本和任务元数据保留。旧版 requests 响应留存策略不由本次队列清理自动改变
- 活跃工作流阻止模型切换，先让任务结束或取消。等待其他模型的任务不会自行切换模型

## SimCourt 与客户端适配

`integrations/task_client.py` 提供工作流创建、等待、心跳、请求关联和释放；`integrations/simcourt_queue.py` 封装庭审上下文

```python
from integrations.simcourt_queue import court_task, chat

# 在庭审worker内部，而不是网页服务启动时调用
with court_task('/path/to/private-task-client.json', 'research-text'):
    result = chat([{'role':'user','content':'合成测试问题'}], max_tokens=256)
    # 后续各阶段继续用chat，或给OpenAI客户端的每次请求传model_headers()
```

私有配置仅含 `base_url` 和该工具专属的普通成员 `api_key`，Unix归调用者所有且0600，不使用上游模型Key或管理员Key。所有庭审的模型调用必须改走同一个网关；不能一边注册队列任务，一边继续直连模型。跨线程/子进程的worker需显式传递任务上下文，ContextVar不会自动跨进程

需要管理员看到具体成员时，使用 `linked_court_task(mapping_file, authenticated_principal, model)`：由运营者在0600私有映射中，把SimCourt服务端已认证身份关联到对应平台成员的专属工具Key。未关联身份拒绝提交；不能从用户填写的表单字段或任意 `user_id` 直接认定身份。使用一个通用SimCourt服务Key只能归属到该服务账号，不能声称识别了真实成员。此映射不是统一登录，平台不会自动把成员网页登录凭证转发到其他网站

本轮提供并测试适配器，未编辑服务器现有SimCourt。上线前读取实际worker和model_adapter，备份后包裹一次庭审入口并修改调用位置，再进行一庭审加两任务的验收。不能未经实测宣称生产已经纳入队列

## 上线与回退

更新前暂停新调用、排空在途任务并用SQLite备份API保存完整状态；新版本加表后逐行比对 users/keys/ledger，不覆盖在线数据库。回退旧代码保留新增表和已知/未知任务状态；旧版本没有统一队列，回退期间不能继续向新接口派发。生产上线步骤见 `docs/serving-v0100.md`

相关官方契约：[OpenAI Chat](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Anthropic SSE](https://platform.claude.com/docs/en/build-with-claude/streaming)、[DSH provider](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)
