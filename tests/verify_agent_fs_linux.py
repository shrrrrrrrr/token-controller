"""Linux stdlib-only verification of the exact deployed credential reader bodies.

Extracts the original functions by AST to avoid installing the web runtime on the
Linux test host. No GPU, Docker, network, actual credentials or production paths.
"""
import ast
import json
import os
from pathlib import Path
import re
import stat
import tempfile


def main():
    if os.name!='posix':raise SystemExit('This check requires Linux filesystem semantics')
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'gateway/runtime_agent.py').read_text(encoding='utf-8'))
    private=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='private_json')
    driver=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DockerDriver')
    credential=next(n for n in driver.body if isinstance(n,ast.FunctionDef) and n.name=='credential')
    scope={'os':os,'Path':Path,'stat':stat,'json':json,'re':re}
    for function in (private,credential):exec(compile(ast.Module(body=[function],type_ignores=[]),'<exact-agent-guard>','exec'),scope)
    class Fake:pass
    cases=0
    # Use a native Linux filesystem, not a Windows-mounted workspace whose mode
    # bits can be synthesized by DrvFS and cannot exercise chmod/NOFOLLOW guards.
    with tempfile.TemporaryDirectory(prefix='model-agent-fs-') as temp:
        folder=Path(temp);key=folder/'key';key.write_text('synthetic-model-credential-for-tests');key.chmod(0o600)
        fake=Fake();fake.manifest={'credential_file':str(key)}
        assert scope['credential'](fake)=='synthetic-model-credential-for-tests';cases+=1
        key.chmod(0o644)
        try:scope['credential'](fake);raise AssertionError('mode accepted')
        except ValueError:cases+=1
        key.chmod(0o600);link=folder/'link';link.symlink_to(key);fake.manifest['credential_file']=str(link)
        try:scope['credential'](fake);raise AssertionError('symlink accepted')
        except OSError:cases+=1
        link.unlink();os.link(key,link);fake.manifest['credential_file']=str(key)
        try:scope['credential'](fake);raise AssertionError('hardlink accepted')
        except ValueError:cases+=1
        link.unlink();key.write_text('x'*1025)
        try:scope['credential'](fake);raise AssertionError('oversized key accepted')
        except ValueError:cases+=1
        fifo=folder/'fifo';os.mkfifo(fifo,0o600);fake.manifest['credential_file']=str(fifo)
        try:scope['credential'](fake);raise AssertionError('fifo accepted')
        except ValueError:cases+=1
        doc=folder/'config.json';doc.write_text('{"synthetic":true}');doc.chmod(0o600)
        assert scope['private_json'](doc)=={'synthetic':True};cases+=1
        doc.chmod(0o640)
        try:scope['private_json'](doc);raise AssertionError('shared config accepted')
        except ValueError:cases+=1
    print(json.dumps({'linux_file_guard_cases_passed':cases,'production_or_network_used':False}))


if __name__=='__main__':main()
