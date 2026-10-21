"""Operator-maintained private links. Reading them never proxies or logs into sites."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def configured_tools(path=None):
    source = Path(path) if path else ROOT / '.state' / 'project-tools.local.json'
    if not source.is_file():
        return []
    try:
        raw = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get('tools'), list):
        return []
    result = []
    for item in raw.get('tools', [])[:20]:
        if not isinstance(item, dict) or not isinstance(item.get('url'), str):
            continue
        try:
            url = urlparse(item['url'])
            port = url.port
        except ValueError:
            continue
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            continue
        if not re.fullmatch(r'[A-Za-z0-9.:-]+', url.hostname):
            continue
        if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
            continue
        host = f'[{url.hostname}]' if ':' in url.hostname else url.hostname
        origin = f'{url.scheme}://{host}' + (f':{port}' if port else '')
        tool_id = str(item.get('id', ''))
        if not tool_id or not all(c.isalnum() or c == '-' for c in tool_id):
            continue
        result.append(dict(id=tool_id, title=str(item.get('title', '项目工具'))[:80],
                           description=str(item.get('description', ''))[:500], url=item['url'], origin=origin,
                           embed=item.get('embed') is True, health='unverified',
                           credentials_forwarded=False, source='private_local_config'))
    return result
