// Optional real Chromium regression. PLAYWRIGHT_MODULE may name an installed module.
// The server and all API data are synthetic and bound to loopback only.
import {createRequire} from 'node:module';
import {test} from 'node:test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip when no browser runtime is installed. */}

test('member drafts survive clicks; defaults and key grants submit once without request storms', {skip:!chromium}, async()=>{
 const root=fileURLToPath(new URL('../gateway/static/',import.meta.url));
 const server=http.createServer((req,res)=>{
  const name=req.url==='/'?'index.html':req.url.split('?')[0].slice(1);
  const file=path.resolve(root,name);
  if(!file.startsWith(root)){res.writeHead(404);res.end();return}
  try{res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file))}catch{res.writeHead(404);res.end()}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try{
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const calls=[],writes=[];
  const model={id:'research-text',display_name:'合成研究模型',kind:'text',status:'ready',deployment_status:'offline',authorized:true,callable:false,description:'测试目录'};
  const members=[{id:'member',name:'合成成员',role:'caller',active:1,models:'["research-text"]',profiles:'[]',budget:10000,spent:0,reserved:0}];
  const identity={mode:'cloud-api',text_source:'cloud_loopback_openai',user:{id:'admin',name:'合成管理员',role:'admin',budget:10000,spent:0,reserved:0},key:{id:'admin-key'}};
  const guide={version:'0.6.1',role:'admin',role_name:'平台管理员',needs_attention:false,allowed_pages:['overview','users','guide'],models:[],steps:[],changes:[]};
  await page.route('**/health',route=>route.fulfill({json:{status:'ok',mode:'cloud-api'}}));
  await page.route('**/api/**',route=>{
   const endpoint=new URL(route.request().url()).pathname;calls.push(endpoint);
   if(route.request().method()==='POST'){
    const body=route.request().postDataJSON();writes.push({endpoint,body});
    if(endpoint==='/api/users'){members.push({...members[0],id:'created',name:body.name,models:JSON.stringify(body.models),budget:body.budget});return route.fulfill({status:201,json:{id:'created'}})}
    if(endpoint==='/api/keys')return route.fulfill({status:201,json:{id:'created-key',key:'fixture-new-member-key'}});
   }
   const responses={'/api/me':identity,'/api/guide':guide,'/api/model-catalog':{models:[model]},'/api/users':members,'/api/keys':[], '/api/notifications':{items:[],unread:0}};
   return route.fulfill({json:responses[endpoint]??{}});
  });
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.locator('#login-key').fill('synthetic-admin-key-only');
  await page.locator('#login-form button').click();
  await page.locator('#nav [data-page="users"]').click();
  await page.getByRole('tab',{name:'创建用户',exact:true}).click();
  await page.waitForTimeout(100);
  const before=calls.length;
  await page.locator('#user-name').click();
  await page.keyboard.type('alice',{delay:20});
  await page.keyboard.insertText('测试');
  await page.waitForTimeout(150);
  assert.equal(await page.locator('#user-name').inputValue(),'alice测试');
  assert.equal(calls.length,before,'Editing must not call /api/me or rerender the page');
  assert.equal(await page.locator('#user-unlimited').isChecked(),true);
  assert.equal(await page.locator('#user-model-0').isChecked(),true);
  assert.equal(await page.locator('#user-budget').isDisabled(),true);
  for(const viewport of [{width:1440,height:1000},{width:1366,height:768},{width:390,height:844}]){
   await page.setViewportSize(viewport);
   assert.equal(await page.locator('#user-name').inputValue(),'alice测试');
   assert.equal(await page.locator('#user-form button[type="submit"]').isVisible(),true);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
   if(viewport.width>=1000)assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=window.innerHeight+1));
   if(process.env.BROWSER_SCREENSHOT_DIR){fs.mkdirSync(process.env.BROWSER_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.BROWSER_SCREENSHOT_DIR,`membership-${viewport.width}.png`),fullPage:true})}
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#user-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('#user-name')?.value==='');
  assert.equal(writes.filter(row=>row.endpoint==='/api/users').length,1);
  assert.deepEqual(writes[0].body,{name:'alice测试',role:'caller',models:['research-text'],profiles:[],budget:-1});
  await page.getByRole('tab',{name:'发放独立 API Key',exact:true}).click();
  await page.locator('#key-user').selectOption('created');
  assert.equal(await page.locator('#key-model-0').isChecked(),true);
  await page.locator('#key-form button[type="submit"]').click();
  await page.getByText('fixture-new-member-key',{exact:true}).waitFor();
  const issued=writes.find(row=>row.endpoint==='/api/keys').body;
  assert.deepEqual(issued,{user_id:'created',models:['research-text'],budget:-1,days:365,rpm:60,tpm:1000000,concurrency:1});
  assert.equal(writes.filter(row=>row.endpoint==='/api/keys').length,1);
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
});
