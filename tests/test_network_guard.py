"""Policy tests; these do not qualify Linux systemd or a real campus login."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def load_guard(monkeypatch):
    if sys.platform=='win32':
        monkeypatch.setitem(sys.modules,'fcntl',types.ModuleType('fcntl'))
    path=Path(__file__).resolve().parents[1]/'ops'/'network_guard.py'
    spec=importlib.util.spec_from_file_location('network_guard_policy',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('observations,expected',[
    ([401,None],True),([200,200],True),([403,200],True),
    ([None,200],False),([302,200],False),([503,302],True),
])
def test_required_cloud_path_cannot_be_masked_by_secondary_probe(monkeypatch,observations,expected):
    assert load_guard(monkeypatch).network_ready(observations) is expected


@pytest.mark.parametrize('initially_reachable',[True,False])
def test_cloud_503_is_reachability_not_failed_campus_authentication(monkeypatch,tmp_path,initially_reachable):
    module=load_guard(monkeypatch)
    helper=b'# approved synthetic helper\n'
    settings={'helper':'/protected/helper.py','helper_sha256':module.hashlib.sha256(helper).hexdigest(),
              'probes':['https://models.example.test/v1/models','https://secondary.example.test/'],
              'portal_probe':'http://models.example.test/','gateway_host':'gateway.example.test'}
    protected_reads=[]
    def root_file(path,limit):
        protected_reads.append(path)
        return module.json.dumps(settings).encode() if path=='/protected/config.json' else helper
    monkeypatch.setattr(module,'root_file',root_file)
    class PrivateState:
        def lstat(self):
            import stat
            return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0)
        def __truediv__(self,name):return tmp_path/name
    original_path=module.Path
    monkeypatch.setattr(module,'Path',lambda value:PrivateState() if value=='/private/state' else original_path(value))
    monkeypatch.setattr(module.os,'getuid',lambda:0,raising=False)
    if sys.platform=='win32':
        monkeypatch.setattr(module.os,'O_NOFOLLOW',0,raising=False)
        module.fcntl.LOCK_EX=2;module.fcntl.LOCK_NB=4
        module.fcntl.flock=lambda *args:None
    # A reachable primary must not read even a nonexistent credential or invoke login.
    secret=tmp_path/'credential.json'
    if not initially_reachable:
        secret.write_text('{"username":"fixture-account","password":"fixture-password"}')
    responses=([(503,''),(302,'https://secondary.example.test/next')] if initially_reachable else
               [(None,''),(200,''),(302,'https://gateway.example.test/login'),
                (503,''),(302,'https://secondary.example.test/next')])
    pending=iter(responses)
    monkeypatch.setattr(module,'probe',lambda session,url:next(pending))
    logins=[]
    def run_helper(*args,**kwargs):
        logins.append(1)
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(module,'subprocess',types.SimpleNamespace(run=run_helper,TimeoutExpired=TimeoutError,PIPE=-1))
    result=module.run('/protected/config.json','/private/state',str(secret))
    assert result['probe_http']==[503,302]
    assert result.get('attention_required',False) is False
    assert result['login_attempted'] is (not initially_reachable)
    assert len(logins)==(0 if initially_reachable else 1)
    assert result['cloud_http_server_error'] is True
    assert result['cloud_https_reachable'] is True
    assert result['model_availability_verified'] is False
    if initially_reachable:assert protected_reads==['/protected/config.json']


def test_credentials_enrollment_never_overwrites_existing_file(monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[1]/'ops'/'network_credentials.py'
    spec=importlib.util.spec_from_file_location('network_credentials_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    protected=tmp_path/'config';protected.mkdir()
    target=protected/'campus-login.json';target.write_text('previous credential fixture')
    original_path=module.Path
    class ProtectedDirectory:
        parents=()
        def lstat(self):
            import stat
            return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o750,st_uid=0)
        def __truediv__(self,name):
            return protected/name
    monkeypatch.setattr(module,'Path',lambda value:ProtectedDirectory() if value=='/etc/token-controller-network' else original_path(value))
    monkeypatch.setattr(module.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(module,'sys',types.SimpleNamespace(
        stdin=types.SimpleNamespace(isatty=lambda:True),stdout=types.SimpleNamespace(isatty=lambda:True)))
    monkeypatch.setattr(module.getpass,'getpass',lambda *args:pytest.fail('must not prompt before refusal'))
    with pytest.raises(ValueError,match='already exists'):
        module.main()
    assert target.read_text()=='previous credential fixture'
