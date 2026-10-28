/* Exercise the actual identity card and clipboard serialization without a browser or inference. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../App/frontend/studio-ui.js'), 'utf8');
const block = source.slice(source.indexOf('  function generationInfo('), source.indexOf('  function deleteGenerationButton('));
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.textContent = '';}
  append(...children) {this.children.push(...children);}
  set innerHTML(value) {throw new Error('Metadata must remain plain text; received ' + typeof value);}
}
async function main() {
  let copied;
  const context = vm.createContext({document:{createElement:tag=>new Element(tag)}, navigator:{clipboard:{writeText:async value=>{copied=value;}}}});
  vm.runInContext(block, context);
  const record = {type:'Image', id:'a'.repeat(64), started_at:'2026-10-28T09:00:00.000Z', duration_sec:0};
  const card = context.generationInfo(record);
  assert.equal(card.tag, 'details');
  assert.match(card.children[0].textContent, /Данные генерации/);
  assert.equal(card.children[1].children.length, 8, 'exactly four labelled fields');
  assert.equal(card.children[1].children[3].textContent, record.id, 'full ID stays selectable');
  assert.equal(card.children[1].children[7].textContent, '0 с', 'zero is a valid completed duration');
  await card.children[2].onclick();
  assert.deepEqual(JSON.parse(copied), record);
  const legacy = context.generationInfo({...record, started_at:null, duration_sec:null});
  assert.equal(legacy.children[1].children[5].textContent, 'неизвестно');
  assert.match(legacy.children[1].children[7].textContent, /неизвестно/);
  console.log('generation UI ok: four fields, full ID, copyable object, zero duration and unknown legacy dates');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
