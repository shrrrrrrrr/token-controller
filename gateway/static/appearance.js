// Public appearance state is shared by every visitor; only admins can change it.
export function createAppearanceUI(o){
 const {api,esc,top,toast,action,render}=o;
 let started=false,busy=false,lastURL='',generation=0,revision=-1;
 const validURL=url=>url==='/site-assets/campus.jpg'||/^\/background-assets\/[a-f0-9]{64}$/.test(url);
 async function apply(data){
  const url=data?.background?.url;
  if(!validURL(url)||!Number.isInteger(data.revision)||data.revision<revision)return;
  revision=data.revision;
  if(url===lastURL)return;
  const current=++generation;
  try{
   await new Promise((resolve,reject)=>{const image=new Image();const timer=setTimeout(()=>{image.onload=image.onerror=null;reject(Error('image timeout'))},12000);image.onload=()=>{clearTimeout(timer);resolve()};image.onerror=()=>{clearTimeout(timer);reject(Error('image unavailable'))};image.src=url});
   if(current!==generation)return;
   document.documentElement.style.setProperty('--campus-background',`url("${url}")`);
   document.documentElement.dataset.backgroundId=data.background.id||'default';lastURL=url;
  }catch{/* Keep the last visible background when a media request fails. */}
 }
 async function sync(){
  if(busy||document.hidden)return;
  busy=true;
  try{const response=await fetch('/api/appearance',{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(4000)});if(response.ok)await apply(await response.json())}catch{/* A background failure must never block login or clear a form. */}finally{busy=false}
 }
 function start(){if(started)return;started=true;void sync();setInterval(()=>void sync(),10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)void sync()});window.addEventListener('focus',()=>void sync())}
 async function view(){
  const data=await api('/api/admin/appearance');
  return top('背景设置','','APPEARANCE')+`<section class="panel"><div class="panel-body"><p class="appearance-caption">保存后应用到登录页和所有成员，已打开的页面会自动同步</p>${data.images.length?`<form id="appearance-form" data-revision="${data.revision}" data-current="${esc(data.background.id)}"><div class="background-grid">${data.images.map(image=>`<label class="background-choice"><input type="radio" name="background" value="${esc(image.id)}" ${image.id===data.background.id?'checked':''}><img src="${esc(image.thumbnail_url||image.url)}" width="320" height="180" alt="${esc(image.label)}" loading="lazy" decoding="async"><span><strong>${esc(image.label)}</strong>${image.id===data.background.id?'<small class="badge good">当前全站背景</small>':''}</span></label>`).join('')}</div><div class="appearance-actions"><button class="primary" type="submit" disabled>应用到全站</button><span id="appearance-selection" role="status">请选择背景</span></div></form>`:'<p>暂无可选背景，请先在服务器登记图片</p>'}</div></section>`;
 }
 function bind(){
  const form=document.querySelector('#appearance-form');if(!form)return;
  const submit=form.querySelector('button[type="submit"]');
  form.addEventListener('change',()=>{const selected=form.querySelector('input:checked');submit.disabled=!selected||selected.value===form.dataset.current;document.querySelector('#appearance-selection').textContent=submit.disabled?'当前背景已生效':'保存后对所有成员生效'});
  form.addEventListener('submit',event=>{event.preventDefault();action(event.submitter,async()=>{const selected=form.querySelector('input:checked');if(!selected)return;const result=await api('/api/admin/appearance','POST',{background_id:selected.value,revision:Number(form.dataset.revision)});void apply(result);toast('全站背景已更新');await render()})});
 }
 return {start,view,bind};
}
