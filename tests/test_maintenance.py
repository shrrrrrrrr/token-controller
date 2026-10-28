"""Fixed privileged maintenance vocabulary; no real systemctl or sudo execution."""
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def maintenance(monkeypatch):
    path=Path(__file__).resolve().parents[1]/'ops'/'maintenance.py'
    spec=importlib.util.spec_from_file_location('maintenance_fixed_vocabulary',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    record={'uid':0,'mode':stat.S_IFREG|0o600,'config':{'tunnel_unit':'model-platform-tunnel.service'}}
    class ProtectedFile:
        parents=()
        def lstat(self):return SimpleNamespace(st_uid=record['uid'],st_mode=record['mode'])
        def stat(self):return SimpleNamespace(st_size=128)
        def read_text(self):return json.dumps(record['config'])
    monkeypatch.setattr(module,'Path',lambda value:ProtectedFile())
    monkeypatch.setattr(module.os,'geteuid',lambda:0,raising=False)
    module.sys=SimpleNamespace(argv=['tc-maintenance','status'])
    module.subprocess=SimpleNamespace(DEVNULL=-3,run=Mock(return_value=SimpleNamespace(stdout='ActiveState=active\n')))
    return module,record


@pytest.mark.parametrize('args',[
    ['status','extra'],['status; id'],['/bin/sh'],['docker'],['--config','/tmp/evil'],[],['network-check && id'],
])
def test_unknown_arguments_never_reach_privileged_command(maintenance,args):
    module,_=maintenance;module.sys.argv=['tc-maintenance',*args]
    with pytest.raises(ValueError):module.main()
    module.subprocess.run.assert_not_called()


def test_nonroot_cannot_invoke_backend_directly(maintenance,monkeypatch):
    module,_=maintenance;monkeypatch.setattr(module.os,'geteuid',lambda:1000)
    with pytest.raises(ValueError):module.main()
    module.subprocess.run.assert_not_called()


@pytest.mark.parametrize('change',[
    {'uid':1000},{'mode':stat.S_IFLNK|0o777},{'mode':stat.S_IFREG|0o666},
    {'config':{'tunnel_unit':'model-platform-tunnel.service; id'}},
    {'config':{'tunnel_unit':'../ssh.service'}},
    {'config':{'tunnel_unit':'model-platform-tunnel.service','command':'id'}},
])
def test_untrusted_configuration_cannot_select_a_command(maintenance,change):
    module,record=maintenance;record.update(change)
    with pytest.raises(ValueError):module.main()
    module.subprocess.run.assert_not_called()


@pytest.mark.parametrize('command,expected',[
    ('status',None),
    ('network-check',['/usr/bin/systemctl','start','token-controller-network.service']),
    ('tunnel-reconnect',['/usr/bin/systemctl','restart','model-platform-tunnel.service']),
])
def test_only_registered_fixed_service_commands_are_executed(maintenance,command,expected):
    module,_=maintenance;module.sys.argv=['tc-maintenance',command];module.main()
    calls=module.subprocess.run.call_args_list
    if expected is not None:assert calls[0].args[0]==expected
    assert len(calls)==(1 if expected is None else 2)
    assert calls[-1].args[0]==['/usr/bin/systemctl','show','token-controller-network.service','model-platform-tunnel.service',
                             '--property=Id,ActiveState,SubState,Result,ExecMainStatus']
    assert all(call.kwargs.get('check') is True and not call.kwargs.get('shell') for call in calls)
