import json

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.project_tools import configured_tools
from gateway.store import Store


def test_private_tools_require_identity_and_allow_only_configured_frames(tmp_path):
    config = tmp_path / 'tools.json'
    config.write_text(json.dumps({'tools': [
        {'id': 'legal', 'title': 'Legal tool', 'url': 'https://legal.example.test/workspace', 'embed': True},
        {'id': 'external', 'url': 'https://external.example.test/', 'embed': False},
    ]}), encoding='utf-8')
    store = Store(tmp_path / 'test.sqlite')
    user = store.create_user('member', 'caller', [], [], 100)
    key = store.issue_key(user, [], 100)['key']
    app = create_app(store, background=False, project_tools_path=config)
    with TestClient(app) as client:
        assert client.get('/api/project-tools').status_code == 401
        reply = client.get('/api/project-tools', headers={'Authorization': 'Bearer ' + key})
        assert reply.status_code == 200
        assert reply.json()['credentials_forwarded'] is False
        assert reply.json()['tools'][0]['health'] == 'unverified'
        csp = reply.headers['Content-Security-Policy']
        assert 'frame-src https://legal.example.test;' in csp
        assert 'external.example.test' not in csp
        assert key not in reply.text
        config.write_text('{}', encoding='utf-8')
        assert "frame-src 'none';" in client.get('/').headers['Content-Security-Policy']


def test_tools_fail_closed_and_reject_unsafe_urls(tmp_path):
    config = tmp_path / 'tools.json'
    assert configured_tools(config) == []
    for raw in ('not json', 'null', '[]', '{"tools": {}}'):
        config.write_text(raw, encoding='utf-8')
        assert configured_tools(config) == []
    urls = ['javascript:alert(1)', 'file:///tmp/test', 'https://user:secret@example.test/',
            'https://example.test/?key=secret', 'https://example.test/#secret',
            'http://example.test/', 'https://example.test:broken/', 'https://[broken/']
    config.write_text(json.dumps({'tools': [{'id': 'bad', 'url': url, 'embed': True} for url in urls]}), encoding='utf-8')
    assert configured_tools(config) == []
    config.write_text(json.dumps({'tools': [{'id': 'local', 'url': 'http://127.0.0.1:9999/', 'embed': True}]}), encoding='utf-8')
    assert configured_tools(config)[0]['origin'] == 'http://127.0.0.1:9999'
