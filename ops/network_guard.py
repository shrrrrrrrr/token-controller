"""Linux oneshot network recovery; operator-reviewed helper only, no daemon API."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

import requests


def root_file(path,limit):
    path=Path(path)
    for parent in (path.parent,*path.parents):
        entry=parent.lstat()
        if not stat.S_ISDIR(entry.st_mode) or entry.st_uid!=0 or entry.st_mode&0o022:
            raise ValueError('unsafe protected parent')
    entry=path.lstat()
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink!=1 or entry.st_uid!=0 or entry.st_mode&0o022 or entry.st_size>limit:
        raise ValueError('unsafe protected file')
    return path.read_bytes()


def probe(session,url):
    try:
        with session.get(url,timeout=(4,6),allow_redirects=False,stream=True) as response:
            return response.status_code,response.headers.get('location','')
    except requests.RequestException:return None,''


def network_ready(observations):
    """A verified HTTPS server error is reachable transport, not captive-portal proof."""
    code=observations[0]
    return code is not None and 200<=code<600 and code not in (301,302,303,307,308)


def connectivity_report(observations):
    code=observations[0]
    server_error=code is not None and 500<=code<600
    return {'cloud_https_reachable':network_ready(observations),
            'cloud_http_server_error':server_error,'application_check_needed':server_error,
            'model_availability_verified':False}


def run(config_path,state_dir,credential_file):
    config=json.loads(root_file(config_path,8192))
    if set(config)!={'helper','helper_sha256','probes','portal_probe','gateway_host'}:raise ValueError('unexpected config fields')
    for url in config['probes']:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('expected fixed HTTPS probes')
    portal=urlsplit(config['portal_probe'])
    if len(config['probes'])!=2 or portal.scheme!='http' or not portal.hostname or portal.username or portal.password or portal.query or portal.fragment:
        raise ValueError('expected bounded portal probe')
    state=Path(state_dir);entry=state.lstat()
    if not stat.S_ISDIR(entry.st_mode) or entry.st_uid!=os.getuid() or stat.S_IMODE(entry.st_mode)!=0o700:
        raise ValueError('private state directory required')
    with (state/'guard.lock').open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'phase':'already_running'}
        with requests.Session() as session:
            session.trust_env=False
            observations=[probe(session,url)[0] for url in config['probes']]
            if network_ready(observations):
                return {'phase':'network_ready','probe_http':observations,'login_attempted':False,
                        **connectivity_report(observations)}
            initial_observations=list(observations)
            code,location=probe(session,config['portal_probe'])
            if code not in (301,302,303,307,308) or urlsplit(location).hostname!=config['gateway_host']:
                return {'phase':'network_failure_not_confirmed_portal','login_attempted':False,'attention_required':True}
            attempts_path=state/'attempts.json';now=time.time()
            attempts=json.loads(attempts_path.read_text()) if attempts_path.exists() else []
            attempts=[n for n in attempts if type(n) in (int,float) and now-n<3600]
            if len(attempts)>=3 or attempts and now-attempts[-1]<300:
                return {'phase':'login_backoff','login_attempted':False,'attention_required':True}
            helper=root_file(config['helper'],100000)
            if hashlib.sha256(helper).hexdigest()!=config['helper_sha256']:raise ValueError('helper digest mismatch')
            credential=Path(credential_file);entry=credential.lstat()
            if not stat.S_ISREG(entry.st_mode) or entry.st_size>4096:raise ValueError('invalid credential')
            secret=json.loads(credential.read_text())
            if set(secret)!={'username','password'} or any(not isinstance(secret[k],str) or not secret[k] or '\n' in secret[k] or '\r' in secret[k] for k in secret):
                raise ValueError('invalid credential fields')
            attempts.append(now)
            fd=os.open(attempts_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(attempts,stream)
            # Both output streams stay private in memory, never in the journal.
            try:
                result=subprocess.run(['/usr/bin/python3','-I','-B',config['helper']],
                    input=(secret['username']+'\n'+secret['password']+'\n').encode(),
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,
                    env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8'})
                helper_ok=result.returncode==0
            except subprocess.TimeoutExpired:helper_ok=False
            observations=[probe(session,url)[0] for url in config['probes']]
            ready=network_ready(observations)
            return {'phase':'network_restored' if ready else 'login_did_not_restore_network',
                    'login_attempted':True,'helper_exit_success':helper_ok,'probe_http':observations,
                    'initial_probe_http':initial_observations,'attention_required':not ready,
                    **connectivity_report(observations)}


if __name__=='__main__':
    try:
        result=run(sys.argv[1],sys.argv[2],sys.argv[3]);print(json.dumps(result),flush=True)
        sys.exit(0 if not result.get('attention_required') else 1)
    except Exception:
        print('{"phase":"network_guard_configuration_error","credentials_printed":false}',flush=True)
        sys.exit(1)
