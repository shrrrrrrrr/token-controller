// Real Chromium and the actual backend. Disposable accounts and simulated models.
import {createRequire} from 'node:module';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let chromium;try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip without installed browser. */}
const root=fileURLToPath(new URL('../',import.meta.url));
test('BUAA console: account icon, two-step password, logout, responsive sign-in',{skip:!chromium,timeout:120000},async()=>{
 const python=process.env.ACCOUNTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const child=spawn(python,['-u','tests/serve_console_fixture.py'],{cwd:root,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,stderr='';child.stderr.on('data',b=>stderr+=b);
 try{
  const port=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('fixture startup timeout')),20000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code))});child.stdout.on('data',b=>{out+=b;try{const d=JSON.parse(out.split('\n')[0]);clearTimeout(timer);resolve(d.port)}catch{}})});
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:960}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const password='Synthetic fixture password 2026',next='Synthetic replacement password 2027';
  await page.goto('http://127.0.0.1:'+port);await page.locator('#auth-username').waitFor();
  assert.equal(await page.locator('.login-brand').isVisible(),true);
  assert.equal(await page.locator('.login-brand').getAttribute('href'),null);
  assert.equal(await page.locator('.login-story').count(),0);
  assert.equal(await page.locator('.login-form-side > .login-lab-footer img').count(),1);
  assert.match(await page.evaluate(()=>getComputedStyle(document.body,'::before').backgroundImage),/campus\.jpg/);
  assert.equal(await page.locator('.signed-out .sidebar').isVisible(),false);
  for(const [width,height] of [[390,844],[360,640],[1024,600]]){
   await page.setViewportSize({width,height});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.locator('#auth-password').scrollIntoViewIfNeeded();assert.equal(await page.locator('#account-auth-form button[type="submit"]').isVisible(),true);
  }
  await page.setViewportSize({width:1440,height:960});
  await page.locator('#auth-username').fill('fixture-member');await page.locator('#auth-password').fill(password);await page.locator('#account-auth-form button[type="submit"]').click();
  await page.locator('#main[data-page="overview"]').waitFor();await page.locator('#message-later').click();
  assert.equal(await page.locator('.sidebar #logout').count(),0);
  assert.equal(await page.locator('#refresh').count(),0);
  assert.equal(await page.locator('.model-mini-list .model-vendor-icon img').count(),4);
  await page.locator('#nav [data-page="play"]').click();await page.locator('#main[data-page="play"]').waitFor();
  await page.evaluate(()=>{window.__brandNavigationMarker=true;window.__brandLoginFlash=false;new MutationObserver(()=>{if(document.body.classList.contains('signed-out'))window.__brandLoginFlash=true}).observe(document.body,{attributes:true,attributeFilter:['class']})});
  let documents=0;const onDocument=request=>{if(request.resourceType()==='document')documents++};page.on('request',onDocument);
  await page.locator('#workspace-home').click();await page.locator('#main[data-page="overview"]').waitFor();
  assert.equal(documents,0);assert.equal(await page.evaluate(()=>window.__brandNavigationMarker&&!window.__brandLoginFlash),true);page.off('request',onDocument);
  const icons=await page.evaluate(()=>['#identity','#open-notifications'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [r.width,r.height]}));assert.deepEqual(icons[0],icons[1]);
  assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('.console-stat'),'::after').content),'none');
  assert.equal(await page.evaluate(()=>[...document.querySelectorAll('.console-stat')].every(card=>{const a=card.getBoundingClientRect(),b=card.querySelector('.stat-icon').getBoundingClientRect();return Math.abs(a.top+a.height/2-b.top-b.height/2)<1})),true);
  await page.locator('#identity').click();await page.locator('#account-summary').filter({hasText:'fixture-member'}).waitFor();
  await page.keyboard.press('Escape');assert.equal(await page.locator('#account-popover').isVisible(),false);
  await page.locator('#identity').click();await page.locator('#account-password').click();await page.locator('#password-current').fill('wrong synthetic password');await page.locator('#password-submit').click();
  await page.locator('#password-error').filter({hasText:'原密码不正确'}).waitFor();assert.equal(await page.locator('#password-new').isVisible(),false);
  await page.locator('#password-current').fill(password);await page.locator('#password-submit').click();await page.locator('#password-new').waitFor({state:'visible'});
  assert.equal(await page.locator('#password-current').inputValue(),'');await page.locator('#password-new').fill(next);await page.locator('#password-confirm').fill('mismatched synthetic password');await page.locator('#password-submit').click();
  await page.locator('#password-error').filter({hasText:'两次密码输入不一致'}).waitFor();await page.locator('#password-confirm').fill(next);await page.locator('#password-submit').click();await page.locator('.password-dialog').waitFor({state:'detached'});
  await page.locator('#identity').click();await page.locator('#account-profile').click();await page.locator('#main[data-page="account"]').waitFor();await page.locator('#open-password-change').click();await page.locator('#password-close').click();
  await page.locator('#identity').click();await page.locator('#logout').click();await page.locator('#auth-username').fill('fixture-member');await page.locator('#auth-password').fill(next);await page.locator('#account-auth-form button[type="submit"]').click();await page.locator('#main[data-page="overview"]').waitFor();
  const summary=await page.evaluate(async()=>await(await fetch('/api/console')).json());assert.equal(summary.quota.unlimited,true);assert.equal(summary.totals.requests,0);
  await page.setViewportSize({width:390,height:844});await page.locator('#identity').click();await page.locator('#account-password').click();assert.equal(await page.locator('#password-current').isVisible(),true);await page.keyboard.press('Escape');await page.locator('.password-dialog').waitFor({state:'detached'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 }finally{await browser?.close();child.kill()}
});
