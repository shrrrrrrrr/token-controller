// Queue polling changes status cells only; other page drafts remain untouched.
export function createJobsUI(o){
 const {api,esc,num,date,panel,table,top,action,toast,navigate}=o;
 let poll=null,busy=false,generation=0;
 const labels={queued:'排队中',waiting_model:'等待模型',running:'正在运行',completed:'已完成',failed:'失败',cancelled:'已取消',expired:'租约已到期',reconciliation:'等待对账',cancelling:'正在取消',preparing:'准备派发'};
 const shortId=value=>`<small class="mono" title="${esc(value)}">${esc(value.slice(0,10)+'…'+value.slice(-6))}</small>`;
 function queueTable(headers,rows){return `<div class="queue-table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map((cell,i)=>`<td data-label="${esc(headers[i])}"><div class="queue-cell">${cell}</div></td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}"><div class="empty">暂无任务</div></td></tr>`}</tbody></table></div>`}
 function meter(d){return `任务 ${num(d.active_tasks)}/${d.task_limit} · 推理 ${num(d.inferences)}/${d.inference_limit} · 等待 ${num(d.waiting)}`}
 function contents(d){
  const admin=o.isAdmin();
  return `<div class="queue-summary"><strong>${meter(d)}</strong><span class="badge ${d.source==='simulation'?'neutral':'good'}">${d.source==='simulation'?'本地模拟':'服务端调度'}</span></div>`+
   queueTable([...(admin?['成员']:[]),'任务','状态','前方排队人数','时间','操作'],d.jobs.map(j=>[
    ...(admin?[`${esc(j.owner_name)}${shortId(j.user_id)}`]:[]),
    `<strong>${esc(j.label)}</strong><small>${esc(j.kind==='simcourt'?'SimCourt 庭审':j.kind==='harness'?'Harness 工作流':'API 请求')} · ${esc(j.model)}</small>${shortId(j.id)}`,
    `<span class="badge ${j.status==='running'?'good':j.status==='reconciliation'||j.status==='failed'?'bad':'neutral'}">${esc(labels[j.status]||j.status)}</span>${j.error?`<small>${esc(j.error)}</small>`:''}<small>${j.calls.map(c=>esc(labels[c.status]||c.status)).join(' / ')}</small>`,
    ['queued','waiting_model'].includes(j.status)?num(j.people_ahead)+' 人':'—',
    `${date(j.created)}${j.finished?'<small>结束 '+date(j.finished)+'</small>':''}`,
    ['queued','waiting_model','running'].includes(j.status)?`<button type="button" class="secondary" data-cancel-work="${esc(j.id)}">取消任务</button>`:'—'
   ]));
 }
 async function view(){const data=await api('/api/jobs');return top(o.isAdmin()?'任务与调度':'我的任务','','TASK QUEUE')+panel('统一任务队列',`<div id="job-list" class="panel-body">${contents(data)}</div>`)+`<p class="field-help">SimCourt 与 Harness 共用任务名额，空闲网页不占用名额；排队不冻结额度，客户端任务需保持 Harness 后台在线</p><button type="button" class="subtle" data-go="tasklegacy">历史调用与视频记录</button>`}
 function bind(){document.querySelectorAll('[data-cancel-work]').forEach(button=>{button.onclick=()=>action(button,async()=>{await api('/api/jobs/'+button.dataset.cancelWork+'/cancel','POST',{});toast('取消请求已登记');await update()})})}
 async function update(){if(busy||document.hidden||document.body.classList.contains('signed-out'))return;busy=true;const version=generation;try{const d=await api('/api/jobs');if(version!==generation)return;const el=document.querySelector('#queue-meter');if(el)el.textContent=meter(d);const list=document.querySelector('#job-list');if(list&&!list.contains(document.activeElement)){list.innerHTML=contents(d);bind()}}catch{/* Keep the last observation; never replay a submission. */}finally{busy=false}}
 function start(){clearInterval(poll);generation++;if(o.canMonitor&&!o.canMonitor())return;let el=document.querySelector('#queue-meter');if(!el){el=document.createElement('button');el.type='button';el.id='queue-meter';el.className='subtle queue-meter';el.setAttribute('aria-label','查看任务队列');el.onclick=()=>navigate('tasks');document.querySelector('.header-end')?.prepend(el)}void update();poll=setInterval(()=>void update(),3000)}
 function reset(){generation++;clearInterval(poll);document.querySelector('#queue-meter')?.remove()}
 return {view,bind,start,reset};
}
