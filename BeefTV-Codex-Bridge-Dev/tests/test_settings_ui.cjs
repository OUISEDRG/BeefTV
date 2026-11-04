'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'settings.html'), 'utf8');
const script = html.match(/<script id="settings-script">([\s\S]*?)<\/script>/)[1];
const clone = value => JSON.parse(JSON.stringify(value));
const initial = () => ({revision:7,overrides:{'gpt-6-astra':'high'},models:[
  {id:'gpt-6-astra',displayName:'GPT 6 Astra',defaultReasoningEffort:'high',supportedReasoningEfforts:[{reasoningEffort:'low',description:'Low'},{reasoningEffort:'high',description:'High'},{reasoningEffort:'ultra',description:'Ultra'}]},
  {id:'gpt-6.1-sol',displayName:'GPT 6.1 Sol',defaultReasoningEffort:'medium',supportedReasoningEfforts:[{reasoningEffort:'low',description:'Low'},{reasoningEffort:'medium',description:'Medium'},{reasoningEffort:'high',description:'High'}]}
]});

class Element {
  constructor() { this.value=''; this.textContent=''; this.disabled=false; this.hidden=false; this.dataset={}; this.children=[]; this.handlers={}; }
  replaceChildren(...children) { this.children=children; }
  addEventListener(type, fn) { this.handlers[type]=fn; }
  async dispatch(type, value) { if (value !== undefined) this.value=value; return this.handlers[type](); }
}

function setup(customPost) {
  const elements={};
  const calls=[];
  let saved=initial();
  const response=(status,body) => ({ok:status>=200&&status<300,status,json:async()=>clone(body)});
  const context={document:{getElementById:id => elements[id] ||= new Element(),createElement:()=>new Element()},
    fetch:async(url,options) => {
      calls.push({url,...options});
      assert.equal(url,'/settings/data','The settings UI must call only the settings endpoint');
      if(options.method==='GET') return response(200,saved);
      const body=JSON.parse(options.body);
      if(customPost) {
        const custom=await customPost(body,{get:()=>clone(saved),set:value=>saved=clone(value)});
        if(custom) return response(custom.status,custom.body);
      }
      assert.equal(body.expectedRevision,saved.revision);
      if(body.effort===null) delete saved.overrides[body.model]; else saved.overrides[body.model]=body.effort;
      saved.revision++;
      return response(200,{saved:true,revision:saved.revision});
    }
  };
  vm.runInNewContext(script,context,{filename:'settings.html'});
  return {elements,calls,get:()=>clone(saved),flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

test('loads configured astra high and shows only supported effort options',async()=>{
  const ui=setup(); await ui.flush();
  assert.equal(ui.elements.model.value,'gpt-6-astra');
  assert.equal(ui.elements.effort.value,'high');
  assert.equal(ui.elements.save.disabled,true);
  assert.deepEqual(ui.elements.effort.children.map(item=>item.value),['','low','high','ultra']);
  assert.equal(ui.calls.length,1);
  assert.equal(ui.calls[0].method,'GET');
});

test('switching models keeps drafts separate and saves only the selected model',async()=>{
  const ui=setup(); await ui.flush();
  await ui.elements.effort.dispatch('change','ultra');
  await ui.elements.model.dispatch('change','gpt-6.1-sol');
  assert.equal(ui.elements.effort.value,'');
  assert.equal(ui.elements.save.disabled,true);
  await ui.elements.save.dispatch('click');
  assert.equal(ui.calls.filter(call=>call.method==='POST').length,0);
  await ui.elements.effort.dispatch('change','low');
  await ui.elements.save.dispatch('click');
  const posts=ui.calls.filter(call=>call.method==='POST');
  assert.equal(posts.length,1);
  assert.deepEqual(JSON.parse(posts[0].body),{model:'gpt-6.1-sol',effort:'low',expectedRevision:7});
  assert.equal(posts[0].headers['X-CSRF-Token'],'__CSRF_TOKEN__');
  assert.equal(ui.get().overrides['gpt-6-astra'],'high');
  await ui.elements.model.dispatch('change','gpt-6-astra');
  assert.equal(ui.elements.effort.value,'ultra');
  assert.equal(ui.elements.dirty.hidden,false);
  await ui.elements.save.dispatch('click');
  assert.deepEqual(JSON.parse(ui.calls.filter(call=>call.method==='POST')[1].body),{model:'gpt-6-astra',effort:'ultra',expectedRevision:8});
  assert.match(ui.elements.notice.textContent,/下一次新请求生效/);
});

test('saving captures the target and locks controls while the POST is pending',async()=>{
  let release;
  const gate=new Promise(resolve=>release=resolve);
  const ui=setup(async()=>{await gate;}); await ui.flush();
  await ui.elements.effort.dispatch('change','ultra');
  const pending=ui.elements.save.dispatch('click');
  await ui.flush();
  assert.equal(ui.elements.model.disabled,true);
  assert.equal(ui.elements.effort.disabled,true);
  await ui.elements.model.dispatch('change','gpt-6.1-sol');
  assert.equal(ui.elements.model.value,'gpt-6-astra');
  release(); await pending;
  assert.equal(ui.get().overrides['gpt-6-astra'],'ultra');
  assert.equal(ui.get().overrides['gpt-6.1-sol'],undefined);
});

test('default choice posts null and conflict refresh does not auto-save',async()=>{
  let first=true;
  const ui=setup(async(body,state)=>{
    if(first){ first=false; const updated=state.get(); updated.revision=8; state.set(updated); return {status:409,body:{error:{message:'conflict'}}}; }
  }); await ui.flush();
  await ui.elements.effort.dispatch('change','');
  await ui.elements.save.dispatch('click');
  assert.equal(ui.calls.filter(call=>call.method==='POST').length,1);
  assert.equal(ui.elements.effort.value,'');
  assert.equal(ui.elements.dirty.hidden,false);
  assert.match(ui.elements.notice.textContent,/其他窗口更新/);
  await ui.elements.save.dispatch('click');
  const last=JSON.parse(ui.calls.filter(call=>call.method==='POST')[1].body);
  assert.deepEqual(last,{model:'gpt-6-astra',effort:null,expectedRevision:8});
  assert.equal(ui.get().overrides['gpt-6-astra'],undefined);
});

test('page has no generation entry point or dynamic HTML injection',()=>{
  assert.ok(html.includes('__CSRF_TOKEN__'));
  assert.doesNotMatch(script,/innerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(script,/chat\/completions|turn\/start|generate/);
  assert.equal((html.match(/<button\b/g)||[]).length,2);
});
