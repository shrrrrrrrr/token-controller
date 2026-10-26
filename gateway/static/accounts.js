// Password/session UI. Secrets stay in the current document's memory only.
export function createAccountsUI(options){
 const {api,$,esc,field,table,panel,top,notice,num,date,modelChecks,selectedModels,quotaControls,selectedBudget,grantEditor,action,toast,render,navigate}=options;
 let editingKey=null;
 const labels={pending:'待管理员审批',approved:'已批准'};
 const keyFields=()=>`<div class="form-grid">${field('Key 名称','self-name','text','我的客户端','required maxlength="50"')}${field('有效天数','self-days','number',365,'min="1" max="365" required')}</div>${quotaControls('self')}<details class="key-settings"><summary>请求保护设置</summary><div class="form-grid spacer">${field('请求 / 分钟','self-rpm','number',60,'min="1" max="60" required')}${field('文本预留上限 / 分钟','self-tpm','number',1000000,'min="100" max="1000000" required')}${field('在途请求上限','self-concurrency','number',1,'min="1" max="1" required')}</div><p class="field-help">Key 设置不能扩大账户权限。预留上限不是实际消耗；模型全局并发保护仍生效。</p></details>`;
 async function csrfContext(){const result=await api('/api/auth/context');options.setCSRF(result.csrf_token);return result}
 async function login(view='login',message='',context=null){
  options.resetForLogin();editingKey=null;
  const state=context||await csrfContext();
  const registering=view==='register',claiming=view==='claim';
  const heading=registering?'注册项目组账号':claiming?'认领或恢复账号':'登录工作空间';
  $('#main').innerHTML=`<div class="login account-login"><section class="login-story"><div><div class="eyebrow">GROUP MODEL SPACE</div><h1>一个账号，<br>连接你的模型工作</h1><p>账号负责登录，API Key 负责调用。<br>成员自主注册，管理员审批与授权。</p></div><div class="login-features"><span>独立账号</span><span>自管 API</span><span>真实用量</span></div></section><section class="login-form-side"><h2>${heading}</h2>${message?notice(esc(message)):''}${state.setup_required?notice('管理员还未初始化网页登录账号，请先在服务器完成账户设置。'):''}<form id="account-auth-form">${registering?field('显示名称','auth-display','text','','required maxlength="60"'):''}${claiming?field('管理员提供的一次性认领 / 恢复码','auth-code','password','','required autocomplete="off"'):''}${field('用户名','auth-username','text','','required minlength="3" maxlength="40" autocomplete="username" spellcheck="false"')}${field(registering||claiming?'设置密码 · 至少15字符':'密码','auth-password','password','','required maxlength="128" autocomplete="'+(registering||claiming?'new-password':'current-password')+'"')}${registering||claiming?field('再次输入密码','auth-confirm','password','','required maxlength="128" autocomplete="new-password"'):''}<button class="primary" type="submit">${registering?'注册并等待审批':claiming?'完成设置':'登录'}</button><p id="auth-error" role="alert" class="field-help"></p></form><div class="auth-links"><button class="subtle" type="button" data-auth-view="login">账号登录</button><button class="subtle" type="button" data-auth-view="register">注册账号</button><button class="subtle" type="button" data-auth-view="claim">旧账号认领 / 密码恢复</button></div><p class="field-help">用户名使用3–40位英文、数字、点、横线或下划线，可在登录后修改。密码支持长口令，无须刻意拼凑字符。${claiming?'恢复密码会撤销旧会话和该账户的 API Key，之后需重新创建 Key。':'API Key 不用于此处登录。'}</p></section></div>`;
  document.querySelectorAll('[data-auth-view]').forEach(button=>button.addEventListener('click',()=>login(button.dataset.authView)));
  $('#account-auth-form').addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{
   try{
    const body={username:$('#auth-username').value,password:$('#auth-password').value};
    if(registering||claiming){if(body.password!==$('#auth-confirm').value)throw new Error('两次密码输入不一致');if(registering)body.display_name=$('#auth-display').value;else body.code=$('#auth-code').value}
    const result=await api('/api/auth/'+(registering?'register':claiming?'claim':'login'),'POST',body);
    if(registering||claiming){await login('login',registering?'注册成功。现在可以登录查看审批状态，管理员批准后可创建调用 Key。':'账号设置成功，请使用新账密登录。');return}
    options.setCSRF(result.csrf_token);await options.onAuthenticated();
   }catch(error){$('#auth-error').textContent=error.message}
  })});
 }
 async function restore(){const state=await csrfContext();if(state.authenticated){try{await options.onAuthenticated();return}catch{/* Expired/disabled sessions return to the sign-in form. */}}await login('login','',state)}
 async function logout(){await api('/api/auth/logout','POST',{});options.setCSRF('');await login()}
 async function profile(){
  const data=await api('/api/account');
  return top('我的账户','管理登录用户名与密码；API Key 在独立的入口管理','ACCOUNT')+
   notice(`账号状态：${esc(labels[data.status]||data.status)}。${data.status==='pending'?'管理员批准前不能创建调用 Key 或调用模型。':'修改账密后旧网页登录会话会失效；已有 API Key 不会因普通改密自动删除。'}`)+
   panel('用户名与密码',`<form id="account-profile-form" class="panel-body">${field('用户名','profile-username','text',data.username,'required minlength="3" maxlength="40" autocomplete="username"')}${field('当前密码 · 用于确认修改','profile-current','password','','required autocomplete="current-password"')}${field('新密码 · 留空则只修改用户名','profile-password','password','','minlength="15" maxlength="128" autocomplete="new-password"')}<button class="primary" type="submit">保存账密</button></form>`);
 }
 async function keys(){
  editingKey=null;const [rows,catalog]=await Promise.all([api('/api/self/keys'),api('/api/model-catalog')]);
  const models=catalog.models.filter(model=>model.authorized);
  return top('我的 API Key','自己创建和管理客户端凭证，完整值仅创建时显示一次','MY API')+
   notice('网页登录会话与调用 Key 分开。Key 仅能使用账户已获授模型；累计不限仍记录真实 token。每个账户最多10个未过期有效 Key。')+
   panel('我的 Key',table(['名称 / 标识','模型','累计已结算','有效期 / 状态','操作'],rows.map(row=>[
    `<strong>${esc(row.name)}</strong><small class="mono">${esc(row.prefix)}</small>`,esc(JSON.parse(row.models).join(' / ')||'无'),`${num(row.spent)} token<small>${row.budget<0?'累计不限':'Key 上限 '+num(row.budget)}</small>`,`${date(row.expires)}<small>${row.revoked?'已吊销':row.expires<Date.now()/1000?'已过期':'有效'}</small>`,row.revoked?'—':`<button class="secondary" data-self-edit="${esc(row.id)}">调整</button> <button class="danger" data-self-revoke="${esc(row.id)}">吊销</button>`])))+
   panel('创建或调整 Key',`<form id="personal-key-form" class="panel-body"><span id="personal-key-data" hidden>${esc(JSON.stringify(rows))}</span>${keyFields()}${modelChecks('self',models,models.map(model=>model.id))}<div class="buttons"><button class="primary" id="personal-key-save" type="submit">创建 Key · 仅显示一次</button><button class="secondary" id="personal-key-new" type="button">切回新建</button></div><div id="personal-key-result" aria-live="polite"></div></form>`)+
   panel('客户端接入',`<div class="panel-body"><p>创建后可在调用验证页使用本次 Key，或复制到自己的客户端。</p><button class="secondary" data-go="connect">查看地址与协议 →</button></div>`);
 }
 async function registrations(){
  const [rows,catalog]=await Promise.all([api('/api/admin/registrations'),api('/api/model-catalog')]);
  return top('注册审批','成员设置自己的账密，管理员决定是否开放模型及额度','REGISTRATIONS')+
   panel('待审批成员',table(['显示名称','登录用户名','申请时间'],rows.map(row=>[esc(row.name),esc(row.username),date(row.created)])))+
   panel('批准并分配权限',`<form id="registration-approve-form" class="panel-body"><div class="field"><label for="registration-user">待审批账号</label><select id="registration-user" required>${rows.map(row=>`<option value="${esc(row.id)}">${esc(row.name)} · ${esc(row.username)}</option>`).join('')}</select></div>${quotaControls('approval')}${modelChecks('approval',catalog.models,catalog.models.map(model=>model.id))}<p class="field-help">默认开放当前全部登记模型，不限制累计用量，继续统计 token；以后新增模型需另行确认。</p><button class="primary" type="submit" ${rows.length?'':'disabled'}>批准账号</button></form>`);
 }
 async function members(){
  const [rows,catalog]=await Promise.all([api('/api/users'),api('/api/model-catalog')]);
  const editable=rows.filter(row=>row.registration_status!=='pending');
  return top('成员与权限','管理账号、模型范围和额度；成员自己设置密码及创建 API Key','MEMBERS')+
   panel('成员列表',table(['成员 / 登录用户名','身份 / 状态','模型授权','累计 token / 限额'],rows.map(row=>[
    `<strong>${esc(row.name)}</strong><small>${esc(row.login_username||'历史成员 · 尚未认领')}</small>`,`${esc(row.role==='admin'?'管理员':'普通成员')}<small>${row.registration_status==='pending'?'待审批':row.active?'启用':'停用'}</small>`,esc(JSON.parse(row.models).join(' / ')||'无'),`${num(row.spent)}<small>${row.budget<0?'累计不限':'上限 '+num(row.budget)}</small>`])))+
   grantEditor(editable,catalog.models)+
   panel('调整累计额度',`<form id="member-budget-form" class="panel-body"><div class="field"><label for="budget-user">成员</label><select id="budget-user">${editable.map(row=>`<option value="${esc(row.id)}" data-budget="${row.budget}">${esc(row.name)}</option>`).join('')}</select></div>${quotaControls('memberbudget')}<p class="field-help">修改上限不会清空历史用量，不能把有限上限降到已用与预留之下。</p><button class="secondary" type="submit">保存额度</button></form>`)+
   panel('历史成员认领 / 密码恢复',`<form id="account-token-form" class="panel-body"><div class="field"><label for="token-user">成员</label><select id="token-user">${editable.filter(row=>row.role==='caller'&&row.active).map(row=>`<option value="${esc(row.id)}" data-login="${row.login_username?'1':'0'}">${esc(row.name)}</option>`).join('')}</select></div><div class="field"><label for="token-purpose">流程</label><select id="token-purpose"><option value="enroll">认领历史账号 · 24小时有效</option><option value="reset">恢复已认领账号密码 · 10分钟有效</option></select></div>${field('你的管理员密码 · 确认签发','token-password','password','','required autocomplete="current-password"')}<p class="field-help">通过可信渠道把一次性码交给对应成员。密码恢复会撤销该成员旧会话和 API Key；认领流程保留原 Key 与用量。</p><button class="secondary" type="submit">生成一次性码</button><div id="account-token-result" aria-live="polite"></div></form>`)+
   panel('登记历史成员',`<form id="user-form" class="panel-body"><p class="field-help">正常新成员应自行注册。本入口保留管理员代建成员记录的功能，创建后还需通过认领流程设置账密。</p>${field('名称','user-name','text','','required maxlength="60"')}<div class="field"><label for="user-role">身份</label><select id="user-role"><option value="caller">普通成员</option><option value="admin">管理员 · 账密须由本机工具初始化</option></select></div>${quotaControls('user')}${modelChecks('user',catalog.models,catalog.models.map(model=>model.id))}<button class="secondary" type="submit">登记成员记录</button></form>`);
 }
 async function keyAudit(){
  const [keys,users]=await Promise.all([api('/api/keys'),api('/api/users')]);
  return top('API Key 安全审计','保留管理审计与吊销能力，调用凭证由成员自行创建','KEY AUDIT')+
   panel('已发放凭证',table(['标识 / 归属','模型授权','用量 / 限制','有效期','操作'],keys.map(row=>[
    `<span class="mono">${esc(row.prefix)}</span><small>${esc(users.find(user=>user.id===row.user_id)?.name||row.user_id)}</small>`,esc(JSON.parse(row.models).join(' / ')),`${num(row.spent)} token<small>${row.rpm} 请求/分 · ${row.concurrency} 在途</small>`,date(row.expires),row.revoked?'已吊销':`<button class="danger" data-revoke="${esc(row.id)}">吊销</button>`])));
 }
 function bind(){
  $('#account-profile-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const body={username:$('#profile-username').value,current_password:$('#profile-current').value};if($('#profile-password').value)body.new_password=$('#profile-password').value;const result=await api('/api/account/profile','POST',body);options.setCSRF(result.csrf_token);$('#profile-current').value='';$('#profile-password').value='';toast('账密已保存，旧网页登录会话已撤销');await render()})});
  const personal=$('#personal-key-form');
  if(personal){
   const rows=JSON.parse($('#personal-key-data').textContent);
   const load=row=>{editingKey=row?.id||null;$('#self-name').value=row?.name||'我的客户端';$('#self-days').value=row?Math.max(1,Math.ceil((row.expires-Date.now()/1000)/86400)):365;$('#self-rpm').value=row?.rpm||60;$('#self-tpm').value=row?.tpm||1000000;$('#self-concurrency').value=1;$('#self-unlimited').checked=!row||row.budget<0;$('#self-budget').value=row&&row.budget>=0?row.budget:1000000;$('#self-unlimited').dispatchEvent(new Event('change'));const selected=row?JSON.parse(row.models):null;document.querySelectorAll('#self-models input[data-model]').forEach(input=>input.checked=selected?selected.includes(input.value):true);$('#personal-key-save').textContent=row?'保存 Key 设置':'创建 Key · 仅显示一次';$('#personal-key-result').textContent=''};
   document.querySelectorAll('[data-self-edit]').forEach(button=>button.addEventListener('click',()=>{load(rows.find(row=>row.id===button.dataset.selfEdit));document.querySelectorAll('.workspace-tabs button').forEach(tab=>{if(tab.textContent==='创建或调整 Key')tab.click()})}));
   $('#personal-key-new').addEventListener('click',()=>load(null));
   document.querySelectorAll('[data-self-revoke]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{await api('/api/self/keys/'+button.dataset.selfRevoke,'DELETE');toast('Key 已吊销，网页账号继续可用');await render()})));
   personal.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const body={name:$('#self-name').value,models:selectedModels('self'),budget:selectedBudget('self'),days:Number($('#self-days').value),rpm:Number($('#self-rpm').value),tpm:Number($('#self-tpm').value),concurrency:1};const result=await api('/api/self/keys'+(editingKey?'/'+editingKey:''),editingKey?'PATCH':'POST',body);if(result.key){options.setCallKey(result.key);$('#personal-key-result').innerHTML=`<div class="key-reveal"><strong>完整 Key 仅本次显示，离开本页后不再显示。</strong><code>${esc(result.key)}</code><p>本次 Key 已临时放入调用验证页，退出登录后清除。</p></div>`}else toast('Key 设置已保存')})});
  }
  $('#registration-approve-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{await api('/api/admin/registrations/'+$('#registration-user').value+'/approve','POST',{models:selectedModels('approval'),budget:selectedBudget('approval')});toast('账号已批准');await render()})});
  const budget=$('#budget-user');
  if(budget){const update=()=>{const value=Number(budget.selectedOptions[0]?.dataset.budget??-1);$('#memberbudget-unlimited').checked=value<0;$('#memberbudget-budget').value=value<0?1000000:value;$('#memberbudget-unlimited').dispatchEvent(new Event('change'))};budget.addEventListener('change',update);update()}
  $('#member-budget-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{await api('/api/users/'+$('#budget-user').value+'/budget','PATCH',{budget:selectedBudget('memberbudget')});toast('额度已更新，历史用量保留');await render()})});
  $('#account-token-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const result=await api('/api/admin/accounts/'+$('#token-user').value+'/token','POST',{purpose:$('#token-purpose').value,current_password:$('#token-password').value});$('#token-password').value='';$('#account-token-result').innerHTML=`<div class="key-reveal"><strong>一次性码仅本次显示</strong><code>${esc(result.code)}</code><p>请成员在登录页选择“旧账号认领 / 密码恢复”。</p></div>`})});
  if($('#token-user')){const update=()=>{$('#token-purpose').value=$('#token-user').selectedOptions[0]?.dataset.login==='1'?'reset':'enroll'};$('#token-user').addEventListener('change',update);update()}
 }
 return {login,restore,logout,profile,keys,registrations,members,keyAudit,bind};
}
