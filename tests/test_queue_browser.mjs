import {createRequire} from 'node:module';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const require=createRequire(import.meta.url);
let chromium;try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'))}catch{/* Explicit skip. */}
const root=fileURLToPath(new URL('../',import.meta.url));
test('queue: members see only themselves and count ahead; admin sees owners and cancels',{skip:!chromium,timeout:120000},async()=>{
 const python=process.env.ACCOUNTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const child=spawn(python,['-u','tests/serve_console_fixture.py','--queue-fixture'],{cwd:root,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser;
 try{
  const port=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('fixture timeout')),20000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code))});child.stdout.on('data',b=>{out+=b;try{const value=JSON.parse(out.split('\n')[0]);clearTimeout(timer);resolve(value.port)}catch{}})});
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{})});
  const admin=await(await browser.newContext({viewport:{width:1440,height:960}})).newPage();
  const member=await(await browser.newContext({viewport:{width:1440,height:960}})).newPage();
  const errors=[];for(const page of [admin,member])page.on('pageerror',e=>errors.push(e.message));
  async function login(page,user){await page.goto('http://127.0.0.1:'+port);await page.locator('#auth-username').fill(user);await page.locator('#auth-password').fill('Synthetic fixture password 2026');await page.locator('#account-auth-form button[type="submit"]').click();await page.locator('#main[data-page="overview"]').waitFor();await page.locator('#message-later').click();await page.locator('#queue-meter').click();await page.locator('#job-list').waitFor()}
  await login(member,'fixture-member');await member.locator('#job-list').filter({hasText:'1 人'}).waitFor();
  assert.equal(await member.locator('#job-list tbody tr').count(),1);
  assert.equal((await member.locator('#job-list').textContent()).includes('Other queue member'),false);
  const data=await member.evaluate(async()=>await(await fetch('/api/jobs')).json());
  assert.equal(data.jobs.length,1);assert.equal(data.jobs[0].people_ahead,1);assert.equal('owner_name' in data.jobs[0],false);assert.equal('user_id' in data.jobs[0],false);
  if(process.env.BROWSER_SCREENSHOT_DIR){fs.mkdirSync(process.env.BROWSER_SCREENSHOT_DIR,{recursive:true});await member.screenshot({path:path.join(process.env.BROWSER_SCREENSHOT_DIR,'queue-member.png'),fullPage:true})}
  await login(admin,'fixture-admin');assert.equal(await admin.locator('#job-list tbody tr').count(),5);await admin.locator('#job-list').filter({hasText:'Other queue member 0'}).waitFor();
  if(process.env.BROWSER_SCREENSHOT_DIR)await admin.screenshot({path:path.join(process.env.BROWSER_SCREENSHOT_DIR,'queue-admin.png'),fullPage:true});
  await admin.locator('#job-list tbody tr').filter({hasText:'Other queue member 0'}).getByRole('button',{name:'取消任务'}).click();
  await admin.locator('#job-list tbody tr').filter({hasText:'Other queue member 0'}).filter({hasText:'已取消'}).waitFor();
  await member.setViewportSize({width:390,height:844});assert.equal(await member.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(process.env.BROWSER_SCREENSHOT_DIR)await member.screenshot({path:path.join(process.env.BROWSER_SCREENSHOT_DIR,'queue-mobile.png'),fullPage:true});
  await member.locator('#job-list [data-cancel-work]').click();await member.locator('#job-list').filter({hasText:'已取消'}).waitFor();
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();child.kill()}
});
