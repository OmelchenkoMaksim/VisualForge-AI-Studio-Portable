/* Run the shared deletion control against fake fetch/DOM; no studio or media is touched. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../App/frontend/studio-ui.js'), 'utf8');
const control = source.slice(source.indexOf('  function deleteGenerationButton('), source.indexOf('  function showLogs('));

async function main() {
  let agree = false, calls = [], notices = [], cleaned = 0, fail = false, release, prepared = 0;
  const removed = [];
  const document = {
    createElement: () => ({disabled:false}),
    querySelectorAll: () => ['Image:one', 'Image:other', 'Music:one'].map(key => ({dataset:{jobKey:key}, remove:()=>removed.push(key)}))
  };
  const context = vm.createContext({document, window:{confirm:()=>agree}, notify:value=>notices.push(value), fetch:async(url, options)=>{
    if (url === '/api/history/delete') assert.ok(prepared > calls.length, 'release the player before sending the delete');
    calls.push([url, options]);
    if (release) await new Promise(resolve=>{release.resolve=resolve;});
    return {ok:!fail, json:async()=>fail ? {error:'Файл занят'} : {deleted:true}};
  }});
  require('./i18n_fixture.cjs')(context);
  vm.runInContext(control, context);
  const button = context.deleteGenerationButton('Image', 'one', async()=>{cleaned++;}, ()=>{prepared++;});
  assert.equal(button.textContent, 'Удалить');
  await button.onclick();
  assert.equal(calls.length, 0, 'rejecting confirmation must not send a request');
  assert.equal(prepared, 0, 'rejecting confirmation must preserve playback');
  agree = true; release = {};
  const pending = button.onclick();
  assert.equal(button.disabled, true);
  await button.onclick();
  assert.equal(calls.length, 1, 'a repeated click must not race a pending delete');
  release.resolve(); await pending; release = null;
  assert.equal(cleaned, 1);
  assert.equal(button.disabled, false);
  assert.deepEqual(removed, ['Image:one'], 'other job notifications must survive');
  assert.equal(calls[0][0], '/api/history/delete');
  assert.equal(calls[0][1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[0][1].body), {id:'one'});
  fail = true;
  await button.onclick();
  assert.equal(cleaned, 1, 'an error must leave the history visible for retry');
  assert.match(notices[0].message, /Файл занят/);
  assert.equal(button.disabled, false);
  fail = false;
  for (const module of ['Video', 'Music']) {
    await context.deleteGenerationButton(module, 'two', async()=>{}).onclick();
    assert.equal(calls.at(-1)[0], '/api/' + module.toLowerCase() + '/delete');
  }
  console.log('job deletion UI ok: confirmation, single POST, retry, per-module routes and notification cleanup');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
