// Real browser, synthetic PNGs, actual backend; no personal photos or model calls.
import {createRequire} from 'node:module';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let chromium;try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip without browser. */}
const root=fileURLToPath(new URL('../',import.meta.url));
test('appearance: admin selection reaches member and login without clearing drafts',{skip:!chromium,timeout:120000},async()=>{
 const python=process.env.ACCOUNTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const child=spawn(python,['-u','tests/serve_console_fixture.py','--appearance-fixture'],{cwd:root,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,stderr='';child.stderr.on('data',b=>stderr+=b);
 try{
  const port=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('fixture timeout')),20000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code))});child.stdout.on('data',b=>{out+=b;try{const data=JSON.parse(out.split('\n')[0]);clearTimeout(timer);resolve(data.port)}catch{}})});
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{})});
  const admin=await(await browser.newContext({viewport:{width:1440,height:960}})).newPage();
  const member=await(await browser.newContext({viewport:{width:1440,height:960}})).newPage();
  const guest=await(await browser.newContext({viewport:{width:390,height:844}})).newPage();
  const errors=[];for(const page of [admin,member,guest])page.on('pageerror',e=>errors.push(e.message));
  const origin='http://127.0.0.1:'+port;
  const login=async(page,user)=>{await page.goto(origin);await page.locator('#auth-username').fill(user);await page.locator('#auth-password').fill('Synthetic fixture password 2026');await page.locator('#account-auth-form button[type="submit"]').click();await page.locator('#main[data-page="overview"]').waitFor();await page.locator('#message-later').click()};
  await login(admin,'fixture-admin');await login(member,'fixture-member');await guest.goto(origin);await guest.locator('#auth-username').waitFor();
  await member.locator('#nav [data-page="mykeys"]').click();await member.getByRole('tab',{name:'创建或调整 Key',exact:true}).click();await member.locator('#self-name').fill('preserve this draft');
  assert.equal(await member.locator('#nav [data-page="appearance"]').count(),0);
  const link=admin.locator('#nav [data-page="appearance"]');await link.locator('xpath=ancestor::details[1]').locator('summary').click();await link.click();await admin.locator('#appearance-form').waitFor();
  assert.equal(await admin.locator('.background-choice').count(),2);
  const selected=admin.locator('.background-choice input').nth(1);const id=await selected.inputValue();await selected.check();await admin.locator('#appearance-form button[type="submit"]').click();
  for(const page of [admin,member,guest]){await page.bringToFront();await page.waitForFunction(id=>document.documentElement.dataset.backgroundId===id,id,{timeout:18000})}
  assert.equal(await member.locator('#self-name').inputValue(),'preserve this draft');
  assert.match(await guest.evaluate(()=>getComputedStyle(document.body,'::before').backgroundImage),new RegExp(id));
  await guest.reload();await guest.waitForFunction(id=>document.documentElement.dataset.backgroundId===id,id);
  const opacity=await admin.evaluate(()=>['.sidebar','.app-header','footer','.panel'].map(selector=>getComputedStyle(document.querySelector(selector)).backgroundColor));
  for(const color of opacity)assert.match(color,/rgba\(.+, 0\.\d+\)/);
  const loginSurface=await guest.locator('.login').evaluate(node=>getComputedStyle(node).backgroundColor);assert.match(loginSurface,/, 0\.8\)/);
  const nav=admin.locator('#workspace-home');const before=await nav.boundingBox();await nav.hover();const after=await nav.boundingBox();assert.deepEqual(after,before);
  const motion=await nav.evaluate(node=>({transform:getComputedStyle(node).transform,transition:getComputedStyle(node).transitionDuration}));assert.equal(motion.transform,'none');assert.equal(motion.transition,'0s');
  await admin.setViewportSize({width:390,height:844});assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();child.kill()}
});
