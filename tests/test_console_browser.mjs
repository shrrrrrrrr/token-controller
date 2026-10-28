// Actual Chromium + actual backend, all data/model replies synthetic and loopback.
import {createRequire} from 'node:module';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let chromium;try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip when no browser is installed. */}
const root=fileURLToPath(new URL('../',import.meta.url));
test('console: responsive dashboard, actual metering, copied Key/config, model switch and placeholder',{skip:!chromium,timeout:120000},async()=>{
 const python=process.env.ACCOUNTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const child=spawn(python,['-u','tests/serve_console_fixture.py'],{cwd:root,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,stderr='';child.stderr.on('data',b=>stderr+=b);
 try{
  const port=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('fixture startup timeout')),20000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code))});child.stdout.on('data',b=>{out+=b;try{const d=JSON.parse(out.split('\n')[0]);clearTimeout(timer);resolve(d.port)}catch{}})});
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:960},permissions:['clipboard-read','clipboard-write']});
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const base='http://127.0.0.1:'+port;
  const login=async username=>{await page.goto(base);await page.locator('#auth-username').fill(username);await page.locator('#auth-password').fill('Synthetic fixture password 2026');await page.locator('#account-auth-form button[type="submit"]').click();await page.locator('#main[data-page="overview"]').waitFor();await page.locator('#message-later').click()};
  await login('fixture-admin');assert.equal(await page.locator('#refresh').count(),0);assert.equal(await page.locator('#nav > details,#nav > button').count(),6);
  const runtime=page.locator('#nav [data-page="runtime"]');await runtime.locator('xpath=ancestor::details[1]').locator('summary').click();await runtime.click();
  await page.locator('[data-switch-model="glm5.3-flash"]').click();await page.locator('.switch-banner').waitFor();
  await page.waitForTimeout(6500);assert.match(await page.locator('[data-model-state="glm5.3-flash"]').innerText(),/正在运行/);
  await page.locator('#identity').click();await page.locator('#logout').click();await login('fixture-member');
  assert.equal(await page.locator('#nav > button').count(),5);assert.equal(await page.locator('#open-notifications').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),false);
  await page.locator('#nav [data-page="mykeys"]').click();await page.getByRole('tab',{name:'创建或调整 Key',exact:true}).click();
  const submissions=[];page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/self/keys'))submissions.push(r.url())});
  await page.locator('#self-name').fill('Synthetic editor');await page.locator('#personal-key-save').click();await page.locator('#copy-created-key').waitFor();
  assert.equal(submissions.length,1);await page.locator('#copy-created-key').click();assert.match(await page.evaluate(()=>navigator.clipboard.readText()),/^lg_/);
  await page.locator('#open-api-quick').click();await page.locator('#copy-api-template').click();const cfg=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));
  assert.equal(cfg.base_url,base+'/v1');assert.equal(cfg.model,'glm5.3-flash');assert.match(cfg.api_key,/^lg_/);
  await page.locator('#close-api-quick').click();await page.locator('#nav [data-page="play"]').click();await page.locator('#main[data-page="play"]').waitFor();assert.equal(await page.locator('.model-card').count(),4);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),false);
  await page.getByRole('tab',{name:'调用测试',exact:true}).click();await page.locator('#test-prompt').fill('Synthetic request');await page.locator('#console-chat button[type="submit"]').click();
  await page.locator('#console-chat-result').filter({hasText:'20 Token'}).waitFor();
  const usage=await page.evaluate(async()=>await (await fetch('/api/usage')).json());
  assert.equal(usage.ledger.length,1);assert.equal(usage.ledger[0].event,'settle');assert.equal(usage.requests[0].reserved,0);
  await page.locator('#main [data-go="queue"]').click();await page.locator('#main[data-page="queue"]').waitFor();assert.match(await page.locator('#main').innerText(),/该功能暂未上线/);
  assert.equal(await page.locator('#reservation-form').count(),0);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('#open-notifications').click();await page.locator('#main[data-page="notifications"]').waitFor();assert.deepEqual(errors,[]);
 }finally{await browser?.close();child.kill();}
});
