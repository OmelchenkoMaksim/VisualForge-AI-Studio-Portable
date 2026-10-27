/* Pure engine selection policy, shared by browser and source tests. */
(function (root) {
  'use strict';
  const DEFAULT_ENGINE = 'qwen-edit-2511';
  function chooseEngine(engines, preferred, mode) {
    const usable = id => Boolean(engines[id] && engines[id].ready && engines[id].modes.includes(mode));
    return usable(preferred) ? preferred : DEFAULT_ENGINE;
  }
  function canGenerate(engines, id, mode) {
    return Boolean(engines[id] && engines[id].ready && engines[id].modes.includes(mode));
  }
  const MODES = ['single', 'reference', 'prompt'];
  function resolveSelection(engines, saved, requestedMode) {
    const mode = MODES.includes(requestedMode) ? requestedMode :
      (saved && MODES.includes(saved.mode) ? saved.mode : 'single');
    const preferred = saved && typeof saved.engine === 'string' ? saved.engine : DEFAULT_ENGINE;
    const engine = chooseEngine(engines, preferred, mode);
    return {engine, mode, fallback: engine !== preferred};
  }
  function supportsMode(engines, id, mode) {
    return Boolean(engines[id] && engines[id].modes.includes(mode));
  }
  // Prompt-only engines (no image inputs) must not stay in single/reference: move to a mode the engine really has.
  function modeForEngine(engines, id, mode) {
    if (supportsMode(engines, id, mode)) return mode;
    return (engines[id] && MODES.find(m => engines[id].modes.includes(m))) || mode;
  }
  // The reverse move: a mode the selected engine lacks switches to the default engine, else to any ready engine that has it.
  function engineForMode(engines, id, mode) {
    if (supportsMode(engines, id, mode)) return id;
    if (supportsMode(engines, DEFAULT_ENGINE, mode)) return DEFAULT_ENGINE;
    return Object.keys(engines).find(key => engines[key].ready && supportsMode(engines, key, mode)) || id;
  }
  const api = {DEFAULT_ENGINE, MODES, chooseEngine, canGenerate, resolveSelection, supportsMode, modeForEngine, engineForMode};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QwenEngineState = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
