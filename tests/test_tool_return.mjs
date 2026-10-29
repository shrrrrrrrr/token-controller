import assert from 'node:assert/strict';
import fs from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../gateway/static/accounts.js',import.meta.url),'utf8');
const {toolReturnPath}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('tool return paths are same-site registered-shape roots only',()=>{
 assert.equal(toolReturnPath('?next=%2Ftools%2Fcase-tool%2F'),'/tools/case-tool/');
 for(const value of ['https://other.example.test/','//other.example.test/','/tools/../','/tools/case-tool/api/jobs','/tools/case-tool/?x=1','/tools/case-tool/#x','/tools/%252e%252e/']){
  assert.equal(toolReturnPath('?next='+encodeURIComponent(value)),null);
 }
 assert.equal(toolReturnPath(''),null);
});
test('tool requests stay on the tool prefix and never send CSRF to another origin',async()=>{
 const calls=[];
 const window={fetch:async(url,options={})=>{
  if(url==='/api/auth/context')return {json:async()=>({authenticated:true,csrf_token:'synthetic-csrf'})};
  calls.push({url:new URL(url),options});return {status:200,ok:true};
 },open(){},top:{location:{assign(){}}}};
 const document={currentScript:{src:'https://models.example.test/tools/case-tool/__bridge.js'},documentElement:{},addEventListener(){}};
 const context={window,document,URL,Request,Headers,Element:class{},MutationObserver:class{observe(){}},location:new URL('https://models.example.test/tools/case-tool/')};
 vm.runInNewContext(fs.readFileSync(new URL('../gateway/static/tool_bridge.js',import.meta.url),'utf8'),context);
 await window.fetch('/api/jobs',{method:'POST',body:'{}'});
 assert.equal(calls[0].url.pathname,'/tools/case-tool/api/jobs');
 assert.equal(calls[0].options.headers.get('X-CSRF-Token'),'synthetic-csrf');
 await assert.rejects(()=>window.fetch('https://external.example.test/api/jobs',{method:'POST',body:'{}'}));
 assert.equal(calls.length,1);
});
