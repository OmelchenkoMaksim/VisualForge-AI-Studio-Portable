'use strict';
const assert = require('node:assert/strict');
const flow = require('../App/frontend/image-clarification.js');

const options = [{id:1,label:'Акварель'}, {id:2,label:'Фотография'}, {id:3,label:'Сумерки'}];
const first = {state:'clarification', round:1, clarification_token:'first', question:'Как показать сцену?', options};
const second = {...first, round:2, clarification_token:'second', options:options.slice(0,2)};
const job = {prompt_id:'queued-image'};
const payload = {engine:'z-image-turbo-int8', mode:'prompt', prompt:'Нарисуй лису', seed:42, clarify:true};

class Element {
  constructor() { this.listeners = new Map(); this.children = []; this.hidden = true; this.value = ''; }
  addEventListener(type, fn) { this.listeners.set(type, fn); }
  removeEventListener(type) { this.listeners.delete(type); }
  replaceChildren() { this.children = []; }
  append(item) { this.children.push(item); }
  focus() { this.focused = true; }
  dispatch(type, event={}) { this.listeners.get(type)?.(event); }
  set innerHTML(value) { throw new Error('Model text must not be assigned to HTML: ' + value); }
}

async function main() {
  assert.equal(flow.selection('13',3), '1,3');
  assert.equal(flow.selection('1, 3, 1',3), '3,1');
  assert.equal(flow.selection('0',3), '0');
  for (const value of ['9','4','0,1','one','','1.3']) assert.throws(() => flow.selection(value,3));

  let sends = [];
  assert.equal(await flow.run(async data => {sends.push(data);return job;}, payload, {ask:()=>{throw Error('No question needed');}}), job);
  assert.equal(sends.length, 1);

  sends = [];
  const states = [];
  const replies = [first, second, job];
  const asks = [];
  assert.equal(await flow.run(async data => {
    sends.push(data);
    if (sends.length === 3) assert.equal(states.at(-1), 'accepted', 'acknowledge the final answer before starting generation');
    return replies.shift();
  }, payload, {ask:async question => {asks.push(question.round);return '12';}, onState:state=>states.push(state)}), job);
  assert.deepEqual(asks, [1,2]);
  assert.equal(sends[1].clarification_token, 'first');
  assert.equal(sends[2].clarification_token, 'second');
  for (const sent of sends) {
    assert.equal(sent.prompt,payload.prompt);
    assert.equal(sent.seed,42);
  }
  assert.equal(payload.clarification_token,undefined,'the captured original request must not be overwritten');

  let count=0, cancelled=null;
  assert.equal(await flow.run(async()=>{count++;return first;},payload,{ask:async()=>null,onCancel:async token=>{cancelled=token;}}),null);
  assert.equal(count,1);
  assert.equal(cancelled,'first');
  await assert.rejects(flow.run(async()=>first,payload,{ask:async()=>'1',isCurrent:()=>false,onCancel:async()=>{}}));

  const maliciousReplies=[first,second,{...second,round:3}];
  let questions=0;
  await assert.rejects(flow.run(async()=>maliciousReplies.shift(),payload,{ask:async()=>{questions++;return '12';}}));
  assert.equal(questions,2,'even a malformed backend response must never display a third question');

  const elements = new Map();
  global.document = {getElementById:id=>{
    if (!elements.has(id)) elements.set(id,new Element());
    return elements.get(id);
  },createElement:()=>new Element()};
  global.StudioI18n = {t:key=>key,remote:value=>value};
  // Assemble this deliberately malformed XSS fixture as text so IDE HTML inspections do not treat it as application markup.
  const maliciousLabel=['<', 'img src=x onerror=alert(1)', '>'].join('');
  let calls=0;
  const pending=flow.run(async()=>++calls===1?{...first,options:[{id:1,label:maliciousLabel},options[1]]}:job,payload);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(elements.get('imageClarification').hidden,false);
  assert.equal(elements.get('imageClarificationOptions').children[0].textContent,'1. '+maliciousLabel);
  const input=elements.get('imageClarificationAnswer');
  input.value='8'; elements.get('imageClarificationApply').dispatch('click');
  assert.equal(calls,1,'invalid numbers are corrected in the same panel, not another Gemma question');
  assert.ok(elements.get('imageClarificationError').textContent);
  input.value='1';
  let prevented=false;
  input.dispatch('keydown',{key:'Enter',preventDefault(){prevented=true;},stopPropagation(){}});
  assert.equal(await pending,job);
  assert.ok(prevented,'Enter must not submit the outer generation form');
  assert.equal(elements.get('imageClarification').hidden,true);
  console.log('image clarification ok');
}

main().catch(error=>{console.error(error);process.exitCode=1;});
