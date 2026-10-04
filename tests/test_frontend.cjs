const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const source = fs.readFileSync('frontend/app.js','utf8').split('applyProvider("icloud", true);')[0];
class Element {
  constructor() { this.listeners={};this.dataset={};this.innerHTML='';this.value='';this.disabled=false;this.classList={add(){},remove(){},toggle(){}}; }
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(){} removeAttribute(){} append(){} remove(){} focus(){}
  querySelector(){return new Element();} querySelectorAll(){return [];}
}
const config = {provider:'icloud',email:'agent@example.com',username:'agent@example.com',password_set:true,target_email:'user@example.com',smtp_host:'smtp.example.com',smtp_port:465,smtp_security:'ssl',imap_host:'imap.example.com',imap_port:993,imap_security:'ssl',imap_folder:'INBOX',poll_interval:10};
function harness(saved = new Map()) {
 const elements=new Map();
 const document={querySelector(selector){if(!elements.has(selector)) elements.set(selector,new Element()); return elements.get(selector);},querySelectorAll(){return [];},createElement(){return new Element();},documentElement:new Element(),body:new Element()};
 const context=vm.createContext({document,localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},matchMedia:()=>({matches:false}),location:{hash:''},addEventListener(){},setTimeout(){},setInterval(){},clearTimeout(){},crypto:{randomUUID},console,Intl,Date,URLSearchParams,Object,JSON,Number,String,Boolean,Error});
 vm.runInContext(source,context);
 vm.runInContext('state.config = '+JSON.stringify(config),context);
 return {context,elements,saved,click:()=>elements.get('#send-test').listeners.click(),get:expression=>vm.runInContext(expression,context)};
}
function testIgnoredReplyDiagnostics() {
 const h = harness();
 h.context.record = {
  id:'ignored-reply-test',status:'waiting',kind:'ask',target_email:'user@example.com',
  created_at:new Date().toISOString(),deadline_at:new Date(Date.now()+300000).toISOString(),body:'Original message',
  ignored_replies:[{
   message_id:'ignored-message',from_email:'<other@example.com>',body:'<script>alert(1)</script>',
   received_at:new Date().toISOString(),
   ignored_reason:{code:'REPLY_SENDER_MISMATCH',message:'发件人与目标邮箱不一致 <img>',hint:'请使用配置的目标邮箱回复 <b>。'}
  }]
 };
 const detail = h.get('renderIgnoredReplies(record)');
 assert.match(detail,/未采纳的回复/);
 assert.match(detail,/REPLY_SENDER_MISMATCH/);
 assert.match(detail,/&lt;script&gt;/);
 assert.match(detail,/&lt;other@example.com&gt;/);
 assert.match(detail,/&lt;img&gt;/);
 assert.match(detail,/&lt;b&gt;/);
 assert.doesNotMatch(detail,/<script>|<img>|<b>/);

 const live = h.elements.get('#live-test');
 const nodes = new Map();
 live.querySelector = selector => {
  if (!nodes.has(selector)) nodes.set(selector,new Element());
  return nodes.get(selector);
 };
 h.get('renderLiveTest(record)');
 const message = nodes.get('[data-live-message]');
 assert.match(message.textContent,/已收到回复，但未采纳/);
 assert.match(message.textContent,/发件人与目标邮箱不一致/);
 assert.match(message.textContent,/请使用配置的目标邮箱回复/);
 assert.match(nodes.get('[data-live-status]').innerHTML,/等待回复/);
 assert.match(nodes.get('[data-live-status]').innerHTML,/回复未采纳/);
 h.context.document.querySelectorAll = selector => selector === '[data-wait-message]' ? [message] : [];
 message.textContent = '';
 h.get('updateCountdowns()');
 assert.match(message.textContent,/发件人与目标邮箱不一致/);
 assert.match(message.textContent,/剩余/);

 h.get('record.status="timed_out"; renderLiveTest(record); state.records=[record]; state.total=1; renderRecords()');
 assert.match(message.textContent,/等待有效回复已超时/);
 assert.match(message.textContent,/发件人与目标邮箱不一致/);
 assert.match(nodes.get('[data-live-status]').innerHTML,/等待超时/);
 assert.match(nodes.get('[data-live-status]').innerHTML,/回复未采纳/);
 assert.match(h.elements.get('#records-list').innerHTML,/发件人与目标邮箱不一致 &lt;img&gt;/);

 h.get('record.status="replied"; record.reply={body:"Accepted response"}; renderRecords(); renderLiveTest(record)');
 assert.match(h.elements.get('#records-list').innerHTML,/Accepted response/);
 assert.match(message.textContent,/已收到你的回复：\nAccepted response/);
 assert.doesNotMatch(message.textContent,/已收到回复，但未采纳/);
 h.get('record.error={message:"Connection failed"}; renderRecords()');
 assert.match(h.elements.get('#records-list').innerHTML,/Connection failed/);
 assert.doesNotMatch(h.elements.get('#records-list').innerHTML,/Accepted response/);
}
(async()=>{
 const first=harness(); const calls=[];
 first.context.handler=async(path,options)=>{if(path==='/api/config')return {config};calls.push(options);throw new Error('Lost HTTP response after request persisted');};
 first.get('api = handler'); await first.click();
 assert.ok(first.get('state.pendingTest')); assert.match(first.elements.get('#send-test').innerHTML,/重试这次测试/);
 const key=first.get('state.pendingTest.key'), payload=first.get('JSON.stringify(state.pendingTest.body)');
 const restored=harness(first.saved);
 restored.context.handler=async(path,options)=>{if(path==='/api/config')return {config};calls.push(options);return {id:'durable-original',status:'waiting',deadline_at:new Date(Date.now()+300000).toISOString()};};
 restored.get('api = handler'); await restored.click();
 assert.equal(calls.length,2); assert.equal(calls[1].headers['Idempotency-Key'],key); assert.equal(JSON.stringify(calls[1].body),payload);assert.equal(restored.get('state.pendingTest'),null);assert.equal(restored.get('state.liveTestId'),'durable-original');
 const invalid=harness(); invalid.context.handler=async()=>{const error=new Error('Validation failed');error.status=422;throw error;}; invalid.get('api = handler');await invalid.click();assert.equal(invalid.get('state.pendingTest'),null);
 const changed=harness(); changed.context.handler=async()=>{throw new Error('lost response');};changed.get('api = handler');await changed.click(); let changedCalls=0;changed.context.handler=async(path)=>{if(path==='/api/config')return {config:{...config,target_email:'other@example.com'}};changedCalls++;throw new Error('must not resend');};changed.get('api = handler');await changed.click();assert.equal(changedCalls,0);assert.ok(changed.get('state.pendingTest'));
 assert.match(restored.get("waitingMessage(new Date(Date.now()-1000).toISOString())"),/最多 30 秒/);
 testIgnoredReplyDiagnostics();
 console.log('PASS: lost-response retry reuses key/payload across reload; success clears pending; 422 clears pending; changed saved config prevents resend; deadline reconciliation copy.');
 console.log('PASS: ignored replies expose reasons safely, preserve original status, survive countdown/timeout, and yield to accepted replies or errors.');
})().catch(error=>{console.error(error);process.exitCode=1;});
