/* Actual audio panels and i18n runtime with a small DOM/fetch fixture; no GPU or browser audio. */
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const installI18n = require('./i18n_fixture.cjs');
class Element {
  constructor() {this.children=[];this.dataset={};this.events={};this.value='';this.files=[];this.attributes={};this._text='';this.isConnected=true;}
  set textContent(value) {this._text=value;this.children=[];}
  get textContent() {return this._text+this.children.map(child=>child.textContent).join(' ');}
  replaceChildren(...children) {this._text='';this.children=children;}
  append(...children) {this.children.push(...children);}
  setAttribute(key,value) {this.attributes[key]=value;}
  getAttribute(key) {return this.attributes[key]??null;}
  removeAttribute(key) {delete this.attributes[key];}
  addEventListener(key,fn) {(this.events[key]??=[]).push(fn);}
  pause() {}
  load() {}
}
const drain = async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
function boot(section, stableDraft=false) {
  const nodes=new Map(),events={},calls=[],state={entries:[],pending:null};
  const el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const profile=section==='music'?{id:'ace-step-15-turbo',name:'ACE-Step 1.5 Turbo',available:true,min_duration:10,max_duration:180,planner:'LM',capabilities:{mode:true,lyrics:true,bpm:true,language:true,planner:true}}:
    {id:'qwen3-tts-12hz-0.6b-base',name:'Qwen3-TTS 0.6B Base',available:true};
  if(stableDraft)Object.assign(profile,{id:'stable-audio-3-medium',capabilities:{mode:false,lyrics:false,bpm:false,language:false,planner:false}});
  const document={body:{dataset:{}},documentElement:{lang:'ru'},readyState:'loading',
    getElementById:el,createElement:()=>new Element(),addEventListener:()=>{}};
  const context=vm.createContext({document,console,
    localStorage:{getItem:()=>stableDraft?JSON.stringify({Engine:'stable-audio-3-medium',Mode:'song',Caption:'Моя идея',Lyrics:'Мои слова'}):null,setItem:()=>{}},setInterval:()=>1,clearInterval:()=>{},
    addEventListener:(name,fn)=>{(events[name]??=[]).push(fn);},dispatchEvent:event=>{for(const fn of events[event.type]||[])fn(event);},
    CustomEvent:class{constructor(type){this.type=type;}},
    Option:class extends Element{constructor(text,value){super();this.textContent=text;this.value=value;}},
    StudioUI:{setHeader(){},modelLabel:(id,name)=>name,modelSource:()=>'',deleteGenerationButton:()=>new Element(),generationInfo:()=>new Element(),notify(){},parentFolder:()=>''},
    fetch:async(url,options)=>{
      calls.push({url,body:options?.body?JSON.parse(options.body):null});
      const route=url.split('/').at(-1).split('?')[0];
      let value={};
      if(route==='availability')value={available:true,engine:profile,engines:[profile],verification:{available:false}};
      if(route==='settings')value={save_dir:'C:/External Audio'};
      if(route==='voices')value={voices:[{id:'a'.repeat(32),name:'Тестовый голос',duration:5}],builtin:[],defaults:{ready:0,wanted:0,state:'idle'}};
      if(route==='history')value={entries:state.entries};
      if(route==='prepare')value={book_id:'b'.repeat(32),title:'Текст',chars:50,fragments:1,chapters:[],normalization:{places:0,examples:[]}};
      if(route==='status')value={id:'c'.repeat(32),state:'queued',phase:'Gemma готовит запрос',created:1};
      if(route==='generate')value=await new Promise(resolve=>{state.pending=resolve;});
      return {ok:true,json:async()=>value};
    }});
  context.window=context;
  installI18n(context);
  el('musicEngine').value='ace-step-15-turbo';el('musicMode').value='instrumental';el('musicDuration').value='20';
  el('musicLanguage').value='unknown';el('musicPlanner').checked=true;
  el('voiceoverLanguage').value='Russian';el('voiceoverFormat').value='mp3';
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../App/frontend',section+'-panel.js'),'utf8'),context);
  return {context,state,calls,el};
}
(async()=>{
  const stable=boot('music',true);
  stable.el('musicTab').onclick();await drain();
  for(const id of ['musicSongPresets','musicLyricsBlock','musicWriteLyricsBlock']) assert.equal(stable.el(id).hidden,true, 'async capabilities must hide song fields for Stable Audio');
  assert.equal(stable.el('musicLyrics').value,'Мои слова');assert.equal(stable.el('musicCaption').value,'Моя идея');
  for(const section of ['music','voiceover']) {
    const app=boot(section),{context,state,calls,el}=app;
    el(section+'Tab').onclick();await drain();
    const history=el(section+'History');assert.equal(history.children.length,0);
    state.entries=[{id:'d'.repeat(32),engine:section==='music'?'ace-step-15-turbo':'qwen3-tts-12hz-0.6b-base',caption:'Ошибка',original_caption:'Ошибка',state:'cancelled',mode:'instrumental',duration:20,seed:7,title:'Анна',voice_name:'Тестовый голос'}];
    el(section+'Tab').onclick();await drain();
    assert.equal(history.children.length,1);
    context.StudioI18n.setLanguage('en');
    assert.equal(history.children.length,1,'locale change must not resurrect empty-history text');
    assert.ok(history.textContent.includes(section==='music'?'Ошибка':'Анна'),'user text stays literal');
    if(section==='voiceover') {
      assert.ok(history.textContent.includes('Qwen3-TTS 0.6B Base'),'same-book TTS results identify their actual engine');
      el('voiceoverText').value='Анна пришла 5 мая.';
      await el('voiceoverPrepare').onclick();
      const originalText = el('voiceoverText').value;
      el('voiceoverCalmPreset').onclick();
      assert.ok(el('voiceoverDelivery').value.includes('Calm and intimate'));
      assert.equal(el('voiceoverText').value, originalText);
      assert.equal(el('voiceoverStyleBlock').hidden, true, 'Qwen cannot expose Vox style controls');
      el('voiceoverDelivery').value='Как интересную историю, без долгих пауз';
    } else {
      el('musicLyrics').value='[Verse]\nНе меняй мои слова.';
      el('musicCaption').value='Тихая песня';
      el('musicCloseVocalPreset').onclick();
      assert.ok(el('musicCaption').value.startsWith('Тихая песня\n'));
      const caption = el('musicCaption').value;
      el('musicCloseVocalPreset').onclick(); assert.equal(el('musicCaption').value, caption);
      assert.equal(el('musicLyrics').value,'[Verse]\nНе меняй мои слова.');
      el('musicCaption').value='я'.repeat(2000); el('musicVerseChorusPreset').onclick();
      assert.equal(el('musicCaption').value.length,2000, 'preset cannot truncate the existing request');
      el('musicCaption').value='Тихое пианино для работы';
    }
    const event={preventDefault(){}};
    const first=el(section+'Form').onsubmit(event);
    await drain();
    await el(section+'Form').onsubmit(event);
    assert.equal(calls.filter(call=>call.url.endsWith('/generate')).length,1,'a pending request blocks duplicate submission');
    state.pending({id:'c'.repeat(32),token:'test',state:'queued'});
    await first;await drain();
    const request=calls.find(call=>call.url.endsWith('/generate')).body;
    if(section==='voiceover') {
      assert.equal(request.delivery,'Как интересную историю, без долгих пауз');
      assert.equal(request.book_id,'b'.repeat(32));assert.equal(request.spoken_text,undefined);
    } else assert.equal(request.caption,'Тихое пианино для работы');
  }
  console.log('audio panels UI ok: empty-to-filled history survives language changes, literal user text, separate delivery, no duplicate submit');
})().catch(error=>{console.error(error);process.exitCode=1;});
