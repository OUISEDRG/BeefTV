'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
}
function setup({policyMode='background-countdown'}={}){
 let now=0;const intervals=[],calls=[],listeners={},root={};
 for(const id of ['signin','key','jobs','login','message','execution-policy'])root[id]=new Element('div');
 const nodes=e=>[e,...e.children.flatMap(c=>typeof c==='object'?nodes(c):[])];
 const store=()=>{const map=new Map();return {getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v)}};
 const task={id:'task-new',status:'prepared',model:'extend',quoteId:'quote-1',pointsEstimate:67,createdAt:101,generationSubmitted:false,autoExecuteAt:5,autoExecuteQuoteId:'quote-1',prompt:'原文不改',kind:'extension',sourceVideo:{duration:4,width:480,height:854},duration:5};
 const context={Date:{now:()=>now},location:{hash:'#test-secret',pathname:'/console'},history:{replaceState(){}},sessionStorage:store(),localStorage:store(),
 document:{visibilityState:'visible',getElementById:id=>root[id],createElement:tag=>new Element(tag),addEventListener:(event,fn)=>{listeners[event]=fn;}},
 window:{addEventListener:(event,fn)=>{listeners[event]=fn;}},setInterval:fn=>{intervals.push(fn);},
 alert:()=>{throw Error('Unexpected modal alert');},confirm:()=>{throw Error('Automatic execution must not require a confirmation dialog');},
 fetch:async(url,options)=>{calls.push({url,options});let result;
  if(url==='/v1/execution-policy')result={mode:policyMode,delaySeconds:5,effectiveAt:100};
  else if(url.endsWith('/models'))result={data:[{id:'extend',name:'测试延长'}]};
  else if(url==='/v1/jobs')result={data:[{...task}],serverTime:now/1000};
  else if(url.endsWith('/approve')){task.status='queued';result={status:'queued'};}
  else if(url.endsWith('/cancel')){task.status='cancelled';result={status:'cancelled'};}
  else throw Error('Unexpected path '+url);
  return {ok:true,json:async()=>result};
 }};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../console.js'),'utf8'),context);
 return {root,calls,task,nodes:()=>nodes(root.jobs),time:n=>now=n,tick:async()=>{for(const fn of intervals)await fn();await new Promise(setImmediate);}};
}
test('the console displays the server deadline without posting approvals from a browser timer',async()=>{
 const h=setup();await new Promise(setImmediate);
 assert.ok(h.nodes().some(e=>e.textContent.includes('5 秒后自动执行')),'Countdown must be shown on the real task card');
 h.time(4999);await h.tick();assert.equal(h.calls.filter(c=>c.url.endsWith('/approve')).length,0);
 h.time(5000);await h.tick();assert.equal(h.calls.filter(c=>c.url.endsWith('/approve')).length,0);
 assert.ok(h.nodes().some(e=>e.textContent.includes('后台')));
});
test('the actual cancel button stops the timer before sending cancellation',async()=>{
 const h=setup();await new Promise(setImmediate);h.time(4999);
 const button=h.nodes().find(e=>e.tag==='button'&&e.textContent==='取消此任务');assert.ok(button);await button.onclick();
 h.time(6000);await h.tick();assert.equal(h.calls.filter(c=>c.url.endsWith('/approve')).length,0);
 assert.equal(h.calls.filter(c=>c.url.endsWith('/cancel')).length,1);
});
test('the immediate execution button submits the exact displayed quote without an extra dialog',async()=>{
 const h=setup();await new Promise(setImmediate);
 const button=h.nodes().find(e=>e.tag==='button'&&e.textContent.startsWith('立即执行'));assert.ok(button);await button.onclick();
 const requests=h.calls.filter(c=>c.url.endsWith('/approve'));assert.equal(requests.length,1);
 assert.deepEqual(JSON.parse(requests[0].options.body),{confirm:true,quoteId:'quote-1',points:67});
});
test('manual policy fallback is reported accurately in the visible policy banner',async()=>{
 const h=setup({policyMode:'manual'});await new Promise(setImmediate);
 assert.ok(h.root['execution-policy'].textContent.includes('手动执行'));
 assert.ok(!h.root['execution-policy'].textContent.includes('已开启后台自动执行'));
});
