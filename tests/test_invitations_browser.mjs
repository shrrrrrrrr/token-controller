// Actual registration UI and backend with disposable accounts only.
import {createRequire} from 'node:module';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let chromium;try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip without browser. */}
const root=fileURLToPath(new URL('../',import.meta.url));
test('invitations: admin generates and copies; invited member skips approval; reuse fails',{skip:!chromium,timeout:120000},async()=>{
 const python=process.env.ACCOUNTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const child=spawn(python,['-u','tests/serve_console_fixture.py'],{cwd:root,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,stderr='';child.stderr.on('data',b=>stderr+=b);
 try{
  const port=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('fixture timeout')),20000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code))});child.stdout.on('data',b=>{out+=b;try{const data=JSON.parse(out.split('\n')[0]);clearTimeout(timer);resolve(data.port)}catch{}})});
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{})});
  const admin=await(await browser.newContext({permissions:['clipboard-read','clipboard-write']})).newPage();
  const member=await(await browser.newContext({viewport:{width:390,height:844}})).newPage();
  const errors=[];admin.on('pageerror',e=>errors.push(e.message));member.on('pageerror',e=>errors.push(e.message));
  const origin='http://127.0.0.1:'+port,password='Synthetic fixture password 2026';
  await admin.goto(origin);await admin.locator('#auth-username').fill('fixture-admin');await admin.locator('#auth-password').fill(password);await admin.locator('#account-auth-form button[type="submit"]').click();await admin.locator('#main[data-page="overview"]').waitFor();await admin.locator('#message-later').click();
  const link=admin.locator('#nav [data-page="invitations"]');await link.locator('xpath=ancestor::details[1]').locator('summary').click();await link.click();await admin.locator('#create-invitation').click();await admin.locator('#copy-invitation').click();
  const code=await admin.evaluate(()=>navigator.clipboard.readText());assert.match(code,/^join_/);
  await member.goto(origin);await member.locator('[data-auth-view="register"]').click();await member.locator('#auth-display').fill('Synthetic invited member');await member.locator('#auth-username').fill('new-invited-member');await member.locator('#auth-password').fill(password);await member.locator('#auth-confirm').fill(password);await member.locator('#auth-invitation').fill(code);
  assert.equal(await member.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await member.locator('#account-auth-form button[type="submit"]').click();await member.locator('#auth-display').waitFor({state:'detached'});await member.locator('#auth-username').fill('new-invited-member');await member.locator('#auth-password').fill(password);await member.locator('#account-auth-form button[type="submit"]').click();await member.locator('#main[data-page="overview"]').waitFor();await member.locator('#message-later').click();
  const info=await member.evaluate(async()=>await(await fetch('/api/account')).json());assert.equal(info.status,'approved');assert.equal(info.user.role,'caller');assert.equal(info.user.budget,-1);assert.equal(JSON.parse(info.user.models).length,4);assert.equal(await member.locator('#nav [data-page="invitations"]').count(),0);
  await member.locator('#identity').click();await member.locator('#logout').click();await member.locator('[data-auth-view="register"]').click();await member.locator('#auth-display').fill('Synthetic replay');await member.locator('#auth-username').fill('replay-member');await member.locator('#auth-password').fill(password);await member.locator('#auth-confirm').fill(password);await member.locator('#auth-invitation').fill(code);await member.locator('#account-auth-form button[type="submit"]').click();await member.locator('#auth-error').filter({hasText:'邀请码无效'}).waitFor();
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();child.kill()}
});
