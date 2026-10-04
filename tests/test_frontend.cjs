const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const source = fs.readFileSync('frontend/app.js','utf8').split('applyProvider("icloud", true);')[0];
class Element {
  constructor() { this.listeners={};this.dataset={};this.innerHTML='';this.value='';this.disabled=false;this.checked=false;this.hidden=false;this.attributes={};this.classList={add(){},remove(){},toggle(){}}; }
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(name,value){this.attributes[name]=value;} removeAttribute(name){delete this.attributes[name];} append(){} remove(){} focus(){}
  querySelector(){return new Element();} querySelectorAll(){return [];}
}
const config = {provider:'icloud',email:'agent@example.com',username:'agent@example.com',password_set:true,target_email:'user@example.com',smtp_host:'smtp.example.com',smtp_port:465,smtp_security:'ssl',imap_host:'imap.example.com',imap_port:993,imap_security:'ssl',imap_folder:'INBOX',poll_interval:10};
function harness(saved = new Map()) {
 const elements=new Map();
 const document={querySelector(selector){if(!elements.has(selector)) elements.set(selector,new Element()); return elements.get(selector);},querySelectorAll(){return [];},createElement(){return new Element();},documentElement:new Element(),body:new Element()};
 const context=vm.createContext({document,localStorage:{getItem:k=>saved.has(k)?saved.get(k):null,setItem:(k,v)=>saved.set(k,v)},matchMedia:()=>({matches:false}),location:{hash:''},addEventListener(){},setTimeout(){},setInterval(){},clearTimeout(){},crypto:{randomUUID},console,Intl,Date,URLSearchParams,Object,JSON,Number,String,Boolean,Error});
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
async function testWechatConfiguration() {
 const h=harness(new Map([['emailcall.theme','dark'],['emailcall.exported','yes']]));
 assert.equal(h.get('storage.get("theme")'),'dark');
 h.get('storage.set("theme","light")');
 assert.equal(h.saved.get('agentcall.theme'),'light');
 assert.equal(h.saved.get('emailcall.theme'),'dark');
 const wechat={enabled:true,mode:'external',service_endpoint:'puppet.example:443',service_token_set:true,target_contact_id:'wxid_123',target_contact_name:'User <img>'};
 h.context.wechat=wechat;
 h.get('applyWechat({config:wechat,status:{state:"logged_in",logged_in:true,available:true,account:{name:"My account"}}})');
 assert.equal(h.elements.get('#wechat-token').value,'');
 assert.match(h.elements.get('#wechat-token').placeholder,/已保存/);
 assert.equal(h.elements.get('#wechat-external-settings').hidden,false);
 assert.equal(h.elements.get('#wechat-load-contacts').disabled,false);
 assert.match(h.elements.get('#wechat-selected-contact').innerHTML,/User &lt;img&gt;/);
 h.elements.get('#wechat-endpoint').value='edited.example:443';
 h.get('markWechatDirty(); applyWechat({config:wechat,status:{logged_in:true,available:true}})');
 assert.equal(h.elements.get('#wechat-endpoint').value,'edited.example:443');
 assert.equal(h.get('state.wechatDirty'),true);
 h.get('renderWechatStatus({state:"scan",logged_in:false,qr_image:"https://tracker.example/qr.png"})');
 assert.doesNotMatch(h.elements.get('#wechat-qr').innerHTML,/<img|tracker/);
 h.get('renderWechatStatus({state:"scan",logged_in:false,qr_image:"data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="})');
 assert.match(h.elements.get('#wechat-qr').innerHTML,/<img src="data:image\/svg\+xml;base64,/);
 h.get('renderWechatStatus({state:"awaiting_scan",logged_in:false,qr_status:"Scanned",qr_image:"data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="})');
 assert.match(h.elements.get('#wechat-status-title').textContent,/已扫码/);
 h.get('renderWechatStatus({state:"awaiting_scan",logged_in:false,qr_status:"Waiting",qr_image:"data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="})');
 assert.equal(h.elements.get('#wechat-status-title').textContent,'等待扫码登录');
 h.get('renderWechatStatus({state:"connecting",logged_in:false,qr_status:"Scanned",qr_image:null})');
 assert.doesNotMatch(h.elements.get('#wechat-status-title').textContent,/已扫码/);
 h.get('renderWechatStatus({state:"error",logged_in:false,error:{message:"<script>bad</script>",hint:"Try <again>",code:"TEST"}})');
 assert.match(h.elements.get('#wechat-error').innerHTML,/&lt;script&gt;/);
 assert.doesNotMatch(h.elements.get('#wechat-error').innerHTML,/<script>/);
 h.get('state.contacts=[{id:"wxid_a",name:"Name <b>",alias:"Alias <img>"}]; renderContacts()');
 assert.match(h.elements.get('#wechat-contacts').innerHTML,/Alias &lt;img&gt;/);
 assert.doesNotMatch(h.elements.get('#wechat-contacts').innerHTML,/<img>|<b>/);
 h.get('state.wechatDirty=false; state.wechatContactDirty=true');
 h.context.handler=async(path,options)=>{assert.equal(path,'/api/wechat');assert.equal(options.body.service_token,'');assert.equal(options.body.target_contact_id,'wxid_123');return {config:wechat,status:{logged_in:true}};};
 h.get('api=handler');
 await h.get('saveWechatConfig()');
 assert.equal(h.get('state.wechatDirty'),false);
 assert.equal(h.get('Object.hasOwn(wechatFormData(),"target_contact_id")'),false);
 h.elements.get('#wechat-selected-contact').listeners.click({target:{closest:()=>true}});
 assert.equal(h.get('wechatFormData().target_contact_id'),'');
 assert.equal(h.get('state.wechatDirty'),true);
}
async function testWechatRequestAndRecords() {
 const h=harness();const calls=[];
 h.get('state.wechat={enabled:true,target_contact_id:"wxid_target"}; state.config=null');
 h.context.document.querySelector('#test-channel').value='wechat';
 h.context.handler=async(path,options)=>{calls.push({path,options});return {id:'wechat-test',status:'waiting',channel:'wechat',deadline_at:new Date(Date.now()+300000).toISOString()};};
 h.get('api=handler');await h.click();
 assert.equal(calls[0].path,'/api/ask');
 assert.equal(calls[0].options.body.channel,'wechat');
 assert.match(h.get('waitingMessage(new Date(Date.now()+10000).toISOString(), "wechat")'),/微信/);
 assert.match(h.get('channelBadge({})'),/邮箱/);
 assert.match(h.get('channelBadge({channel:"wechat"})'),/微信/);
 h.context.record={id:'wechat-record',kind:'ask',status:'replied',channel:'wechat',recipient_label:'Contact <b>',created_at:new Date().toISOString(),body:'Request',reply:{from_name:'User <img>',body:'Reply <script>',received_at:new Date().toISOString()},fallback_reason:null};
 h.elements.get('#record-dialog').open=true;
 h.get('state.detailId=record.id; renderDetail(record)');
 const html=h.elements.get('#record-detail').innerHTML;
 assert.match(html,/目标联系人/);assert.match(html,/Contact &lt;b&gt;/);assert.match(html,/User &lt;img&gt;/);assert.match(html,/Reply &lt;script&gt;/);assert.doesNotMatch(html,/<img>|<script>|<b>/);
 assert.match(h.get('renderFallback({fallback_reason:{message:"Offline <x>"}})'),/Offline &lt;x&gt;/);
 h.get('state.records=[record];state.total=1;renderRecords()');
 assert.match(h.elements.get('#records-list').innerHTML,/channel-badge wechat/);
}
async function testWechatConnectionDiagnosticsWithoutTarget() {
 const h=harness();const calls=[];
 h.get('state.wechat={enabled:true,target_contact_id:""};state.config=null');
 h.context.document.querySelector('#test-channel').value='wechat';
 h.context.handler=async(path,options)=>{calls.push({path,options});return {ok:false,record_id:'saved-diagnostic',checks:[{name:'微信登录与目标联系人',ok:false,error:{code:'WECHAT_NOT_LOGGED_IN',message:'微信尚未登录。',hint:'请扫码登录。'}}]};};
 h.get('api=handler');
 await h.elements.get('#test-connection').listeners.click();
 assert.equal(calls.length,1);
 assert.equal(calls[0].path,'/api/wechat/test');
 assert.equal(calls[0].options.method,'POST');
 assert.match(h.elements.get('#test-results').innerHTML,/微信尚未登录/);
 assert.match(h.elements.get('#test-results').innerHTML,/请扫码登录/);
 await h.click();
 assert.equal(calls.length,1,'Conversation test must still require a saved target');
 h.get('state.wechatDirty=true');
 await h.elements.get('#test-connection').listeners.click();
 assert.equal(calls.length,1,'Unsaved changes must not silently test the old configuration');
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
 await testWechatConfiguration();
 await testWechatRequestAndRecords();
 await testWechatConnectionDiagnosticsWithoutTarget();
 console.log('PASS: lost-response retry reuses key/payload across reload; success clears pending; 422 clears pending; changed saved config prevents resend; deadline reconciliation copy.');
 console.log('PASS: ignored replies expose reasons safely, preserve original status, survive countdown/timeout, and yield to accepted replies or errors.');
 console.log('PASS: WeChat configuration preserves edits and secret fields; QR stays local; contacts/replies escape HTML; channel-specific requests and legacy email records render correctly.');
 console.log('PASS: connection checks reach durable backend diagnostics before WeChat login/target setup, while conversation tests and unsaved changes remain guarded.');
})().catch(error=>{console.error(error);process.exitCode=1;});
