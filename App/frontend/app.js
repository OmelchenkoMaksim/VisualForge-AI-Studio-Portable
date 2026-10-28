'use strict';
const byId = (id) => document.getElementById(id);
const requestedMode = new URLSearchParams(location.search).get('mode');
// Opened by the launcher with ?launch=<nonce>: its splash waits until this page reports that the ComfyUI check is done.
const launchToken = new URLSearchParams(location.search).get('launch');
if (launchToken) {
  const params = new URLSearchParams(location.search); params.delete('launch');
  history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : '') + location.hash);
}
function reportLaunch(ok) {
  if (!launchToken) return;
  fetch('/api/launch-ready', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({launch:launchToken, ok})}).catch(() => {});
}
let mode = 'single';
const modeSelect = byId('modeSelect');
function renderMode() {
  modeSelect.value = mode;
  StudioI18n.bind(byId('title'), "textContent", () => ('VisualForge AI Studio'), true);
  StudioUI.setHeader('image', {description: {single:StudioI18n.t("image.mode_source_image_editing_instruction"), reference:StudioI18n.t("image.mode_base_image_reference"), prompt:StudioI18n.t("image.mode_create_image_text")}[mode]});
  byId('referenceBlock').hidden = mode !== 'reference';
  byId('image2').required = mode === 'reference';
  byId('image2').disabled = mode !== 'reference';
  byId('photosBlock').hidden = mode === 'prompt';
  byId('pasteStatus').hidden = mode === 'prompt';
  byId('image1').required = mode !== 'prompt';
  byId('image1').disabled = mode === 'prompt';
  byId('promptOnlyControls').hidden = mode !== 'prompt';
  StudioI18n.bind(byId('promptLabelText'), "textContent", () => (mode === 'prompt' ? StudioI18n.t("image.prompt_what_should_created") : StudioI18n.t("image.prompt_what_should_change")), true);
  StudioI18n.bind(byId('prompt'), "placeholder", () => (mode === 'prompt' ? StudioI18n.t("image.prompt_describe_image_you_want") : StudioI18n.t("image.prompt_describe_what_change_preserve")), true);
  StudioI18n.bind(byId('generate'), "textContent", () => (mode === 'prompt' ? StudioI18n.t("image.generation_create_from_prompt_button_label") : StudioI18n.t("image.generation_create_image_button_label")), true);
  if (mode !== 'reference') choosePasteTarget('image1');
}
function readPreferredSelection() {
  try {
    const value = JSON.parse(localStorage.getItem('qwen.uiSelection.v1') || 'null');
    if (value && typeof value === 'object' && !Array.isArray(value)) return value;
    return {engine:localStorage.getItem('qwen.successfulEngine') || 'qwen-edit-2511',mode:'single'};
  } catch { return null; }
}
function rememberSelection() {
  if (!engineReady()) return;
  try { localStorage.setItem('qwen.uiSelection.v1', JSON.stringify({engine:selectedEngine,mode})); } catch { /* Storage can be unavailable. */ }
}
const MODE_NAMES = {single:'Single', reference:'Reference', prompt:'Prompt'};
// Wording that differs per engine; Qwen engines use the generic native_prompt branches in renderEngine().
const ENGINE_TEXT = {
  'flux3-klein-int8': {
    header: StudioI18n.t("image.app_engine_header_text_image_generation_flux_3_klein"),
    promptHint: StudioI18n.t("image.app_engine_prompt_hint_native_text_image_generation_single_editing"),
    transparency: StudioI18n.t("image.app_engine_transparency_flux_3_klein_outputs_rgb_without")
  },
  'z-image-turbo-int8': {
    header: StudioI18n.t("image.app_engine_header_text_image_generation_z_turbo"),
    promptHint: StudioI18n.t("image.app_engine_prompt_hint_native_text_image_generation_mode_up"),
    transparency: StudioI18n.t("image.app_engine_transparency_z_image_turbo_outputs_rgb_without")
  }
};
modeSelect.addEventListener('change', () => {
  if (busy) { modeSelect.value = mode; return; }
  if (!QwenEngineState.MODES.includes(modeSelect.value)) { modeSelect.value = mode; return; }
  mode = modeSelect.value;
  const previous = selectedEngine;
  selectedEngine = QwenEngineState.engineForMode(engineCatalog, selectedEngine, mode);
  StudioI18n.bind(byId('selectionNotice'), "textContent", () => (selectedEngine !== previous
    ? StudioI18n.t("image.selection_notice_does_not_support_mode_selected_model", [engineCatalog[previous].name, StudioI18n.remote(MODE_NAMES[mode]), engineCatalog[selectedEngine].name]) : ''), true);
  renderMode(); renderEngine(); rememberSelection();
  if (!engineReady() && QwenEngineState.supportsMode(engineCatalog, selectedEngine, mode)) window.repairStudioModel?.('image', selectedEngine);
});

let QUALITY_STEPS = {low:40, good:50, high:60};
let QUALITY_HINTS = {
  low:StudioI18n.t("image.app_quality_hints_low_40_steps_verified_baseline"),
  good:StudioI18n.t("image.app_quality_hints_good_50_steps_more_computation_improvements_depend"),
  high:StudioI18n.t("image.app_quality_hints_high_60_steps_maximum_sampling_budget_slower")
};
const engineSelect = byId('engineSelect');
let engineCatalog = {};
let selectedEngine = 'qwen-edit-2511';
let engineInitialized = false;
let progressWsUrl = 'ws://127.0.0.1:8188/ws';
function readPreferredEngine() {
  try { return localStorage.getItem('qwen.successfulEngine'); } catch { return null; }
}
function rememberEngine(id) {
  try { localStorage.setItem('qwen.successfulEngine', id); } catch { /* Storage may be disabled. */ }
}
function engineReady() { return QwenEngineState.canGenerate(engineCatalog, selectedEngine, mode); }
function renderEngine() {
  const info = engineCatalog[selectedEngine];
  engineSelect.value = selectedEngine;
  if (info) {
    renderPromptGuide();
    QUALITY_STEPS = info.quality_steps;
    const sampler = info.sampler[0].toUpperCase() + info.sampler.slice(1).replace(/_/g, ' ');
    const presets = Object.keys(QUALITY_STEPS);
    QUALITY_HINTS = Object.fromEntries(Object.entries(QUALITY_STEPS).map(([key, steps]) =>
      [key, StudioI18n.t("image.engine_steps_cfg", [steps, sampler, info.cfg, presets.length === 1 ? StudioI18n.t("image.engine_model_s_preset") : (info.native_prompt && key !== 'low' ? StudioI18n.t("image.engine_experimental_preset") : '')])]));
    // A model with a single preset hides the other Quality choices instead of leaving dead options.
    for (const item of document.querySelectorAll('input[name="quality"]')) {
      item.closest('label').hidden = !presets.includes(item.value);
      if (presets.includes(item.value)) StudioI18n.bind(item.nextElementSibling, "textContent", () => (presets.length === 1 ? StudioI18n.t("image.next_element_sibling_steps", [QUALITY_STEPS[item.value]]) : item.value[0].toUpperCase() + item.value.slice(1)), true);
    }
    if (!presets.includes(selected('quality'))) document.querySelector(`input[name="quality"][value="${presets[0]}"]`).checked = true;
    for (const option of modeSelect.options) {
      option.hidden = option.disabled = !info.modes.includes(option.value);
    }
    const text = ENGINE_TEXT[selectedEngine] || {};
    byId('expandGroup').hidden = !info.expansion;
    StudioI18n.bind(byId('engineStatus'), "textContent", () => (`${info.name} · ${info.ready ? StudioI18n.t("image.engine_installed_workflow_verified") : StudioI18n.t("image.model_availability_unavailable_label")} · ${{single:StudioI18n.t("image.engine_single_image"),reference:StudioI18n.t("image.engine_reference_mode_label"),prompt:StudioI18n.t("image.engine_text_to_image_mode_label")}[mode]}`), true);
    StudioI18n.bind(byId('engineCapabilities'), "textContent", () => (info.capabilities), true);
    StudioI18n.bind(byId('transparencyHint'), "textContent", () => (text.transparency || (info.native_prompt
      ? StudioI18n.t("image.transparency_hint_qwen_2_1_supports_rgba_but")
      : StudioI18n.t("image.transparency_hint_qwen_2511_outputs_rgb_source_not"))), true);
    StudioUI.setHeader('image', {model: info.name});
    if (mode === 'prompt') {
      StudioUI.setHeader('image', {description: text.header || (info.native_prompt ? StudioI18n.t("image.engine_generate_image_directly_text") : StudioI18n.t("image.engine_qwen_image_edit_blank_canvas"))});
      StudioI18n.bind(byId('promptEngineHint'), "textContent", () => (text.promptHint || (info.native_prompt
        ? StudioI18n.t("image.prompt_engine_hint_native_text_image_generation_up_1024")
        : StudioI18n.t("image.prompt_engine_hint_qwen_image_edit_2511_receives_blank"))), true);
    }
    StudioUI.setHeader('image', {tone: info.ready ? 'good' : 'bad',
      status: info.ready ? StudioI18n.t("image.engine_comfy_ui_dependencies_workflow_schema_available") : info.reasons.join('; ')});
  }
  byId('guideUseExample').disabled = busy;
  byId('generate').disabled = busy || !engineReady();
  engineSelect.disabled = busy || !engineInitialized;
  modeSelect.disabled = busy || !engineInitialized;
  refreshSettings();
  refreshVariantButton();
}
engineSelect.addEventListener('change', () => {
  if (busy) { engineSelect.value = selectedEngine; return; }
  if (!engineCatalog[engineSelect.value]) { engineSelect.value = selectedEngine; return; }
  selectedEngine = engineSelect.value;
  const nextMode = QwenEngineState.modeForEngine(engineCatalog, selectedEngine, mode);
  StudioI18n.bind(byId('selectionNotice'), "textContent", () => (nextMode !== mode
    ? StudioI18n.t("image.selection_notice_supports_mode_has_been_changed", [engineCatalog[selectedEngine].name, StudioI18n.remote(MODE_NAMES[nextMode])]) : ''), true);
  mode = nextMode;
  renderMode(); renderEngine(); rememberSelection();
  if (!engineReady()) window.repairStudioModel?.('image', selectedEngine);
});

function renderPromptGuide() {
  const guide = VisualForgeGuides.get(selectedEngine, mode);
  if (!guide) return;
  const fields = {promptGuideTitle:'title',guidePurpose:'purpose',guideStructure:'structure',guideLanguage:'language',guideMistakes:'mistakes',guideExample:'example',guideQuality:'quality'};
  for (const [id,key] of Object.entries(fields)) StudioI18n.bind(byId(id), "textContent", () => (guide[key]), true);
}
byId('guideUseExample').addEventListener('click', () => {
  if (busy) return;
  const guide = VisualForgeGuides.get(selectedEngine, mode);
  if (guide) { byId('prompt').value = VisualForgeGuides.append(byId('prompt').value, guide.example).slice(0,5000); byId('prompt').dispatchEvent(new Event('input')); }
});

let upscaleAvailable = false;
const selected = (name) => document.querySelector(`input[name="${name}"]:checked`).value;
function refreshSettings() {
  const q = selected('quality');
  const u = Number(selected('upscale'));
  StudioI18n.bind(byId('qualityHint'), "textContent", () => (QUALITY_HINTS[q]), true);
  StudioI18n.bind(byId('upscaleHint'), "textContent", () => (u===1
    ? StudioI18n.t("image.upscale_hint_no_original_qwen_resolution")
    : (u===2 ? StudioI18n.t("image.upscale_hint_ai_2_width_height_4_pixels") : StudioI18n.t("image.upscale_hint_ai_4_width_height_16_pixels"))), true);
  if (u > 1 && !upscaleAvailable) {
    StudioI18n.append(byId('upscaleHint'), "textContent", () => (StudioI18n.t("image.upscale_hint_model_has_not_yet_been_confirmed")), true);
  }
  const info = engineCatalog[selectedEngine];
  StudioI18n.bind(byId('settingsSummary'), "textContent", () => (`${info ? info.name : 'Qwen 2511'} · ${QUALITY_STEPS[q]} steps · CFG ${info ? info.cfg : 4} · Upscale ${u===1?'Off':u+'×'}`), true);
}
for (const item of document.querySelectorAll('input[name="quality"],input[name="upscale"],input[name="canvas"]')) {
  item.addEventListener('change', refreshSettings);
}
refreshSettings();
function lockPresets(locked) {
  byId('seed').disabled = locked;
  byId('randomSeed').disabled = locked;
  byId('reuseSeed').disabled = locked || lastSeed === null;
  byId('expandPrompt').disabled = locked;
  for (const button of document.querySelectorAll('.history-actions button')) button.disabled = locked;
  engineSelect.disabled = locked || !engineInitialized;
  modeSelect.disabled = locked || !engineInitialized;
  for (const item of document.querySelectorAll('input[name="quality"],input[name="upscale"],input[name="canvas"]')) {
    item.disabled = locked;
  }
}

let busy = false;
let lastSeed = null;
let activeJob = null;
let cancellationInFlight = false;
let hasSuccessfulResult = false;
let lastResultInfo = null; // VARIANT_PATCH_V2
let liveState = null;
let statusTicker = null;
const cancelButton = byId('cancel');
const variantButton = byId('variant');
const saveDirInput = byId('saveDir');
const chooseSaveDirButton = byId('chooseSaveDir');
const saveSaveDirButton = byId('saveSaveDir');
const openSaveDirButton = byId('openSaveDir');
const saveDirStatus = byId('saveDirStatus');

function formatDuration(totalSeconds) {
  const sec = Math.max(0, Number(totalSeconds) || 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h > 0
    ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
    : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function clearStatusTicker() {
  if (statusTicker) {
    clearInterval(statusTicker);
    statusTicker = null;
  }
}
function setStatusText(text) {
  StudioI18n.bind(byId('status'), "textContent", () => (text), true);
}
function renderLiveStatus() {
  if (!liveState) return;
  const now = Date.now();
  const elapsedSec = Math.floor((now - liveState.startedAt) / 1000);
  const sinceUpdateSec = Math.floor((now - liveState.lastPollAt) / 1000);
  let firstLine = StudioI18n.t("image.generation_live_status_prefix");
  if (StudioI18n.remote(liveState.phase) === 'preparing') {
    firstLine += liveState.detail || StudioI18n.t("image.live_preparing_data");
  } else if (StudioI18n.remote(liveState.phase) === 'submitted') {
    firstLine += StudioI18n.t("image.live_task_submitted_comfy_ui");
    if (liveState.promptId) firstLine += ` · ID ${liveState.promptId}`;
  } else if (StudioI18n.remote(liveState.phase) === 'queued') {
    firstLine += StudioI18n.t("image.live_waiting_queue");
    if (Number.isFinite(liveState.queuePosition)) {
      firstLine += StudioI18n.t("image.generation_queue_position_template", [liveState.queuePosition]);
      if (Number.isFinite(liveState.queueTotal)) firstLine += StudioI18n.t("image.generation_queue_total_template", [liveState.queueTotal]);
    }
  } else if (StudioI18n.remote(liveState.phase) === 'running') {
    firstLine += StudioI18n.t("image.agent_step_running_status");
    if (liveState.progressPct !== null && liveState.progressPct !== undefined) {
      firstLine += ` · ${liveState.progressPct}%`;
    }
    if (liveState.node) {
      firstLine += ` · ${liveState.node}`;
    } else if (liveState.detail) {
      firstLine += ` · ${liveState.detail}`;
    }
  } else if (StudioI18n.remote(liveState.phase) === 'cancelling') {
    firstLine += StudioI18n.t("image.live_comfy_ui_finishing_interrupted_task");
  }
  let secondLine = StudioI18n.t("image.live_elapsed_comfy_ui_responded_s_ago", [formatDuration(elapsedSec), sinceUpdateSec]);
  if (liveState.detail2) secondLine += ` · ${liveState.detail2}`;
  setStatusText(`${firstLine}\n${secondLine}`);
}
function startLiveStatus(phase, detail) {
  liveState = {
    phase,
    detail: detail || '',
    detail2: '',
    queuePosition: null,
    queueTotal: null,
    progressPct: null,
    node: '',
    promptId: '',
    startedAt: Date.now(),
    lastPollAt: Date.now()
  };
  clearStatusTicker();
  renderLiveStatus();
  statusTicker = setInterval(renderLiveStatus, 1000);
}
function updateLiveStatus(patch) {
  if (!liveState) startLiveStatus('submitted', '');
  Object.assign(liveState, patch || {});
  liveState.lastPollAt = Date.now();
  renderLiveStatus();
}
function finishLiveStatus(message) {
  clearStatusTicker();
  liveState = null;
  setStatusText(message);
}
function refreshVariantButton() {
  variantButton.hidden = false;
  // A variant is an edit of the previous result, so engines without the single mode cannot make one.
  const canEdit = QwenEngineState.supportsMode(engineCatalog, selectedEngine, 'single');
  variantButton.disabled = busy || !canEdit || !hasSuccessfulResult || !lastResultInfo || !engineReady() || lastResultInfo.engine !== selectedEngine || lastResultInfo.mode !== mode;
  StudioI18n.bind(variantButton, "title", () => (!canEdit
    ? StudioI18n.t("image.variant_button_model_does_not_edit_images_change")
    : !lastResultInfo
    ? StudioI18n.t("image.variant_button_generate_image_first")
    : StudioI18n.t("image.variant_button_create_similar_variation_latest_result_target")), true);
}
function clearTask() {
  busy = false;
  activeJob = null;
  cancellationInFlight = false;
  byId('generate').disabled = !engineReady();
  lockPresets(false);
  engineSelect.disabled = !engineInitialized;
  cancelButton.disabled = true;
  cancelButton.hidden = true;
  StudioI18n.bind(cancelButton, "textContent", () => (StudioI18n.t("image.cancel_button_stop_generation")), true);
  refreshVariantButton();
}
function beginTask() {
  qwenResetProgressUI();
  busy = true;
  byId('generate').disabled = true;
  lockPresets(true);
  variantButton.disabled = true;
  cancelButton.hidden = false;
  cancelButton.disabled = true;
}
cancelButton.addEventListener('click', async () => {
  if (!busy || !activeJob || cancellationInFlight) return;
  cancellationInFlight = true;
  cancelButton.disabled = true;
  StudioI18n.bind(cancelButton, "textContent", () => (StudioI18n.t("image.cancel_button_stopping")), true);
  updateLiveStatus({phase:'cancelling', detail:StudioI18n.t("image.cancel_button_stopping_task")});
  try {
    const result = await request('/api/cancel', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(activeJob)
    });
    if (result.cancelled) {
      updateLiveStatus({phase:'cancelling', detail:StudioI18n.t("image.cancel_button_cancellation_requested")});
    } else {
      updateLiveStatus({phase:'running', detail:StudioI18n.t("image.cancel_button_task_almost_complete_checking_status_again")});
    }
  } catch(e) {
    finishLiveStatus(StudioI18n.t("image.cancel_button_cancellation_error") + StudioI18n.remote(e.message) + StudioI18n.t("image.cancel_button_generation_may_still_running"));
    cancellationInFlight = false;
    cancelButton.disabled = false;
    StudioI18n.bind(cancelButton, "textContent", () => (StudioI18n.t("image.cancel_button_retry_stopping")), true);
  }
});
variantButton.addEventListener('click', async () => {
  if (busy) return;
  await runGeneration(true);
});

const SUPPORTED_PASTE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const DIRECT_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_IMAGE_BYTES = 100 * 1048576;
const previewURLs = new Map();
let pasteTarget = 'image1';
const uploadSlots = [
  {id:'image1', tile:byId('tile1'), button:byId('pick1')},
  {id:'image2', tile:byId('referenceBlock'), button:byId('pick2')}
];
function choosePasteTarget(inputId) {
  if (mode !== 'reference') inputId = 'image1';
  pasteTarget = inputId;
  for (const slot of uploadSlots) {
    const selected = slot.id === inputId;
    slot.tile.classList.toggle('paste-selected', selected);
    slot.button.setAttribute('aria-pressed', String(selected));
    StudioI18n.bind(slot.button, "textContent", () => (selected ? StudioI18n.t("image.slot_button_paste_here_ctrl_v") : StudioI18n.t("image.slot_button_select_paste_target_ctrl_v")), true);
  }
  StudioI18n.bind(byId('pasteStatus'), "textContent", () => ((inputId === 'image1' ? StudioI18n.t("image.paste_image_1_base_selected") : StudioI18n.t("image.paste_image_2_reference_selected")) +
    StudioI18n.t("image.paste_copy_image_browser_press_ctrl_v")), true);
}
for (const slot of uploadSlots) {
  slot.button.addEventListener('click', () => choosePasteTarget(slot.id));
  slot.tile.addEventListener('pointerdown', () => choosePasteTarget(slot.id));
  byId(slot.id).addEventListener('focus', () => choosePasteTarget(slot.id));
}
choosePasteTarget('image1');
document.addEventListener('paste', (event) => {
  if (mode === 'prompt') return;
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find((item) => item.kind === 'file' && item.type.startsWith('image/'));
  if (!imageItem) return;
  event.preventDefault();
  if (busy) {
    StudioI18n.bind(byId('pasteStatus'), "textContent", () => (StudioI18n.t("image.paste_generation_running_stop_wait_finish_before")), true);
    return;
  }
  if (!SUPPORTED_PASTE_TYPES.has(imageItem.type)) {
    StudioI18n.bind(byId('pasteStatus'), "textContent", () => (StudioI18n.t("image.paste_clipboard_format_not_supported_use_png")), true);
    return;
  }
  const blob = imageItem.getAsFile();
  if (!blob || !blob.size || blob.size > MAX_IMAGE_BYTES) {
    StudioI18n.bind(byId('pasteStatus'), "textContent", () => (StudioI18n.t("image.paste_rejected_empty_image_size_exceeds_100")), true);
    return;
  }
  try {
    const ext = {'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[blob.type] || 'png';
    const file = new File([blob], 'clipboard-image.' + ext, {type:imageItem.type});
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const selectedInput = byId(pasteTarget);
    selectedInput.files = transfer.files;
    selectedInput.dispatchEvent(new Event('change', {bubbles:true}));
    StudioI18n.bind(byId('pasteStatus'), "textContent", () => (StudioI18n.t("image.paste_image_pasted_you_continue")), true);
  } catch (error) {
    StudioI18n.bind(byId('pasteStatus'), "textContent", () => (StudioI18n.t("image.paste_clipboard_image_failed") + StudioI18n.remote(error.message)), true);
  }
});
window.addEventListener('pagehide', () => {
  for (const url of previewURLs.values()) URL.revokeObjectURL(url);
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function request(url, options) {
  const r = await fetch(url, {cache:'no-store',...options});
  const body = await r.json();
  if (!r.ok) throw new Error(StudioI18n.remote(body.error) || `HTTP ${r.status}`);
  return body;
}
async function loadSettings() {
  try {
    const data = await request('/api/settings');
    saveDirInput.value = data.save_dir || '';
    StudioI18n.bind(saveDirStatus, "textContent", () => (data.save_dir_warning || StudioI18n.t("image.save_dir_results_saved_folder_automatically_after_successful")), true);
    byId('useInternalResults').hidden = !data.save_dir_warning;
    byId('useInternalResults').onclick = async () => { try { await persistSaveDir(data.internal_save_dir); byId('useInternalResults').hidden=true; } catch(e) { StudioI18n.bind(saveDirStatus, "textContent", () => (StudioI18n.remote(e.message)), true); } };
  } catch (e) {
    StudioI18n.bind(saveDirStatus, "textContent", () => (StudioI18n.t("image.save_dir_load_autosave_folder_failed") + StudioI18n.remote(e.message)), true);
  }
}
async function persistSaveDir(pathText) {
  const payload = await request('/api/settings', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({save_dir: pathText})
  });
  saveDirInput.value = payload.save_dir || pathText;
  StudioI18n.bind(saveDirStatus, "textContent", () => (StudioI18n.t("image.save_dir_autosave_folder_saved_future_results_there")), true);
}

// Directory picker is displayed inside the app window, avoiding Windows Z-order issues.
const folderPicker = byId('folderPicker');
const folderPickerPath = byId('folderPickerPath');
const folderPickerItems = byId('folderPickerItems');
const folderPickerStatus = byId('folderPickerStatus');
const folderPickerSelect = byId('folderPickerSelect');
let folderPickerCurrent = '';
let folderPickerParent = null;
let folderPickerSequence = 0;
let folderPickerSaving = false;
let folderPickerCommit = persistSaveDir;
let folderPickerReturnFocus = chooseSaveDirButton;
window.chooseStudioFolder = (path, commit, returnFocus) => {
  if (folderPickerSaving) return;
  folderPickerCommit = commit; folderPickerReturnFocus = returnFocus;
  folderPickerCurrent = '';
  folderPicker.hidden = false; folderPickerPath.focus(); browseSaveFolder(path, true);
};

function closeFolderPicker() {
  if (folderPickerSaving) return;
  folderPickerSequence++;
  folderPicker.hidden = true;
  folderPickerReturnFocus?.focus();
}
async function browseSaveFolder(path, initial = false) {
  const requestId = ++folderPickerSequence;
  StudioI18n.bind(folderPickerStatus, "textContent", () => (StudioI18n.t("image.folder_picker_loading_folders")), true);
  folderPickerSelect.disabled = true;
  folderPickerItems.replaceChildren();
  try {
    const data = await request('/api/list-save-dirs?initial=' + (initial ? '1' : '0') + '&path=' + encodeURIComponent(path || ''));
    if (requestId !== folderPickerSequence || folderPicker.hidden) return;
    folderPickerCurrent = data.path;
    folderPickerParent = data.parent;
    folderPickerPath.value = data.path;
    byId('folderPickerUp').disabled = !data.parent;
    const drives = byId('folderPickerDrives');
    drives.replaceChildren();
    for (const drive of data.drives || []) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary slim';
      StudioI18n.bind(button, "textContent", () => (drive), true);
      button.addEventListener('click', () => browseSaveFolder(drive));
      drives.append(button);
    }
    for (const dir of data.directories || []) {
      const button = document.createElement('button');
      button.type = 'button';
      StudioI18n.bind(button, "textContent", () => ('📁 ' + dir.name), false);
      StudioI18n.bind(button, "title", () => (dir.path), false);
      button.addEventListener('click', () => browseSaveFolder(dir.path));
      folderPickerItems.append(button);
    }
    if (!data.directories?.length) {
      const info = document.createElement('p');
      StudioI18n.bind(info, "textContent", () => (StudioI18n.t("image.info_no_subfolders")), true);
      info.className = 'save-hint';
      folderPickerItems.append(info);
    }
    StudioI18n.bind(folderPickerStatus, "textContent", () => ((data.directories?.length || 0) + StudioI18n.t("image.folder_picker_folders") + (data.truncated ? StudioI18n.t("image.folder_picker_showing_first_500") : '')), true);
    folderPickerSelect.disabled = false;
  } catch(e) {
    if (requestId !== folderPickerSequence || folderPicker.hidden) return;
    StudioI18n.bind(folderPickerStatus, "textContent", () => (StudioI18n.t("image.folder_picker_open_failed") + StudioI18n.remote(e.message)), true);
  }
}
chooseSaveDirButton.addEventListener('click', () => {
  window.chooseStudioFolder(saveDirInput.value, persistSaveDir, chooseSaveDirButton);
});
byId('folderPickerClose').addEventListener('click', closeFolderPicker);
byId('folderPickerCancel').addEventListener('click', closeFolderPicker);
byId('folderPickerUp').addEventListener('click', () => {
  if (folderPickerParent) browseSaveFolder(folderPickerParent);
});
byId('folderPickerGo').addEventListener('click', () => browseSaveFolder(folderPickerPath.value));
folderPickerPath.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    browseSaveFolder(folderPickerPath.value);
  }
});
folderPickerSelect.addEventListener('click', async () => {
  if (!folderPickerCurrent || folderPickerSaving) return;
  folderPickerSaving = true;
  folderPickerSelect.disabled = true;
  try {
    await folderPickerCommit(folderPickerCurrent);
    folderPickerSaving = false;
    closeFolderPicker();
  } catch(e) {
    folderPickerSaving = false;
    folderPickerSelect.disabled = false;
    StudioI18n.bind(folderPickerStatus, "textContent", () => (StudioI18n.t("image.folder_picker_save_failed") + StudioI18n.remote(e.message)), true);
  }
});
folderPicker.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    event.preventDefault();
    closeFolderPicker();
  }
  if (event.key === 'Tab') {
    const elements = [...folderPicker.querySelectorAll('button:not([disabled]),input:not([disabled])')];
    if (!elements.length) return;
    if (event.shiftKey && document.activeElement === elements[0]) {
      event.preventDefault();
      elements[elements.length - 1].focus();
    } else if (!event.shiftKey && document.activeElement === elements[elements.length - 1]) {
      event.preventDefault();
      elements[0].focus();
    }
  }
});
saveSaveDirButton.addEventListener('click', async () => {
  saveSaveDirButton.disabled = true;
  try {
    await persistSaveDir(saveDirInput.value);
  } catch (e) {
    StudioI18n.bind(saveDirStatus, "textContent", () => (StudioI18n.t("image.folder_picker_save_failed") + StudioI18n.remote(e.message)), true);
  } finally {
    saveSaveDirButton.disabled = false;
  }
});
openSaveDirButton.addEventListener('click', async () => {
  openSaveDirButton.disabled = true;
  try {
    const result = await request('/api/open-save-dir', {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
    saveDirInput.value = result.save_dir || saveDirInput.value;
    StudioI18n.bind(saveDirStatus, "textContent", () => (StudioI18n.t("image.save_dir_autosave_folder_opened_file_explorer")), true);
  } catch (e) {
    StudioI18n.bind(saveDirStatus, "textContent", () => (StudioI18n.t("image.folder_picker_open_failed") + StudioI18n.remote(e.message)), true);
  } finally {
    openSaveDirButton.disabled = false;
  }
});
async function check() {
  try {
    const data = await request('/api/preflight');
    upscaleAvailable = Boolean(data.upscale_available);
    progressWsUrl = data.progress_ws_url || progressWsUrl;
    engineCatalog = data.engines || {};
    if (!engineInitialized) {
      const selection = QwenEngineState.resolveSelection(engineCatalog, readPreferredSelection(), requestedMode);
      selectedEngine = selection.engine;
      mode = selection.mode;
      StudioI18n.bind(byId('selectionNotice'), "textContent", () => (selection.fallback
        ? StudioI18n.t("image.selection_notice_saved_model_unavailable_qwen_image_edit") : ''), true);
    }
    engineInitialized = true;
    for (const option of engineSelect.options) {
      const info = engineCatalog[option.value];
      StudioI18n.bind(option, "textContent", () => (`${StudioUI.modelLabel(option.value, info ? info.name : option.value)} — ${info && info.ready ? StudioI18n.t("image.component_installation_installed_label") : StudioI18n.t("image.model_availability_unavailable_label")}`), true);
      StudioI18n.bind(option, "title", () => (StudioUI.modelSource(option.value)), true);
    }
    StudioI18n.bind(byId('engineDependencyDetails'), "textContent", () => (Object.values(engineCatalog).map(info =>
      `${info.name}: ${info.ready ? StudioI18n.t("image.model_readiness_ready_label") : info.reasons.join('; ')}`).join('\n\n')), true);
    qwenInitProgressWebSocket(data.progress_client_id);
    lockPresets(busy);
    renderMode();
    renderEngine();
    return true;
  } catch(e) {
    engineCatalog = {};
    byId('generate').disabled = true;
    StudioUI.setHeader('image', {tone:'bad', status:StudioI18n.t("image.backend_preflight_comfy_ui_check_unavailable") + StudioI18n.remote(e.message)});
    return false;
  }
}
check().then(reportLaunch);
document.addEventListener('studio:models-changed', () => check());
loadSettings();
function selectPreview(id, pic, text) {
  const file = byId(id).files[0];
  const view = byId(pic);
  const previous = previewURLs.get(id);
  if (previous) { URL.revokeObjectURL(previous); previewURLs.delete(id); }
  view.removeAttribute('src');
  view.hidden = true;
  if (!file) {StudioI18n.bind(byId(text), "textContent", () => (StudioI18n.t("image.select_preview_any_image_file_up_100_mi")), true); return;}
  const blob = URL.createObjectURL(file);
  previewURLs.set(id, blob);
  view.src = blob;
  view.hidden = false;
  StudioI18n.bind(byId(text), "textContent", () => (StudioI18n.t("image.select_preview_mi_b", [file.name, (file.size / 1048576).toFixed(1)])), false);
}
for (const [input,pic,text] of [['image1','preview1','name1'],['image2','preview2','name2']]) {
  byId(input).addEventListener('change', () => selectPreview(input,pic,text));
}
async function fileToBase64(file) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(StudioI18n.t("image.reader_image_read_error")));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.readAsDataURL(file);
  });
}
async function blobToBase64(blob) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(StudioI18n.t("image.reader_read_converted_image_failed")));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.readAsDataURL(blob);
  });
}
async function convertUnsupportedImageToPng(file) {
  const objectUrl = URL.createObjectURL(file);
  try {
    const image = await new Promise((resolve, reject) => {
      const tag = new Image();
      tag.onload = () => resolve(tag);
      tag.onerror = () => reject(new Error(StudioI18n.t("image.tag_format_not_directly_supported_could_decoded")));
      tag.src = objectUrl;
    });
    const width = image.naturalWidth || image.width;
    const height = image.naturalHeight || image.height;
    if (!width || !height) throw new Error(StudioI18n.t("image.convert_unsupported_to_png_determine_image_dimensions_conversion_failed"));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error(StudioI18n.t("image.convert_unsupported_to_png_canvas_unavailable_conversion_impossible"));
    ctx.drawImage(image, 0, 0);
    const pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!pngBlob) throw new Error(StudioI18n.t("image.convert_unsupported_to_png_browser_could_not_image"));
    if (pngBlob.size > MAX_IMAGE_BYTES) throw new Error(StudioI18n.t("image.convert_unsupported_to_png_converted_exceeds_100_mi_b"));
    return {base64: await blobToBase64(pngBlob)};
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
async function encodeFile(file) {
  if (!file) throw new Error(StudioI18n.t("image.encode_file_no_image_selected"));
  if (file.size > MAX_IMAGE_BYTES) throw new Error(StudioI18n.t("image.encode_file_image_exceeds_100_mi_b"));
  const fileType = String(file.type || '').toLowerCase();
  if (DIRECT_IMAGE_TYPES.has(fileType)) {
    return {base64: await fileToBase64(file)};
  }
  return await convertUnsupportedImageToPng(file);
}
// Take the last generated image as a visual anchor, not the original user input.
// Downscale very large (e.g. 4x-upscaled) results for the next edit pass.
async function variantSourceImage(href) {
  const response = await fetch(href, {cache:'no-store'});
  if (!response.ok) throw new Error(StudioI18n.t("image.variant_retrieve_previous_result_variation_failed"));
  const sourceBlob = await response.blob();
  if (!sourceBlob.size) throw new Error(StudioI18n.t("image.variant_previous_result_empty"));
  const bitmap = await createImageBitmap(sourceBlob);
  try {
    const maxSide = 1536;
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error(StudioI18n.t("image.variant_canvas_unavailable_creating_variation"));
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob || !blob.size || blob.size > MAX_IMAGE_BYTES) {
      throw new Error(StudioI18n.t("image.variant_prepare_previous_result_variation_failed"));
    }
    return {base64: await blobToBase64(blob)};
  } finally {
    bitmap.close();
  }
}
function variationPrompt(originalPrompt) {
  // 80/20 is a creative target, not a measurable similarity guarantee.
  return String(originalPrompt).trim() + '\n\n' +
    'Make a subtle but clearly visible alternative of the supplied image. ' +
    'Treat this supplied image as the visual source of truth. ' +
    'Preserve the subject identity, important objects, layout, overall composition and style. ' +
    'Vary only a few secondary details, colors, lighting nuances, textures or small design elements. ' +
    'Aim for roughly 80 percent consistency and no more than about 20 percent creative difference. ' +
    'Do not reimagine the whole scene, change the main subject, or radically alter the framing.';
}

function reportImageError(error) {
  finishLiveStatus(StudioI18n.t("image.generation_error_notification_prefix") + StudioI18n.remote(error.message));
  StudioUI.notify({module:'Image', id:activeJob?.prompt_id, error:true, message:StudioI18n.remote(error.message), onLogs:() => byId('openLogs').click()});
}

async function runGeneration(isVariant) {
  if (busy || !engineReady() || (isVariant && (!lastResultInfo || lastResultInfo.engine !== selectedEngine || lastResultInfo.mode !== mode))) return;
  const prior = lastResultInfo;
  const selectionMode = mode;
  const requestEngine = isVariant ? prior.engine : selectedEngine;
  const canvasShape = selected('canvas');
  const sourceFile = byId('image1').files[0];
  const quality = isVariant ? prior.quality : selected('quality');
  const upscale = isVariant ? prior.upscale : Number(selected('upscale'));
  const originalPrompt = isVariant ? prior.originalPrompt : byId('prompt').value.trim();
  let fixedSeed;
  try { fixedSeed = readFixedSeed(); }
  catch (error) { finishLiveStatus(StudioI18n.remote(error.message)); StudioUI.notify({module:'Image',error:true,message:StudioI18n.remote(error.message),onLogs:()=>byId('openLogs').click()}); return; }
  const referenceFile = isVariant ? prior.referenceFile :
    (mode === 'reference' ? byId('image2').files[0] : null);
  const requestMode = selectionMode;
  beginTask();
  try {
    startLiveStatus('preparing', isVariant ? StudioI18n.t("image.run_generation_preparing_similar_variation_latest_result") : StudioI18n.t("image.run_generation_preparing_images_prompt"));
    const image1 = isVariant ? await variantSourceImage(prior.href) :
      (requestMode === 'prompt' ? null : await encodeFile(sourceFile));
    const image2 = requestMode === 'reference' ? await encodeFile(referenceFile) : null;
    updateLiveStatus({phase:'preparing', detail:StudioI18n.t("image.run_generation_uploading_images_local_comfy_ui")});
    const result = await request('/api/generate', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        engine:requestEngine,
        mode:requestMode,
        operation:isVariant ? 'variation' : 'generate',
        prompt:isVariant ? variationPrompt(originalPrompt) : originalPrompt,
        original_prompt:originalPrompt,
        image1,image2,quality,upscale,seed:fixedSeed,
        canvas:requestMode === 'prompt' ? canvasShape : undefined,
        expand:engineCatalog[requestEngine]?.expansion ? byId('expandPrompt').checked : undefined
      })
    });
    activeJob = {prompt_id:result.prompt_id,cancel_token:result.cancel_token,engine:result.engine,node_names:result.node_names,sampler_node_ids:result.sampler_node_ids};
    cancelButton.disabled = false;
    updateLiveStatus({phase:'submitted', promptId: result.prompt_id});
    qwenRecordStage(StudioI18n.t("image.run_generation_task_accepted_comfy_ui"));
    const started = Date.now();
    let misses = 0;
    for (;;) {
      await sleep(2000);
      // One failed look (ComfyUI busy for a moment) is not a failed job: the job keeps running and the bridge still saves it.
      let job;
      try { job = await request('/api/status?id=' + encodeURIComponent(result.prompt_id)); misses = 0; }
      catch (error) {
        if (++misses >= 6) {reportImageError(error);return;}
        updateLiveStatus({detail2: StudioI18n.t("image.run_generation_no_response_comfy_ui_retrying")});
        continue;
      }
      if (liveState?.detail2) liveState.detail2 = '';
      if (job.state === 'cancelled') {
        finishLiveStatus(StudioI18n.t("image.run_generation_cancelled_you_start_another"));
        break;
      }
      if (job.state === 'cancelling') {
        updateLiveStatus({phase:'cancelling'});
        continue;
      }
      if (job.state === 'error') {reportImageError({message:StudioI18n.remote(job.error) || StudioI18n.t("image.run_generation_execution_error")});return;}
      if (job.state === 'done') {
        lastSeed = result.seed;
        qwenRecordStage(StudioI18n.t("image.run_generation_done_result_received"));
        hasSuccessfulResult = true;
        rememberEngine(result.engine);
        rememberSelection();
        const saveLine = job.save_error
          ? StudioI18n.t("image.run_generation_autosave_failed_copy_remains_comfy_ui", [job.save_error])
          : (job.saved_count ? StudioI18n.t("image.run_generation_autosaved_file_s", [job.saved_count, job.save_dir]) : StudioI18n.t("image.run_generation_checking_autosave"));
        finishLiveStatus(StudioI18n.t("image.run_generation_results", [isVariant ? StudioI18n.t("image.run_generation_new_variation_ready") : StudioI18n.t("image.generation_completed_status_label"), job.images, quality.toUpperCase(), upscale===1?'Off':upscale+'×', StudioI18n.remote(saveLine)]));
        let firstResultHref = null;
        for (let i = 0; i < job.images; i++) {
          const wrapper = document.createElement('div');
          const meta = document.createElement('p');
          meta.className='result-meta';
          StudioI18n.bind(meta, "textContent", () => (`${engineCatalog[result.engine]?.name || result.engine} · ${quality.toUpperCase()} · ${result.steps} steps · Upscale ${upscale===1?'Off':upscale+'×'} · Seed ${result.seed}`), true);
          const image = document.createElement('img');
          image.loading = 'lazy'; image.decoding = 'async';
          const href = '/api/result?id=' + encodeURIComponent(result.prompt_id) + '&index=' + i;
          if (i === 0) firstResultHref = href;
          image.src = href; StudioI18n.bind(image, "alt", () => (StudioI18n.t("image.run_generation_generated_image")), true);
          image.addEventListener('load', () => { StudioI18n.append(meta, "textContent", () => (` · ${image.naturalWidth}×${image.naturalHeight} px`), true); }, {once:true});
          const link = document.createElement('a');
          link.href = href; link.download = `${result.filename_base || 'Generated Image'}${i ? ' ' + (i + 1) : ''}.png`; StudioI18n.bind(link, "textContent", () => (StudioI18n.t("image.link_save_result")), true);
          wrapper.append(meta);
          if (job.expanded_prompt) {
            // What the image model actually received after the upsampler; textContent keeps the model output inert.
            const details = document.createElement('details');
            const summary = document.createElement('summary');
            StudioI18n.bind(summary, "textContent", () => (StudioI18n.t("image.summary_expanded_prompt_upsampler")), true);
            const text = document.createElement('pre');
            StudioI18n.bind(text, "textContent", () => (job.expanded_prompt), false);
            details.append(summary, text);
            wrapper.append(details);
          }
          wrapper.append(image, link); byId('images').prepend(wrapper);
        }
        if (firstResultHref) {
          lastResultInfo = {href:firstResultHref, originalPrompt, quality, upscale, referenceFile, engine:result.engine, mode:selectionMode};
          hasSuccessfulResult = true;
        }
        StudioUI.notify({module:'Image', id:result.prompt_id, error:!!job.save_error,
          message:job.save_error ? StudioI18n.t("image.run_generation_image_created_but_autosave_failed")+job.save_error : StudioI18n.t("image.run_generation_image_created_saved"),
          folder:job.saved_count ? job.save_dir : '', onLogs:() => byId('openLogs').click()});
        loadResultHistory();
        break;
      }
      if (Date.now() - started > 60 * 60 * 1000) {reportImageError({message:StudioI18n.t("image.run_generation_no_result_hour_check_comfy_ui")});return;}
      if (job.state === 'queued') {
        if (!qwenRealtime || Date.now() - qwenRealtimeUpdated > 5000) qwenRecordStage(StudioI18n.t("image.run_generation_waiting_comfy_ui_queue"));
        updateLiveStatus({phase:'queued', queuePosition: Number.isFinite(job.queue_position) ? job.queue_position : null, queueTotal: Number.isFinite(job.queue_total) ? job.queue_total : null});
      } else {
        if (qwenRealtime && Date.now() - qwenRealtimeUpdated <= 5000) {
          const current = qwenRealtime;
          updateLiveStatus({phase:'running', detail: StudioI18n.t("image.run_generation_live_comfy_ui_stage"), progressPct: null,
            node: current.step === null ? current.node : StudioI18n.t("image.run_generation_step", [StudioI18n.remote(current.node), current.step, current.total])});
        } else {
          updateLiveStatus({phase:'running', detail: job.status || StudioI18n.t("image.run_generation_comfy_ui_running_task"), progressPct: (typeof job.progress_pct === 'number') ? job.progress_pct : null, node: job.node || ''});
        }
      }
    }
  } catch(e) {
    reportImageError(e);
  } finally {
    clearTask();
  }
}
byId('form').addEventListener('submit', async (event) => { event.preventDefault(); await runGeneration(false); });
refreshVariantButton();

function readFixedSeed() {
  const text = byId('seed').value.trim();
  if (!text) return undefined;
  const value = Number(text);
  if (!/^\d+$/.test(text) || !Number.isSafeInteger(value) || value < 0 || value >= 2**48) {
    throw new Error(StudioI18n.t("image.read_fixed_seed_must_integer_0_281474976710655"));
  }
  return value;
}
function rememberDraft() {
  try { localStorage.setItem('visualforge.imageDraft.v1', JSON.stringify({prompt:byId('prompt').value.slice(0,5000),seed:byId('seed').value})); } catch { /* Local storage can be unavailable. */ }
}
try {
  const draft = JSON.parse(localStorage.getItem('visualforge.imageDraft.v1') || 'null');
  if (draft && typeof draft.prompt === 'string') byId('prompt').value = draft.prompt.slice(0,5000);
  if (draft && typeof draft.seed === 'string' && /^\d{0,15}$/.test(draft.seed)) byId('seed').value = draft.seed;
} catch { /* A damaged browser draft must not block generation. */ }
byId('prompt').addEventListener('input', rememberDraft);
byId('seed').addEventListener('input', () => {
  try { readFixedSeed(); byId('seed').setCustomValidity(''); }
  catch (error) { byId('seed').setCustomValidity(StudioI18n.remote(error.message)); }
  rememberDraft();
});
function useSeed(value) {
  if (busy) return;
  byId('seed').value = value === null ? '' : String(value);
  byId('seed').setCustomValidity('');
  rememberDraft();
}
byId('randomSeed').addEventListener('click', () => useSeed(null));
byId('reuseSeed').addEventListener('click', () => useSeed(lastSeed));
window.addEventListener('beforeunload', event => {
  if (busy) { event.preventDefault(); event.returnValue = ''; }
});
async function loadResultHistory() {
  try {
    const result = await request('/api/history');
    const rows = Array.isArray(result.entries) ? result.entries : [];
    if (lastSeed === null && Number.isSafeInteger(rows[0]?.seed)) lastSeed = rows[0].seed;
    byId('reuseSeed').disabled = busy || lastSeed === null;
    byId('historyCards').replaceChildren();
    StudioI18n.bind(byId('historyStatus'), "textContent", () => (StudioI18n.remote(result.warning) || (rows.length ? StudioI18n.t("image.history_tasks_showing_latest_files_remain_folders", [rows.length, Math.min(20,rows.length)]) : StudioI18n.t("image.history_new_saved_results_appear_here_persist"))), true);
    for (const row of rows.slice(0,20)) {
      const card = document.createElement('article'); card.className = 'history-card';
      const heading = document.createElement('strong'); StudioI18n.bind(heading, "textContent", () => (row.filename_base || StudioI18n.t("image.heading_saved_result")), false);
      const meta = document.createElement('p'); meta.className = 'preset-hint';
      StudioI18n.bind(meta, "textContent", () => (`${row.engine} · ${row.quality || ''} · Seed ${row.seed} · ${row.upscale || 1}×`), true);
      const prompt = document.createElement('p'); prompt.className = 'history-prompt'; StudioI18n.bind(prompt, "textContent", () => (row.original_prompt || ''), false);
      card.append(heading,meta,prompt);
      const links = document.createElement('div'); links.className = 'history-actions';
      for (const index of row.image_indices || []) {
        const link = document.createElement('a'); link.href = '/api/result?id='+encodeURIComponent(row.prompt_id)+'&index='+index;
        StudioI18n.bind(link, "textContent", () => (index ? StudioI18n.t("image.history_result_indexed_open_accessibility_template", [index+1]) : StudioI18n.t("image.history_result_open_button_label")), true); link.target = '_blank'; link.rel = 'noopener'; links.append(link);
      }
      if (!row.image_indices?.length) { const missing = document.createElement('span'); StudioI18n.bind(missing, "textContent", () => (StudioI18n.t("image.missing_files_currently_unavailable_check_output_folder")), true); links.append(missing); }
      if (Number.isSafeInteger(row.seed) && row.seed >= 0 && row.seed < 2**48) {
        const seedButton = document.createElement('button'); seedButton.type = 'button'; seedButton.className='secondary slim'; StudioI18n.bind(seedButton, "textContent", () => (StudioI18n.t("image.seed_button_use")), true); seedButton.disabled=busy; seedButton.addEventListener('click',()=>useSeed(row.seed)); links.append(seedButton);
      }
      links.append(StudioUI.deleteGenerationButton('Image', row.prompt_id, async () => {
        for (const link of byId('images').querySelectorAll('a[href^="/api/result"]')) {
          if (new URL(link.href, location.href).searchParams.get('id') === row.prompt_id) link.parentElement.remove();
        }
        if (lastResultInfo && new URL(lastResultInfo.href, location.href).searchParams.get('id') === row.prompt_id) {
          lastResultInfo = null; hasSuccessfulResult = false; refreshVariantButton();
        }
        await loadResultHistory();
      }));
      card.append(links);
      if (row.generation) card.append(StudioUI.generationInfo(row.generation));
      byId('historyCards').append(card);
    }
  } catch { StudioI18n.bind(byId('historyStatus'), "textContent", () => (StudioI18n.t("image.history_currently_unavailable_generation_saved_files_remain")), true); }
}
loadResultHistory();


// QWEN_LOGS_PANEL_V1: local-only, read-only diagnostics UI.
const logsPanel = byId('logsPanel');
const logsOutput = byId('logsOutput');
const logsStatus = byId('logsStatus');
const logsScope = byId('logsScope');
const logsCopy = byId('logsCopy');
const logsRefresh = byId('logsRefresh');
const logsSaveTxt = byId('logsSaveTxt');
let logsSequence = 0;
let logsLastFocus = null;
let logsText = '';

function closeLogsPanel() {
  ++logsSequence;
  logsPanel.hidden = true;
  (logsLastFocus || byId('openLogs')).focus();
}
async function updateLogsPanel() {
  const sequence = ++logsSequence;
  logsRefresh.disabled = true;
  StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_reading_recent_entries")), true);
  try {
    const result = await request('/api/logs?scope=' + encodeURIComponent(logsScope.value));
    if (sequence !== logsSequence || logsPanel.hidden) return;
    logsText = String(result.text || StudioI18n.t("image.update_logs_panel_no_entries"));
    StudioI18n.bind(logsOutput, "textContent", () => (logsText), true);
    StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.diagnostics_log_file_count_template", [result.files || 0]) +
      (result.truncated ? StudioI18n.t("image.logs_showing_recent_excerpts") : '') + StudioI18n.t("image.logs_local_never_sent_online")), true);
  } catch (error) {
    if (sequence !== logsSequence || logsPanel.hidden) return;
    logsText = '';
    StudioI18n.bind(logsOutput, "textContent", () => (StudioI18n.t("image.logs_output_read_failed") + StudioI18n.remote(error.message)), true);
    StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_retrieve_failed")), true);
  } finally {
    if (sequence === logsSequence) logsRefresh.disabled = false;
  }
}
byId('openLogs').addEventListener('click', () => {
  logsLastFocus = document.activeElement;
  logsPanel.hidden = false;
  byId('logsClose').focus();
  updateLogsPanel();
});
byId('logsClose').addEventListener('click', closeLogsPanel);
logsPanel.addEventListener('click', event => {
  if (event.target === logsPanel) closeLogsPanel();
});
logsPanel.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    event.preventDefault();
    closeLogsPanel();
    return;
  }
  if (event.key !== 'Tab') return;
  const controls = Array.from(logsPanel.querySelectorAll('button:not([disabled]),select:not([disabled]),pre[tabindex]'));
  if (!controls.length) return;
  if (event.shiftKey && document.activeElement === controls[0]) {
    event.preventDefault();
    controls[controls.length - 1].focus();
  } else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) {
    event.preventDefault();
    controls[0].focus();
  }
});
logsScope.addEventListener('change', updateLogsPanel);
logsRefresh.addEventListener('click', updateLogsPanel);
async function copyLogsText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* Clipboard API permissions vary between browser app windows; try the legacy API. */ }
  const temp = document.createElement('textarea');
  temp.value = text;
  temp.className = 'logs-copy-buffer';
  try {
    document.body.append(temp);
    temp.select();
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    // Selection or copying can fail; the temporary field must be removed on every exit.
    temp.remove();
  }
}
logsCopy.addEventListener('click', async () => {
  if (!logsText) { StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_load_first")), true); return; }
  if (await copyLogsText(logsText)) {
    StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_copied_clipboard")), true);
  } else {
    StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_copying_unavailable_select_log_text_manually")), true);
    logsOutput.focus();
  }
});
logsSaveTxt.addEventListener('click', () => {
  if (!logsText) { StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_load_first")), true); return; }
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const file = new Blob([logsText], {type:'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(file);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = `VisualForgeLogs_${logsScope.value}_${stamp}.txt`;
    document.body.append(link);
    link.click();
    link.remove();
    StudioI18n.bind(logsStatus, "textContent", () => (StudioI18n.t("image.logs_txt_sent_browser_saving")), true);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }
});


// QWEN_UX_BUNDLE_V1 - persistent favorites and real ComfyUI WebSocket progress.
const qwenFavSaveButton = byId('qwenFavoriteSave');
const qwenFavOpenButton = byId('qwenFavoriteOpen');
const qwenFavStatus = byId('qwenFavoriteStatus');
const qwenArchivePanel = byId('qwenArchivePanel');
const qwenArchiveEntries = byId('qwenArchiveEntries');
const qwenArchiveSearch = byId('qwenArchiveSearch');
const qwenArchiveStatus = byId('qwenArchiveStatus');
const qwenStagePanel = byId('qwenStagePanel');
const qwenStageName = byId('qwenStageName');
const qwenStageStep = byId('qwenStageStep');
const qwenSamplerProgress = byId('qwenSamplerProgress');
const qwenStageEvents = byId('qwenStageEvents');
const qwenProgressHint = byId('qwenProgressHint');
let qwenFavorites = [];
let qwenLastFocus = null;
let qwenProgressSocket = null;
let qwenWsClientId = null;
let qwenRealtime = null;
let qwenRealtimeUpdated = 0;
let qwenStageHistory = [];
let qwenLastRecordedStage = '';
let qwenLastSamplerStep = -1;
const qwenNodeNames = {
  '41': StudioI18n.t("image.app_qwen_names_loading_source_image"),
  '83': StudioI18n.t("image.app_qwen_names_loading_reference"),
  '161': StudioI18n.t("image.app_qwen_names_loading_diffusion_model"),
  '162': StudioI18n.t("image.app_qwen_names_loading_text_encoder"),
  '146': StudioI18n.t("image.app_qwen_names_loading_vae"),
  '170': StudioI18n.t("image.app_qwen_names_applying_lo_ra"),
  '145': StudioI18n.t("image.app_qwen_names_configuring_sampling"),
  '152': StudioI18n.t("image.app_qwen_names_preparing_cfg"),
  '160': StudioI18n.t("image.app_qwen_names_scaling_input_image"),
  '151': StudioI18n.t("image.app_qwen_names_processing_prompt"),
  '149': StudioI18n.t("image.app_qwen_names_preparing_conditioning"),
  '148': StudioI18n.t("image.app_qwen_names_preparing_references"),
  '147': StudioI18n.t("image.app_qwen_names_preparing_conditioning"),
  '156': StudioI18n.t("image.app_qwen_names_encoding_input_image_vae"),
  '169': StudioI18n.t("image.app_qwen_names_generating_image_k_sampler"),
  '158': StudioI18n.t("image.app_qwen_names_decoding_result_vae"),
  '200': StudioI18n.t("image.app_qwen_names_loading_real_esrgan_model"),
  '201': StudioI18n.t("image.app_qwen_names_ai_upscale_first_pass"),
  '202': StudioI18n.t("image.app_qwen_names_ai_upscale_second_pass"),
  '9': StudioI18n.t("image.app_qwen_names_writing_output_file")
};
function qwenAddStage(text) {
  if (!text || text === qwenLastRecordedStage) return;
  qwenLastRecordedStage = text;
  const date = new Date();
  const clock = date.toLocaleTimeString(StudioI18n.locale, {hour12:false});
  qwenStageHistory.push(`[${clock}] ${text}`);
  qwenStageHistory = qwenStageHistory.slice(-8);
  StudioI18n.bind(qwenStageEvents, "textContent", () => (qwenStageHistory.join('\n')), true);
}
function qwenResetProgressUI() {
  qwenStageHistory = [];
  qwenLastRecordedStage = '';
  qwenLastSamplerStep = -1;
  qwenRealtime = null;
  qwenRealtimeUpdated = 0;
  qwenStagePanel.hidden = false;
  qwenSamplerProgress.hidden = true;
  StudioI18n.bind(qwenStageStep, "textContent", () => (''), true);
  StudioI18n.bind(qwenStageName, "textContent", () => (StudioI18n.t("image.qwen_stage_name_preparing_request")), true);
  StudioI18n.bind(qwenStageEvents, "textContent", () => (''), true);
  StudioI18n.bind(qwenProgressHint, "textContent", () => (StudioI18n.t("image.qwen_progress_hint_real_comfy_ui_events_shown_step")), true);
  qwenAddStage(StudioI18n.t("image.qwen_stage_name_preparing_request"));
}
function qwenRecordStage(stage, step, maxSteps) {
  qwenStagePanel.hidden = false;
  StudioI18n.bind(qwenStageName, "textContent", () => (stage), true);
  if (Number.isFinite(step) && Number.isFinite(maxSteps) && maxSteps > 0) {
    StudioI18n.bind(qwenStageStep, "textContent", () => (StudioI18n.t("image.qwen_stage_step", [step, maxSteps])), true);
    qwenSamplerProgress.hidden = false;
    qwenSamplerProgress.max = maxSteps;
    qwenSamplerProgress.value = Math.max(0, Math.min(maxSteps, step));
    if (step !== qwenLastSamplerStep) {
      qwenLastSamplerStep = step;
      qwenAddStage(StudioI18n.t("image.qwen_record_stage_k_sampler_step", [step, maxSteps]));
    }
  } else {
    StudioI18n.bind(qwenStageStep, "textContent", () => (''), true);
    qwenSamplerProgress.hidden = true;
    qwenAddStage(stage);
  }
}
function qwenSocketMessage(event) {
  if (typeof event.data !== 'string' || !activeJob || !activeJob.prompt_id || !busy) return;
  let obj;
  try { obj = JSON.parse(event.data); } catch { return; }
  if (!obj || typeof obj !== 'object') return;
  const data = obj.data || {};
  if (data.prompt_id !== activeJob.prompt_id) return; // Never display another job's progress.
  const type = obj.type;
  let node = null, value = null, maximum = null;
  if (type === 'executing' || type === 'progress') {
    node = data.node;
    if (type === 'progress') {
      value = Number(data.value);
      maximum = Number(data.max);
    }
  } else if (type === 'progress_state' && data.nodes && typeof data.nodes === 'object') {
    const running = Object.values(data.nodes).find(n => n && n.state === 'running');
    if (!running) return;
    node = running.node_id;
    value = Number(running.value);
    maximum = Number(running.max);
  } else if (type === 'execution_start') {
    qwenRecordStage(StudioI18n.t("image.qwen_socket_message_comfy_ui_started_execution"));
    return;
  } else {
    return;
  }
  if (node === null || node === undefined) return;
  const label = (activeJob.node_names || qwenNodeNames)[String(node)] || `ComfyUI · node ${String(node).slice(0, 12)}`;
  const sampler = (activeJob.sampler_node_ids || ['169']).includes(String(node));
  const step = sampler && Number.isFinite(value) && Number.isFinite(maximum) && maximum > 0 ? value : null;
  const total = step === null ? null : maximum;
  qwenRealtime = {node: label, step, total};
  qwenRealtimeUpdated = Date.now();
  qwenRecordStage(label, step, total);
  if (liveState && busy) {
    // The existing 2-second HTTP polling remains the authoritative job-state fallback.
    updateLiveStatus({phase:'running', node: step === null ? label : StudioI18n.t("image.run_generation_step", [StudioI18n.remote(label), step, total]), progressPct:null});
  }
}
function qwenInitProgressWebSocket(clientId) {
  if (!clientId || typeof clientId !== 'string' || !/^[a-f0-9]{32}$/.test(clientId)) return;
  if (qwenWsClientId === clientId && qwenProgressSocket &&
      (qwenProgressSocket.readyState === WebSocket.OPEN || qwenProgressSocket.readyState === WebSocket.CONNECTING)) return;
  qwenWsClientId = clientId;
  try {
    const ws = new WebSocket(progressWsUrl + '?clientId=' + encodeURIComponent(clientId));
    qwenProgressSocket = ws;
    ws.addEventListener('message', qwenSocketMessage);
    ws.addEventListener('close', () => {
      if (qwenProgressSocket !== ws) return;
      qwenProgressSocket = null;
      StudioI18n.bind(qwenProgressHint, "textContent", () => (StudioI18n.t("image.qwen_progress_hint_live_unavailable_task_status_still_reliably")), true);
    });
    ws.addEventListener('error', () => {
      StudioI18n.bind(qwenProgressHint, "textContent", () => (StudioI18n.t("image.qwen_progress_hint_no_web_socket_http_status_checks")), true);
    });
  } catch (error) {
    StudioI18n.bind(qwenProgressHint, "textContent", () => (StudioI18n.t("image.qwen_progress_hint_comfy_ui_web_socket_could_not")), true);
  }
}

async function qwenReloadFavorites() {
  const result = await request('/api/favorites');
  qwenFavorites = Array.isArray(result.entries) ? result.entries : [];
  qwenRenderFavorites();
}
function qwenCloseArchive() {
  qwenArchivePanel.hidden = true;
  (qwenLastFocus || qwenFavOpenButton).focus();
}
function qwenRenderFavorites() {
  const search = qwenArchiveSearch.value.toLocaleLowerCase(StudioI18n.locale);
  const matches = qwenFavorites.filter(item => item.prompt.toLocaleLowerCase(StudioI18n.locale).includes(search));
  qwenArchiveEntries.replaceChildren();
  if (!matches.length) {
    const empty = document.createElement('p');
    empty.className = 'qwen-muted';
    StudioI18n.bind(empty, "textContent", () => (search ? StudioI18n.t("image.empty_no_matches") : StudioI18n.t("image.empty_archive_save_prompt")), true);
    qwenArchiveEntries.append(empty);
    return;
  }
  for (const item of matches) {
    const wrap = document.createElement('div');
    wrap.className = 'qwen-archive-item';
    const meta = document.createElement('div');
    meta.className = 'qwen-archive-meta';
    StudioI18n.bind(meta, "textContent", () => (StudioI18n.t("image.prompt_archive_entry_saved_date_template", [item.created_at])), true);
    const pre = document.createElement('pre');
    StudioI18n.bind(pre, "textContent", () => (item.prompt), false); // Never inject prompt text as HTML.
    const actions = document.createElement('div');
    actions.className = 'qwen-archive-buttons';
    const append = document.createElement('button');
    append.type = 'button';
    append.className = 'secondary';
    StudioI18n.bind(append, "textContent", () => (StudioI18n.t("image.prompt_archive_append_to_prompt_button_label")), true);
    append.addEventListener('click', () => {
      const target = byId('prompt');
      const separator = target.value.trim() ? '\n\n' : '';
      if ((target.value + separator + item.prompt).length > 5000) {
        StudioI18n.bind(qwenArchiveStatus, "textContent", () => (StudioI18n.t("image.qwen_archive_combined_prompt_would_exceed_5000_characters")), true);
        return;
      }
      target.value += separator + item.prompt;
      target.dispatchEvent(new Event('input', {bubbles:true}));
      qwenCloseArchive();
      target.focus();
      target.setSelectionRange(target.value.length, target.value.length);
      StudioI18n.bind(qwenFavStatus, "textContent", () => (StudioI18n.t("image.qwen_fav_archived_prompt_appended_text")), true);
    });
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'qwen-danger';
    StudioI18n.bind(del, "textContent", () => (StudioI18n.t("image.del_delete_archive")), true);
    del.addEventListener('click', async () => {
      if (!confirm(StudioI18n.t("image.del_delete_archived_prompt_cannot_undone"))) return;
      del.disabled = true;
      try {
        const result = await request('/api/favorites', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({action:'delete', id:item.id})
        });
        qwenFavorites = result.entries;
        StudioI18n.bind(qwenArchiveStatus, "textContent", () => (StudioI18n.t("image.qwen_archive_entry_deleted")), true);
        qwenRenderFavorites();
      } catch (error) {
        del.disabled = false;
        StudioI18n.bind(qwenArchiveStatus, "textContent", () => (StudioI18n.t("image.qwen_archive_deletion_error") + StudioI18n.remote(error.message)), true);
      }
    });
    actions.append(append,del);
    wrap.append(meta,pre,actions);
    qwenArchiveEntries.append(wrap);
  }
}
qwenFavSaveButton.addEventListener('click', async () => {
  const prompt = byId('prompt').value;
  if (!prompt.trim()) { StudioI18n.bind(qwenFavStatus, "textContent", () => (StudioI18n.t("image.qwen_fav_enter_prompt_first")), true); return; }
  qwenFavSaveButton.disabled = true;
  try {
    const result = await request('/api/favorites', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'add',prompt})
    });
    qwenFavorites = result.entries;
    StudioI18n.bind(qwenFavStatus, "textContent", () => (result.duplicate ? StudioI18n.t("image.qwen_fav_prompt_already_archived") : StudioI18n.t("image.qwen_fav_saved_portable_archive")), true);
  } catch(error) { StudioI18n.bind(qwenFavStatus, "textContent", () => (StudioI18n.t("image.qwen_fav_save_error") + StudioI18n.remote(error.message)), true); }
  finally { qwenFavSaveButton.disabled = false; }
});
qwenFavOpenButton.addEventListener('click', async () => {
  qwenLastFocus = document.activeElement;
  qwenArchivePanel.hidden = false;
  StudioI18n.bind(qwenArchiveStatus, "textContent", () => (StudioI18n.t("image.qwen_archive_loading")), true);
  qwenArchiveSearch.value = '';
  byId('qwenArchiveClose').focus();
  try {
    await qwenReloadFavorites();
    StudioI18n.bind(qwenArchiveStatus, "textContent", () => (StudioI18n.t("image.qwen_archive_saved_prompts", [qwenFavorites.length])), true);
  } catch(error) {
    StudioI18n.bind(qwenArchiveStatus, "textContent", () => (StudioI18n.t("image.qwen_archive_read_failed") + StudioI18n.remote(error.message)), true);
  }
});
byId('qwenArchiveClose').addEventListener('click', qwenCloseArchive);
qwenArchivePanel.addEventListener('click', event => {
  if (event.target === qwenArchivePanel) qwenCloseArchive();
});
qwenArchiveSearch.addEventListener('input', qwenRenderFavorites);
qwenArchivePanel.addEventListener('keydown', event => {
  if (event.key === 'Escape') {event.preventDefault(); qwenCloseArchive(); return;}
  if (event.key !== 'Tab') return;
  const controls = Array.from(qwenArchivePanel.querySelectorAll('input,button:not([disabled])'));
  if (!controls.length) return;
  if (event.shiftKey && document.activeElement === controls[0]) {
    event.preventDefault(); controls[controls.length-1].focus();
  } else if (!event.shiftKey && document.activeElement === controls[controls.length-1]) {
    event.preventDefault(); controls[0].focus();
  }
});
