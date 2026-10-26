import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createMessagesUI} from '../gateway/static/messages.js';

test('notification network failure does not invalidate an otherwise successful login',async()=>{
 const warnings=[];
 const messages=createMessagesUI({api:async()=>{throw new Error('Synthetic outage')},toast:(...args)=>warnings.push(args),setCount(){}});
 await assert.doesNotReject(messages.loginPrompts({first_visit:false}));
 assert.equal(warnings.length,1);
});

test('a guide already claimed by another tab is not automatically presented again',async()=>{
 const paths=[];
 const messages=createMessagesUI({api:async path=>{paths.push(path);return path==='/api/guide/seen'?{first_visit:false}:{unread:0,unread_announcements:[]}},setCount(){},toast(){throw new Error('Unexpected failure')}});
 await messages.loginPrompts({first_visit:true,steps:[]});
 assert.deepEqual(paths,['/api/notifications?limit=1','/api/guide/seen']);
});
