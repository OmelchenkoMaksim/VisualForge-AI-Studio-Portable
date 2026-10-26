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
  const api = {DEFAULT_ENGINE, MODES, chooseEngine, canGenerate, resolveSelection};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QwenEngineState = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
