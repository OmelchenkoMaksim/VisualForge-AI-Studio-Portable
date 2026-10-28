/* Exercise live translation with production dictionaries, without touching model runtimes. */
'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm');
const install=require('./i18n_fixture.cjs');
function boot(saved=null,blocked=false){
  const events={},storage=new Map(saved?[['vf-language',saved]]:[]);
  const doc={documentElement:{lang:'ru'}};
  const context=vm.createContext({document:doc,
    localStorage:{getItem:key=>{if(blocked)throw Error('blocked');return storage.get(key)??null;},setItem:(key,value)=>{if(blocked)throw Error('blocked');storage.set(key,value);}},
    addEventListener:(name,fn)=>{(events[name]??=[]).push(fn);},
    dispatchEvent:event=>{for(const fn of events[event.type]||[])fn(event);},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}
  });
  install(context);
  return {api:context.StudioI18n,context,doc,events,storage};
}
const key=(ctx,ru)=>Object.entries(ctx.VisualForgeStrings).find(([,entry])=>entry.ru===ru)?.[0];
const app=boot(),i=app.api;
assert.equal(i.language,'ru');
const button={textContent:'',isConnected:true},user={textContent:'Ошибка',isConnected:true},input={value:'Ошибка · Готово'};
i.bind(button,'textContent',()=>i.t(key(app.context,'Создать изображение')));
i.bind(user,'textContent',()=>input.value,false);
assert.equal(button.textContent,'Создать изображение');
let changes=0;app.events['studio-languagechange']=[()=>{changes++;}];
assert.equal(i.setLanguage('en'),true);
assert.equal(button.textContent,'Create image');
assert.equal(user.textContent,'Ошибка · Готово');
assert.equal(input.value,'Ошибка · Готово');
assert.equal(app.doc.documentElement.lang,'en');
assert.equal(app.storage.get('vf-language'),'en');
assert.equal(changes,1);
i.setLanguage('en');assert.equal(changes,1,'same language has no side effects');
assert.equal(i.setLanguage('fr'),false);
assert.equal(i.remote('Модель отключена в разделе «Компоненты».'),'Model disabled in Components.');
assert.equal(i.remote('Итерация 2 из 10…'),'Iteration 2 of 10…');
assert.equal(i.remote('16 ГБ'),'16 GB');
assert.equal(i.remote('WAV · 48 кГц'),'WAV · 48 kHz');
assert.equal(i.remote('Редактирование, Редактирование с референсом'),'Editing, Editing with reference');
assert.equal(i.remote('Контекст заполнен на 75%: агент заканчивает итерацию…'),'Context is 75% full: the agent is finishing the iteration…');
const filename='Ошибка · Готово';
const template=key(app.context,'Не удалось открыть «{0}»');
assert.equal(i.remote(i.t(template,[filename])),'Could not open “Ошибка · Готово”','parameters are data even when they match UI labels');
const once={textContent:'',isConnected:true};i.value(once,'textContent','Распознавание речи');assert.equal(once.textContent,'Speech recognition');
const appended={textContent:'',isConnected:true};i.bind(appended,'textContent',()=>i.t(key(app.context,'Готово')));i.append(appended,'textContent',()=>': 1');
i.setLanguage('ru');assert.equal(button.textContent,'Создать изображение');assert.equal(once.textContent,'Распознавание речи');assert.equal(appended.textContent,'Готово: 1');
app.events.storage[0]({key:'vf-language',newValue:'en'});assert.equal(i.language,'en');
app.events.storage[0]({key:'vf-language',newValue:null});assert.equal(i.language,'ru');
i.value(button,'textContent','',false);i.setLanguage('en');assert.equal(button.textContent,'','cleared bindings cannot resurrect old text');
assert.equal(boot('en').api.language,'en');assert.equal(boot('invalid').api.language,'ru');assert.equal(boot(null,true).api.setLanguage('en'),true);
const staticApp=boot(),staticKey=key(staticApp.context,'Настройки');
const text={nodeType:3,nodeValue:' Настройки ',isConnected:true};
const comment={data:'i18n:'+staticKey,nextSibling:text};
const attrs={dataset:{i18nAttrs:JSON.stringify({'aria-label':staticKey})},values:{},isConnected:true,getAttribute(name){return this.values[name];},setAttribute(name,value){this.values[name]=value;}};
const select={value:'',addEventListener(){}};
staticApp.doc.createTreeWalker=()=>{let done=false;return{nextNode(){if(done)return null;done=true;return comment;}};};
staticApp.doc.querySelectorAll=()=>[attrs];staticApp.doc.getElementById=()=>select;
staticApp.api.initStatic();staticApp.api.setLanguage('en');
assert.equal(text.nodeValue,' Settings ');assert.equal(attrs.values['aria-label'],'Settings');assert.equal(select.value,'en');
const dynamicTitle={title:'',isConnected:true,dataset:{i18nAttrs:JSON.stringify({title:staticKey})}};
staticApp.doc.querySelectorAll=()=>[dynamicTitle];
staticApp.api.bind(dynamicTitle,'title',()=>staticApp.api.t(key(staticApp.context,'Копировать')),false);
staticApp.api.initStatic();assert.equal(dynamicTitle.title,'Copy','static title must not overwrite a dynamically owned attribute');
staticApp.api.bind(dynamicTitle,'@title',()=>staticApp.api.t(staticKey),false);
staticApp.api.setLanguage('ru');assert.equal(dynamicTitle.title,'Настройки','attribute/property aliases share one binding');
staticApp.api.value(dynamicTitle,'title','',false);staticApp.api.initStatic();staticApp.api.setLanguage('en');
assert.equal(dynamicTitle.title,'','a deliberately cleared dynamic title must not regain its static fallback');
console.log('i18n UI ok: live RU/EN, persistence, cross-window changes, static labels, dynamic status, safe parameters, user text and cleared bindings');
