# 预约与通知安全边界 · 2026-10-22

本期按 API 加固与数据库迁移安全流程审阅。当前 JSON 业务的主要风险是越权读取/修改、批量字段越权、申请及通知资源消耗、并发审批覆盖。依据 OWASP API 2023 的对象授权、功能授权和资源消耗原则，所有写入重新核对当前凭证与角色；私有用途和通知按账户隔离；共享队列仅返回必要元数据；业务状态、通知和审计在短事务内提交。HTTPS 仍是公网部署前的缺口，不能将本机 HTTP 预览直接公开。

核对时锁定与实装一致：FastAPI 0.141.1、Starlette 1.9.0、Pydantic 2.14.1、Uvicorn 0.52.4。已检索相关厂商公告和上游版本；已知旧版 Starlette 文件/表单路径公告不等于当前 JSON 接口受到相同影响。本次不是完整依赖漏洞扫描，不据此声明无漏洞；未为新增功能盲目升级依赖。实践上继续采用对象归属、幂等、持久化状态、分页和有限轮询；不接受客户端指定的宿主命令、镜像、通知 webhook 或上游 URL。

具体措施：每账户3个未结束预约、每天20次申请、全局100个未结束预约；通知列表最多100条；静态模式提示和真实权限分开；审批乐观版本检查；批准时间窗检查重叠；其他成员不可读用途、完整申请 ID 或审批说明。共享管理审计保存说明摘要指纹，正文仅保留在本人通知与授权审批记录中。

剩余上线工作：正式登录与调用 Key scope 分离、入口鉴权/IP与全局速率限制、HTTPS、校内受控隧道、网络隔离、备份恢复、依赖扫描与真实容量测试。预约业务限额不是全站 DDoS 防护。当前没有服务器端 harness；将来接入 Shell/文件工具时必须重新审阅工具权限、网络出口、挂载和人工确认边界。

来源：[OWASP API1](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/)、[OWASP API4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/)、[OWASP API5](https://api-security.owasp.org/editions/2023/en/0xa5-broken-function-level-authorization/)、[授权检查表](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)、[SSRF 检查表](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)、[Agent 过度授权](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)、[异步请求响应模式](https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply)。
