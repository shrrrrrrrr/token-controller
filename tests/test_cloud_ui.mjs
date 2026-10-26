// View-contract tests, not browser or viewport verification. Run: node --test tests/test_cloud_ui.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const source=fs.readFileSync(new URL('../gateway/static/app.js',import.meta.url),'utf8');
const model={id:'sample-planned-model',display_name:'任意登记模型 <script>',kind:'text',deployment_status:'planned',authorized:true,callable:false,description:'只登记，未运行'};
function setup(mode='cloud-preview',role='admin'){
 const elements=new Map(),calls=[];
 function element(selector){if(!elements.has(selector))elements.set(selector,{innerHTML:'',textContent:'',dataset:{},style:{},classList:{add(){},remove(){},contains(){return false}},setAttribute(){},addEventListener(){},focus(){}});return elements.get(selector)}
 const document={body:element('body'),querySelector:element,querySelectorAll(){return []},addEventListener(){},hidden:false};
 const identity={mode,text_source:mode==='cloud-api'?'cloud_loopback_openai':'unavailable',user:{id:'user-admin',name:'示例管理员',role,budget:100,spent:0,reserved:0},key:{id:'current-key'}};
 const responses={
  '/health':{mode:'cloud-preview',text_source:'unavailable',runtime_source:'disabled'},
  '/api/me':identity,
  '/api/model-catalog':{models:[mode==='cloud-api'?{...model,status:'ready',callable:role==='caller'}:model],source:'private_configuration',runtime_source:'disabled'},
  '/v1/models':{data:[{id:model.id}]},
  '/api/users':[{id:'user-member',name:'示例成员',role:'caller',active:1,models:JSON.stringify([model.id]),profiles:'[]',budget:100,spent:0,reserved:0}],
  '/api/keys':[],
 };
 const context=vm.createContext({document,console,setTimeout,clearTimeout,setInterval,clearInterval,crypto:{randomUUID:()=> 'test-request-id'},location:{origin:'https://models.example.invalid'},fetch:async path=>{calls.push(path);if(mode==='cloud-preview')assert.ok(!path.startsWith('/v1'),'Cloud views must not access disabled inference routes');if(!(path in responses))throw new Error('Unexpected request '+path);return {ok:true,status:200,json:async()=>responses[path]}},matchMedia:()=>({matches:false}),requestAnimationFrame:()=>{}});
 vm.runInContext(source.replace('void initialize();',''),context);
 vm.runInContext('me='+JSON.stringify(identity)+'; environment={mode:'+JSON.stringify(mode)+'};',context);
 return {context,calls,elements,run:code=>vm.runInContext(code,context)};
}

test('cloud views use catalog data and contain no inference or lifecycle forms',async()=>{
 const ui=setup();
 for(const view of ['overview','assets','play','runtime','connect']){
  const html=await ui.run(view+'()');
  assert.doesNotMatch(html,/id="(?:chat-form|video-form|runtime-form|policy-form|qwen-probe|copy-base)"/);
  if(view!=='runtime')assert.match(html,/sample-planned-model/);
  assert.doesNotMatch(html,/<script>/);
 }
 assert.ok(ui.calls.every(path=>path==='/api/model-catalog'));
});

test('cloud pilot member selects registered alias and never receives video or control forms',async()=>{
 const ui=setup('cloud-api','caller');const html=await ui.run('play()');
 assert.match(html,/id="chat-model"/);assert.match(html,/value="sample-planned-model"/);
 assert.match(html,/id="chat-form"/);assert.doesNotMatch(html,/id="(?:video-form|runtime-form|policy-form)"/);
 assert.doesNotMatch(html,/Qwen3-32B/);assert.doesNotMatch(html,/<script>/);
});

test('cloud pilot admin has probe and management views but no inference form',async()=>{
 const ui=setup('cloud-api');const html=await ui.run('play()');
 assert.doesNotMatch(html,/id="chat-form"/);assert.match(html,/id="qwen-probe"/);
 assert.doesNotMatch(await ui.run('runtime()'),/id="(?:runtime-form|policy-form)"/);
 assert.match(await ui.run('overview()'),/sample-planned-model/);
});

test('membership and key forms use the registered models and only two new roles',async()=>{
 const ui=setup();const html=await ui.run('users()');
 for(const prefix of ['user','key','grant'])assert.match(html,new RegExp('id="'+prefix+'-model-0" data-model="sample-planned-model"'));
 assert.match(html,/任意登记模型 &lt;script&gt;/);
 const roles=html.match(/<select id="user-role">(.*?)<\/select>/s)[1];
 assert.match(roles,/value="admin"/);assert.match(roles,/value="caller"/);
 assert.doesNotMatch(roles,/value="(?:operator|auditor)"/);
 assert.doesNotMatch(html,/id="(?:user-qwen|user-h3|key-qwen|key-h3|grant-qwen|grant-h3)"/);
 assert.match(html,/id="user-unlimited" checked/);
 assert.match(html,/id="key-unlimited" checked/);
 assert.match(html,/data-model="sample-planned-model" value="sample-planned-model" checked/);
});

test('cloud login explicitly labels preview and never claims simulated generation',async()=>{
 const ui=setup();await ui.run('initialize()');
 const html=ui.elements.get('#main').innerHTML;
 assert.match(html,/云端受限预发布/);assert.match(html,/模型未连接，推理和启停关闭/);
 assert.doesNotMatch(html,/默认模拟文本与运行控制/);
 assert.equal(ui.elements.get('#environment-name').textContent,'云端预发布');
});
