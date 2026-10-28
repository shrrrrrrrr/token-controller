"""Reviewable role navigation. Page permissions remain enforced by the backend."""

ADMIN_GROUPS = [
    ('overview', '总览', ['overview']),
    ('models', '模型管理', ['runtime', 'assets', 'play', 'tasks', 'queue']),
    ('members', '成员与权限', ['registrations', 'invitations', 'users', 'keyaudit']),
    ('records', '用量与审计', ['usage']),
    ('messages', '通知与公告', ['announcements']),
    ('settings', '工具与设置', ['tools', 'appearance', 'tasklegacy', 'runtimelegacy', 'queuelegacy', 'connect', 'guide', 'account']),
]
MEMBER_GROUPS = [
    ('overview', '仪表台', ['overview']),
    ('api', 'API 配置', ['mykeys', 'connect']),
    ('models', '模型中心', ['play', 'tasks', 'queue', 'tasklegacy']),
    ('quota', '我的额度', ['quota', 'usage']),
    ('tools', '项目工具', ['tools']),
]



def navigation(role, allowed_pages):
    allowed = set(allowed_pages)
    groups = ADMIN_GROUPS if role in ('admin', 'operator', 'auditor') else MEMBER_GROUPS
    result=[dict(id=group_id, title=title, pages=[page for page in pages if page in allowed])
            for group_id, title, pages in groups if any(page in allowed for page in pages)]
    if not result:
        account_pages=[page for page in ('account','guide') if page in allowed]
        if account_pages:result=[dict(id='account',title='账户与帮助',pages=account_pages)]
    return result
