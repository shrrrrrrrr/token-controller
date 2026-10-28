'use strict';
import {createAccountsUI} from './accounts.js';
import {createMessagesUI} from './messages.js';
import {createConsoleUI} from './console.js';
import {createJobsUI} from './jobs.js';
import {createAppearanceUI} from './appearance.js';
let jobsPanel;
const jobsUI=()=>jobsPanel||(jobsPanel=createJobsUI({api,esc,num,date,panel,table,top,action,toast,navigate,isAdmin:()=>me?.user.role==='admin',canMonitor:()=>me?.account?.status!=='pending'}));
let appearancePanel;
const appearanceUI=()=>appearancePanel||(appearancePanel=createAppearanceUI({api,esc,top,toast,action,render}));
let consolePanel;
const consoleUI=()=>consolePanel||(consolePanel=createConsoleUI({api,$,esc,num,date,panel,table,top,action,toast,navigate,render,vendorIcon,canMonitor:()=>me?.account?.status!=='pending',getPage:()=>page,getCallKey:()=>callKey,setCallKey:value=>{callKey=value},isAdmin:()=>me?.user.role==='admin'}));
let key = '', callKey = '', csrfToken = '', authGeneration = 0, me = null, page = 'overview', toastTimer;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = x => esc(JSON.stringify(x,null,2));
const num = x => Number(x || 0).toLocaleString('zh-CN');
const date = t => t ? new Date(t*1000).toLocaleString('zh-CN',{hour12:false}) : '未采集';

const pageLabels = {appearance:'背景设置',invitations:'邀请码',overview:'仪表台',quota:'我的额度',assets:'模型资产',runtime:'模型管理',runtimelegacy:'历史配置与演练',play:'模型中心',tasks:'任务与调度',tasklegacy:'历史调用与视频',queuelegacy:'历史预约与排程',tools:'项目工具',users:'成员与额度',usage:'用量记录',connect:'接入指南',guide:'使用引导',queue:'模型申请',notifications:'通知中心',announcements:'公告管理',account:'我的账户',mykeys:'API 配置',registrations:'注册审批',keyaudit:'API 安全审计'};
let renderSequence=0;
const icons = {
 queue:'<path d="M4 5h16M4 12h12M4 19h8M18 16l3 3-3 3M21 19h-7"/>',
 notifications:'<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5l-2 3Zm5 3h4M12 2v2"/>',
 guide:'<path d="M4 4h6l2 2 2-2h6v16h-6l-2 2-2-2H4V4Zm8 2v16M7 9h2M15 9h2M7 13h2M15 13h2"/>',
 overview:'<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
 assets:'<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
 runtime:'<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><path d="M7 7h.01M7 17h.01M16 7h2M16 17h2"/>',
 play:'<path d="m5 5 14 7-14 7V5Z"/>',
 tools:'<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM17 13v8M13 17h8"/>',
 users:'<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5"/>',
 usage:'<path d="M4 3v18h17M8 16v-4M13 16V7M18 16v-7"/>',
 connect:'<path d="m9 15 6-6M8 7l2-2a5 5 0 0 1 7 7l-2 2M16 17l-2 2a5 5 0 0 1-7-7l2-2"/>'
};
Object.assign(icons,{quota:'<path d="M3 6h18v14H3zM3 6V4h16M16 12h5v5h-5z"/>',mykeys:'<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-4-4 3-3m-6 0 3-3"/>',tasks:'<path d="M5 3h14v18H5zM8 7h8M8 11h8M8 15h5"/>'});
const icon = id => `<svg class="nav-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter">${icons[id]||icons.overview}</svg>`;
function vendorIcon(model){const candidate=model.vendor_logo||'';const source=/^\/model-logos\/[a-z]+\.svg$/.test(candidate)?candidate:'/model-logos/generic.svg';return `<span class="model-vendor-icon" title="${esc(model.vendor_name||'模型厂商')}"><img src="${esc(source)}" alt="${esc(model.vendor_name||'模型厂商')}" width="32" height="32" loading="lazy"></span>`}
const pixelRack = () => `<div class="pixel-rack" aria-hidden="true"><div class="rack-unit"><i></i><b></b></div><div class="rack-unit"><i></i><b></b></div><div class="rack-unit"><i></i><b></b></div></div>`;
function closeMenu(){document.body.classList.remove('menu-open');$('#menu-toggle').setAttribute('aria-expanded','false');$('#menu-toggle').setAttribute('aria-label','展开导航')}
async function navigate(id){page=id;closeMenu();await render();$('#main').focus({preventScroll:true})}

let environment={mode:'unknown'};
const cloudPreview=()=>me?.mode==='cloud-preview'||environment.mode==='cloud-preview';
const cloudApi=()=>me?.mode==='cloud-api'||environment.mode==='cloud-api';
const passwordAuth=()=>environment.web_accounts===true||me?.auth_mode==='password_session';
const textSourceLabel=()=>cloudApi()?'云端固定模型通道':cloudPreview()?'未接入模型':me?.text_source==='simulation'?'本地模拟':me?.text_source==='unavailable'?'不可用':'已配置上游，需健康核验';
const deploymentNames={simulation:'暂未运行',planned:'暂未运行',offline:'暂未运行',ready:'正在运行',unconfigured:'暂未运行',unavailable:'暂未运行',unknown:'暂未运行',disabled:'暂未运行'};
const roleName = {admin:'平台管理员',operator:'运行操作员',caller:'普通成员',auditor:'只读审计员'};
const stateName = {ready:'就绪',stopped:'已停止',unknown:'待核实',draining:'等待排空',queued:'排队中',running:'运行中',completed:'已完成',succeeded:'已结算',failed:'失败',cancelled:'已取消',reconciliation:'待对账',restored:'已恢复',reserved:'已预留',dispatched:'已派发'};
const errors = {queue_wait_timeout:'排队等待超时，尚未派发的请求已取消；请查看任务状态',task_has_pending_calls:'任务仍有请求在执行或等待，请先查看任务状态',task_queue_full:'任务队列已满，请稍后再提交',task_lease_expired:'任务租约已到期，请重新创建任务',member_calling_key_required:'请使用普通成员调用 Key；管理员凭证仅管理平台',gateway_capacity_or_reconciliation_limit:'模型正在处理请求，或有未知请求等待对账；请稍后查看用量记录',upstream_credential_not_configured:'上游凭证尚未登记，请联系管理员',upstream_configuration_inflight:'存在在途或待对账请求，暂不能更新上游凭证',upstream_configuration_busy:'上游配置正在更新，请稍后重试',upstream_request_failed:'上游未返回成功结果；已提交的请求需要核对状态',upstream_outcome_unknown:'上游结果尚未确认，请保留请求 ID 并联系管理员对账',upstream_outcome_unknown_reconciliation:'请求结果未知，请保留原 ID，不要重复提交',conservative_context_limit:'输入与最大输出的保守上界超过当前上下文限制，请缩短内容或输出',streaming_not_supported_in_mvp:'当前上游尚未启用流式输出',protocol_not_supported_in_cloud_pilot:'当前版本尚未支持这类模型协议',cloud_runtime_control_disabled:'云端模型运行控制尚未开放',cloud_preview_execution_disabled:'云端受限预发布尚未接入模型，推理与运行操作当前关闭',role_not_enabled:'当前身份尚未启用，请联系管理员核对账户',model_not_registered:'该模型尚未登记，请刷新目录后重新选择',model_catalog_unavailable:'模型目录暂不可用，请联系管理员检查服务端配置',https_required:'请通过管理员提供的 HTTPS 地址访问平台',cloud_preview_inference_disabled:'云端预发布尚未接入模型，推理接口当前关闭',cloud_preview_runtime_disabled:'云端预发布未开放模型启停',cloud_preview_role_disabled:'此身份未在云端启用，请联系管理员',reservation_window_conflict:'批准的时间窗与其他预约重叠，请调整开始时间',reservation_version_conflict:'记录已被修改，请刷新后重新审核；本次输入未提交',reservation_schedule_required:'批准预约时必须安排开始时间',reservation_transition_forbidden:'预约已结束，或当前状态不允许此变更',reservation_limit_reached:'每账户最多3个未结束预约',reservation_daily_limit:'已达到每天20次申请的上限',reservation_queue_full:'预约队列已满，请稍后再试',client_runtime_ack_required:'请先确认客户端实际执行时的后台在线要求',reservation_time_out_of_range:'开始时间需在当前至未来90天内',role_forbidden:'当前身份没有此操作权限',model_forbidden:'此 Key 未获得模型调用授权',insufficient_quota:'可用额度不足，请联系管理员',rate_or_concurrency_limit:'已达到速率或并发限制',model_not_ready_or_draining:'模型尚未就绪，或正在等待在途任务结束',protected_always_on_service:'常驻 Qwen 受保护，拒绝停止或切换',invalid_or_expired_key:'Key 无效、已到期或已吊销',qwen_not_configured_demo_mode:'当前是模拟文本模式，未配置真实 Qwen',qwen_probe_required_or_stale:'请先运行只读 Qwen 健康探测（5分钟有效）',simulation_control_unreachable:'本地模拟控制代理未连接，请检查启动终端',request_reconciliation:'此请求正在等待对账，请勿换 ID 重复提交',only_queued_task_can_be_cancelled:'只能取消尚未派发的队列任务',idempotency_conflict:'此请求 ID 已用于不同参数，请使用新的 ID',use_switch_for_running_service:'已有另一个配置运行，请选择“安全切换”',resource_operation_locked:'另一个运行变更正在进行，请稍后刷新'};
function toast(text,error=false){$('#toast').textContent=text;$('#toast').className=error?'error':'';$('#toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').style.display='none',6500)}
Object.assign(errors,{context_length_exceeded:'输入与最大输出超过模型容量，请减少最大输出或拆分任务',context_budget_unavailable:'暂时无法检查模型输入长度，本次未提交生成',request_too_large:'请求内容超过大小限制，请拆分任务'});
async function api(path,method='GET',body,extra={}){
 const generation=authGeneration;
 const credential=passwordAuth()?(path.startsWith('/v1/')?callKey:''):key;
 const headers={...(credential?{Authorization:'Bearer '+credential}:{}),...(body?{'Content-Type':'application/json'}:{}),...extra};
 if(passwordAuth()&&!['GET','HEAD','OPTIONS'].includes(method))headers['X-CSRF-Token']=csrfToken;
 const response=await fetch(path,{method,credentials:'same-origin',headers,body:body?JSON.stringify(body):undefined});
 let data;try{data=await response.json()}catch{throw new Error(response.status===503?'服务尚未就绪，请稍后刷新':response.status===429?'访问过于频繁，请稍后再试':'服务器返回了非预期响应，请检查连接')}
 if(generation!==authGeneration){const error=new Error('登录身份已变化，旧请求结果不会显示，请核对原请求记录');error.code='session_changed';throw error}
 if(!response.ok){const error=new Error(errors[data.error?.code]||data.error?.code||'请求失败');error.code=data.error?.code;throw error}return data;
}
Object.assign(errors,{invalid_invitation_code:'邀请码无效、已过期或已使用，请联系管理员',invitation_limit:'有效邀请码已达到上限，请稍后生成',appearance_changed:'背景设置已被其他管理员更新，请用浏览器刷新后重选',background_not_registered:'背景未登记，请重新选择',background_gallery_unavailable:'背景图片暂不可用，请联系管理员',password_verification_expired:'验证已过期，请重新验证原密码',resident_service_protected:'常驻模型需要明确的停止指令',runtime_agent_not_connected:'服务器控制代理尚未连接',runtime_agent_unreachable:'服务器控制连接中断',runtime_agent_refused:'服务器拒绝了操作，请检查运行状态',model_request_not_launched:'该功能暂未上线',invalid_login:'用户名或密码不正确',password_length:'密码需6–128个字符',password_too_common:'密码过于常见，请换一个长口令',invalid_username:'用户名需3–40位英文、数字、点、横线或下划线',username_unavailable:'这个用户名已被使用',registration_pending:'账号等待管理员审批',registration_not_pending:'该申请已被处理，请刷新',registration_approval_required:'请在注册审批页处理该账号',session_required:'请先使用账号密码登录',session_expired:'登录已过期，请重新登录',account_disabled:'账号已停用，请联系管理员',csrf_required:'登录状态已变化，请刷新后重试',authentication_rate_limit:'登录尝试较多，请稍后再试',authentication_busy:'认证服务正忙，请稍后再试',invalid_account_token:'一次性码无效、过期或已使用',account_token_not_allowed:'该成员状态不适合所选认领或恢复流程',personal_key_limit:'最多保留10个未过期有效 Key，请先吊销不用的 Key',personal_key_daily_limit:'已达到每天新建20个 Key 的上限',key_models_exceed_user_grant:'Key 模型范围不能超过账户授权',key_not_found:'未找到你拥有的有效 Key',use_personal_key_management:'调用 Key 由成员在我的 API 中自行创建'});
const badge = state => `<span class="badge ${['ready','completed','succeeded','restored'].includes(state)?'good':['unknown','draining','reconciliation','queued'].includes(state)?'warn':state==='failed'?'bad':'neutral'}">${esc(stateName[state]||state)}</span>`;
const top = (title,sub,tag='WORKSPACE') => `<div class="page-top"><div><div class="eyebrow">${tag}</div><h1>${title}</h1></div></div>`;
const notice = text => `<div class="notice"><span class="notice-symbol">ⓘ</span><div>${text}</div></div>`;
function table(head,rows){return `<div class="table-wrap"><table><thead><tr>${head.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(x=>`<tr>${x.map(y=>`<td>${y}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${head.length}"><div class="empty">暂无记录 · 执行一次授权操作后在这里查看</div></td></tr>`}</tbody></table></div>`}
function panel(title,body,sub=''){return `<section class="panel"><div class="panel-head"><h2>${title}</h2><small>${sub}</small></div>${body}</section>`}
const field = (label,name,type='text',value='',extra='') => `<div class="field"><label for="${name}">${label}</label><input id="${name}" name="${name}" type="${type}" value="${esc(value)}" ${extra}></div>`;
const navGroupsOpen=new Set();
function nav(){
 $('#workspace-home').onclick=()=>navigate('overview');
 const ids=guideInfo?.allowed_pages||['play','tools','usage','connect','guide'];
 const groups=guideInfo?.navigation||[{id:'workspace',title:'工作空间',pages:ids.filter(id=>id!=='notifications')}];
 const item=(id,title=pageLabels[id],active=page===id)=>`<button class="nav-item ${active?'active':''}" data-page="${id}" ${active?'aria-current="page"':''}>${icon(id)}<span>${esc(title)}</span>${active?'<span class="nav-arrow" aria-hidden="true">↗</span>':''}</button>`;
 $('#nav').innerHTML=groups.map(group=>me.user.role==='caller'?item(group.pages[0],group.title,group.pages.includes(page)):group.pages.length===1?item(group.pages[0],group.title):`<details class="nav-group" data-nav-group="${esc(group.id)}" ${group.pages.includes(page)?'open':''}><summary>${icon(group.pages[0])}<span>${esc(group.title)}</span></summary><div class="nav-children">${group.pages.map(id=>item(id)).join('')}</div></details>`).join('');
 $('#nav').querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page)));
 $('#nav').querySelectorAll('[data-nav-group]').forEach(group=>group.addEventListener('toggle',()=>{if(group.open)navGroupsOpen.add(group.dataset.navGroup);else navGroupsOpen.delete(group.dataset.navGroup)}));
 $('#environment-detail').innerHTML=cloudApi()?'云端受限文本调用<br>运行控制与视频关闭':cloudPreview()?'云端受限预发布<br>模型未接入，推理与启停已关闭':me.text_source==='simulation'?'文本与运行控制均为模拟<br>真实模型启停已关闭':'文本上游已配置<br>运行控制仍为模拟';
 $('#environment-name').textContent=cloudApi()?'云端文本试运行':cloudPreview()?'云端预发布':'本地开发环境';
 $('#environment-badge').textContent=cloudApi()?'受限文本 API':cloudPreview()?'受限预发布':'本地 MVP';
 $('#workspace-edition').textContent=cloudApi()?'TEXT PILOT':cloudPreview()?'PREVIEW':'LOCAL';
 $('#release-label').textContent=(cloudApi()?'CLOUD TEXT PILOT / ':cloudPreview()?'CLOUD PREVIEW / ':'LOCAL EDITION / ')+guideInfo.version;
 $('#open-guide').innerHTML='使用引导'+(guideInfo.needs_attention?'<span class="guide-new">新</span>':'');
 $('#current-page').textContent=pageLabels[page]||'工作台';$('#identity').onclick=()=>passwordAuth()?accounts().menu():navigate('account');$('#identity').setAttribute('aria-label',me?`我的账号 · ${me.user.name}`:'我的账号');
}

async function action(button,fn){button.disabled=true;try{await fn()}catch(e){toast(e.message,true)}finally{button.disabled=false}}
function login(){if(passwordAuth())return accounts().login();messages().reset();consolePanel?.reset();jobsPanel?.reset();stopCommunityMonitor();renderSequence++;workspaceObserver?.disconnect();guideInfo=null;me=null;key='';delete $('#main').dataset.page;document.body.classList.add('signed-out');closeMenu();$('#identity').setAttribute('aria-label','我的账号');$('#current-page').textContent='欢迎';$('#nav').innerHTML='';$('#main').innerHTML=`<div class="login"><section class="login-form-side"><div class="eyebrow">WELCOME TO YOUR WORKSPACE</div><h2>进入工作空间</h2><p class="subtitle">${cloudApi()?'使用管理员分配的独立 Key；管理员与成员权限分开':cloudPreview()?'受限验收：使用管理员分配的网页访问 Key':'使用管理员分配的 API Key 访问'}</p><form id="login-form"><div class="field"><label for="login-key">API Key</label><input id="login-key" type="password" placeholder="输入你的独立 Key" autocomplete="off" spellcheck="false" required><span class="field-help">仅保存在当前页面内存中，刷新后需重新登录</span></div><button class="primary" type="submit">进入工作台 <span aria-hidden="true">→</span></button><p id="login-error" class="field-help" role="alert"></p></form><details><summary>首次使用与凭证管理</summary><p>${cloudPreview()||cloudApi()?'请向项目管理员领取访问 Key。管理员在云服务器独立服务目录初始化账户；当前启动的是兼容 Key 登录模式；完成账户迁移后可启用独立账密会话，管理员 Key 不能填入外部客户端。':'管理员在服务所在机器的独立环境执行 python -m gateway bootstrap；Key 遗失时执行 recover-admin。完整步骤见项目 README。'}</p></details><p class="login-footnote">${cloudApi()?'云端受限文本试运行 · 管理员凭证只管理平台':cloudPreview()?'云端受限预发布 · 模型未连接，推理和启停关闭':environment.mode==='unknown'?'服务环境尚未确认，请检查连接':'本地演示 · 默认模拟文本与运行控制'}</p><div class="lab-wordmark login-lab-footer"><img src="/site-assets/lab-logo.png" alt="虚拟现实技术与系统全国重点实验室" width="2169" height="725"></div></section></div>`;$('#login-form').addEventListener('submit',e=>{e.preventDefault();action(e.submitter,async()=>{key=$('#login-key').value.trim();try{me=await api('/api/me');document.body.classList.remove('signed-out');guideInfo=await api('/api/guide');guideStep=0;page='overview';await render();await messages().loginPrompts(guideInfo)}catch(error){key='';$('#login-error').textContent=error.message;throw error}})})}
async function overview(){if(cloudApi())return cloudApiOverview();if(cloudPreview())return cloudOverview();const [inv,usage]=await Promise.all([api('/api/inventory'),api('/api/usage')]);const simulation=me.text_source==='simulation';const pending=usage.requests.filter(x=>x.status==='reconciliation').length;return top('模型工作台','查看共享资源，开始一次调用，或进入你的项目工具','OVERVIEW')+`<section class="hero" aria-labelledby="hero-title"><div><div class="eyebrow">ONE SPACE, SHARED POSSIBILITIES</div><h2 id="hero-title">把算力连接起来，把想法继续下去</h2><p>模型调用、访问权限与项目工具，在同一个工作空间里协作</p><div class="buttons"><button class="primary" data-go="play">发起一次调用 <span aria-hidden="true">→</span></button><button class="secondary" data-go="connect">查看接入指南</button></div></div>${pixelRack()}</section><div class="metrics"><div class="metric"><label>已登记模型权重</label><div class="value">${num(inv.assets.length)}<span>项</span></div><small>登记数量不代表在线服务数量</small></div><div class="metric"><label>当前文本来源</label><div class="value">${simulation?'模拟':'已配置'}<span>${simulation?'本地演示':'需健康核验'}</span></div><small>${simulation?'未调用真实模型':'已配置上游，健康状态另行核验'}</small></div><div class="metric"><label>我的可用额度</label><div class="value">${me.user.budget<0?'不限':num(me.user.budget-me.user.spent-me.user.reserved)}<span>${me.user.budget<0?'累计用量':'点'}</span></div><small>预留 ${num(me.user.reserved)} 点 · 非电费</small></div><div class="metric"><label>最近请求待对账</label><div class="value">${num(pending)}<span>项</span></div><small>最近 50 条 · 保留记录等待核验</small></div></div>`+notice('当前资产来自'+(inv.source==='synthetic_example_only'?'合成示例':'登记快照')+'，不代表服务器实时状态。运行管理为模拟演练，不会启停真实模型')+panel('模型与服务',table(['实例','登记状态','API 健康','数据说明'],inv.instances.map(x=>[`<strong class="mono">${esc(x.name)}</strong>`,esc(x.reported_state),badge('unknown'),'<span class="muted">登记快照 · 非实时</span>'])),'MODEL INVENTORY')+`<div class="two-col">${panel('GPU 资源概况',`<div class="gpu-grid">${inv.gpus.map(g=>`<div class="gpu"><div class="gpu-top"><strong>GPU ${esc(g.index)}</strong>${badge('unknown')}</div><div class="memory">规格与显存待采集</div><small>未取得当前服务器监控数据</small><div class="unknown-bar" aria-hidden="true"></div></div>`).join('')}</div>`,'OBSERVATION PENDING')}${panel('从这里开始',`<div class="panel-body"><div class="step"><span>01</span><div>调用模型<small>使用已授权模型，验证回复与用量记录</small></div></div><div class="step"><span>02</span><div>连接你的客户端<small>查看地址、模型 ID 与当前协议边界</small></div></div><div class="step"><span>03</span><div>进入项目工具<small>法律等已有应用，保留各自的身份与数据边界</small></div></div><div class="buttons spacer"><button class="secondary" data-go="tools">打开项目工具 ↗</button></div></div>`,'QUICK START')}</div>`}
async function assets(){if(cloudApi())return cloudAssets();if(cloudPreview())return cloudAssets();const [inv,ev,catalog]=await Promise.all([api('/api/inventory'),api('/api/evidence'),api('/api/model-catalog')]);return top('资产与证据','权重、镜像、启动配置与容器分别登记；仅展示合成示例或本地私有快照，未知值明确标注。','ASSETS / EVIDENCE')+notice(`<strong>公开示例核验：</strong>${ev.checks.filter(x=>x.match).length} / ${ev.checks.length} 个公开合成配置 hash 与基线相符。真实资产文件请放在本地忽略目录中。`)+panel('模型目录',catalogTable(catalog.models))+panel('模型权重',table(['资产','宿主路径','固定版本 / revision','许可'],inv.assets.map(x=>[esc(x.id),`<span class="mono">${esc(x.path)}</span>`,`<span class="mono">${esc(x.revision||'未记录固定revision')}</span>`,esc(x.license)])))+panel('容器实例 · 包含已停止项',table(['容器','报告状态','入口','采集状态'],inv.instances.map(x=>[`<span class="mono">${esc(x.name)}</span>`,esc(x.reported_state),`<span class="mono">${esc(x.reported_ports)}</span>`,badge('unknown')])),'SYNTHETIC FIXTURE')+panel('启动配置与固定指纹',table(['Profile','GPU / 入口','引擎','配置 hash / 镜像'],inv.profiles.map(p=>[`<strong class="mono">${esc(p.id)}</strong><small>真实操作待审批；模拟器使用固定白名单</small>`,`GPU ${p.gpus.join(' / ')}<small>端口 ${p.port}</small>`,esc(p.engine),`<span class="mono">${esc(p.hash)}</span><small class="mono">${esc(p.image)}</small>`])))+panel('仍需核实',`<div class="panel-body">${inv.unknowns.map(x=>`<div class="step"><span>○</span>${esc(x)}</div>`).join('')}</div>`)+`<details><summary>查看合成示例 hash 明细</summary><pre>${pretty(ev)}</pre></details>`}
async function runtime(){if(cloudApi())return cloudApiRuntime();if(cloudPreview())return cloudRuntime();const r=await api('/api/runtime');return top('运行管理','策略与实际状态分开。操作只发送到本机模拟控制代理，真实启停始终关闭。','RUNTIME / SIMULATION')+notice('<strong>模拟演练：</strong>Qwen 常驻保护；H3 单卡与双卡配置共用 GPU 2 和 30010 端口。切换先排空在途任务，失败恢复原路由。')+panel('模拟运行状态',table(['模型 / alias','当前 profile','观察状态','期望状态 / 策略'],r.routes.map(x=>[esc(x.model),`<span class="mono">sim-${esc(x.profile)}</span>`,badge(x.draining?'draining':r.instances.find(i=>i.profile===x.profile)?.status),`${esc(x.desired)}<small>${esc(x.policy)} · 自动调度关闭</small>`])))+`<div class="two-col equal">${panel('配置操作演练',`<form id="runtime-form" class="panel-body"><div class="field"><label for="runtime-profile">预置配置</label><select id="runtime-profile"><option value="h3-sp2-v1">H3 · 双卡 SP2 + AdaLN · GPU 2/3</option><option value="h3-single-v1">H3 · 单卡省显存 · GPU 2</option><option value="qwen-tp2-v1">Qwen · 常驻保护（操作会拒绝）</option></select></div><div class="field"><label for="runtime-action">动作</label><select id="runtime-action"><option value="start">启动模拟实例</option><option value="switch">安全切换（等待在途任务）</option><option value="stop">停止模拟实例</option></select></div><button class="primary" type="submit">提交模拟操作 →</button><p class="field-help spacer">没有 GPU/镜像/任意命令输入框。操作员只能选择被授权的 profile。</p></form>`)}${panel('生命周期策略',`<form id="policy-form" class="panel-body"><div class="field"><label for="policy-value">H3 期望策略</label><select id="policy-value"><option value="manual">人工管理 manual</option><option value="on_demand">按需 on_demand</option><option value="always_on">常驻保护 always_on</option><option value="idle_stop">允许空闲停止 idle_stop</option></select></div><p class="small muted">此处保存策略意图。MVP 不自动冷启动或空闲停止；需要操作员发起模拟动作。真实 GPU 策略不受影响。</p><button class="secondary spacer" type="submit">保存策略意图</button></form>`)}</div>`+panel('操作记录',table(['操作 ID','动作 / 目标','结果','说明'],r.operations.map(o=>[`<span class="mono">${esc(o.id)}</span><small>${date(o.created)}</small>`,`${esc(o.action)}<small class="mono">${esc(o.target)}</small>`,badge(o.status),esc(o.detail)])),'SIMULATED OPERATIONS')}
async function play(){if(cloudApi())return cloudApiPlay();if(cloudPreview())return cloudPlay();const [models,tasks]=await Promise.all([passwordAuth()&&!callKey?Promise.resolve({data:[]}):api('/v1/models'),api('/api/tasks')]);const canQwen=models.data.some(m=>m.id==='qwen3-32b');const canH3=models.data.some(m=>m.id==='h3');return top('调用与任务','调用时选择获授权模型；每次请求绑定版本、用量与归属。','PLAYGROUND / OWN TASKS')+notice(`<strong>文本来源：</strong>${me.text_source==='simulation'?'本地模拟响应，未调用 Qwen。':'真实 Qwen 适配已配置；先执行健康探测，再提交。'} <strong>视频：</strong>本地模拟队列，下载内容为 JSON，无 MP4 成片。`)+`<div class="two-col equal">${panel('文本调用 · Qwen3-32B',`<form id="chat-form" class="panel-body">${callingKeyField()}<div class="field"><label for="chat-prompt">输入内容</label><textarea id="chat-prompt" required placeholder="输入合成测试问题，验证调用和额度闭环"></textarea></div><div class="form-grid">${field('最大输出 token','max-tokens','number',256,'min="16" max="4096"')}${field('幂等请求 ID','chat-id','text',crypto.randomUUID(),'required maxlength="120"')}</div><div class="buttons"><button class="primary" ${canQwen?'':'disabled'} type="submit">发送请求 →</button><button class="secondary" id="qwen-probe" type="button">只读 Qwen 探测</button><button class="secondary" id="new-chat-id" type="button">新请求 ID</button></div><p class="field-help spacer">重试同一次请求请保留 ID；更换问题或参数前先生成新 ID。</p><div id="chat-result" class="result" aria-live="polite">等待发送${canQwen?'':' · 此Key没有文本模型权限'}</div></form>`)}${panel('视频任务 · MiniMax H3',`<form id="video-form" class="panel-body"><div class="field"><label for="video-prompt">模拟任务描述</label><textarea id="video-prompt" required placeholder="例如：合成测试任务，不含案件资料"></textarea></div><div class="form-grid">${field('请求时长 / 秒','duration','number',5,'min="1" max="10"')}${field('推理步数','steps','number',50,'min="2" max="50"')}</div><div class="field"><label for="video-id">幂等任务 ID</label><input id="video-id" value="${crypto.randomUUID()}" required maxlength="120"></div><button class="primary" ${canH3?'':'disabled'} type="submit">加入模拟队列 →</button><p class="field-help spacer">需先在运行管理中启动 H3 模拟实例。每用户最多 3 项，100 点/请求秒。${canH3?'':'当前Key没有H3权限。'}</p></form>`)}</div>`+panel('我的视频任务',table(['任务 ID','状态','来源','创建时间','操作'],tasks.map(t=>[`<span class="mono">${esc(t.id)}</span>`,badge(t.status),'SIMULATION',date(t.created),t.status==='queued'?`<button class="secondary" data-cancel="${esc(t.id)}">取消排队</button>`:t.status==='completed'?`<button class="secondary" data-download="${esc(t.id)}">下载模拟结果</button>`:'—'])),'OWNERSHIP CHECKED BY BACKEND')}
function catalogTable(models){return table(['模型 / API ID','类型','部署状态','当前权限'],models.map(model=>[`<span class="model-title">${vendorIcon(model)}<strong>${esc(model.display_name)}</strong></span><small class="mono">${esc(model.id)}</small><small>${esc(model.description)}</small>`,esc(model.kind),esc(model.status_label||deploymentNames[model.status||model.deployment_status]||'暂未运行'),`${model.authorized?'账户可调用':'账户未开放'}<small>${cloudPreview()?'推理未开放':model.callable?'可进入调用验证':'暂不可调用'}</small>`]))}
function modelChecks(prefix,models,selected=[]){return `<div class="checkline" id="${prefix}-models">${models.map((model,i)=>`<label for="${prefix}-model-${i}"><input type="checkbox" id="${prefix}-model-${i}" data-model="${esc(model.id)}" value="${esc(model.id)}" ${selected.includes(model.id)?'checked':''}>${esc(model.display_name)} <small>${esc(model.status_label||deploymentNames[model.status||model.deployment_status]||'暂未运行')}</small></label>`).join('')||'<p class="field-help">尚无登记模型。管理员需先配置服务端模型目录。</p>'}</div>`}
const selectedModels=prefix=>[...document.querySelectorAll('#'+prefix+'-models input[data-model]:checked')].map(input=>input.value);

async function cloudApiOverview(){
 const catalog=await api('/api/model-catalog');
 return top('模型工作台','受限文本 API 已接入；模型可用性按接口健康检查展示','CLOUD / TEXT PILOT')+
 notice(passwordAuth()?'管理员账号管理平台；成员自行创建调用 Key。当前只支持非流式文本，未知提交等待对账，不自动重试。':'管理员 Key 用于管理平台，调用模型请使用普通成员 Key。当前只支持非流式文本；未知提交等待对账，不自动重试。')+
 panel('模型目录',catalogTable(catalog.models))+
 panel('使用顺序',`<div class="panel-body"><p>${passwordAuth()?'成员注册 → 管理员审批与授权 → 成员自行创建 Key → 验证调用并核对 token':'创建普通成员并分配模型和额度 → 发放独立 Key → 用成员 Key 验证调用 → 核对真实 token 与账本。'}</p><p>同时只派发一个文本请求。忙碌或存在待对账请求时返回429，预约登记不等于推理执行队列。</p><div class="buttons"><button class="primary" data-go="users">管理成员</button><button class="secondary" data-go="connect">接入说明</button></div></div>`);
}
async function cloudApiRuntime(){
 return top('运行管理','文本通道与模型启停分开管理','RUNTIME / DISABLED')+
 notice('模型 API 可用性由只读探测核验；本版没有启停、自动切换、视频执行或服务端 harness。')+
 panel('运行控制边界','<div class="panel-body"><p>网页进程不持有 Docker socket。后续真实运行管理仍需批准的 Profile、资源互斥、在途任务与常驻保护检查。</p></div>');
}
async function cloudApiPlay(){
 let probeWarning='';
 if(me.user.role==='caller'&&!passwordAuth()){try{await api('/v1/models')}catch(error){probeWarning=error.message}}
 const catalog=await api('/api/model-catalog');
 const models=catalog.models.filter(model=>model.kind==='text'&&model.authorized&&model.callable);
 const head=top('文本调用验证','使用普通成员授权，先预留额度，再按真实 usage 结算','CLOUD / TEXT PILOT')+
 notice('支持文本流式与函数工具；三个工作流上限，实际推理槽位以任务页为准。修改输入需新请求 ID；结果未知时不要换 ID 重新提交。')+
 (probeWarning?notice(esc(probeWarning)):'')+panel('模型与权限',catalogTable(catalog.models));
 if(me.user.role!=='caller')return head+panel('管理员接入检查','<div class="panel-body"><p>请创建普通成员并发放调用 Key，再以成员身份验证模型。管理员凭证不允许调用推理接口。</p><button class="secondary" id="qwen-probe">只读模型接口探测</button></div>');
 return head+panel('非流式文本请求',`<form id="chat-form" class="panel-body"><div class="field"><label for="chat-model">模型</label><select id="chat-model">${models.map(model=>`<option value="${esc(model.id)}">${esc(model.display_name)}</option>`).join('')}</select></div>${callingKeyField()}<div class="field"><label for="chat-prompt">测试问题</label><textarea id="chat-prompt" required placeholder="先使用简短合成文本验证"></textarea></div><div class="form-grid">${field('最大输出 token','max-tokens','number',128,'min="16" max="4096"')}${field('幂等请求 ID','chat-id','text',crypto.randomUUID(),'required maxlength="120"')}</div><div class="buttons"><button class="primary" type="submit" ${models.length?'':'disabled'}>发送测试请求</button><button class="secondary" id="new-chat-id" type="button">新请求 ID</button></div><div id="chat-result" class="result" aria-live="polite">${models.length?'等待提交':'暂无已通过健康检查且获授的模型，请联系管理员'}</div></form>`);
}

async function cloudOverview(){
 const catalog=await api('/api/model-catalog');
 return top('模型工作台','先完成成员、授权和入口验收，接通模型后再开放推理','CLOUD / PREVIEW')+
 `<section class="hero"><div><div class="eyebrow">YOUR SHARED MODEL SPACE</div><h2>工作空间已准备，模型连接待完成</h2><p>当前可以管理成员与额度、查看登记模型、预约和通知。</p><div class="buttons"><button class="primary" data-go="users">管理成员 →</button><button class="secondary" data-go="connect">查看接入进度</button></div></div>${pixelRack()}</section>`+
 notice('受限预发布：推理接口、模型探测和启停均已关闭。这里不会返回模拟生成结果。目录中的“待部署”只代表部署计划。')+
 panel('模型目录',catalogTable(catalog.models),'REGISTERED MODELS')+
 panel('接通模型的顺序','<div class="panel-body"><div class="step"><span>01</span><div>登记模型<small>管理员维护服务端私有目录；本页名称、模型 ID 和成员授权项由目录自动生成。</small></div></div><div class="step"><span>02</span><div>部署与连接<small>在 GPU 服务器验证模型，再建立受控连接并登记上游路由。</small></div></div><div class="step"><span>03</span><div>验证后开放调用<small>验证鉴权、真实生成、计量和客户端协议后，才允许成员发送推理请求。</small></div></div></div>');
}
async function cloudAssets(){const catalog=await api('/api/model-catalog');return top('模型目录与部署计划','显示名称和 API ID 来自服务端登记，部署状态与调用权限分别管理','MODEL CATALOG')+notice('当前未采集 GPU、容器和权重文件的实时信息。登记计划不表示模型已经下载或运行；本版通过服务器私有配置文件维护目录，尚无网页编辑登记表。')+panel('已登记目录',catalogTable(catalog.models))+panel('权限与运行配置','<div class="panel-body"><p>成员授权只能从已登记模型中选择；有效权限是账户授权与 Key 授权的交集。显示名称可以用于识别，API 调用使用独立的模型 ID。</p><p>真实部署还需要权重版本、可信镜像、批准的 Profile、资源检查和上游路由，单独添加名字不会启动 Docker。</p></div>')}
async function cloudRuntime(){return top('运行管理','模型服务器尚未接入，当前没有运行控制能力','RUNTIME / DISABLED')+notice('推理和启停已在服务端禁用。页面不提供模拟启停、策略写入或真实模型探测。')+panel('等待连接与验收','<div class="panel-body"><p>完成 GPU 服务器部署和安全连接后，再逐项验证模型健康状态、在途任务、GPU 冲突和常驻保护。</p><p>管理员默认获得全部已批准 Profile 的操作资格；这些资源保护仍由后端检查。未批准的配置不能执行。</p></div>')}
async function cloudPlay(){const catalog=await api('/api/model-catalog');return top('模型与调用准备','查看获授权模型；模型接通前不提交推理请求','MODELS / PREVIEW')+notice('本期推理接口关闭，包括模型列表 API。以下为平台登记目录，不能用于判断模型正在运行。不会生成测试回复或扣除推理额度。')+panel('模型目录',catalogTable(catalog.models))+panel('接下来可以做什么','<div class="panel-body"><p>核对模型名称和授权范围；如需使用时间窗，可以先登记预约。预约审批不会自动启动模型或执行任务。</p><div class="buttons spacer"><button class="secondary" data-go="queue">查看模型排队 →</button><button class="secondary" data-go="connect">查看接入说明 →</button></div></div>')}

function quotaControls(prefix){return `<div class="checkline"><label for="${prefix}-unlimited"><input type="checkbox" id="${prefix}-unlimited" checked>不限制累计用量，继续统计 token</label></div><div id="${prefix}-limited" hidden>${field('累计额度上限',''+prefix+'-budget','number',1000000,'min="0" max="1000000000" required disabled')}</div>`}
const selectedBudget=prefix=>$('#'+prefix+'-unlimited').checked?-1:Number($('#'+prefix+'-budget').value);
function quotaSummary(record){return `${num(record.spent)} ${cloudApi()?'token':'记账单位'}<small>${record.budget<0?'不限制累计用量':'累计上限 '+num(record.budget)}</small>`}
async function users(){
 if(passwordAuth())return accounts().members();
 const [members,catalog]=await Promise.all([api('/api/users'),api('/api/model-catalog')]);
 const admin=me.user.role==='admin',keys=admin?await api('/api/keys'):[];
 const preferred=members.find(member=>member.role==='caller')||members[0];
 return top('成员与权限','新成员默认可使用全部已登记模型，累计用量不限；仍统计每次真实调用。','MEMBERS / ACCESS')+
 notice('默认不设置累计点数上限；输入、输出 token 和请求状态仍完整记录。模型授权不等于模型在线，也不会触发模型切换。已有账号与 Key 的额度保持原设置。')+
 panel('用户与角色',table(['用户','角色','模型调用授权','已结算用量 / 累计限制'],members.map(member=>[
  `<strong>${esc(member.name)}</strong><small class="mono">${esc(member.id)}</small>`,esc(roleName[member.role]),esc(JSON.parse(member.models).join(' / ')||'无'),quotaSummary(member)])))+
 (admin?`<div class="two-col equal">${panel('创建用户',`<form id="user-form" class="panel-body">${field('名称','user-name','text','','required maxlength="60"')}<div class="field"><label for="user-role">角色</label><select id="user-role"><option value="caller">普通成员</option><option value="admin">平台管理员</option></select></div>${quotaControls('user')}${modelChecks('user',catalog.models,catalog.models.map(model=>model.id))}<p class="field-help">默认勾选本次登记目录的全部模型，可按需取消。以后新增模型仍需管理员确认授权。</p><button class="primary" type="submit">创建用户</button></form>`)}
 ${panel('发放独立 API Key',`<form id="key-form" class="panel-body"><div class="field"><label for="key-user">归属用户</label><select id="key-user">${members.map(member=>`<option value="${esc(member.id)}" data-models="${esc(member.models)}" ${member.id===preferred?.id?'selected':''}>${esc(member.name)} · ${esc(roleName[member.role])}</option>`).join('')}</select></div>${quotaControls('key')}${modelChecks('key',catalog.models,preferred?JSON.parse(preferred.models):[])}<p class="field-help">默认勾选该用户获授的全部模型，Key 不会扩大账户权限。</p><details class="key-settings"><summary>有效期与请求保护 · 默认即可</summary><div class="form-grid spacer">${field('有效天数','key-days','number',365,'min="1" max="365" required')}${field('请求上限 / 分钟','key-rpm','number',60,'min="1" max="1000" required')}${field('文本预留上限 / 分钟','key-tpm','number',1000000,'min="100" max="10000000" required')}${field('在途请求上限','key-concurrency','number',1,'min="1" max="20" required')}</div><p class="field-help">有效期决定 Key 何时失效；每分钟请求数限制调用频率；文本预留上限按输入和最大输出估算，不是实际 token 消耗；在途上限限制尚未完成的请求。当前云网关全局只接纳一个文本请求，提高单 Key 上限不会增加模型容量。</p></details><button class="primary" type="submit">生成 Key · 仅显示一次</button><div id="key-result" aria-live="polite"></div></form>`)}</div>`+
 grantEditor(members,catalog.models)+panel('已发放的 Key',table(['Key / 归属','模型授权','已结算用量 / 累计限制','请求保护','到期 / 操作'],keys.map(item=>[
  `<span class="mono">${esc(item.prefix)}</span><small>${esc(members.find(member=>member.id===item.user_id)?.name||item.user_id)}</small>`,esc(JSON.parse(item.models).join(' / ')),quotaSummary(item),`${item.rpm} 请求/分 · ${item.concurrency} 在途<small>${num(item.tpm)} 预留上限/分</small>`,`${date(item.expires)}<small>${item.revoked?'已吊销':item.id===me.key.id?'当前登录Key':`<button class="danger" data-revoke="${esc(item.id)}">吊销</button>`}</small>`]))):'');
}
async function memberUsage(){
 const data=await api('/api/usage');
 return top('我的 token 用量','按真实回复的 usage 统计输入和输出，不显示累计点数。','TOKEN USAGE')+
 notice(`累计已结算 ${num(data.totals?data.totals.input_tokens+data.totals.output_tokens:me.user.spent)} token。${me.user.budget<0?'累计用量不限。':'账户仍沿用管理员设置的累计额度。'}下表显示最近50个请求；未知结果保留请求 ID 等待核实，不重复提交。`)+
 panel('调用记录',table(['请求 ID / 时间','模型','状态','输入 token','输出 token','总 token','耗时'],data.requests.map(row=>[
  `<span class="mono">${esc(row.id)}</span><small>${date(row.created)}</small>`,esc(row.model),badge(row.status),row.input_tokens??'—',row.output_tokens??'—',row.input_tokens!=null&&row.output_tokens!=null?row.input_tokens+row.output_tokens:'—',row.elapsed_ms!=null?num(row.elapsed_ms)+' ms':'—'])));
}
async function usage(){if((cloudApi()||passwordAuth())&&me.user.role==='caller')return memberUsage();const data=await api('/api/usage');const manager=['admin','operator','auditor'].includes(me.user.role);const audits=manager?await api('/api/audit'):[];return top('用量与审计','每条预留、结算、释放都绑定唯一请求。未知用量保留额度，等待人工确认。','USAGE / AUDIT')+notice('列表显示最近 50 个请求；可通过 API 的 limit / offset 分页。正文与完整 Key 不出现在这些表格中。'+(me.user.role==='admin'?'待对账请求可在下方凭上游证据处理。':''))+panel('请求记录',table(['请求 ID / 时间','模型 / 类型','状态','输入 / 输出 token','预留 → 实际 / 点','耗时'],data.requests.map(r=>[`<span class="mono">${esc(r.id)}</span><small>${date(r.created)}</small>`,`${esc(r.model)}<small>${esc(r.kind)} · ${esc(r.source)}</small>`,`${badge(r.status)}${r.error?`<small>${esc(r.error)}</small>`:''}`,`${r.input_tokens??'—'} / ${r.output_tokens??'—'}`,`${num(r.reserved)} → ${r.actual??'—'}`,r.elapsed_ms!=null?`${num(r.elapsed_ms)} ms`:'—'])))+(me.user.role==='admin'?panel('人工对账',`<form id="reconcile-form" class="panel-body"><div class="form-grid"><div class="field"><label for="reconcile-id">待对账请求</label><select id="reconcile-id" required>${data.requests.filter(r=>r.status==='reconciliation').map(r=>`<option value="${esc(r.id)}">${esc(r.id)} · ${r.kind}</option>`).join('')}</select></div><div class="field"><label for="reconcile-outcome">上游核验结果</label><select id="reconcile-outcome"><option value="not_executed">确认未执行（退回预留）</option><option value="usage_verified">已核实文本真实 usage</option></select></div>${field('核实输入 token','reconcile-input','number',0,'min="0"')}${field('核实输出 token','reconcile-output','number',0,'min="0"')}</div><div class="field"><label for="reconcile-evidence">核验记录 / 理由（不填密钥或正文）</label><textarea id="reconcile-evidence" required minlength="10" maxlength="500"></textarea></div><button class="secondary" type="submit">按核验结果结算</button></form>`):'')+panel('点数账本',table(['请求','事件','点数','时间'],data.ledger.map(l=>[`<span class="mono">${esc(l.request_id)}</span>`,esc({reserve:'预留',settle:'结算',release:'释放'}[l.event]),num(l.points),date(l.created)])))+(manager?panel('管理审计',table(['时间','动作','目标','变更详情'],audits.map(a=>[date(a.created),`<span class="mono">${esc(a.action)}</span><small class="mono">${esc(a.actor)}</small>`,`<span class="mono">${esc(a.target)}</span>`,`<span class="mono">${esc(a.detail)}</span>`]))):'')}
function bind(){const forms={
 'runtime-form':async()=>{const r=await api('/api/runtime/operations','POST',{action:$('#runtime-action').value,profile:$('#runtime-profile').value});toast(`模拟操作已登记：${r.status}。稍后刷新查看结果。`);await render()},
 'policy-form':async()=>{await api('/api/runtime/policy','PATCH',{model:'h3',policy:$('#policy-value').value});toast('策略意图已保存；自动启停仍关闭');await render()},
 'chat-form':async()=>{if(passwordAuth()){callKey=$('#play-key')?.value.trim()||callKey;if(!callKey)throw new Error('请先在我的 API 创建 Key，再填写调用 Key')}const r=await api('/v1/chat/completions','POST',{model:$('#chat-model')?.value||'qwen3-32b',messages:[{role:'user',content:$('#chat-prompt').value}],max_tokens:Number($('#max-tokens').value)},{'Idempotency-Key':$('#chat-id').value});$('#chat-result').textContent=r.choices[0].message.content+`\n\n输入 ${r.usage.prompt_tokens} / 输出 ${r.usage.completion_tokens} token\n请求 ${r.gateway.request_id}\n来源 ${r.gateway.source}`;toast('调用成功，账本已结算')},
 'video-form':async()=>{const r=await api('/v1/videos','POST',{model:'h3',prompt:$('#video-prompt').value,duration_seconds:Number($('#duration').value),num_inference_steps:Number($('#steps').value)},{'Idempotency-Key':$('#video-id').value});toast(`模拟任务已登记：${r.id}`);await render()},
 'user-form':async()=>{const role=$('#user-role').value;const models=selectedModels('user');await api('/api/users','POST',{name:$('#user-name').value,role,models,profiles:[],budget:selectedBudget('user')});toast('用户已创建');await render()},
 'key-form':async()=>{const models=selectedModels('key');const r=await api('/api/keys','POST',{user_id:$('#key-user').value,models,budget:selectedBudget('key'),days:Number($('#key-days').value),rpm:Number($('#key-rpm').value),tpm:Number($('#key-tpm').value),concurrency:Number($('#key-concurrency').value)});$('#key-result').innerHTML=`<div class="key-reveal"><strong>完整 Key 仅本次显示，离开页面后不再显示。</strong><code>${esc(r.key)}</code></div>`;toast('Key 已创建，请保存到密码管理器')},
 'reconcile-form':async()=>{if(!$('#reconcile-id').value)throw new Error('当前没有待对账请求');await api('/api/requests/'+$('#reconcile-id').value+'/reconcile','POST',{outcome:$('#reconcile-outcome').value,input_tokens:Number($('#reconcile-input').value),output_tokens:Number($('#reconcile-output').value),evidence:$('#reconcile-evidence').value});toast('已按核验记录结算');await render()}
 };Object.entries(forms).forEach(([id,fn])=>{const form=$('#'+id);if(form)form.addEventListener('submit',e=>{e.preventDefault();action(e.submitter,fn)})});$('#qwen-probe')?.addEventListener('click',e=>action(e.target,async()=>{const r=await api('/api/qwen/probe','POST',{});toast('只读探测通过：'+r.model)}));$('#new-chat-id')?.addEventListener('click',()=>$('#chat-id').value=crypto.randomUUID());document.querySelectorAll('[data-cancel]').forEach(b=>b.addEventListener('click',()=>action(b,async()=>{await api('/v1/videos/'+b.dataset.cancel+'/cancel','POST',{});toast('排队任务已取消，额度已退回');await render()})));document.querySelectorAll('[data-revoke]').forEach(b=>b.addEventListener('click',()=>action(b,async()=>{await api('/api/keys/'+b.dataset.revoke,'DELETE');toast('Key 已吊销');await render()})));document.querySelectorAll('[data-download]').forEach(b=>b.addEventListener('click',()=>action(b,async()=>{const r=await fetch('/v1/videos/'+b.dataset.download+'/content',{headers:{Authorization:'Bearer '+(passwordAuth()?callKey:key)}});if(!r.ok)throw new Error('下载权限检查失败');const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=b.dataset.download+'-simulation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})))}
function grantEditor(users,models){return panel('更新已有用户的权限与状态',`<form id="grant-form" class="panel-body"><div class="form-grid"><div class="field"><label for="grant-user">用户</label><select id="grant-user">${users.map(u=>`<option value="${esc(u.id)}" data-role="${esc(u.role)}" data-active="${u.active}" data-models="${esc(u.models)}" data-profiles="${esc(u.profiles)}">${esc(u.name)}</option>`).join('')}</select></div><div class="field"><label for="grant-role">角色</label><select id="grant-role" required><option value="" disabled>请选择当前支持的身份</option><option value="caller">普通成员</option><option value="admin">平台管理员</option></select></div><div class="field"><label for="grant-active">账号状态</label><select id="grant-active"><option value="1">启用</option><option value="0">停用</option></select></div></div>${modelChecks('grant',models)}<p id="grant-warning" class="field-help" role="status"></p><button class="secondary" type="submit">保存权限与状态</button><p class="field-help spacer">有效模型为用户与 Key 授权的交集。角色仅启用管理员和普通成员，历史身份需管理员明确选择后才能更改。管理员的运行资格仍受已批准 Profile 和资源保护检查约束。</p></form>`)}
function bindGrants(){
 for(const prefix of ['user','key','self','approval','memberbudget']){
  const toggle=$('#'+prefix+'-unlimited');if(!toggle)continue;
  const update=()=>{$('#'+prefix+'-limited').hidden=toggle.checked;$('#'+prefix+'-budget').disabled=toggle.checked};
  toggle.addEventListener('change',update);update();
 }
 const keyUser=$('#key-user');
 if(keyUser){const update=()=>{const granted=JSON.parse(keyUser.selectedOptions[0]?.dataset.models||'[]');document.querySelectorAll('#key-models input[data-model]').forEach(input=>{input.disabled=!granted.includes(input.value);input.checked=!input.disabled})};keyUser.addEventListener('change',update);update()}
 const form=$('#grant-form');if(!form)return;
 function load(){const option=$('#grant-user').selectedOptions[0];if(!option)return;$('#grant-role').value=['admin','caller'].includes(option.dataset.role)?option.dataset.role:'';$('#grant-active').value=option.dataset.active;const models=JSON.parse(option.dataset.models);document.querySelectorAll('#grant-models input[data-model]').forEach(input=>{input.checked=models.includes(input.value)});$('#grant-warning').textContent=['admin','caller'].includes(option.dataset.role)?'':'该账户保留历史身份，当前未自动更改。请明确选择管理员或普通成员。'}
 $('#grant-user').addEventListener('change',load);load();
 form.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const option=$('#grant-user').selectedOptions[0];if(!$('#grant-role').value)throw new Error('请明确选择新的账户身份');const known=[...document.querySelectorAll('#grant-models input[data-model]')].map(input=>input.value);if(JSON.parse(option.dataset.models).some(model=>!known.includes(model)))throw new Error('账户包含目录外的历史授权，请先由管理员核对模型目录，避免覆盖');await api('/api/users/'+$('#grant-user').value,'PATCH',{role:$('#grant-role').value,active:$('#grant-active').value==='1',models:selectedModels('grant'),profiles:JSON.parse(option.dataset.profiles)});toast('权限已更新');await render()})});
}

async function render(){const sequence=++renderSequence;try{const identity=await api('/api/me');const guide=await api('/api/guide');if(sequence!==renderSequence)return;me=identity;if(guideInfo?.version!==guide.version||guideInfo?.role!==guide.role)guideStep=0;guideInfo=guide;if(!guideInfo.allowed_pages.includes(page))page='guide';nav();const views={invitations:()=>accounts().invitations(),appearance:()=>appearanceUI().view(),overview:()=>consoleUI().overview(),quota:()=>consoleUI().quota(),assets,runtimelegacy:runtime,queuelegacy:modelQueue,tasks:()=>jobsUI().view(),tasklegacy:play,runtime:()=>consoleUI().runtime(),play:()=>consoleUI().models(),tools:projectTools,users,usage,connect:()=>consoleUI().connection(),guide:accountGuide,queue:()=>consoleUI().queue(),notifications:notificationsPage,account:()=>accounts().profile(),mykeys:()=>accounts().keys(),registrations:()=>accounts().registrations(),keyaudit:()=>accounts().keyAudit(),announcements:()=>messages().admin()};const html=await views[page]();if(sequence!==renderSequence)return;$('#main').innerHTML=html;if(page==='queuelegacy')$('#reservation-form')?.closest('.panel')?.remove();$('#main').dataset.page=page;$('#main').classList.toggle('console-page',['overview','connect','quota','queue','appearance','tasks'].includes(page));if(!['overview','connect','quota','queue','appearance','tasks'].includes(page))mountWorkspace();if(me.user.role==='caller'){const group=guideInfo.navigation.find(g=>g.pages.includes(page));if(group?.pages.length>1){if(page==='play'){const tabs=$('#main .workspace-tabs');const row=document.createElement('div');row.className='model-navigation';tabs.before(row);row.append(tabs);row.insertAdjacentHTML('beforeend',`<div class="workspace-links">${group.pages.filter(id=>id!=='play').map(id=>`<button class="secondary" data-go="${id}">${esc(pageLabels[id])}</button>`).join('')}</div>`)}else $('#main .page-top')?.insertAdjacentHTML('afterend',`<div class="console-subnav" aria-label="功能分区">${group.pages.map(id=>`<button class="${id===page?'primary':'secondary'}" data-go="${id}">${esc({mykeys:'API Key',connect:'接入配置',play:'模型中心',queue:'模型申请',quota:'额度概览',usage:'调用记录'}[id]||pageLabels[id])}</button>`).join('')}</div>`)}}consoleUI().bind();consoleUI().monitor();tidyCopy();bind();bindGrants();bindGuide();bindRoleControls();bindCommunity();if(passwordAuth())accounts().bind();messages().bind();appearanceUI().bind();jobsUI().bind();jobsUI().start();if($('#play-key')){$('#play-key').value=callKey;$('#play-key').addEventListener('input',event=>{callKey=event.target.value.trim()})}document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.go)));$('#copy-base')?.addEventListener('click',e=>action(e.currentTarget,async()=>{try{await navigator.clipboard.writeText(location.origin+'/v1');toast('API 地址已复制')}catch{toast('无法自动复制，请选中地址手动复制',true)}}))}catch(e){if(sequence===renderSequence)toast(e.message,true)}}
async function projectTools(){const result=await api('/api/project-tools');return top('项目工具','统一进入项目组已有应用。每个工具保留自己的登录和数据边界。','GROUP / TOOLS')+notice('平台 API Key 不会发送给工具网站。内嵌仍取决于原站证书与防嵌入策略；如内嵌受限，请使用“在原站打开”。')+(result.tools.length?result.tools.map(t=>panel(esc(t.title),`<div class="panel-body"><p class="small muted">${esc(t.description)}</p><div class="buttons spacer"><a class="primary" href="${esc(t.url)}" target="_blank" rel="noopener noreferrer">在原站打开 ↗</a><span class="badge warn">连接状态待验证</span></div>${t.embed?`<iframe class="tool-frame" src="${esc(t.url)}" title="${esc(t.title)}" referrerpolicy="no-referrer" sandbox="allow-forms allow-scripts allow-same-origin allow-downloads allow-popups" loading="lazy"></iframe>`:''}<p class="field-help spacer">项目门户仅提供入口，不会替原站认证或把本机地址自动变为公网地址。</p></div>`,'PRIVATE LOCAL CONFIG')).join(''):panel('配置项目工具',`<div class="panel-body"><p>本机尚未登记工具。管理员可将地址配置到忽略目录中的 <code>.state/project-tools.local.json</code>，格式见 README。真实站点地址不要写入公开示例。</p></div>`))}

async function connect(){
 const catalog=await api('/api/model-catalog');const models=catalog.models.filter(model=>model.authorized);
 return top('接入指南','网页入口、平台 API 地址和 GPU 上游服务各自承担不同职责','CONNECT')+
 notice(cloudPreview()?'云端受限预发布：下方是未来接入所需的信息，当前 /v1 接口统一关闭。请等待真实生成和客户端协议验收后再填写客户端；管理员 Key 不应用于客户端。':'当前只支持非流式 Chat Completions，完整工具调用、Responses 与 Anthropic Messages 尚待适配；登记目录不等于健康模型列表。')+
 `<div class="two-col equal">${panel(cloudPreview()?'未来 API 接入信息':'你的 API 连接',`<div class="panel-body"><div class="connection-row"><span>Base URL${cloudPreview()?' · 尚未开放':''}</span><code>${esc(location.origin)}/v1</code></div><div class="connection-row"><span>目标协议</span><code>OpenAI Chat Completions</code></div><div class="connection-row"><span>当前文本来源</span><span>${esc(textSourceLabel())}</span></div><div class="connection-row"><span>已授权目录 / 模型 ID</span><code>${models.map(model=>`${esc(model.display_name)}<br>${esc(model.id)} · ${esc(model.status_label||deploymentNames[model.status||model.deployment_status]||'暂未运行')}`).join('<br><br>')||'暂无授权模型'}</code></div><div class="buttons spacer">${cloudPreview()?'':'<button id="copy-base" class="secondary">复制 API 地址</button>'}<button class="secondary" data-go="play">${cloudPreview()?'查看部署准备':'查看调用验证'} →</button></div><p class="field-help spacer">模型显示名称用于识别，API 使用登记的模型 ID。上线后使用独立的普通成员 Key，完整 Key 不在此页回显。</p></div>`,'CONNECTION')}${panel('客户端与配置管理',`<div class="panel-body"><div class="step"><span>01</span><div>服务器接通<small>云端网页网关通过受控通道访问 GPU 服务器；成员访问网页不等于这条通道已经建立。</small></div></div><div class="step"><span>02</span><div>成员凭证与模型授权<small>${passwordAuth()?'成员注册并获批后，自行创建调用 Key；管理员管理模型授权和额度。':'管理员创建成员，指定模型与额度，再发放独立 Key。'}模型必须已部署并通过健康和真实调用验证。</small></div></div><div class="step"><span>03</span><div>验证 harness 与 CC Switch<small>CC Switch 用于管理兼容客户端的配置；实际任务由 harness 执行。每款客户端的流式和工具协议仍需独立验收。</small></div></div><p class="field-help spacer">本期没有服务器端 harness。客户端型任务执行时需保持客户端后台和网络可用。</p></div>`,'CLIENT SETUP')}</div>`;
}

$('#open-api-quick').addEventListener('click',()=>consoleUI().quick());
$('#menu-toggle').addEventListener('click',()=>{const opened=document.body.classList.toggle('menu-open');$('#menu-toggle').setAttribute('aria-expanded',String(opened));$('#menu-toggle').setAttribute('aria-label',opened?'收起导航':'展开导航')});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('menu-open')){closeMenu();$('#menu-toggle').focus()}});
document.addEventListener('click',e=>{if(document.body.classList.contains('menu-open')&&!e.target.closest('#sidebar')&&!e.target.closest('#menu-toggle'))closeMenu()});

// Tabs move the existing nodes instead of re-rendering, preserving form values.
function tidyCopy(){document.querySelectorAll('#main p,.field-help,.subtitle,.message-dialog-content p').forEach(el=>{if(el.closest('.message-body'))return;const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let last,node;while(node=walker.nextNode())if(node.textContent.trim())last=node;if(last)last.textContent=last.textContent.replace(/。\s*$/,'')})}
const workspaceTabs = new Map();
let workspaceObserver;
function bindRoleControls(){
  document.querySelectorAll('[data-go]').forEach(button=>{if(!guideInfo.allowed_pages.includes(button.dataset.go)){button.disabled=true;button.title='当前角色没有此功能权限'}});
  if(me.user.role!=='admin')document.querySelectorAll('#policy-form input,#policy-form select,#policy-form button').forEach(control=>{control.disabled=true;control.title='生命周期策略由管理员维护'});
  if(me.user.role==='auditor')document.querySelectorAll('#runtime-form input,#runtime-form select,#runtime-form button').forEach(control=>{control.disabled=true;control.title='审计员只读查看运行记录'});
  const probe=$('#qwen-probe');if(probe&&!['admin','operator'].includes(me.user.role)){probe.disabled=true;probe.title='上游探测由管理员或运行操作员执行'};
}

function mountWorkspace(){
  const root = $('#main');
  root.dataset.page = page;
  const panels = [...root.querySelectorAll('.panel')];
  if(!panels.length)return;
  const heading = root.querySelector('.page-top');
  const notices = [...root.querySelectorAll(':scope > .notice')];
  const prefix = page==='overview'?[root.querySelector('.hero'),root.querySelector('.metrics')].filter(Boolean):[];
  const extra = [...root.querySelectorAll(':scope > details')];
  for(const detail of extra){
    const panelNode=document.createElement('section');panelNode.className='panel';
    const title=document.createElement('div');title.className='panel-head';
    const h=document.createElement('h2');h.textContent='配置核验详情';title.append(h);
    const body=document.createElement('div');body.className='panel-body';body.append(detail);detail.open=true;
    panelNode.append(title,body);panels.push(panelNode);
  }
  const stage=document.createElement('div');stage.className='desktop-stage';
  const tabs=document.createElement('div');tabs.className='workspace-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','页面功能');
  const panes=document.createElement('div');panes.className='workspace-panes';
  const buttons=[];
  const selectTab=(index,focus=false)=>{
    workspaceTabs.set(page,index);
    buttons.forEach((button,i)=>{button.setAttribute('aria-selected',String(i===index));button.tabIndex=i===index?0:-1;panels[i].hidden=i!==index});
    if(focus)buttons[index].focus();
    requestAnimationFrame(()=>paginateVisibleTables());
  };
  panels.forEach((node,index)=>{
    const button=document.createElement('button');button.type='button';button.id=`workspace-tab-${index}`;
    button.textContent=node.querySelector('h2')?.textContent||`功能 ${index+1}`;
    button.setAttribute('role','tab');button.setAttribute('aria-controls',`workspace-pane-${index}`);
    node.id=`workspace-pane-${index}`;node.setAttribute('role','tabpanel');node.setAttribute('aria-labelledby',button.id);node.tabIndex=0;
    button.addEventListener('click',()=>selectTab(index));
    button.addEventListener('keydown',event=>{
      let next=index;
      if(event.key==='ArrowRight')next=(index+1)%panels.length;
      else if(event.key==='ArrowLeft')next=(index+panels.length-1)%panels.length;
      else if(event.key==='Home')next=0;
      else if(event.key==='End')next=panels.length-1;
      else return;
      event.preventDefault();selectTab(next,true);
    });
    buttons.push(button);tabs.append(button);panes.append(node);
  });
  stage.append(tabs,panes);
  const nodes=[heading,root.querySelector('.switch-banner'),...prefix];
  if(notices.length){
    const hint=document.createElement('details');hint.className='workspace-hint';
    const summary=document.createElement('summary');summary.textContent='页面说明与当前限制';
    const content=document.createElement('div');content.className='workspace-hint-content';content.append(...notices);
    hint.append(summary,content);nodes.push(hint);
  }
  root.replaceChildren(...nodes.filter(Boolean),stage);
  panels.forEach(node=>node.querySelectorAll('.table-wrap').forEach(installTablePager));
  selectTab(Math.min(workspaceTabs.get(page)||0,panels.length-1));
  workspaceObserver?.disconnect();
  if(typeof ResizeObserver!=='undefined'){
    workspaceObserver=new ResizeObserver(()=>paginateVisibleTables());workspaceObserver.observe(stage);
  }
}

const tablePagers = new WeakMap();
function installTablePager(wrap){
  const rows=[...wrap.querySelectorAll('tbody > tr')];
  if(!rows.length||wrap.querySelector('.empty'))return;
  const controls=document.createElement('div');controls.className='table-pager';
  controls.innerHTML='<span class="pager-label" aria-live="polite"></span><div><button class="secondary" type="button" aria-label="上一页">←</button><button class="secondary" type="button" aria-label="下一页">→</button></div>';
  wrap.after(controls);
  const [prev,next]=controls.querySelectorAll('button');
  const state={rows,index:0,size:5,controls};tablePagers.set(wrap,state);
  const draw=()=>{
    const pages=Math.max(1,Math.ceil(rows.length/state.size));state.index=Math.min(state.index,pages-1);
    rows.forEach((row,i)=>{row.hidden=i<state.index*state.size||i>=(state.index+1)*state.size});
    controls.querySelector('.pager-label').textContent=`${state.index*state.size+1}–${Math.min((state.index+1)*state.size,rows.length)} / ${rows.length} 条`;
    prev.disabled=state.index===0;next.disabled=state.index>=pages-1;
  };
  state.draw=draw;
  prev.addEventListener('click',()=>{state.index--;draw()});next.addEventListener('click',()=>{state.index++;draw()});
  draw();
}
function paginateVisibleTables(){
  $('#main').querySelectorAll('.panel:not([hidden]) .table-wrap').forEach(wrap=>{
    const state=tablePagers.get(wrap);if(!state)return;
    const desktop=matchMedia('(min-width: 1000px) and (min-height: 650px)').matches;
    if(!desktop){state.size=5;state.draw();return}
    const height=wrap.clientHeight;if(!height)return;
    state.rows.forEach(row=>{row.hidden=false});
    const head=wrap.querySelector('thead')?.getBoundingClientRect().height||40;
    let consumed=head+6,size=0;
    for(const row of state.rows){const rowHeight=row.getBoundingClientRect().height;if(consumed+rowHeight>height&&size>0)break;consumed+=rowHeight;size++}
    state.size=Math.max(1,size);state.draw();
  });
}

let guideInfo=null,guideStep=0;
function guideStepBody(){
  const step=guideInfo.steps[guideStep];
  return `<div class="eyebrow">STEP ${String(guideStep+1).padStart(2,'0')} / ${String(guideInfo.steps.length).padStart(2,'0')}</div><h3>${esc(step.title)}</h3><p>${esc(step.text)}</p><button id="guide-target" class="secondary">前往${esc(pageLabels[step.target]||'对应功能')} ↗</button>`;
}
async function accountGuide(){
  return top('你的使用引导',`${guideInfo.role_name} · v${guideInfo.version} · 功能与引导随版本一起更新`,'YOUR WORKSPACE GUIDE')+
    panel('从你的角色开始',`<div class="panel-body guide-body"><div class="guide-summary"><span class="badge good">${esc(guideInfo.role_name)}</span><span>${guideInfo.models.length?'当前获授模型：'+esc(guideInfo.models.join(' / ')):'当前没有模型调用权限'}</span></div><div class="guide-wizard"><div class="guide-step-list" aria-label="引导步骤">${guideInfo.steps.map((step,i)=>`<button type="button" data-guide-step="${i}" ${i===guideStep?'aria-current="step"':''}><span>${String(i+1).padStart(2,'0')}</span>${esc(step.title)}</button>`).join('')}</div><article id="guide-step-content" class="guide-step-content" aria-live="polite">${guideStepBody()}</article></div><div class="guide-actions"><div><button class="secondary" id="guide-prev">← 上一步</button><button class="secondary" id="guide-next">下一步 →</button></div><button class="primary" id="guide-ack">${guideInfo.needs_attention?'已了解，进入工作台':'返回工作台'} →</button></div></div>`,'ROLE-BASED GUIDE')+
    panel('本次更新与功能边界',`<div class="panel-body"><div class="release-version">v${esc(guideInfo.version)} <span>当前工作空间版本</span></div>${guideInfo.changes.map((text,i)=>`<div class="step"><span>0${i+1}</span><div>${esc(text)}</div></div>`).join('')}<p class="field-help spacer">${esc(guideInfo.boundary)}</p><p class="field-help">当前文本来源：${esc(textSourceLabel())}；运行控制：${cloudPreview()||cloudApi()?'已关闭':'模拟'}</p></div>`,'WHAT IS NEW');
}
function bindGuide(){
  if(page!=='guide')return;
  const update=()=>{
    $('#guide-step-content').innerHTML=guideStepBody();
    document.querySelectorAll('[data-guide-step]').forEach(button=>{if(Number(button.dataset.guideStep)===guideStep)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current')});
    $('#guide-prev').disabled=guideStep===0;$('#guide-next').disabled=guideStep===guideInfo.steps.length-1;
    $('#guide-target').addEventListener('click',()=>navigate(guideInfo.steps[guideStep].target));
  };
  document.querySelectorAll('[data-guide-step]').forEach(button=>button.addEventListener('click',()=>{guideStep=Number(button.dataset.guideStep);update()}));
  $('#guide-prev').addEventListener('click',()=>{guideStep=Math.max(0,guideStep-1);update()});
  $('#guide-next').addEventListener('click',()=>{guideStep=Math.min(guideInfo.steps.length-1,guideStep+1);update()});
  $('#guide-ack').addEventListener('click',event=>action(event.currentTarget,async()=>{
    if(guideInfo.needs_attention)await api('/api/guide/ack','POST',{version:guideInfo.version});
    await navigate(me.account?.status==='pending'?'account':me.user.role==='caller'?'play':'overview');
  }));
  update();
}

const reservationState={pending:'待审批',approved:'已安排',rejected:'未批准',cancelled:'已取消',closed:'预约结束'};
const bjDate=value=>value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short',hour12:false}).format(new Date(value*1000)):'待安排';
const bjInput=value=>value?new Date((value+8*3600)*1000).toISOString().slice(0,16):'';
const bjTimestamp=value=>value?new Date(value+':00+08:00').getTime()/1000:null;
function reservationBadge(status){return `<span class="badge ${status==='approved'?'good':status==='pending'?'warn':'neutral'}">${esc(reservationState[status]||status)}</span>`}
function queueTable(rows){return table(['顺序 / 参考号','模型','状态','计划开始（北京时间）','预计时长'],rows.map(row=>[`<strong>${row.position}</strong><small>${esc(row.queue_ref)}${row.is_owner?' · 我的':''}</small>`,esc(row.model),reservationBadge(row.status),bjDate(row.scheduled_start||row.preferred_start),`${row.estimated_minutes} 分钟`]))}
function mineTable(rows){return table(['模型 / 用途摘要','状态','计划时间','管理员说明','操作'],rows.map(row=>[`${esc(row.model)}<small>${esc(row.purpose)}</small>`,reservationBadge(row.status),bjDate(row.scheduled_start||row.preferred_start),esc(row.reason||'等待审核'),['pending','approved'].includes(row.status)?`<button class="secondary" data-reservation-cancel="${esc(row.id)}">取消预约</button>`:'—']))}
async function modelQueue(){
  const data=await api('/api/model-requests');
  return top('模型排队','申请共享模型时间窗，查看排队与审批；本期不自动执行推理','MODEL RESERVATIONS')+
    notice('队列每10秒更新，显示顺序是排程参考，不是精确执行承诺。预约不扣调用额度、不保存提示词、不切换模型。所有时间均为北京时间')+
    panel('队列概览',`<div class="live-toolbar"><span id="queue-updated">更新于 ${bjDate(data.updated_at)}</span><span>其他成员用途与身份不公开</span></div><div id="queue-live" class="live-list">${queueTable(data.queue)}</div>`,'SHARED / REDACTED')+
    (data.can_request?panel('申请时间窗',`<form id="reservation-form" class="panel-body"><div class="form-grid"><div class="field"><label for="reservation-model">获授权模型</label><select id="reservation-model">${data.allowed_models.map(model=>`<option value="${esc(model)}">${esc(model)}</option>`).join('')}</select></div><div class="field"><label for="reservation-start">期望开始（北京时间，可留空）</label><input id="reservation-start" type="datetime-local"></div>${field('预计使用时长 / 分钟','reservation-minutes','number',30,'min="5" max="240" required')}</div><div class="field"><label for="reservation-purpose">用途摘要，不填提示词或敏感资料</label><textarea id="reservation-purpose" required minlength="4" maxlength="300" placeholder="例如：项目组代码实验，预计需要一个时间窗"></textarea></div><div class="form-grid"><div class="field"><label for="reservation-mode">执行方式</label><select id="reservation-mode"><option>客户端交互 · 实际执行需后台在线</option><option disabled>服务端离线执行 · 尚未上线</option></select></div>${field('申请 ID · 重试时保持不变','reservation-id','text',crypto.randomUUID(),'required maxlength="100"')}</div><div class="checkline"><label class="client-ack"><input type="checkbox" id="reservation-ack" required>我已了解：提交申请后可退出网页；客户端型 Agent 真正执行时，需保持 harness 后台和网络可用。仅登录网页并不够</label></div><button class="primary" type="submit">提交预约申请 →</button><p class="field-help spacer">本期仅登记与审批，不替客户端自动执行。每账户最多3个未结束预约、每天20次申请；选择模型不会授予启停权限</p></form>`,'CLIENT REQUIREMENT'):panel('申请权限',`<div class="panel-body">${me.user.role==='auditor'?'审计员可只读查看队列，不能提交申请':'当前用户与 Key 没有共同获授的模型，请联系管理员'}</div>`))+
    panel('我的预约',`<div id="queue-mine" class="live-list">${mineTable(data.mine)}</div>`,'OWN REQUESTS')+
    (data.can_manage?panel('审批与排程',`<form id="reservation-admin-form" class="panel-body"><div class="field"><label for="reservation-select">申请记录</label><select id="reservation-select">${data.manage.map(row=>`<option value="${esc(row.id)}" data-record="${esc(JSON.stringify(row))}">${esc(row.model)} · ${esc(reservationState[row.status])} · ${esc(row.purpose.slice(0,45))}</option>`).join('')}</select></div><div class="form-grid"><div class="field"><label for="reservation-status">审批状态</label><select id="reservation-status"><option value="pending">待审批</option><option value="approved">批准并安排时间</option><option value="rejected">不批准</option><option value="cancelled">取消预约</option><option value="closed">人工结束预约</option></select></div>${field('显示优先级 · 数字越小越靠前','reservation-priority','number',100,'min="0" max="9999" required')}${field('安排时长 / 分钟','reservation-admin-minutes','number',30,'min="5" max="240" required')}<div class="field"><label for="reservation-scheduled">计划开始（北京时间）</label><input id="reservation-scheduled" type="datetime-local"></div></div><div class="field"><label for="reservation-reason">调整理由，会通知申请人</label><textarea id="reservation-reason" required minlength="4" maxlength="300"></textarea></div><button class="primary" id="reservation-save" type="submit">保存审批与排程</button><p class="field-help spacer">批准窗口不能重叠。本期只调整预约，不执行启停、取消真实请求或生成结果。概览会自动更新；此表单保留输入，遇到版本冲突请手动刷新后重审</p></form>`,'ADMIN ONLY'):'');
}
function notificationTable(items){return messages().rows(items)}
async function notificationsPage(){return messages().inbox()}
function updateNotificationCount(count){const badge=$('#notification-count');badge.textContent=count>99?'99+':String(count);badge.hidden=count===0}
function bindCommunityActions(scope=document){
  messages().bindDetails(scope);
  scope.querySelectorAll('[data-reservation-cancel]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{await api('/api/model-requests/'+button.dataset.reservationCancel+'/cancel','POST',{});toast('预约已取消，不影响已经运行的模型或请求');await render()})));
  scope.querySelectorAll('[data-notice-read]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{await api('/api/notifications/'+button.dataset.noticeRead+'/read','POST',{});await render()})));
}
function bindCommunity(){
  bindCommunityActions();
  $('#reservation-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{await api('/api/model-requests','POST',{model:$('#reservation-model').value,purpose:$('#reservation-purpose').value,preferred_start:bjTimestamp($('#reservation-start').value),estimated_minutes:Number($('#reservation-minutes').value),client_ack:$('#reservation-ack').checked,client_request_id:$('#reservation-id').value});toast('预约已登记，等待管理员审批；尚未执行任务');await render()})});
  const form=$('#reservation-admin-form');
  if(form){
    const load=()=>{const option=$('#reservation-select').selectedOptions[0];if(!option){$('#reservation-save').disabled=true;return}const row=JSON.parse(option.dataset.record);$('#reservation-status').value=row.status;$('#reservation-priority').value=row.priority;$('#reservation-admin-minutes').value=row.estimated_minutes;$('#reservation-scheduled').value=bjInput(row.scheduled_start||row.preferred_start);$('#reservation-reason').value=row.reason;$('#reservation-save').disabled=!['pending','approved'].includes(row.status)};
    $('#reservation-select').addEventListener('change',load);load();
    form.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const selected=$('#reservation-select').selectedOptions[0];const row=JSON.parse(selected.dataset.record);await api('/api/model-requests/'+row.id,'PATCH',{status:$('#reservation-status').value,priority:Number($('#reservation-priority').value),scheduled_start:bjTimestamp($('#reservation-scheduled').value),estimated_minutes:Number($('#reservation-admin-minutes').value),reason:$('#reservation-reason').value,expected_revision:row.revision});toast('排程已保存并发送站内通知；未触发模型操作');await render()})});
  }
  $('#notice-read-all')?.addEventListener('click',event=>action(event.currentTarget,async()=>{await api('/api/notifications/read-all','POST',{});await render()}));
  startCommunityMonitor();
}
let communityTimer=null,communityBusy=false;
function stopCommunityMonitor(){if(communityTimer)clearInterval(communityTimer);communityTimer=null}
function replaceLiveList(id,html){const container=$(id);if(!container)return;const old=tablePagers.get(container.querySelector('.table-wrap'));container.innerHTML=html;container.querySelectorAll('.table-wrap').forEach(wrap=>{installTablePager(wrap);const state=tablePagers.get(wrap);if(state&&old){state.index=old.index;state.size=old.size;state.draw()}});bindCommunityActions(container);requestAnimationFrame(paginateVisibleTables)}
async function pollCommunity(){
  if(!me||document.hidden||communityBusy)return;
  const sessionKey=key,sessionUser=me.user.id,requestedPage=page;communityBusy=true;
  try{
    const notices=await api(requestedPage==='notifications'?messages().inboxPath():'/api/notifications?limit=1');
    if(key!==sessionKey||!me||me.user.id!==sessionUser)return;
    updateNotificationCount(notices.unread);
    if(page==='notifications'&&requestedPage===page){replaceLiveList('#notification-live',notificationTable(notices.items));if($('#notification-summary'))$('#notification-summary').textContent=messages().summary(notices)}
    if(page==='queue'&&requestedPage===page){const snapshot=await api('/api/model-requests');if(key!==sessionKey||!me||me.user.id!==sessionUser||page!=='queue')return;replaceLiveList('#queue-live',queueTable(snapshot.queue));replaceLiveList('#queue-mine',mineTable(snapshot.mine));if($('#queue-updated'))$('#queue-updated').textContent='更新于 '+bjDate(snapshot.updated_at)}
  }catch{/* Keep drafts and last successful snapshot; manual refresh shows errors. */}
  finally{communityBusy=false}
}
function startCommunityMonitor(){if(!communityTimer){communityTimer=setInterval(pollCommunity,10000);void pollCommunity()}}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&me)void pollCommunity()});

$('#open-notifications').addEventListener('click',()=>navigate('notifications'));
$('#open-guide').addEventListener('click',()=>navigate('guide'));
$('#logout').addEventListener('click',event=>{if(passwordAuth())action(event.currentTarget,()=>accounts().logout());else login()});
async function initialize(){appearanceUI().start();try{const response=await fetch('/health',{cache:'no-store'});if(response.ok)environment=await response.json()}catch{/* Login remains available; environment stays explicitly unknown. */}$('#environment-name').textContent=cloudApi()?'云端文本试运行':cloudPreview()?'云端预发布':environment.mode==='unknown'?'环境待确认':'本地开发环境';$('#environment-badge').textContent=cloudApi()?'受限文本 API':cloudPreview()?'受限预发布':environment.mode==='unknown'?'待连接':'本地 MVP';$('#workspace-edition').textContent=cloudApi()?'TEXT PILOT':cloudPreview()?'PREVIEW':environment.mode==='unknown'?'PENDING':'LOCAL';$('#environment-detail').textContent=cloudApi()?'普通成员可调用非流式文本；启停关闭':cloudPreview()?'模型未连接；推理与启停关闭':environment.mode==='unknown'?'未获取服务环境，请检查连接':'本地演示；真实启停关闭';if(passwordAuth())await accounts().restore();else login()}
let messageWorkspace;
function messages(){
 if(!messageWorkspace)messageWorkspace=createMessagesUI({tidyCopy,api,$,esc,table,panel,top,notice,field,date:bjDate,action,toast,render,navigate,setCount:updateNotificationCount,
  refreshBadge:async()=>{const data=await api('/api/notifications?limit=1');updateNotificationCount(data.unread)}
 });return messageWorkspace;
}

let accountWorkspace;
const callingKeyField=()=>passwordAuth()?field('调用 API Key · 使用你自己创建的 Key','play-key','password','','autocomplete="off" spellcheck="false"')+'<p class="field-help">可以先在“我的 API”创建 Key。仅保留在当前页面内存，退出后清除。填写已有 Key 后可用浏览器刷新核对权限。</p>':'';
function accounts(){
 if(!accountWorkspace)accountWorkspace=createAccountsUI({tidyCopy,api,$,esc,field,table,panel,top,notice,num,date,modelChecks,selectedModels,quotaControls,selectedBudget,grantEditor,action,toast,render,navigate,
  setCSRF:value=>{csrfToken=value},setCallKey:value=>{callKey=value},
  resetForLogin:()=>{messages().reset();consolePanel?.reset();jobsPanel?.reset();authGeneration++;stopCommunityMonitor();renderSequence++;workspaceObserver?.disconnect();guideInfo=null;me=null;key='';callKey='';delete $('#main').dataset.page;document.body.classList.add('signed-out');closeMenu();$('#identity').setAttribute('aria-label','我的账号');$('#current-page').textContent='账号登录';$('#nav').innerHTML=''},
  onAuthenticated:async()=>{me=await api('/api/me');guideInfo=await api('/api/guide');document.body.classList.remove('signed-out');guideStep=0;page=me.account?.status==='pending'?'account':'overview';await render();await messages().loginPrompts(guideInfo)}
 });return accountWorkspace;
}

void initialize();
