"""Account dashboard and model-control facade. No host commands in the web process."""
import json
import asyncio
import time

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .store import Fault, current_identity
from .branding import vendor_info


MODEL_SHELF = [
    dict(id='glm5.3-flash', display_name='GLM-5.3-Flash', kind='text', quantization='UD-Q4_K_XL', context=16384, gpu_count=3),
    dict(id='qwen3.8-flash-next', display_name='Qwen3.8-Flash-Next', kind='text', quantization='Q8_0', context=16384, gpu_count=3),
    dict(id='qwen3-32b', display_name='Qwen3-32B', kind='text', quantization='BF16', context=16384, gpu_count=2),
    dict(id='h3', display_name='MiniMax-H3', kind='video', quantization='', context=None, gpu_count=None),
]


class SwitchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    model: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{0,95}$')
    action: str = Field(pattern=r'^(switch|stop)$')
    operation_id: str = Field(pattern=r'^op_[a-f0-9]{32}$')
    allow_resident_stop: bool = False


def mount_console(app, store, provider, identity, registered_models, *, public_mode=False, controller=None):
    public_cache={}
    public_lock=asyncio.Lock()
    async def shelf(who=None):
        registered = {item['id']: item for item in registered_models()}
        entries = {item['id']: dict(item) for item in MODEL_SHELF}
        for model_id, item in registered.items():
            entries[model_id] = {**entries.get(model_id, {}), **item}
        runtime = await controller.snapshot() if controller else None
        ready_model = None
        if runtime:
            ready_model = runtime.get('running_model')
        elif hasattr(provider, 'probe'):
            try:
                if hasattr(provider, 'alias'):
                    await provider.probe(cached=True)
                    ready_model = provider.alias if provider.ready else None
            except Fault:
                pass
        elif provider.source == 'simulation':
            ready_model = 'qwen3-32b'
        user_models = set(json.loads(who['user']['models'])) if who else set()
        profiles = runtime.get('models', {}) if runtime else {}
        operation = runtime.get('operation') if runtime else None
        for model_id, item in entries.items():
            item.update(vendor_info(model_id, item.get('vendor')))
            item.update(running=model_id == ready_model,
                        status_label='正在运行' if model_id == ready_model else '暂未运行',
                        resident=model_id == (runtime.get('resident_model', 'glm5.3-flash') if runtime else 'glm5.3-flash'),
                        accessible=model_id in user_models,
                        callable=model_id == ready_model and model_id in user_models and item.get('kind') == 'text'
                                 and not operation and model_id in registered,
                        control_configured=bool(profiles.get(model_id, {}).get('configured')),
                        source='live_runtime_agent' if runtime and runtime.get('source') == 'live' else
                               'simulation' if provider.source == 'simulation' else 'live_api_probe' if model_id == ready_model else 'registration')
        return dict(models=list(entries.values()), operation=operation, last_operation=runtime.get('last_operation') if runtime else None,
                    runtime_connected=bool(runtime and runtime.get('source') in ('live','simulation')),
                    source=runtime.get('source') if runtime else 'unconnected',
                    runtime_error=runtime.get('error') if runtime else None)

    app.state.console_shelf = shelf

    @app.get('/api/service-info')
    async def service_info():
        # Registration names only. No account, topology, credentials or control configuration.
        async with public_lock:
            if time.monotonic()-public_cache.get('time',0)<15:
                return public_cache['value']
            data = await shelf()
            result={'models': [{k: item.get(k) for k in ('id','display_name','kind','running','status_label','resident','vendor','vendor_name','vendor_logo')}
                               for item in data['models']], 'switching': bool(data['operation'])}
            public_cache.update(time=time.monotonic(),value=result)
            return result

    @app.get('/api/console')
    async def console(who=Depends(identity)):
        with store.connect() as db:
            user, _ = current_identity(db, who, allow_pending=True)
        member_id = user['id']
        totals = store.one('SELECT COUNT(*) AS requests,COALESCE(SUM(input_tokens),0) AS input_tokens,'
                           'COALESCE(SUM(output_tokens),0) AS output_tokens,'
                           "COALESCE(SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END),0) AS succeeded "
                           'FROM requests WHERE user_id=?', (member_id,))
        days = store.rows("SELECT strftime('%Y-%m-%d',created,'unixepoch') AS day,COUNT(*) AS requests,"
                          'COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens '
                          'FROM requests WHERE user_id=? AND created>=? GROUP BY day ORDER BY day', (member_id,time.time()-30*86400))
        keys = store.one('SELECT COUNT(*) AS count FROM keys WHERE user_id=? AND revoked=0 AND expires>?', (member_id,time.time()))['count']
        return {**await shelf(who), 'totals': totals, 'days': days, 'active_keys': keys,
                'quota': dict(limit=user['budget'], used=user['spent'], remaining=None if user['budget'] < 0 else max(0,user['budget']-user['spent']),
                              unlimited=user['budget'] < 0, reserved=0, metering='actual', stop_policy='between_calls'),
                'model_requests_available': False, 'observed_at': time.time()}

    @app.get('/api/console/status')
    async def console_status(who=Depends(identity)):
        return await shelf(who)

    @app.get('/api/model-control')
    async def control_state(who=Depends(identity)):
        if who['user']['role'] != 'admin':
            raise Fault('role_forbidden',403)
        return await shelf(who)

    @app.post('/api/model-control/operations', status_code=202)
    async def operate(body: SwitchInput, who=Depends(identity)):
        with store.tx() as db:
            store.check_admin(db, who)
        if controller is None:
            raise Fault('runtime_agent_not_connected',503)
        if body.model not in {item['id'] for item in (await shelf(who))['models']}:
            raise Fault('model_not_registered',400)
        return await controller.submit(body.model, body.action, body.operation_id, who['user']['id'],authorization=who,
                                       allow_resident_stop=body.allow_resident_stop)

    @app.get('/api/model-control/history')
    def control_history(who=Depends(identity)):
        if who['user']['role']!='admin':raise Fault('role_forbidden',403)
        return store.rows('SELECT id,model,action,phase,created,updated,error FROM model_switches ORDER BY created DESC LIMIT 50')

    @app.get('/api/console/requests')
    def own_requests(who=Depends(identity), offset: int = Query(default=0,ge=0)):
        return store.rows('SELECT id,model,status,input_tokens,output_tokens,actual,created,elapsed_ms FROM requests '
                          'WHERE user_id=? ORDER BY created DESC LIMIT 30 OFFSET ?', (who['user']['id'],offset))
