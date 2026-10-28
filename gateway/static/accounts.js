// Password/session UI. Secrets stay in the current document's memory only.
export function createAccountsUI(options){
 const {api,$,esc,field,table,panel,top,notice,num,date,modelChecks,selectedModels,quotaControls,selectedBudget,grantEditor,action,toast,render,navigate}=options;
 let editingKey=null,passwordDialog=null;
 function closeMenu(){const menu=$('#account-popover');if(menu)menu.hidden=true;$('#identity')?.setAttribute('aria-expanded','false')}
 document.addEventListener('click',event=>{if(!event.target.closest('.account-control'))closeMenu()});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeMenu();$('#identity')?.focus()}});
 async function menu(){
  const target=$('#account-popover');if(!target.hidden){closeMenu();return}
  const data=await api('/api/account');
  $('#account-summary').innerHTML=`<strong>${esc(data.user.name)}</strong><span>@${esc(data.username)}</span><span class="badge neutral">${data.user.role==='admin'?'平台管理员':'普通成员'}</span><small>${esc(labels[data.status]||data.status)}</small>`;
  target.hidden=false;$('#identity').setAttribute('aria-expanded','true');$('#account-profile').focus();
 }
 async function password(){
  closeMenu();if(passwordDialog)return;
  const opener=document.activeElement;let proof='';
  const dialog=document.createElement('dialog');passwordDialog=dialog;dialog.className='password-dialog login-dialog';dialog.setAttribute('aria-labelledby','password-dialog-title');
  dialog.innerHTML=`<div class="message-dialog-head"><h2 id="password-dialog-title">修改账号密码</h2><button type="button" class="icon-button" id="password-close" aria-label="关闭修改密码">×</button></div><div class="message-dialog-content"><p class="password-step" id="password-step">1 / 2 · 验证原密码</p><form id="password-change-form">${field('原密码','password-current','password','','required autocomplete="current-password" maxlength="128"')}<div id="password-next-fields" hidden>${field('新密码 · 至少6位','password-new','password','','autocomplete="new-password" minlength="6" maxlength="128" disabled')}${field('再次输入新密码','password-confirm','password','','autocomplete="new-password" minlength="6" maxlength="128" disabled')}</div><p id="password-error" role="alert"></p><button class="primary" type="submit" id="password-submit">验证原密码</button></form></div>`;
  document.body.append(dialog);
  dialog.querySelector('#password-close').onclick=()=>dialog.close();
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close()}});
  dialog.onclose=()=>{proof='';dialog.querySelectorAll('input').forEach(input=>input.value='');dialog.remove();passwordDialog=null;if(opener?.isConnected)opener.focus();else $('#identity')?.focus()};
  dialog.querySelector('form').onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{
   const error=dialog.querySelector('#password-error');error.textContent='';
   try{
    if(!proof){
     const result=await api('/api/account/password/verify','POST',{current_password:dialog.querySelector('#password-current').value});
     if(!dialog.open)return;proof=result.verification_token;
     const current=dialog.querySelector('#password-current');current.value='';current.disabled=true;current.closest('.field').hidden=true;
     dialog.querySelector('#password-next-fields').hidden=false;dialog.querySelectorAll('#password-next-fields input').forEach(input=>{input.disabled=false;input.required=true});
     dialog.querySelector('#password-step').textContent='2 / 2 · 设置新密码';dialog.querySelector('#password-submit').textContent='保存新密码';dialog.querySelector('#password-new').focus();
    }else{
     const value=dialog.querySelector('#password-new').value;if(value!==dialog.querySelector('#password-confirm').value)throw new Error('两次密码输入不一致');
     const result=await api('/api/account/password/change','POST',{verification_token:proof,new_password:value});
     options.setCSRF(result.csrf_token);dialog.close();toast('密码已更新');
    }
   }catch(e){if(dialog.open){error.textContent=e.code==='invalid_login'?'原密码不正确':e.message;if(e.code==='password_verification_expired'){proof='';dialog.querySelector('#password-next-fields').hidden=true;dialog.querySelectorAll('#password-next-fields input').forEach(input=>{input.value='';input.disabled=true});const current=dialog.querySelector('#password-current');current.disabled=false;current.closest('.field').hidden=false;dialog.querySelector('#password-step').textContent='1 / 2 · 验证原密码';dialog.querySelector('#password-submit').textContent='验证原密码';current.focus()}}}
  })};dialog.showModal();dialog.querySelector('#password-current').focus();
 }
 const labels={pending:'待管理员审批',approved:'已批准'};
 const keyFields=()=>`<div class="form-grid">${field('Key 名称','self-name','text','我的客户端','required maxlength="50"')}${field('有效天数','self-days','number',365,'min="1" max="365" required')}</div>${quotaControls('self')}<details class="key-settings"><summary>请求保护设置</summary><div class="form-grid spacer">${field('请求 / 分钟','self-rpm','number',60,'min="1" max="60" required')}${field('请求大小保护 / 分钟','self-tpm','number',1000000,'min="100" max="1000000" required')}${field('在途请求上限','self-concurrency','number',3,'min="1" max="3" required')}</div><p class="field-help">Key 设置不能扩大账户权限。不冻结额度；模型并发保护仍生效</p></details>`;
 async function csrfContext(){const result=await api('/api/auth/context');options.setCSRF(result.csrf_token);return result}
 async function login(view='login',message='',context=null){
  closeMenu();passwordDialog?.close();options.resetForLogin();editingKey=null;
  const state=context||await csrfContext();
  const registering=view==='register',claiming=view==='claim';
  const heading=registering?'注册账号':claiming?'认领或恢复账号':'登录';
  $('#main').innerHTML=`<div class="login account-login"><section class="login-form-side"><h2>${heading}</h2>${message?notice(esc(message)):''}${state.setup_required?notice('管理员还未初始化网页登录账号，请先在服务器完成账户设置。'):''}<form id="account-auth-form">${registering?field('显示名称','auth-display','text','','required maxlength="60"'):''}${claiming?field('管理员提供的一次性认领 / 恢复码','auth-code','password','','required autocomplete="off"'):''}${field('用户名','auth-username','text','','required minlength="3" maxlength="40" autocomplete="username" spellcheck="false"')}${field(registering||claiming?'设置密码 · 至少6位':'密码','auth-password','password','','required maxlength="128" autocomplete="'+(registering||claiming?'new-password':'current-password')+'"')}${registering||claiming?field('再次输入密码','auth-confirm','password','','required maxlength="128" autocomplete="new-password"'):''}${registering?field('邀请码 · 选填','auth-invitation','password','','maxlength="128" autocomplete="off"')+'<p class="field-help">有效邀请码可直接开通，不填则等待管理员审批</p>':''}<button class="primary" type="submit">${registering?'注册账号':claiming?'完成设置':'登录'}</button><p id="auth-error" role="alert" class="field-help"></p></form><div class="auth-links"><button class="subtle" type="button" data-auth-view="login">账号登录</button><button class="subtle" type="button" data-auth-view="register">注册账号</button><button class="subtle" type="button" data-auth-view="claim">旧账号认领 / 密码恢复</button></div>${claiming?'<p class="field-help">恢复后需重新创建 API Key</p>':''}<div class="lab-wordmark login-lab-footer"><img src="/site-assets/lab-logo.png" alt="虚拟现实技术与系统全国重点实验室" width="2169" height="725"></div></section></div>`;
  options.tidyCopy?.();
  $('#main .lab-wordmark img')?.addEventListener('error',event=>{event.target.closest('.lab-wordmark').hidden=true});
  document.querySelectorAll('[data-auth-view]').forEach(button=>button.addEventListener('click',()=>login(button.dataset.authView)));
  $('#account-auth-form').addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{
   try{
    const body={username:$('#auth-username').value,password:$('#auth-password').value};
    if(registering||claiming){if(body.password!==$('#auth-confirm').value)throw new Error('两次密码输入不一致');if(registering){body.display_name=$('#auth-display').value;const invitation=$('#auth-invitation').value.trim();if(invitation)body.invitation_code=invitation}else body.code=$('#auth-code').value}
    const result=await api('/api/auth/'+(registering?'register':claiming?'claim':'login'),'POST',body);
    if(registering||claiming){await login('login',registering?(result.status==='approved'?'注册成功，账号已开通，请登录':'注册成功，请登录查看审批状态'):'账号设置成功，请使用新账密登录。');return}
    options.setCSRF(result.csrf_token);await options.onAuthenticated();
   }catch(error){$('#auth-error').textContent=error.message}
  })});
 }
 async function restore(){const state=await csrfContext();if(state.authenticated){try{await options.onAuthenticated();return}catch{/* Expired/disabled sessions return to the sign-in form. */}}await login('login','',state)}
 async function logout(){await api('/api/auth/logout','POST',{});options.setCSRF('');await login()}
 async function profile(){
  const data=await api('/api/account');
  return top('我的账户','','ACCOUNT')+
   panel('账号信息',`<div class="panel-body account-details"><dl><div><dt>显示名称</dt><dd>${esc(data.user.name)}</dd></div><div><dt>登录用户名</dt><dd>${esc(data.username)}</dd></div><div><dt>账号状态</dt><dd>${esc(labels[data.status]||data.status)}</dd></div></dl><button class="primary" type="button" id="open-password-change">修改账号密码</button><details class="rename-account"><summary>修改登录用户名</summary><form id="account-profile-form">${field('用户名','profile-username','text',data.username,'required minlength="3" maxlength="40" autocomplete="username"')}${field('当前密码','profile-current','password','','required autocomplete="current-password"')}<button class="secondary" type="submit">保存用户名</button></form></details></div>`);
 }
 async function keys(){
  editingKey=null;const [rows,catalog]=await Promise.all([api('/api/self/keys'),api('/api/model-catalog')]);
  const models=catalog.models.filter(model=>model.authorized);
  return top('我的 API Key','','MY API')+
   notice('完整 Key 仅在创建时显示，最多保留10个有效 Key')+
   panel('我的 Key',table(['名称 / 标识','模型','累计已结算','有效期 / 状态','操作'],rows.map(row=>[
    `<strong>${esc(row.name)}</strong><small class="mono">${esc(row.prefix)}</small>`,esc(JSON.parse(row.models).join(' / ')||'无'),`${num(row.spent)} token<small>${row.budget<0?'累计不限':'Key 上限 '+num(row.budget)}</small>`,`${date(row.expires)}<small>${row.revoked?'已吊销':row.expires<Date.now()/1000?'已过期':'有效'}</small>`,row.revoked?'—':`<button class="secondary" data-self-edit="${esc(row.id)}">调整</button> <button class="danger" data-self-revoke="${esc(row.id)}">吊销</button>`])))+
   panel('创建或调整 Key',`<form id="personal-key-form" class="panel-body"><span id="personal-key-data" hidden>${esc(JSON.stringify(rows))}</span>${keyFields()}${modelChecks('self',models,models.map(model=>model.id))}<div class="buttons"><button class="primary" id="personal-key-save" type="submit">创建 Key · 仅显示一次</button><button class="secondary" id="personal-key-new" type="button">切回新建</button></div><div id="personal-key-result" aria-live="polite"></div></form>`)+
   panel('客户端接入',`<div class="panel-body"><p>创建后可在调用验证页使用本次 Key，或复制到自己的客户端。</p><button class="secondary" data-go="connect">查看地址与协议 →</button></div>`);
 }
 async function invitations(){
  const rows=await api('/api/admin/invitations');
  return top('邀请码','','INVITATIONS')+panel('生成邀请码',`<div class="panel-body"><p>有效期24小时，每个码限注册一个普通成员，默认全部已登记模型、累计不限额，继续记录Token</p><button class="primary" type="button" id="create-invitation">生成邀请码</button><div id="invitation-result" aria-live="polite"></div></div>`)+panel('生成记录',table(['标识','生成时间','失效时间','状态'],rows.map(row=>[esc(row.prefix)+'…',date(row.created),date(row.expires),row.used_at?'已使用':row.expires<=Date.now()/1000?'已过期':'可使用'])));
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
   panel('调整累计额度',`<form id="member-budget-form" class="panel-body"><div class="field"><label for="budget-user">成员</label><select id="budget-user">${editable.map(row=>`<option value="${esc(row.id)}" data-budget="${row.budget}">${esc(row.name)}</option>`).join('')}</select></div>${quotaControls('memberbudget')}<p class="field-help">修改上限不会清空历史用量，不能把有限上限降到已用量之下</p><button class="secondary" type="submit">保存额度</button></form>`)+
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
  $('#create-invitation')?.addEventListener('click',event=>action(event.currentTarget,async()=>{const result=await api('/api/admin/invitations','POST',{});$('#invitation-result').innerHTML=`<div class="key-reveal"><strong>邀请码仅本次完整显示</strong><code>${esc(result.code)}</code><button type="button" class="secondary" id="copy-invitation">复制邀请码</button><p>有效至 ${date(result.expires)}，限使用一次</p></div>`;$('#copy-invitation').onclick=async()=>{try{await navigator.clipboard.writeText(result.code);toast('邀请码已复制')}catch{toast('请手动复制邀请码',true)}}}));
  $('#open-password-change')?.addEventListener('click',()=>password());
  $('#account-profile-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const body={username:$('#profile-username').value,current_password:$('#profile-current').value};const result=await api('/api/account/profile','POST',body);options.setCSRF(result.csrf_token);$('#profile-current').value='';toast('账密已保存，旧网页登录会话已撤销');await render()})});
  const personal=$('#personal-key-form');
  if(personal){
   const rows=JSON.parse($('#personal-key-data').textContent);
   const load=row=>{editingKey=row?.id||null;$('#self-name').value=row?.name||'我的客户端';$('#self-days').value=row?Math.max(1,Math.ceil((row.expires-Date.now()/1000)/86400)):365;$('#self-rpm').value=row?.rpm||60;$('#self-tpm').value=row?.tpm||1000000;$('#self-concurrency').value=1;$('#self-unlimited').checked=!row||row.budget<0;$('#self-budget').value=row&&row.budget>=0?row.budget:1000000;$('#self-unlimited').dispatchEvent(new Event('change'));const selected=row?JSON.parse(row.models):null;document.querySelectorAll('#self-models input[data-model]').forEach(input=>input.checked=selected?selected.includes(input.value):true);$('#personal-key-save').textContent=row?'保存 Key 设置':'创建 Key · 仅显示一次';$('#personal-key-result').textContent=''};
   document.querySelectorAll('[data-self-edit]').forEach(button=>button.addEventListener('click',()=>{load(rows.find(row=>row.id===button.dataset.selfEdit));document.querySelectorAll('.workspace-tabs button').forEach(tab=>{if(tab.textContent==='创建或调整 Key')tab.click()})}));
   $('#personal-key-new').addEventListener('click',()=>load(null));
   document.querySelectorAll('[data-self-revoke]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{await api('/api/self/keys/'+button.dataset.selfRevoke,'DELETE');toast('Key 已吊销，网页账号继续可用');await render()})));
   personal.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const body={name:$('#self-name').value,models:selectedModels('self'),budget:selectedBudget('self'),days:Number($('#self-days').value),rpm:Number($('#self-rpm').value),tpm:Number($('#self-tpm').value),concurrency:1};const result=await api('/api/self/keys'+(editingKey?'/'+editingKey:''),editingKey?'PATCH':'POST',body);if(result.key){options.setCallKey(result.key);await render();$('#personal-key-result').innerHTML=`<div class="key-reveal"><strong>完整 Key 仅显示一次</strong><code>${esc(result.key)}</code><button class="secondary" type="button" id="copy-created-key">复制 Key</button><p>请保存此 Key</p></div>`;$('#copy-created-key').onclick=async()=>{try{await navigator.clipboard.writeText(result.key);toast('Key 已复制')}catch{toast('请手动复制 Key',true)}}}else {toast('Key 设置已保存');await render()}})});
  }
  $('#registration-approve-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{await api('/api/admin/registrations/'+$('#registration-user').value+'/approve','POST',{models:selectedModels('approval'),budget:selectedBudget('approval')});toast('账号已批准');await render()})});
  const budget=$('#budget-user');
  if(budget){const update=()=>{const value=Number(budget.selectedOptions[0]?.dataset.budget??-1);$('#memberbudget-unlimited').checked=value<0;$('#memberbudget-budget').value=value<0?1000000:value;$('#memberbudget-unlimited').dispatchEvent(new Event('change'))};budget.addEventListener('change',update);update()}
  $('#member-budget-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{await api('/api/users/'+$('#budget-user').value+'/budget','PATCH',{budget:selectedBudget('memberbudget')});toast('额度已更新，历史用量保留');await render()})});
  $('#account-token-form')?.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const result=await api('/api/admin/accounts/'+$('#token-user').value+'/token','POST',{purpose:$('#token-purpose').value,current_password:$('#token-password').value});$('#token-password').value='';$('#account-token-result').innerHTML=`<div class="key-reveal"><strong>一次性码仅本次显示</strong><code>${esc(result.code)}</code><p>请成员在登录页选择“旧账号认领 / 密码恢复”。</p></div>`})});
  if($('#token-user')){const update=()=>{$('#token-purpose').value=$('#token-user').selectedOptions[0]?.dataset.login==='1'?'reset':'enroll'};$('#token-user').addEventListener('change',update);update()}
 }
 $('#account-profile')?.addEventListener('click',()=>{closeMenu();navigate('account')});
 $('#account-password')?.addEventListener('click',()=>password());
 return {login,restore,logout,profile,keys,registrations,members,keyAudit,bind,menu,password,invitations};
}
