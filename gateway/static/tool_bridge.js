// Runs before the trusted tool's scripts. No calling Key or password is used.
(()=>{
 const prefix=new URL(document.currentScript.src).pathname.replace(/\/__bridge\.js$/,'');
 const nativeFetch=window.fetch.bind(window),nativeOpen=window.open.bind(window);
 const login='/?next='+encodeURIComponent(prefix+'/');
 const redirect=()=>{window.top.location.assign(login)};
 function route(value){
  const url=new URL(value,location.href);
  if(url.origin===location.origin&&!url.pathname.startsWith(prefix+'/')&&
     (url.pathname.startsWith('/api/')||url.pathname.startsWith('/static/'))){url.pathname=prefix+url.pathname}
  return url;
 }
 const ready=nativeFetch('/api/auth/context',{credentials:'same-origin',cache:'no-store'})
  .then(async response=>{const context=await response.json();if(!context.authenticated){redirect();throw new Error('请登录平台')}return context});
 // A rejected initial context must not become an unhandled promise while a
 // cached tool page finishes loading. Individual operations still fail closed.
 ready.catch(()=>{});
 window.fetch=async(input,options={})=>{
  const original=input instanceof Request?input:null;
  const url=route(original?original.url:input),method=(options.method||original?.method||'GET').toUpperCase();
  if(url.origin!==location.origin)throw new Error('工具请求地址不受支持');
  const headers=new Headers(original?.headers);new Headers(options.headers).forEach((value,name)=>headers.set(name,value));
  if(!['GET','HEAD','OPTIONS'].includes(method)){headers.set('X-CSRF-Token',(await ready).csrf_token)}
  const response=await nativeFetch(original?new Request(url,original):url,{...options,headers,credentials:'same-origin'});
  if(response.status===401)redirect();
  if(url.pathname===prefix+'/api/auth/logout'&&response.ok)window.top.location.assign('/');
  return response;
 };
 function rewrite(element){
  if(!(element instanceof Element))return;
  for(const name of ['href','src']){const value=element.getAttribute(name);if(value?.startsWith('/api/')||value?.startsWith('/static/'))element.setAttribute(name,route(value).href)}
 }
 new MutationObserver(records=>{for(const record of records){rewrite(record.target);for(const node of record.addedNodes){rewrite(node);node.querySelectorAll?.('[href],[src]').forEach(rewrite)}}})
  .observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['href','src']});
 document.addEventListener('click',event=>{const link=event.target.closest?.('a[href]');if(link)rewrite(link)},true);
 window.open=(url,...args)=>nativeOpen(url?route(url).href:url,...args);
 document.addEventListener('DOMContentLoaded',()=>{const link=document.createElement('a');link.href='/';link.className='platform-tool-return';link.textContent='← 返回模型工作台';document.body.prepend(link)},{once:true});
})();
