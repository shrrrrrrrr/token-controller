"""Versioned account help; never grants access or runs model operations."""
import json

from .release import RELEASE_NOTES, UI_VERSION

ROLE_NAMES = {'admin': '平台管理员', 'operator': '运行操作员', 'caller': '普通成员', 'auditor': '只读审计员'}
ROLE_PAGES = {
    'admin': ['overview', 'assets', 'runtime', 'play', 'queue', 'notifications', 'tools', 'users', 'usage', 'connect', 'guide'],
    'operator': ['overview', 'assets', 'runtime', 'play', 'queue', 'notifications', 'tools', 'users', 'usage', 'connect', 'guide'],
    'caller': ['play', 'queue', 'notifications', 'tools', 'usage', 'connect', 'guide'],
    'auditor': ['overview', 'assets', 'runtime', 'queue', 'notifications', 'tools', 'users', 'usage', 'connect', 'guide'],
}

COMMON = [
    dict(title='预约与个人通知', text='模型排队支持脱敏概览、获授模型申请和管理员排程。通知中心接收自己的审批消息。当前只登记预约，不执行推理；客户端型任务执行时需保持 harness 后台和网络可用，不要求一直登录网页。', target='queue'),
    dict(title='了解连接方式', text='在接入指南复制 API 地址，使用自己的调用 Key。当前只支持非流式聊天，完整 Agent 工具协议仍待适配。', target='connect'),
    dict(title='进入项目工具', text='工具沿用原站登录。平台不会把 API Key 发送给工具网站，内嵌受限时可以在原站打开。', target='tools'),
]
ROLE_STEPS = {
    'admin': [
        dict(title='登记成员与调用权限', text='新建账户仅启用管理员和普通成员。模型勾选项来自服务端目录，再设置预算和独立 Key。Key 继承账户角色，管理员 Key 只用于管理网页；客户端应使用普通成员 Key。', target='users'),
        dict(title='核对资产与运行演练', text='模型登记快照不是实时监控。管理员默认具有全部已批准 Profile 的操作资格，仍须检查 GPU 冲突、在途任务和常驻保护。当前本地运行操作只发送到模拟代理。', target='runtime'),
        dict(title='检查用量与待对账项', text='核实上游证据后再结算未知用量，不通过换请求 ID 盲目重发。其他成员的正文和产物不因管理员身份自动开放。', target='usage'),
    ],
    'operator': [
        dict(title='先看配置与资源边界', text='这是本地保留的历史运行操作员身份；新账户不再提供此选项，云端预发布不开放该身份登录。在资产中区分权重、镜像、配置和实例，未知健康状态不能当作模型已经上线。', target='assets'),
        dict(title='执行已授权运行演练', text='只能使用获授的 profile。切换先排空在途请求；Qwen 常驻保护。当前没有真实 GPU 启停能力。', target='runtime'),
        dict(title='检查操作记录', text='在用量记录和审计中核对演练结果。不能创建成员、发放 Key、调整预算、修改生命周期策略或人工结算。', target='usage'),
    ],
    'caller': [
        dict(title='开始一次模型调用', text='调用与任务只开放用户和 Key 共同获授的模型。模型名称与授权来自服务端目录，待部署的登记不能调用。当前本地默认是模拟文本；视频也是模拟任务，不会生成 MP4。', target='play'),
        dict(title='查看自己的任务与用量', text='任务结果按账户归属检查。同一次请求重试保留幂等 ID，修改输入或参数时使用新 ID。', target='usage'),
        dict(title='申请模型时间窗', text='在模型排队中申请获授模型，填写用途摘要和预计时间，不填真实提示词。审批通知在个人通知中心查看。预约不冻结额度、不自动启动任务或切换模型。', target='queue'),
    ],
    'auditor': [
        dict(title='只读核对资产', text='这是本地保留的历史审计员身份；新账户不再提供此选项，云端预发布不开放该身份登录。查看登记来源和缺失信息，区分合成数据、历史快照和实时状态。当前 GPU 健康值保持待核实。', target='assets'),
        dict(title='复核运行记录', text='可以读取运行和操作记录，不能执行启停或切换，也不能改变运行策略。', target='runtime'),
        dict(title='检查账本与管理审计', text='核对预留、结算和释放。不能调用模型、调整成员权限或预算、人工对账，也不自动获得任务正文和产物访问权。', target='usage'),
    ],
}


def guide_target(role):
    return f'{UI_VERSION}:{role}'


CLOUD_COMMON = [
    dict(title='查看模型目录与部署状态', text='模型名称和 API ID 来自服务端私有目录。待部署表示计划，尚未下载、连接或提供推理；成员只看到账户与 Key 共同获授的目录项。本版没有网页编辑登记表。', target='play'),
    dict(title='预约与个人通知', text='可以登记模型时间窗并接收个人审批通知；预约不会启动模型、执行推理或冻结调用额度。客户端任务实际执行时需保持 harness 后台与网络可用，提交后无需一直登录网页。', target='queue'),
    dict(title='等待模型与客户端接入验收', text='云端推理 API 关闭，接入指南中的 Base URL 和模型 ID 为后续接入准备。GPU 服务器部署、安全通道、真实调用计量和客户端工具协议都需验收；管理员 Key 不应填入 harness 或 CC Switch。', target='connect'),
    dict(title='进入项目工具', text='工具仍使用各自的登录，平台不会转发访问 Key。云端入口不代表校内工具已经连通。', target='tools'),
]


def account_guide(store, who, text_source, cloud_preview=False, cloud_api=False):
    user, key = who['user'], who['key']
    role = user['role']
    acknowledged = store.one("SELECT id FROM audit WHERE actor=? AND action='guide.ack' AND target=? LIMIT 1",
                             (user['id'], guide_target(role))) is not None
    models = sorted(set(json.loads(user['models'])) & set(json.loads(key['models']))) if role != 'auditor' else []
    if cloud_api:
        steps = ([dict(title='发放普通成员调用 Key', text='现有账户和额度继续保留。管理员 Key 只管理平台；为普通成员分配已登记模型、预算及独立 Key。用户与 Key 权限取交集，普通成员 Key 才能调用公网文本 API。', target='users'),
                  dict(title='核对上游与待对账项', text='运营者通过固定 HTTPS 接口登记上游凭证，服务器私有文件保存，不在网页或日志回显。未知提交不自动重试或退款，需要证据对账；未知任务会阻止后续派发。', target='usage')]
                 if role == 'admin' else [dict(title='使用获授的文本模型', text='使用普通成员 Key 调用 OpenAI 非流式 Chat Completions。模型目录只在只读 API 检查通过后显示可用；网关全局同时派发一个文本请求，满载返回429，预约队列不代替推理队列。', target='play')])
        steps += [dict(title='查看用量与保留请求 ID', text='额度先预留、按真实 usage 结算。对同一请求保留 Idempotency-Key；未知提交不得换 ID 盲目重发。上游调用没有自动重试。', target='usage'),
                  dict(title='接入边界', text='当前只有非流式文本 API；SSE、工具调用、Responses 和 Anthropic Messages 尚未实现，不代表完整兼容任何 harness。管理网页登录仍使用内存中的 Key，尚未实现独立网页登录会话。', target='connect'),
                  dict(title='预约与工具入口', text='预约只登记时间窗，不自动切换模型。项目工具沿用原站登录，校内工具链接不代表已完成公网代理。模型启停、视频生成和服务端 harness 仍未开放。', target='queue')]
    elif cloud_preview:
        steps = ([dict(title='验收成员与权限管理', text='仅启用管理员和普通成员。先从登记目录分配成员模型范围与预算，再发放成员 Key；账户与 Key 权限取交集。本期网页登录仍使用内存中的高熵 Key，正式会话与调用凭证分离尚待完成。', target='users'),
                  dict(title='确认运行控制边界', text='云端受限预发布不连接控制代理，不开放真实或模拟启停。未来管理员可操作全部已批准 Profile，但 GPU 冲突、在途任务和常驻保护仍会在后端检查。', target='runtime')]
                 if role == 'admin' else [dict(title='进入受限预发布工作空间', text='可以查看获授目录、个人记录、预约和通知。模型尚未接入，当前没有真实或模拟推理，也不会产生新的推理扣费。刷新页面需重新输入访问 Key。', target='play')])
        steps += CLOUD_COMMON
    else:
        steps = ROLE_STEPS[role] + COMMON
    return dict(version=UI_VERSION, role=role, role_name=ROLE_NAMES[role], needs_attention=not acknowledged,
                allowed_pages=ROLE_PAGES[role], models=models, text_source=text_source,
                runtime_source='disabled' if cloud_preview or cloud_api else 'simulation', steps=steps, changes=RELEASE_NOTES,
                boundary=('云端受限文本试运行：普通成员获授 Key 可调用非流式文本。管理员凭证不得调用模型；运行控制、视频、SSE、工具协议和独立网页登录会话尚未开放。' if cloud_api else '云端受限预发布：推理与运行控制均关闭。仅验收成员、权限、目录、预约和通知；正式会话与调用 Key scope 分离、模型通道和客户端兼容仍待完成。'
                          if cloud_preview else '预约登记、审批和个人通知已实现；自动执行、夜间模型切换、服务端 harness 和真实 GPU 控制尚未上线。'))
