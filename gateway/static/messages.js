// Plain-text announcements, account-scoped receipts and one-time onboarding.
export function createMessagesUI(options){
 const {api,$,esc,table,panel,top,notice,field,date,action,toast,render,navigate}=options;
 let editId=null,revision=null,offset=0,adminOffset=0,notices=new Map(),dialog=null,prompts=[],generation=0;
 const statusName={draft:'草稿',published:'已发布',withdrawn:'已撤回'};
 function reset(){generation++;prompts=[];notices.clear();offset=0;adminOffset=0;editId=null;revision=null;if(dialog){dialog.onclose=null;dialog.close();dialog.remove();dialog=null}}
 function showPrompt(item,after=()=>{}){
  if(dialog){dialog.onclose=null;dialog.close();dialog.remove()}
  dialog=document.createElement('dialog');dialog.id='login-dialog';dialog.className='login-dialog';
  const guide=item.kind==='guide';
  dialog.setAttribute('aria-labelledby','login-dialog-title');
  dialog.innerHTML=`<div class="message-dialog-head"><span class="eyebrow">${guide?'FIRST VISIT':'ANNOUNCEMENT'}</span><h2 id="login-dialog-title">${esc(item.title)}</h2><p class="field-help">${guide?'首次使用提示，以后可从顶部“使用引导”打开':date(item.created)+' · '+(item.kind==='announcement'?'管理员公告':'个人消息')+' · '+(item.read_at?'已读':'未读')}</p></div><div class="message-dialog-content">${guide?item.steps.map(step=>`<section><h3>${esc(step.title)}</h3><p>${esc(step.text)}</p></section>`).join(''):`<p class="message-body">${esc(item.body)}</p>`}</div><div class="message-dialog-actions"><button class="secondary" id="message-later">${guide?'关闭，进入工作台':'稍后查看'}</button>${guide?'<button class="primary" id="message-guide">打开完整引导</button>':'<button class="primary" id="message-read">已阅读</button>'}</div>`;
  document.body.append(dialog);options.tidyCopy?.();
  const current=dialog,epoch=generation;
  current.onclose=()=>{current.remove();if(dialog===current)dialog=null;if(epoch===generation)after()};
  current.querySelector('#message-later').onclick=()=>current.close();
  current.querySelector('#message-guide')?.addEventListener('click',()=>{prompts=[];current.close();void navigate('guide')});
  current.querySelector('#message-read')?.addEventListener('click',event=>action(event.currentTarget,async()=>{await api('/api/notifications/'+item.id+'/read','POST',{});if(epoch!==generation)return;current.close();void options.refreshBadge()}));
  current.showModal();
 }
 async function nextPrompt(){
  const item=prompts.shift(),epoch=generation;if(!item)return;
  try{
   if(item.kind==='guide'){const first=await api('/api/guide/seen','POST',{});if(epoch!==generation||!first.first_visit)return}
   showPrompt(item,nextPrompt);
  }catch{if(epoch===generation)toast('通知或引导暂时无法加载，可从顶部按钮查看',true)}
 }
 async function loginPrompts(guide){
  const epoch=generation;
  let data;
  try{data=await api('/api/notifications?limit=1')}catch{
   if(epoch!==generation)return;
   toast('通知暂时无法加载，可从顶部通知按钮重试',true);data={unread:0,unread_announcements:[]};
  }
  if(epoch!==generation)return;
  options.setCount(data.unread);
  prompts=[...(data.unread_announcements||[])];
  if(guide.first_visit)prompts.push({kind:'guide',title:'欢迎使用模型工作台',steps:guide.steps});
  await nextPrompt();
 }
 const inboxPath=()=>'/api/notifications?limit=40&offset='+offset;
 function rows(items){
  notices=new Map(items.map(item=>[item.id,item]));
  return table(['消息','内容摘要','时间','状态 / 操作'],items.map(item=>[
   `<strong>${esc(item.title)}</strong><small>${item.kind==='announcement'?'管理员公告':'个人消息'}</small>`,esc(item.body.length>80?item.body.slice(0,80)+'…':item.body),date(item.created),
   `${item.read_at?'<span class="badge neutral">已读</span>':'<span class="badge warn">未读</span>'} <button class="secondary" data-inbox-detail="${esc(item.id)}">查看</button>${item.read_at?'':` <button class="secondary" data-notice-read="${esc(item.id)}">标为已读</button>`}`]));
 }
 function summary(data){return `未读 ${data.unread} 条 · 历史共 ${data.total??data.items.length} 条 · 第 ${Math.floor(offset/40)+1} 页`}
 async function inbox(){
  let data=await api(inboxPath());
  if(offset&&offset>=(data.total||0)){offset=0;data=await api(inboxPath())}
  return top('通知中心','查看管理员公告与自己的预约消息，已读状态按账号保存','NOTIFICATIONS')+
   notice('登录优先提示未读公告；点击“已阅读”才标记。稍后查看或关闭不会标记为已读。历史公告和个人消息可在这里分页查看。')+
   panel('通知与公告',`<div class="live-toolbar"><span id="notification-summary">${summary(data)}</span><button class="secondary" id="notice-read-all">全部标为已读</button></div><div id="notification-live" class="live-list">${rows(data.items)}</div><div class="message-pagination"><button class="secondary" id="inbox-prev" ${offset?'':'disabled'}>上一页</button><button class="secondary" id="inbox-next" ${offset+40<(data.total||0)?'':'disabled'}>下一页</button></div>`);
 }
 async function admin(){
  const data=await api('/api/announcements?limit=50&offset='+adminOffset);
  editId=null;revision=null;
  return top('公告管理','保存草稿、编辑、发布与撤回；成员登录优先看到未读公告','ANNOUNCEMENTS')+
   notice('面向全部已批准成员。保存草稿不会改变已发布内容；再次发布产生独立通知及已读记录。撤回后历史正文对成员隐藏，草稿仍保留。请勿填写密码、Key 或敏感资料。')+
   panel('公告列表',table(['标题','状态','更新时间','操作'],data.items.map(row=>[esc(row.title),`${esc(statusName[row.status])}${row.status==='published'&&row.revision!==row.published_revision?'<small>有未发布修改</small>':''}`,date(row.updated),`<button class="secondary" data-ann-edit="${esc(row.id)}">编辑</button>`]))+`<div class="message-pagination"><span>共 ${data.total} 篇 · 第 ${Math.floor(adminOffset/50)+1} 页</span><button class="secondary" id="ann-prev" ${adminOffset?'':'disabled'}>上一页</button><button class="secondary" id="ann-next" ${adminOffset+50<data.total?'':'disabled'}>下一页</button></div>`)+
   panel('编辑与发布',`<form id="announcement-form" class="panel-body"><span id="announcement-data" hidden>${esc(JSON.stringify(data.items))}</span><p id="announcement-state" class="field-help">新建草稿</p>${field('公告标题','announcement-title','text','','required maxlength="100"')}<div class="field"><label for="announcement-body">正文 · 纯文本</label><textarea id="announcement-body" required maxlength="6000" rows="9"></textarea></div><div class="buttons"><button class="secondary" id="announcement-save" type="submit">保存草稿</button><button class="primary" id="announcement-publish" type="button">保存并发布</button><button class="secondary" id="announcement-new" type="button">新建另一篇</button><button class="danger" id="announcement-withdraw" type="button" disabled>撤回</button></div><p class="field-help spacer" id="announcement-result" aria-live="polite"></p></form>`);
 }
 function bindDetails(scope=document){
  scope.querySelectorAll('[data-inbox-detail]').forEach(button=>button.addEventListener('click',()=>{const item=notices.get(button.dataset.inboxDetail);if(item)showPrompt(item,()=>{void render()})}));
 }
 function bind(){
  $('#ann-prev')?.addEventListener('click',()=>{adminOffset=Math.max(0,adminOffset-50);void render()});
  $('#ann-next')?.addEventListener('click',()=>{adminOffset+=50;void render()});
  $('#inbox-prev')?.addEventListener('click',()=>{offset=Math.max(0,offset-40);void render()});
  $('#inbox-next')?.addEventListener('click',()=>{offset+=40;void render()});
  const form=$('#announcement-form');if(!form)return;
  const items=JSON.parse($('#announcement-data').textContent);
  const load=row=>{editId=row?.id||null;revision=row?.revision||null;$('#announcement-title').value=row?.title||'';$('#announcement-body').value=row?.body||'';$('#announcement-state').textContent=row?`${statusName[row.status]} · 草稿版本 ${row.revision}`:'新建草稿';$('#announcement-withdraw').disabled=row?.status!=='published';$('#announcement-result').textContent=''};
  document.querySelectorAll('[data-ann-edit]').forEach(button=>button.addEventListener('click',()=>{load(items.find(row=>row.id===button.dataset.annEdit));document.querySelectorAll('.workspace-tabs button').forEach(tab=>{if(tab.textContent==='编辑与发布')tab.click()})}));
  $('#announcement-new').onclick=()=>load(null);
  const save=async()=>{
   const body={title:$('#announcement-title').value,body:$('#announcement-body').value,...(editId?{expected_revision:revision}:{})};
   const result=await api('/api/announcements'+(editId?'/'+editId:''),editId?'PATCH':'POST',body);
   editId=result.id;revision=result.revision;$('#announcement-state').textContent='已保存 · 草稿版本 '+revision;
   return result;
  };
  form.onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{await save();$('#announcement-result').textContent='草稿已保存，尚未发布。';toast('草稿已保存')})};
  $('#announcement-publish').onclick=event=>{if(form.reportValidity())action(event.currentTarget,async()=>{await save();await api('/api/announcements/'+editId+'/publish','POST',{expected_revision:revision});toast('公告已发布');await render()})};
  $('#announcement-withdraw').onclick=event=>action(event.currentTarget,async()=>{await api('/api/announcements/'+editId+'/withdraw','POST',{expected_revision:revision});toast('公告已撤回');await render()});
 }
 return {reset,loginPrompts,rows,inbox,admin,bind,bindDetails,inboxPath,summary};
}
