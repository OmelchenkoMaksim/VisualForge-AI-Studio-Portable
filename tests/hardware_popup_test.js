/* Compatibility cards: user preferences, expiry and delayed-response races without a browser or GPU. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../App/frontend/hardware.js'), 'utf8');
const key = 'vf-compatibility-popup';
const modules = {engineSelect: 'image', videoEngine: 'video', musicEngine: 'music', textModel: 'text'};
class Element {
  constructor(id = '') {this.id = id;this.hidden = false;this.visible = false;this.dataset = {};this.events = {};this.children = [];}
  addEventListener(type, fn) {(this.events[type] ||= []).push(fn);}
  fire(type, extra = {}) {const event = {target: this, ...extra};for (const fn of this.events[type] || []) fn(event);if (this['on' + type]) this['on' + type](event);}
  setAttribute(name, value) {this[name] = value;}
  getClientRects() {return this.visible ? [{}] : [];}
  getAnimations() {return [];} // No CSS animations in this DOM fixture.
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this.children = items;}
  showModal() {this.open = true;}
  close() {this.open = false;}
}
function boot({stored, storageFails = false, response} = {}) {
  const elements = new Map(), timers = new Map(), observers = [], storage = new Map();
  if (stored !== undefined) storage.set(key, stored);
  const el = id => {if (!elements.has(id)) elements.set(id, new Element(id));return elements.get(id);};
  const document = new Element();document.body = new Element('body');
  document.getElementById = el;document.querySelector = () => el('tabs');document.createElement = () => new Element();document.dispatchEvent = () => {};
  const card = el('compatibilityCard');card.hidden = true;
  const hardware = {cpu: 'CPU', ram_gb: 64, gpus: [{name: 'GPU', vram_gb: 16, cuda: true}]};
  const models = {};
  for (const [id, module] of Object.entries(modules)) {
    const select = el(id);select.value = module + '-model';select.selectedOptions = [{textContent: module + ' model'}];select.visible = id === 'engineSelect';
    models[module + ':' + select.value] = {status: 'green', title: 'Compatible', reasons: [], profile: {name: 'Model', note: 'Note', recommended_vram_gb: 16, recommended_ram_gb: 32}, actual_vram_gb: 16, actual_ram_gb: 64, caveat: 'Estimate', gpu_name: 'GPU'};
  }
  let now = 0, nextTimer = 0;
  const context = {document, window: {addEventListener() {}}, Event: function(type) {this.type = type;},
    localStorage: {getItem: name => {if (storageFails) throw new Error('storage');return storage.get(name) || null;}, setItem: (name, value) => {if (storageFails) throw new Error('storage');storage.set(name, value);}},
    MutationObserver: class {constructor(fn) {this.fn = fn;} observe(target, options) {observers.push({target, options, fn: this.fn});}},
    fetch: async () => ({ok: true, json: async () => response ? await response : {hardware, models}}),
    setTimeout: (fn, delay) => {const id = ++nextTimer;timers.set(id, {fn, at: now + delay});return id;}, clearTimeout: id => timers.delete(id)};
  vm.createContext(context);require('./i18n_fixture.cjs')(context);vm.runInContext(source, context);
  function advance(ms) {
    const target = now + ms;
    for (;;) {
      const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at;timers.delete(next[0]);next[1].fn();
    }
    now = target;
  }
  function select(id) {for (const name of Object.keys(modules)) el(name).visible = name === id;document.fire('change', {target: el(id)});}
  function toggle(enabled) {el('compatibilityPopupEnabled').checked = enabled;el('compatibilityPopupEnabled').fire('change');}
  function duration(seconds) {el('compatibilityPopupSeconds').value = String(seconds);el('compatibilityPopupSeconds').fire('input');}
  function changeSection() {for (const observer of observers) if (observer.target === document.body) observer.fn();}
  return {el, card, document, storage, advance, select, toggle, duration, changeSection, result: {hardware, models}, timers};
}
async function settle() {for (let i = 0; i < 12; i++) await Promise.resolve();}
(async () => {
  const app = boot();await settle();assert.equal(app.card.hidden, true, 'initial hardware load must not open a card');
  for (const id of Object.keys(modules)) {
    app.select(id);assert.equal(app.card.hidden, false, id);
    app.advance(2999);assert.equal(app.card.hidden, false);
    app.advance(1);assert.equal(app.card.hidden, true, 'default lifetime is three seconds');
  }
  app.select('engineSelect');app.advance(2000);app.select('videoEngine');app.advance(1000);assert.equal(app.card.hidden, false);
  app.advance(2000);assert.equal(app.card.hidden, true, 'a model change replaces the old timer');
  app.el('openCompatibilitySettings').fire('click');assert.equal(app.el('compatibilitySettingsDialog').open, true);
  app.duration(5);assert.equal(app.el('compatibilityPopupSecondsValue').textContent, '5 с');
  app.el('compatibilitySettingsDone').fire('click');app.select('musicEngine');app.advance(4999);assert.equal(app.card.hidden, false);app.advance(1);assert.equal(app.card.hidden, true);
  app.toggle(false);assert.equal(app.el('compatibilityPopupSeconds').disabled, true);
  for (const id of Object.keys(modules)) {app.select(id);assert.equal(app.card.hidden, true, 'one preference covers every module');}
  app.el('hardwareCompatibility').fire('click');assert.equal(app.card.hidden, false);
  app.advance(60000);assert.equal(app.card.hidden, false, 'manual cards remain readable');app.el('compatibilityClose').fire('click');assert.equal(app.card.hidden, true);
  const restored = boot({stored: app.storage.get(key)});await settle();restored.select('videoEngine');assert.equal(restored.card.hidden, true);
  restored.el('openCompatibilitySettings').fire('click');assert.equal(restored.el('compatibilityPopupSeconds').value, '5');assert.equal(restored.el('compatibilityPopupEnabled').checked, false);
  app.toggle(true);app.select('engineSelect');app.document.fire('keydown', {key: 'Escape'});assert.equal(app.card.hidden, true);assert.equal(app.timers.size, 0);
  app.select('engineSelect');app.changeSection();assert.equal(app.card.hidden, true);assert.equal(app.timers.size, 0);
  app.select('engineSelect');app.toggle(false);assert.equal(app.card.hidden, true);
  for (const stored of ['{bad json', 'null', '{"enabled":"no","seconds":"wrong"}']) {
    const defaults = boot({stored});await settle();defaults.select('engineSelect');assert.equal(defaults.card.hidden, false);defaults.advance(3000);assert.equal(defaults.card.hidden, true);
  }
  const blocked = boot({storageFails: true});await settle();blocked.toggle(false);blocked.toggle(true);blocked.select('engineSelect');blocked.advance(3000);assert.equal(blocked.card.hidden, true);
  for (const [seconds, expected] of [[0, 1], [100, 30]]) {
    const bounded = boot({stored: JSON.stringify({enabled: true, seconds})});await settle();bounded.el('openCompatibilitySettings').fire('click');assert.equal(bounded.el('compatibilityPopupSeconds').value, String(expected));
  }
  for (const dismiss of [app => app.el('compatibilityClose').fire('click'), app => app.toggle(false), app => app.changeSection()]) {
    let release;const response = new Promise(resolve => {release = resolve;});const delayed = boot({response});delayed.select('engineSelect');dismiss(delayed);release(delayed.result);await settle();assert.equal(delayed.card.hidden, true, 'a late response must not undo dismissal');
  }
  let release;const response = new Promise(resolve => {release = resolve;});const latest = boot({response});latest.select('engineSelect');latest.select('videoEngine');release(latest.result);await settle();
  assert.equal(latest.card.hidden, false);assert.equal(latest.el('compatibilityModel').textContent, 'video model');latest.advance(3000);assert.equal(latest.card.hidden, true);
  console.log('hardware_popup_test: PASS (timers, four modules, manual access, preferences, persistence, invalid storage, delayed responses)');
})().catch(error => {console.error(error);process.exitCode = 1;});
