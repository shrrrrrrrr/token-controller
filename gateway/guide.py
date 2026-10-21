"""Versioned account help; never grants access or runs model operations."""
import json

from .release import RELEASE_NOTES, UI_VERSION

ROLE_NAMES = {'admin': '平台管理员', 'operator': '运行操作员', 'caller': '普通成员', 'auditor': '只读审计员'}
ROLE_PAGES = {
    'admin': ['overview', 'assets', 'runtime', 'play', 'tools', 'users', 'usage', 'connect', 'guide'],
    'operator': ['overview', 'assets', 'runtime', 'play', 'tools', 'users', 'usage', 'connect', 'guide'],
    'caller': ['play', 'tools', 'usage', 'connect', 'guide'],
    'auditor': ['overview', 'assets', 'runtime', 'tools', 'users', 'usage', 'connect', 'guide'],
}

COMMON = [
    dict(title='了解连接方式', text='在接入指南复制 API 地址，使用自己的调用 Key。当前只支持非流式聊天，完整 Agent 工具协议仍待适配。', target='connect'),
    dict(title='进入项目工具', text='工具沿用原站登录。平台不会把 API Key 发送给工具网站，内嵌受限时可以在原站打开。', target='tools'),
]
ROLE_STEPS = {
    'admin': [
        dict(title='登记成员与调用权限', text='先创建账户，再分别设置模型范围、预算和独立 Key。API Key 继承用户角色，管理 Key 应与普通调用凭证分开。', target='users'),
        dict(title='核对资产与运行演练', text='模型登记快照不是实时监控。运行操作还需 profile 授权，当前只发送到本地模拟代理。', target='runtime'),
        dict(title='检查用量与待对账项', text='核实上游证据后再结算未知用量，不通过换请求 ID 盲目重发。其他成员的正文和产物不因管理员身份自动开放。', target='usage'),
    ],
    'operator': [
        dict(title='先看配置与资源边界', text='在模型资产中区分权重、镜像、配置和实例。未知健康状态不能当作模型已经上线。', target='assets'),
        dict(title='执行已授权运行演练', text='只能使用获授的 profile。切换先排空在途请求；Qwen 常驻保护。当前没有真实 GPU 启停能力。', target='runtime'),
        dict(title='检查操作记录', text='在用量记录和审计中核对演练结果。不能创建成员、发放 Key、调整预算、修改生命周期策略或人工结算。', target='usage'),
    ],
    'caller': [
        dict(title='开始一次模型调用', text='调用与任务只开放用户和 Key 共同获授的模型。当前默认是模拟文本；视频也是模拟任务，不会生成 MP4。', target='play'),
        dict(title='查看自己的任务与用量', text='任务结果按账户归属检查。同一次请求重试保留幂等 ID，修改输入或参数时使用新 ID。', target='usage'),
        dict(title='了解额度与资源申请', text='额度不足或模型未就绪时联系管理员。当前没有上线模型预约或夜间自动切换，选择模型不会赋予启停权限。', target='connect'),
    ],
    'auditor': [
        dict(title='只读核对资产', text='查看登记来源和缺失信息，区分合成数据、历史快照和实时状态。当前 GPU 健康值保持待核实。', target='assets'),
        dict(title='复核运行记录', text='可以读取运行和操作记录，不能执行启停或切换，也不能改变运行策略。', target='runtime'),
        dict(title='检查账本与管理审计', text='核对预留、结算和释放。不能调用模型、调整成员权限或预算、人工对账，也不自动获得任务正文和产物访问权。', target='usage'),
    ],
}


def guide_target(role):
    return f'{UI_VERSION}:{role}'


def account_guide(store, who, text_source):
    user, key = who['user'], who['key']
    role = user['role']
    acknowledged = store.one("SELECT id FROM audit WHERE actor=? AND action='guide.ack' AND target=? LIMIT 1",
                             (user['id'], guide_target(role))) is not None
    models = sorted(set(json.loads(user['models'])) & set(json.loads(key['models']))) if role != 'auditor' else []
    return dict(version=UI_VERSION, role=role, role_name=ROLE_NAMES[role], needs_attention=not acknowledged,
                allowed_pages=ROLE_PAGES[role], models=models, text_source=text_source,
                runtime_source='simulation', steps=ROLE_STEPS[role] + COMMON, changes=RELEASE_NOTES,
                boundary='引导只解释已实现功能，不授予额外权限。预约、夜间自动切换和真实 GPU 控制尚未上线。')
