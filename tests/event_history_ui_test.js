/* Settings journal, paging, stale responses and microphone events against a fake local API. */
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
class Element {
  constructor(){this.children=[];this.handlers={};this.value='';this.open=false;this.hidden=false;this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  addEventListener(name,callback){this.handlers[name]=callback;}
  showModal(){this.open=true;}
  close(){this.open=false;this.handlers.close?.();}
  focus(){this.focused=true;}
  set innerHTML(value){throw new Error('Event values must remain plain text; received ' + typeof value);}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function main(){
  const names=['eventHistoryDialog','eventHistoryEntries','eventHistoryStatus','eventHistoryMore','eventHistoryType','eventHistoryScope','eventHistoryState','openEventHistory','eventHistoryClose','eventHistoryRefresh','studioSettingsToggle'];
  const elements=Object.fromEntries(names.map(name=>[name,new Element()]));
  const document={getElementById:id=>elements[id],createElement:()=>new Element()};
  const window={handlers:{},addEventListener(name,callback){this.handlers[name]=callback;}};
  let copied, calls=[], delayed=null;
  const record=(type,id)=>({type,id:id.repeat(64),started_at:'2026-10-28T10:00:00.000Z',duration_sec:1.25});
  const image={generation:record('Image','a'),state:'done',technical:false,parent_id:null};
  const technical={generation:record('SpeechModelLoad','b'),state:'done',technical:true,parent_id:image.generation.id};
  const cursor='2026-10-28T10:00:00.000Z|'+technical.generation.id;
  const catalog=[{id:'Image',label:'Изображение'},{id:'SpeechModelLoad',label:'Загрузка распознавания'}];
  const context=vm.createContext({document,window,URLSearchParams,AbortController,Date,JSON,Map,Set,navigator:{clipboard:{writeText:async text=>{copied=text;}}},fetch:async(url,options={})=>{
    calls.push({url,options});
    if(options.method==='POST')return{ok:true,json:async()=>({generation:record('MicrophoneSession','c')})};
    if(delayed)await new Promise(resolve=>{delayed.resolve=resolve;});
    const query=new URL('http://localhost'+url).searchParams;
    const rows=query.get('before')?[{...image,generation:record('Image','d')}]:query.get('scope')==='technical'?[technical]:[image,technical];
    return{ok:true,json:async()=>({entries:rows,next:query.get('before')?null:cursor,types:catalog})};
  }});
  require('./i18n_fixture.cjs')(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../App/frontend/event-history.js'),'utf8'),context);
  elements.openEventHistory.handlers.click();await tick();
  assert.equal(elements.eventHistoryDialog.open,true);
  assert.equal(elements.eventHistoryEntries.children.length,2);
  await elements.eventHistoryEntries.children[0].children[3].onclick();
  assert.deepEqual(JSON.parse(copied),image.generation);
  elements.eventHistoryMore.handlers.click();await tick();
  assert.equal(elements.eventHistoryEntries.children.length,3);
  assert.equal(new URL('http://localhost'+calls.at(-1).url).searchParams.get('before'),cursor);
  elements.eventHistoryScope.value='technical';elements.eventHistoryScope.handlers.change();await tick();
  assert.equal(elements.eventHistoryEntries.children.length,1);
  assert.equal(calls.filter(call=>call.options.method==='POST').length,0,'opening and filtering must not collect click analytics');
  const pending=window.StudioEvents.microphoneStart();
  const microphone=await pending;await window.StudioEvents.microphoneFinish(pending,'cancelled');
  const posts=calls.filter(call=>call.options.method==='POST');
  assert.equal(posts.length,2);
  assert.deepEqual(JSON.parse(posts[1].options.body),{action:'finish',id:microphone.id,state:'cancelled'});
  delayed={};elements.eventHistoryRefresh.handlers.click();await tick();
  elements.eventHistoryDialog.close();delayed.resolve();await tick();
  assert.equal(elements.eventHistoryDialog.open,false);
  assert.equal(elements.eventHistoryEntries.children.length,0,'a late response must not refill the closed dialog');
  console.log('event history UI ok: settings, copy, paging, filters, microphone lifecycle, no click analytics and stale responses');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
