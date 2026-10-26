// Optional real-browser test against the real backend with synthetic data only.
import {createRequire} from 'node:module';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip. */}
const root=fileURLToPath(new URL('../',import.meta.url));
const password='A synthetic browser passphrase 2026!';

test('real accounts: register, approve, login, self-managed Key, usage and grouped navigation', {skip:!chromium,timeout:120000},async()=>{
 const python=process.env.ACCOUNTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const child=spawn(python,['-u','tests/serve_accounts_fixture.py'],{cwd:root,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,stderr='';child.stderr.on('data',data=>stderr+=data);
 try{
  const port=await new Promise((resolve,reject)=>{
   let output='';const timer=setTimeout(()=>reject(new Error('Fixture startup timeout: '+stderr)),20000);
   child.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exit '+code+': '+stderr))});
   child.stdout.on('data',data=>{output+=data;const line=output.split('\n')[0];try{const parsed=JSON.parse(line);clearTimeout(timer);resolve(parsed.port)}catch{/* Await full line. */}});
  });
  browser=await chromium.launch({headless:true});
  const adminContext=await browser.newContext({viewport:{width:1440,height:1000}});
  const memberContext=await browser.newContext({viewport:{width:1440,height:1000}});
  const admin=await adminContext.newPage(),member=await memberContext.newPage();
  const errors=[];
  for(const page of [admin,member]){
   page.on('pageerror',error=>errors.push(error.message));
   page.on('response',response=>{if(response.status()>=400)console.error('BROWSER_HTTP',new URL(response.url()).pathname,response.status())});
  }
  const base='http://127.0.0.1:'+port;
  const signIn=async(page,username)=>{
   await page.locator('#auth-username').fill(username);await page.locator('#auth-password').fill(password);
   await page.locator('#account-auth-form button[type="submit"]').click();await page.locator('#nav button[data-page]').first().waitFor();
  };
  const go=async(page,id)=>{
   const button=page.locator('#nav [data-page="'+id+'"]');
   if(!await button.isVisible())await button.locator('xpath=ancestor::details[1]').locator('summary').click();
   await button.click();await page.locator('#main[data-page="'+id+'"]').waitFor();
  };
  await member.goto(base);
  await member.locator('[data-auth-view="register"]').click();
  await member.locator('#auth-display').fill('合成成员');await member.locator('#auth-username').fill('fixture-member');
  await member.locator('#auth-password').fill(password);await member.locator('#auth-confirm').fill(password);
  await member.locator('#account-auth-form button[type="submit"]').click();
  await member.getByText('注册成功。现在可以登录查看审批状态，管理员批准后可创建调用 Key。',{exact:true}).waitFor();
  await signIn(member,'fixture-member');await go(member,'account');
  assert.equal(await member.locator('#nav [data-page="mykeys"]').count(),0);
  await admin.goto(base);await signIn(admin,'fixture-admin');
  assert.equal(await admin.locator('#nav > details, #nav > button').count(),6);
  await go(admin,'registrations');await admin.getByRole('tab',{name:'批准并分配权限',exact:true}).click();
  assert.equal(await admin.locator('#approval-unlimited').isChecked(),true);
  assert.equal(await admin.locator('#approval-models input:checked').count(),2);
  await admin.locator('#registration-approve-form button[type="submit"]').click();
  await admin.getByText('账号已批准',{exact:true}).waitFor();
  await member.reload();await member.locator('#nav [data-page="mykeys"]').waitFor({state:'attached'});
  assert.equal(await member.locator('#nav > details, #nav > button').count(),5);
  await go(member,'mykeys');await member.getByRole('tab',{name:'创建或调整 Key',exact:true}).click();
  const requests=[];member.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))requests.push(request.url())});
  const before=requests.length;
  await member.locator('#self-name').fill('自己的编辑器');await member.waitForTimeout(100);
  assert.equal(requests.length,before,'Typing must preserve drafts without API requests');
  assert.equal(await member.locator('#self-unlimited').isChecked(),true);
  await member.locator('#personal-key-save').click();await member.locator('#personal-key-result code').waitFor();
  const key=await member.locator('#personal-key-result code').textContent();assert.ok(key.length>20);
  await go(member,'play');
  assert.equal(await member.locator('#play-key').inputValue(),key);
  await member.locator('#chat-prompt').fill('合成浏览器测试');await member.locator('#chat-form button[type="submit"]').click();
  await member.locator('#chat-result').filter({hasText:'输入'}).waitFor();
  await go(member,'usage');await member.locator('table tbody tr').first().waitFor();
  await go(member,'mykeys');assert.equal(await member.locator('#personal-key-result code').count(),0);
  await member.getByRole('tab',{name:'我的 Key',exact:true}).click();
  await member.locator('[data-self-edit]').first().click();assert.equal(await member.locator('#self-name').inputValue(),'自己的编辑器');
  await member.locator('#self-name').fill('调整后名称');await member.locator('#personal-key-save').click();
  await member.getByText('Key 设置已保存',{exact:true}).waitFor();
  for(const viewport of [{width:1440,height:1000},{width:1366,height:768},{width:390,height:844}]){
   await member.setViewportSize(viewport);
   assert.ok(await member.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   if(viewport.width>=1000)assert.ok(await member.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));
   if(process.env.BROWSER_SCREENSHOT_DIR){fs.mkdirSync(process.env.BROWSER_SCREENSHOT_DIR,{recursive:true});await member.screenshot({path:path.join(process.env.BROWSER_SCREENSHOT_DIR,`accounts-${viewport.width}.png`),fullPage:true})}
  }
  await member.setViewportSize({width:1440,height:1000});await go(member,'mykeys');
  await member.getByRole('tab',{name:'我的 Key',exact:true}).click();await member.locator('[data-self-revoke]').first().click();
  await member.getByText('Key 已吊销，网页账号继续可用',{exact:true}).waitFor();
  await member.locator('#logout').click();await member.locator('#account-auth-form').waitFor();
  assert.equal(await member.evaluate(()=>Object.keys(localStorage).length),0);
  assert.deepEqual(errors,[]);
 }finally{if(browser)await browser.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
});
