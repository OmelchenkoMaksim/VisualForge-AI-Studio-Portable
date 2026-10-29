/* Voice bar regressions without a microphone, GPU or network. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../App/frontend/voice-input.js'), 'utf8');
const key = 'visualforge.voice.enhance.v1';

class Element {
  constructor(tag = '') {
    this.tagName = tag;this.dataset = {};this.children = [];this.events = {};this.style = {};this.attributes = {};
    this.hidden = false;this.checked = false;this.disabled = false;this.textContent = '';this.value = '';
    const values = new Set();
    this.classList = {add: value => values.add(value), remove: value => values.delete(value), contains: value => values.has(value),
      toggle: (value, enabled) => {if (enabled) values.add(value);else values.delete(value);}};
  }
  addEventListener(type, handler) {(this.events[type] ||= []).push(handler);}
  fire(type, extra = {}) {
    const event = {target: this, preventDefault() {}, ...extra};
    for (const handler of this.events[type] || []) handler(event);
    this['on' + type]?.(event);
  }
  append(...children) {for (const child of children) {child.parent = this;this.children.push(child);}}
  setAttribute(name, value) {this.attributes[name] = value;}
  getAttribute(name) {return this.attributes[name] ?? null;}
  contains(element) {return !!element && (element === this || this.children.some(child => child.contains(element)));}
  closest() {for (let node = this; node; node = node.parent) if (node.id === 'voiceBar' || node.id === 'studioBusy') return node;return null;}
  getBoundingClientRect() {return {top:200, right:600};}
  get offsetHeight() {return 49;}
}
class TextArea extends Element {}
class Input extends Element {}

function boot({stored, unavailable = false, storageFails = false} = {}) {
  const document = new Element(), window = new Element(), storage = new Map(), timers = new Map(), requests = [];
  let now = 0, serial = 0, busy = false, status = {loaded:[], generating:[]}, failStatus = false, prepareAccepted = true;
  if (stored !== undefined) storage.set(key, stored);
  document.body = new Element();document.activeElement = null;
  document.createElement = tag => tag === 'input' ? new Input(tag) : new Element(tag);
  document.querySelector = () => null;
  window.innerWidth = 800;window.StudioUI = {isBusy: () => busy};
  const schedule = (handler, delay) => {const id = ++serial;timers.set(id, {handler, at:now + delay});return id;};
  const cancel = id => timers.delete(id);
  const localStorage = {getItem: name => {if (storageFails) throw Error('blocked');return storage.get(name) ?? null;},
    setItem: (name, value) => {if (storageFails) throw Error('blocked');storage.set(name, value);}};
  const fetch = async (url, options = {}) => {
    requests.push({url, body: options.body && JSON.parse(options.body)});
    if (url === '/api/speech/availability') return {ok:true, json:async () => ({available:true, enhance:{available:!unavailable, model:'gemma:test', reason:'Нет модели'}})};
    if (url === '/api/speech/prepare') return {ok:true, json:async () => ({preparing:prepareAccepted})};
    if (url === '/api/text/status') {
      if (failStatus) throw Error('offline');
      const value = JSON.parse(JSON.stringify(status));
      return {ok:true, json:async () => value};
    }
    throw Error('Unexpected endpoint ' + url);
  };
  const context={document, window, localStorage, fetch, HTMLElement:Element, HTMLTextAreaElement:TextArea,
    HTMLInputElement:Input, setTimeout:schedule, clearTimeout:cancel, setInterval:schedule, clearInterval:cancel, Date:{now:()=>now}};
  require('./i18n_fixture.cjs')(context);vm.runInContext(source,context);
  const bar = document.body.children[0];
  const find = className => bar.children.find(child => child.className === className);
  const enhance = find('voice-enhance'), checkbox = enhance.children[0], hint = find('voice-hint'), label = find('voice-label');
  const field = new TextArea();field.value = '';
  const focus = element => {
    const before = document.activeElement;
    document.activeElement = element;
    if (before) document.fire('focusout', {target:before, relatedTarget:element});
    if (bar.contains(element)) bar.fire('focusin', {target:element});
    document.fire('focusin', {target:element});
  };
  const toggle = value => {checkbox.checked = value;checkbox.fire('change');};
  const advance = async ms => {
    now += ms;
    const ready = [...timers].filter(([, value]) => value.at <= now);
    for (const [id, value] of ready) if (timers.delete(id)) value.handler();
    await settle();
  };
  const setBusy = (value, kind) => {busy = value;window.fire(value ? 'studio:busy' : 'studio:idle', {detail:{kind}});};
  return {bar, checkbox, hint, label, enhance, field, focus, toggle, advance, setBusy, storage, requests,
    status: value => {status = value;}, fail: () => {failStatus = true;}, refusePrepare: () => {prepareAccepted = false;}};
}
async function settle() {for (let i = 0; i < 20; i++) await Promise.resolve();}
const preparations = app => app.requests.filter(r => r.url === '/api/speech/prepare' && r.body?.enhance).length;

(async () => {
  const app = boot();
  assert.equal(app.checkbox.checked, false, 'new profiles default to off');
  assert.equal(app.enhance.children[2].textContent, 'Gemma AI');
  app.focus(app.field);await settle();
  assert.equal(preparations(app), 0, 'default voice input must not warm Gemma');
  assert.equal(app.requests.filter(r => r.url === '/api/text/status').length, 0);
  app.focus(app.checkbox);app.toggle(true);await settle();
  await app.advance(500);
  assert.equal(app.bar.hidden, false, 'checkbox focus must not dismiss the bar');
  assert.equal(app.storage.get(key), '1');
  assert.equal(preparations(app), 1);
  assert.equal(app.label.textContent, '', 'loading text must stay out of the inline label');
  assert.equal(app.hint.classList.contains('visible'), true);
  await app.advance(9000);
  assert.equal(app.hint.classList.contains('visible'), true, 'loading must not expire on a fixed eight-second timer');
  app.status({loaded:[{name:'gemma:test'}], generating:['voice-polish']});await app.advance(1000);
  assert.equal(app.hint.classList.contains('visible'), true, 'wait for the warmup request to finish');
  app.status({loaded:[{name:'gemma:test'}], generating:[]});await app.advance(1000);
  assert.equal(app.hint.classList.contains('visible'), false, 'ready model hides the hint');
  assert.equal(app.bar.hidden, false);
  assert.equal(preparations(app), 1, 'polling must not start repeated inference requests');

  const restored = boot({stored:app.storage.get(key)});
  restored.status({loaded:[{name:'gemma:test'}], generating:[]});restored.focus(restored.field);await settle();
  assert.equal(restored.checkbox.checked, true);
  assert.equal(preparations(restored), 0, 'an already loaded model needs no warmup');
  restored.toggle(false);assert.equal(restored.storage.get(key), '0');
  assert.equal(boot({stored:restored.storage.get(key)}).checkbox.checked, false);

  const busy = boot({stored:'1'});busy.setBusy(true, 'generation');busy.focus(busy.field);await settle();
  assert.match(busy.hint.textContent, /ждёт/);assert.equal(preparations(busy), 0);
  busy.setBusy(false, 'generation');await busy.advance(1000);
  assert.equal(preparations(busy), 1);
  busy.setBusy(true, 'voice');assert.equal(busy.enhance.classList.contains('blocked'), false);
  busy.toggle(false);await busy.advance(2000);
  assert.equal(busy.hint.classList.contains('visible'), false, 'late polls must not reopen a disabled enhancement');

  const ownWarmup = boot({stored:'1'});ownWarmup.status({loaded:[], generating:['voice-polish']});ownWarmup.setBusy(true, 'voice');
  ownWarmup.focus(ownWarmup.field);await settle();assert.equal(preparations(ownWarmup), 0, 'observe an existing warmup');
  ownWarmup.focus(new Element());await ownWarmup.advance(250);
  assert.equal(ownWarmup.bar.hidden, true, 'leaving both field and bar still dismisses the UI');
  const requests = ownWarmup.requests.length;await ownWarmup.advance(5000);assert.equal(ownWarmup.requests.length, requests);

  for (const options of [{stored:'bad'}, {storageFails:true}]) {
    const defaults = boot(options);assert.equal(defaults.checkbox.checked, false);defaults.focus(defaults.field);await settle();
    defaults.toggle(true);await settle();assert.equal(defaults.bar.hidden, false);
  }
  const absent = boot({stored:'1', unavailable:true});absent.focus(absent.field);await settle();
  assert.equal(absent.checkbox.checked, true, 'temporary unavailability must not erase saved intent');
  assert.equal(preparations(absent), 0);
  for (const fail of [app => app.fail(), app => app.refusePrepare()]) {
    const failed = boot({stored:'1'});fail(failed);failed.focus(failed.field);await settle();
    assert.equal(failed.hint.classList.contains('visible'), true);
    assert.doesNotMatch(failed.hint.textContent, /загружается/);
    await failed.advance(8000);assert.equal(failed.hint.classList.contains('visible'), false);
  }
  console.log('voice UI ok: default-off, persistence, focus scope, loading/readiness, busy GPU, cancellation and errors');
})().catch(error => {console.error(error);process.exitCode = 1;});
