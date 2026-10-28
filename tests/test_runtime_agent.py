"""Synthetic lifecycle driver only: never invokes Docker, GPUs or a real server."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from gateway.runtime_agent import Agent,OperationInput,create_agent,validate_manifest
from gateway.store import Fault,uid


class Driver:
    manifest={'profiles':{'old':{},'new':{}},'resident_model':'new'}
    def __init__(self):self.current='old';self.events=[];self.limit=1;self.active=False;self.fail=False
    def inspect(self,model):return {}
    def running(self):return self.current
    def pause_court(self):self.events.append('pause');old=self.limit;self.limit=0;return old
    def court_idle(self):return not self.active
    async def slots_idle(self):return not self.active
    def resources(self,model):self.events.append('resources')
    def stop(self,model):assert self.limit==0 and not self.active;self.events.append('stop:'+model);self.current=None
    def start(self,model):
        self.events.append('start:'+model)
        if self.fail:raise TimeoutError
        self.current=model
    async def healthy(self,model):self.events.append('health');return self.current==model
    def resume_court(self,model,limit):self.events.append('resume');self.limit=limit


@pytest.fixture
def driver_time(monkeypatch):
    async def no_wait(_):return
    monkeypatch.setattr('gateway.runtime_agent.asyncio.sleep',no_wait)


def test_agent_switches_only_after_drain_then_health_and_restores_court_limit(tmp_path,driver_time):
    async def run():
        driver=Driver();agent=Agent(tmp_path,driver);body=OperationInput(operation_id=uid('op_'),model='new',action='switch')
        await agent.submit(body);await agent.task
        assert agent.operation()['phase']=='completed'
        assert driver.events==['pause','stop:old','resources','start:new','health','resume']
        assert driver.limit==1 and driver.current=='new'
        await agent.submit(body)
        assert driver.events.count('start:new')==1
    asyncio.run(run())


def test_agent_does_not_stop_an_inflight_court_or_model(tmp_path,driver_time):
    async def run():
        driver=Driver();driver.active=True;agent=Agent(tmp_path,driver)
        await agent.submit(OperationInput(operation_id=uid('op_'),model='new',action='switch'));await agent.task
        assert agent.operation()['phase']=='recovery_required'
        assert agent.operation()['error']=='inflight_drain_timeout'
        assert driver.current=='old' and driver.limit==0
        assert not any(x.startswith('stop:') or x.startswith('start:') for x in driver.events)
    asyncio.run(run())


def test_unknown_start_not_replayed_after_agent_restart(tmp_path,driver_time):
    async def run():
        driver=Driver();driver.fail=True;agent=Agent(tmp_path,driver);body=OperationInput(operation_id=uid('op_'),model='new',action='switch')
        await agent.submit(body);await agent.task
        assert agent.operation()['phase']=='recovery_required'
        agent2=Agent(tmp_path,driver);await agent2.submit(body)
        assert driver.events.count('start:new')==1 and driver.limit==0
        with pytest.raises(Fault):await agent2.submit(OperationInput(operation_id=uid('op_'),model='new',action='switch'))
    asyncio.run(run())


def test_agent_rejects_unapproved_model_and_idempotency_conflict(tmp_path,driver_time):
    async def run():
        driver=Driver();agent=Agent(tmp_path,driver)
        with pytest.raises(Fault):await agent.submit(OperationInput(operation_id=uid('op_'),model='arbitrary',action='switch'))
        body=OperationInput(operation_id=uid('op_'),model='new',action='switch')
        await agent.submit(body);await agent.task
        with pytest.raises(Fault):await agent.submit(OperationInput(operation_id=body.operation_id,model='old',action='switch'))
        assert driver.events.count('start:new')==1
    asyncio.run(run())


def test_agent_api_requires_separate_secret_and_does_not_echo_it(tmp_path):
    agent=Agent(tmp_path,Driver());token='synthetic-runtime-auth-for-tests-only'
    with TestClient(create_agent(agent,token)) as client:
        assert client.get('/state').status_code==401
        assert client.post('/operations',json={}).status_code==401
        response=client.get('/state',headers={'Authorization':'Bearer '+token})
        assert response.status_code==200 and token not in response.text
        assert response.json()['running_model']=='old'


def test_manifest_requires_immutable_images_containers_and_configuration():
    manifest={'profiles':{'model':{'container_id':'a'*64,'image_id':'sha256:'+'b'*64,'configuration_sha256':'c'*64,'gpus':[0,1,2],'minimum_free_mib':70000}},
              'resident_model':'model','credential_file':'/var/lib/example/model.key','simcourt_config':'/var/lib/example/config.json','simcourt_container':'d'*64}
    assert validate_manifest(manifest)==manifest
    for field,value in [('container_id','mutable-name'),('image_id','latest'),('configuration_sha256','unknown'),('gpus',[0,0]),('minimum_free_mib',-1)]:
        changed=json.loads(json.dumps(manifest));changed['profiles']['model'][field]=value
        with pytest.raises(ValueError):validate_manifest(changed)
    with pytest.raises(ValueError):validate_manifest({**manifest,'command':'arbitrary'})
