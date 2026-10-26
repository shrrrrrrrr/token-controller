"""Reviewable role navigation. Page permissions remain enforced by the backend."""

ADMIN_GROUPS = [
    ('overview', '总览', ['overview']),
    ('models', '模型服务', ['assets', 'play', 'runtime', 'queue']),
    ('members', '成员与权限', ['registrations', 'users', 'keyaudit']),
    ('records', '用量与审计', ['usage']),
    ('tools', '项目工具', ['tools']),
    ('settings', '帮助与设置', ['connect', 'guide', 'account']),
]
MEMBER_GROUPS = [
    ('models', '模型服务', ['play', 'queue']),
    ('api', '我的 API', ['mykeys', 'connect']),
    ('usage', '我的用量', ['usage']),
    ('tools', '项目工具', ['tools']),
    ('account', '账户与帮助', ['account', 'guide']),
]


def navigation(role, allowed_pages):
    allowed = set(allowed_pages)
    groups = ADMIN_GROUPS if role in ('admin', 'operator', 'auditor') else MEMBER_GROUPS
    return [dict(id=group_id, title=title, pages=[page for page in pages if page in allowed])
            for group_id, title, pages in groups if any(page in allowed for page in pages)]
