'use strict';
// Engine/mode policy and prompt guides, driven by the readiness shape that /api/preflight returns.
const assert = require('assert');
const path = require('path');
const state = require(path.join(__dirname, '../App/frontend/engine-state.js'));
const guides = require(path.join(__dirname, '../App/frontend/prompt-guides.js'));

const engines = {
  'qwen-edit-2511': {ready: true, modes: ['single', 'reference', 'prompt', 'multi']},
  'qwen-image-21-uc-fp8': {ready: true, modes: ['single', 'reference', 'prompt', 'multi']},
  'z-image-turbo-int8': {ready: true, modes: ['prompt', 'detail', 'control', 'inpaint', 'tile']},
  'flux3-klein-int8': {ready: true, modes: ['single', 'reference', 'prompt', 'multi', 'layout'], expansion: true},
};

// A prompt-only engine cannot generate in an edit mode, ready or not.
assert.strictEqual(state.canGenerate(engines, 'z-image-turbo-int8', 'prompt'), true);
assert.strictEqual(state.canGenerate(engines, 'z-image-turbo-int8', 'single'), false);
assert.strictEqual(state.canGenerate(engines, 'z-image-turbo-int8', 'reference'), false);
assert.strictEqual(state.canGenerate(engines, 'z-image-turbo-int8', 'detail'), true);
assert.strictEqual(state.canGenerate(engines, 'qwen-edit-2511', 'detail'), false);

// Selecting such an engine moves the mode to the one it has; the other engines keep the mode.
assert.strictEqual(state.modeForEngine(engines, 'z-image-turbo-int8', 'single'), 'prompt');
assert.strictEqual(state.modeForEngine(engines, 'z-image-turbo-int8', 'reference'), 'prompt');
assert.strictEqual(state.modeForEngine(engines, 'qwen-image-21-uc-fp8', 'reference'), 'reference');

// Selecting an edit mode with a prompt-only engine falls back to the default engine.
assert.strictEqual(state.engineForMode(engines, 'z-image-turbo-int8', 'single'), 'qwen-edit-2511');
assert.strictEqual(state.engineForMode(engines, 'z-image-turbo-int8', 'prompt'), 'z-image-turbo-int8');

// A saved prompt-only selection survives; the same engine saved with an edit mode falls back.
assert.deepStrictEqual(state.resolveSelection(engines, {engine: 'z-image-turbo-int8', mode: 'prompt'}, null),
  {engine: 'z-image-turbo-int8', mode: 'prompt', fallback: false});
assert.strictEqual(state.resolveSelection(engines, {engine: 'z-image-turbo-int8', mode: 'single'}, null).engine, 'qwen-edit-2511');
assert.strictEqual(state.resolveSelection({...engines, 'z-image-turbo-int8': {ready: false, modes: ['prompt']}},
  {engine: 'z-image-turbo-int8', mode: 'prompt'}, null).fallback, true);
assert.deepStrictEqual(state.resolveSelection(engines, {engine:'z-image-turbo-int8', mode:'detail'}, null),
  {engine:'z-image-turbo-int8', mode:'detail', fallback:false});
assert.deepStrictEqual(state.resolveSelection({...engines, 'z-image-turbo-int8':{ready:true, modes:['prompt']}},
  {engine:'z-image-turbo-int8', mode:'detail'}, null), {engine:'qwen-edit-2511', mode:'single', fallback:true});

// Every engine has a guide for each of its modes, and a prompt-only engine has none for the edit modes.
for (const [id, info] of Object.entries(engines))
  for (const mode of state.MODES)
    assert.strictEqual(Boolean(guides.get(id, mode)), info.modes.includes(mode), id + ' ' + mode);

// Translating backend text must not change the protocol states used to choose a progress message.
const fs = require('fs'), vm = require('vm');
const studioScript = fs.readFileSync(path.join(__dirname, '../App/frontend/image-studio.js'), 'utf8');
const progressSource = studioScript.slice(studioScript.indexOf('function renderLiveStatus()'), studioScript.indexOf('function startLiveStatus('));
let rendered = '';
const progressContext = {
  liveState:{phase:'preparing', detail:'PREPARING DETAIL', startedAt:Date.now(), lastPollAt:Date.now(), promptId:''},
  StudioI18n:{t:(key,args=[])=>key+' '+args.join('|'), remote:value=>({preparing:'Подготовка',running:'В работе',queued:'В очереди',submitted:'Принято',cancelling:'Отмена'}[value] || value)},
  formatDuration:()=> '00:00', setStatusText:value=>{rendered=value;}
};
vm.createContext(progressContext);
vm.runInContext(progressSource, progressContext);
progressContext.renderLiveStatus();
assert.ok(rendered.includes('PREPARING DETAIL'), rendered);
assert.ok(!rendered.includes('comfy_ui_responded'), 'No ComfyUI poll exists during Gemma preparation');
for (const [phase, expected] of Object.entries({submitted:'live_task_submitted',queued:'live_waiting_queue',running:'agent_step_running',cancelling:'live_comfy_ui_finishing'})) {
  progressContext.liveState.phase=phase; progressContext.liveState.promptId='test-job';
  progressContext.renderLiveStatus();
  assert.ok(rendered.includes(expected), phase+': '+rendered);
}
let failedNotifications = 0;
progressContext.finishLiveStatus = value => {rendered=value;};
progressContext.StudioUI = {notify:()=>{failedNotifications++;}};
progressContext.activeJob = null;
vm.runInContext(studioScript.slice(studioScript.indexOf('function reportImageError('), studioScript.indexOf('async function runGeneration(')), progressContext);
progressContext.reportImageError({message:'cancelled', cancelled:true});
assert.ok(rendered.includes('image.prompt_manager_cancelled'));
assert.strictEqual(failedNotifications, 0, 'An acknowledged Stop is not a failed image notification');
progressContext.reportImageError({message:'real failure'});
assert.strictEqual(failedNotifications, 1);

console.log('engine state ok');
