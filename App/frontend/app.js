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
  byId('title').textContent = 'VisualForge AI Studio';
  StudioUI.setHeader('image', {description: {single:'Исходное изображение и текстовая инструкция', reference:'Изображение-основа и референс', prompt:'Создание изображения по тексту'}[mode]});
  byId('referenceBlock').hidden = mode !== 'reference';
  byId('image2').required = mode === 'reference';
  byId('image2').disabled = mode !== 'reference';
  byId('photosBlock').hidden = mode === 'prompt';
  byId('pasteStatus').hidden = mode === 'prompt';
  byId('image1').required = mode !== 'prompt';
  byId('image1').disabled = mode === 'prompt';
  byId('promptOnlyControls').hidden = mode !== 'prompt';
  byId('promptLabelText').textContent = mode === 'prompt' ? 'Что создать?' : 'Что изменить?';
  byId('prompt').placeholder = mode === 'prompt' ? 'Опишите желаемое изображение…' : 'Опишите изменение и то, что нужно сохранить…';
  byId('generate').textContent = mode === 'prompt' ? 'Создать по промпту' : 'Создать изображение';
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
    header: 'Генерация изображения по тексту (FLUX.3 Klein)',
    promptHint: 'Нативная генерация по тексту; редактирование одного изображения и работа с референсом — в режимах Single и Reference. Короткий промпт расширяет официальный upsampler Qwen3.5. Размер до 1024×1024; негативный промпт не используется.',
    transparency: 'FLUX.3 Klein выдаёт RGB без прозрачности. AI Upscale увеличивает готовое изображение.'
  },
  'z-image-turbo-int8': {
    header: 'Генерация изображения по тексту (Z-Image Turbo)',
    promptHint: 'Нативная генерация по тексту, только режим Prompt. Размер до 1024×1024; негативный промпт не используется (CFG 1).',
    transparency: 'Z-Image Turbo выдаёт RGB без прозрачности. AI Upscale увеличивает готовое изображение.'
  }
};
modeSelect.addEventListener('change', () => {
  if (busy) { modeSelect.value = mode; return; }
  if (!QwenEngineState.MODES.includes(modeSelect.value)) { modeSelect.value = mode; return; }
  mode = modeSelect.value;
  const previous = selectedEngine;
  selectedEngine = QwenEngineState.engineForMode(engineCatalog, selectedEngine, mode);
  byId('selectionNotice').textContent = selectedEngine !== previous
    ? `${engineCatalog[previous].name} не поддерживает режим ${MODE_NAMES[mode]}. Выбрана модель ${engineCatalog[selectedEngine].name}.` : '';
  renderMode(); renderEngine(); rememberSelection();
  if (!engineReady() && QwenEngineState.supportsMode(engineCatalog, selectedEngine, mode)) window.repairStudioModel?.('image', selectedEngine);
});

let QUALITY_STEPS = {low:40, good:50, high:60};
let QUALITY_HINTS = {
  low:'40 шагов · проверенный базовый режим.',
  good:'50 шагов · больше вычислений; улучшение зависит от изображения.',
  high:'60 шагов · максимальный бюджет sampling, медленнее.'
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
      [key, `${steps} шагов · ${sampler} · CFG ${info.cfg}${presets.length === 1 ? ' · единственный пресет модели' : (info.native_prompt && key !== 'low' ? ' · экспериментальный пресет' : '')}`]));
    // A model with a single preset hides the other Quality choices instead of leaving dead options.
    for (const item of document.querySelectorAll('input[name="quality"]')) {
      item.closest('label').hidden = !presets.includes(item.value);
      if (presets.includes(item.value)) item.nextElementSibling.textContent = presets.length === 1 ? `${QUALITY_STEPS[item.value]} шагов` : item.value[0].toUpperCase() + item.value.slice(1);
    }
    if (!presets.includes(selected('quality'))) document.querySelector(`input[name="quality"][value="${presets[0]}"]`).checked = true;
    for (const option of modeSelect.options) {
      option.hidden = option.disabled = !info.modes.includes(option.value);
    }
    const text = ENGINE_TEXT[selectedEngine] || {};
    byId('expandGroup').hidden = !info.expansion;
    byId('engineStatus').textContent = `${info.name} · ${info.ready ? 'установлена, workflow проверен' : 'недоступна'} · ${{single:'одно изображение',reference:'с референсом',prompt:'по тексту'}[mode]}`;
    byId('engineCapabilities').textContent = info.capabilities;
    byId('transparencyHint').textContent = text.transparency || (info.native_prompt
      ? 'Qwen 2.1 поддерживает RGBA, но прозрачность исходника при редактировании не гарантируется. AI Upscale сохраняет полученный альфа-канал.'
      : 'Qwen 2511 выдаёт RGB. Прозрачность исходника при редактировании не гарантируется.');
    StudioUI.setHeader('image', {model: info.name});
    if (mode === 'prompt') {
      StudioUI.setHeader('image', {description: text.header || (info.native_prompt ? 'Генерация изображения непосредственно из текста' : 'Qwen Image Edit и пустой холст')});
      byId('promptEngineHint').textContent = text.promptHint || (info.native_prompt
        ? 'Нативная генерация по тексту. Размер до 1024×1024; усилитель промпта отключён.'
        : 'Qwen Image Edit 2511 получает пустой холст. Это экспериментальный способ генерации.');
    }
    StudioUI.setHeader('image', {tone: info.ready ? 'good' : 'bad',
      status: info.ready ? 'ComfyUI: зависимости и схема workflow доступны. GPU-проверка выполняется при генерации.' : info.reasons.join('; ')});
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
  byId('selectionNotice').textContent = nextMode !== mode
    ? `${engineCatalog[selectedEngine].name} работает только в режиме ${MODE_NAMES[nextMode]}; режим переключён.` : '';
  mode = nextMode;
  renderMode(); renderEngine(); rememberSelection();
  if (!engineReady()) window.repairStudioModel?.('image', selectedEngine);
});

function renderPromptGuide() {
  const guide = VisualForgeGuides.get(selectedEngine, mode);
  if (!guide) return;
  const fields = {promptGuideTitle:'title',guidePurpose:'purpose',guideStructure:'structure',guideLanguage:'language',guideMistakes:'mistakes',guideExample:'example',guideQuality:'quality'};
  for (const [id,key] of Object.entries(fields)) byId(id).textContent = guide[key];
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
  byId('qualityHint').textContent = QUALITY_HINTS[q];
  byId('upscaleHint').textContent = u===1
    ? 'Без upscale · исходное разрешение Qwen.'
    : (u===2 ? 'AI 2× по ширине и высоте · 4× пикселей.' : 'AI 4× по ширине и высоте · 16× пикселей (два прохода).');
  if (u > 1 && !upscaleAvailable) {
    byId('upscaleHint').textContent += ' · Модель пока не подтверждена ComfyUI; сервер проверит её перед запуском.';
  }
  const info = engineCatalog[selectedEngine];
  byId('settingsSummary').textContent = `${info ? info.name : 'Qwen 2511'} · ${QUALITY_STEPS[q]} steps · CFG ${info ? info.cfg : 4} · Upscale ${u===1?'Off':u+'×'}`;
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
  byId('status').textContent = text;
}
function renderLiveStatus() {
  if (!liveState) return;
  const now = Date.now();
  const elapsedSec = Math.floor((now - liveState.startedAt) / 1000);
  const sinceUpdateSec = Math.floor((now - liveState.lastPollAt) / 1000);
  let firstLine = 'Генерация: ';
  if (liveState.phase === 'preparing') {
    firstLine += liveState.detail || 'подготовка данных';
  } else if (liveState.phase === 'submitted') {
    firstLine += 'задача отправлена в ComfyUI';
    if (liveState.promptId) firstLine += ` · ID ${liveState.promptId}`;
  } else if (liveState.phase === 'queued') {
    firstLine += 'ожидание в очереди';
    if (Number.isFinite(liveState.queuePosition)) {
      firstLine += ` · позиция ${liveState.queuePosition}`;
      if (Number.isFinite(liveState.queueTotal)) firstLine += ` из ${liveState.queueTotal}`;
    }
  } else if (liveState.phase === 'running') {
    firstLine += 'выполняется';
    if (liveState.progressPct !== null && liveState.progressPct !== undefined) {
      firstLine += ` · ${liveState.progressPct}%`;
    }
    if (liveState.node) {
      firstLine += ` · ${liveState.node}`;
    } else if (liveState.detail) {
      firstLine += ` · ${liveState.detail}`;
    }
  } else if (liveState.phase === 'cancelling') {
    firstLine += 'ComfyUI завершает прерванную задачу…';
  }
  let secondLine = `Прошло: ${formatDuration(elapsedSec)} · ответ ComfyUI: ${sinceUpdateSec} с назад`;
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
  variantButton.title = !canEdit
    ? 'Эта модель не редактирует изображения. Для нового результата измените Seed и создайте заново.'
    : !lastResultInfo
    ? 'Сначала сгенерируй изображение.'
    : 'Создать похожий вариант на основе последнего результата (ориентир: до 20% отличий).';
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
  cancelButton.textContent = 'Остановить генерацию';
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
  cancelButton.textContent = 'Остановка…';
  updateLiveStatus({phase:'cancelling', detail:'Остановка задачи'});
  try {
    const result = await request('/api/cancel', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(activeJob)
    });
    if (result.cancelled) {
      updateLiveStatus({phase:'cancelling', detail:'Отмена запрошена'});
    } else {
      updateLiveStatus({phase:'running', detail:'Задача уже почти завершилась; перепроверяем статус'});
    }
  } catch(e) {
    finishLiveStatus('Ошибка отмены: ' + e.message + '. Генерация может продолжаться.');
    cancellationInFlight = false;
    cancelButton.disabled = false;
    cancelButton.textContent = 'Повторить остановку';
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
    slot.button.textContent = selected ? 'Вставлять сюда (Ctrl+V)' : 'Выбрать для вставки (Ctrl+V)';
  }
  byId('pasteStatus').textContent =
    (inputId === 'image1' ? 'Выбрано Image 1 (основа).' : 'Выбрано Image 2 (референс).') +
    ' Скопируй картинку в браузере и нажми Ctrl+V в окне VisualForge.';
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
    byId('pasteStatus').textContent = 'Генерация идёт. Останови или дождись завершения перед заменой изображения.';
    return;
  }
  if (!SUPPORTED_PASTE_TYPES.has(imageItem.type)) {
    byId('pasteStatus').textContent = 'Этот формат буфера не поддерживается. Используй PNG, JPEG или WebP.';
    return;
  }
  const blob = imageItem.getAsFile();
  if (!blob || !blob.size || blob.size > MAX_IMAGE_BYTES) {
    byId('pasteStatus').textContent = 'Вставка отклонена: пустое изображение или размер больше 100 МиБ.';
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
    byId('pasteStatus').textContent = 'Изображение вставлено. Можно продолжить работу.';
  } catch (error) {
    byId('pasteStatus').textContent = 'Не удалось вставить изображение из буфера: ' + error.message;
  }
});
window.addEventListener('pagehide', () => {
  for (const url of previewURLs.values()) URL.revokeObjectURL(url);
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function request(url, options) {
  const r = await fetch(url, {cache:'no-store',...options});
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
async function loadSettings() {
  try {
    const data = await request('/api/settings');
    saveDirInput.value = data.save_dir || '';
    saveDirStatus.textContent = data.save_dir_warning || 'Результаты автоматически сохраняются в эту папку после успешной генерации.';
    byId('useInternalResults').hidden = !data.save_dir_warning;
    byId('useInternalResults').onclick = async () => { try { await persistSaveDir(data.internal_save_dir); byId('useInternalResults').hidden=true; } catch(e) { saveDirStatus.textContent=e.message; } };
  } catch (e) {
    saveDirStatus.textContent = 'Не удалось загрузить папку автосохранения: ' + e.message;
  }
}
async function persistSaveDir(pathText) {
  const payload = await request('/api/settings', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({save_dir: pathText})
  });
  saveDirInput.value = payload.save_dir || pathText;
  saveDirStatus.textContent = 'Папка автосохранения сохранена. Следующие результаты будут сохраняться туда автоматически.';
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
  folderPickerStatus.textContent = 'Загружаем папки…';
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
      button.textContent = drive;
      button.addEventListener('click', () => browseSaveFolder(drive));
      drives.append(button);
    }
    for (const dir of data.directories || []) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '📁 ' + dir.name;
      button.title = dir.path;
      button.addEventListener('click', () => browseSaveFolder(dir.path));
      folderPickerItems.append(button);
    }
    if (!data.directories?.length) {
      const info = document.createElement('p');
      info.textContent = 'Вложенных папок нет.';
      info.className = 'save-hint';
      folderPickerItems.append(info);
    }
    folderPickerStatus.textContent = (data.directories?.length || 0) + ' папок' + (data.truncated ? ' · показаны первые 500' : '');
    folderPickerSelect.disabled = false;
  } catch(e) {
    if (requestId !== folderPickerSequence || folderPicker.hidden) return;
    folderPickerStatus.textContent = 'Не удалось открыть папку: ' + e.message;
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
    folderPickerStatus.textContent = 'Не удалось сохранить папку: ' + e.message;
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
    saveDirStatus.textContent = 'Не удалось сохранить папку: ' + e.message;
  } finally {
    saveSaveDirButton.disabled = false;
  }
});
openSaveDirButton.addEventListener('click', async () => {
  openSaveDirButton.disabled = true;
  try {
    const result = await request('/api/open-save-dir', {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
    saveDirInput.value = result.save_dir || saveDirInput.value;
    saveDirStatus.textContent = 'Папка автосохранения открыта в Проводнике.';
  } catch (e) {
    saveDirStatus.textContent = 'Не удалось открыть папку: ' + e.message;
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
      byId('selectionNotice').textContent = selection.fallback
        ? 'Сохранённая модель недоступна. Выбран Qwen Image Edit 2511; режим сохранён.' : '';
    }
    engineInitialized = true;
    for (const option of engineSelect.options) {
      const info = engineCatalog[option.value];
      option.textContent = `${StudioUI.modelLabel(option.value, info ? info.name : option.value)} — ${info && info.ready ? 'установлена' : 'недоступна'}`;
      option.title = StudioUI.modelSource(option.value);
    }
    byId('engineDependencyDetails').textContent = Object.values(engineCatalog).map(info =>
      `${info.name}: ${info.ready ? 'готова' : info.reasons.join('; ')}`).join('\n\n');
    qwenInitProgressWebSocket(data.progress_client_id);
    lockPresets(busy);
    renderMode();
    renderEngine();
    return true;
  } catch(e) {
    engineCatalog = {};
    byId('generate').disabled = true;
    StudioUI.setHeader('image', {tone:'bad', status:'Проверка ComfyUI недоступна: ' + e.message});
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
  if (!file) {byId(text).textContent = 'Любой файл изображения до 100 МиБ · прямые форматы сохраняются как есть, остальные конвертируются в PNG'; return;}
  const blob = URL.createObjectURL(file);
  previewURLs.set(id, blob);
  view.src = blob;
  view.hidden = false;
  byId(text).textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} МиБ`;
}
for (const [input,pic,text] of [['image1','preview1','name1'],['image2','preview2','name2']]) {
  byId(input).addEventListener('change', () => selectPreview(input,pic,text));
}
async function fileToBase64(file) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Ошибка чтения изображения'));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.readAsDataURL(file);
  });
}
async function blobToBase64(blob) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Ошибка чтения изображения после конвертации'));
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
      tag.onerror = () => reject(new Error('Формат не поддерживается напрямую и не удалось декодировать его для конвертации в PNG.'));
      tag.src = objectUrl;
    });
    const width = image.naturalWidth || image.width;
    const height = image.naturalHeight || image.height;
    if (!width || !height) throw new Error('Не удалось определить размер изображения для конвертации.');
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas недоступен; конвертация в PNG невозможна.');
    ctx.drawImage(image, 0, 0);
    const pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!pngBlob) throw new Error('Браузер не смог конвертировать изображение в PNG.');
    if (pngBlob.size > MAX_IMAGE_BYTES) throw new Error('PNG после конвертации больше 100 МиБ.');
    return {base64: await blobToBase64(pngBlob)};
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
async function encodeFile(file) {
  if (!file) throw new Error('Не выбран файл изображения');
  if (file.size > MAX_IMAGE_BYTES) throw new Error('Изображение больше 100 МиБ');
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
  if (!response.ok) throw new Error('Не удалось получить предыдущий результат для варианта.');
  const sourceBlob = await response.blob();
  if (!sourceBlob.size) throw new Error('Предыдущий результат пустой.');
  const bitmap = await createImageBitmap(sourceBlob);
  try {
    const maxSide = 1536;
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas недоступен для создания варианта.');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob || !blob.size || blob.size > MAX_IMAGE_BYTES) {
      throw new Error('Не удалось подготовить предыдущий результат для варианта.');
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
  finishLiveStatus('Ошибка: ' + error.message);
  StudioUI.notify({module:'Image', id:activeJob?.prompt_id, error:true, message:error.message, onLogs:() => byId('openLogs').click()});
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
  catch (error) { finishLiveStatus(error.message); StudioUI.notify({module:'Image',error:true,message:error.message,onLogs:()=>byId('openLogs').click()}); return; }
  const referenceFile = isVariant ? prior.referenceFile :
    (mode === 'reference' ? byId('image2').files[0] : null);
  const requestMode = selectionMode;
  beginTask();
  try {
    startLiveStatus('preparing', isVariant ? 'подготовка похожего варианта из последнего результата' : 'подготовка изображений и prompt');
    const image1 = isVariant ? await variantSourceImage(prior.href) :
      (requestMode === 'prompt' ? null : await encodeFile(sourceFile));
    const image2 = requestMode === 'reference' ? await encodeFile(referenceFile) : null;
    updateLiveStatus({phase:'preparing', detail:'загрузка изображений в локальный ComfyUI'});
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
    qwenRecordStage('Задание принято ComfyUI');
    const started = Date.now();
    let misses = 0;
    for (;;) {
      await sleep(2000);
      // One failed look (ComfyUI busy for a moment) is not a failed job: the job keeps running and the bridge still saves it.
      let job;
      try { job = await request('/api/status?id=' + encodeURIComponent(result.prompt_id)); misses = 0; }
      catch (error) {
        if (++misses >= 6) {reportImageError(error);return;}
        updateLiveStatus({detail2: 'нет ответа от ComfyUI, повторяю…'});
        continue;
      }
      if (liveState?.detail2) liveState.detail2 = '';
      if (job.state === 'cancelled') {
        finishLiveStatus('Генерация отменена. Можно запускать следующую.');
        break;
      }
      if (job.state === 'cancelling') {
        updateLiveStatus({phase:'cancelling'});
        continue;
      }
      if (job.state === 'error') {reportImageError({message:job.error || 'Ошибка исполнения'});return;}
      if (job.state === 'done') {
        lastSeed = result.seed;
        qwenRecordStage('Готово: результат получен');
        hasSuccessfulResult = true;
        rememberEngine(result.engine);
        rememberSelection();
        const saveLine = job.save_error
          ? `\nАвтосохранение не удалось: ${job.save_error}. Копия осталась в ComfyUI.`
          : (job.saved_count ? `\nАвтосохранено: ${job.saved_count} файл(ов) · ${job.save_dir}` : '\nАвтосохранение проверяется…');
        finishLiveStatus(`${isVariant ? 'Новый вариант готов' : 'Готово'}. Результатов: ${job.images} · ${quality.toUpperCase()} / ${upscale===1?'Off':upscale+'×'}${saveLine}`);
        let firstResultHref = null;
        for (let i = 0; i < job.images; i++) {
          const wrapper = document.createElement('div');
          const meta = document.createElement('p');
          meta.className='result-meta';
          meta.textContent=`${engineCatalog[result.engine]?.name || result.engine} · ${quality.toUpperCase()} · ${result.steps} steps · Upscale ${upscale===1?'Off':upscale+'×'} · Seed ${result.seed}`;
          const image = document.createElement('img');
          image.loading = 'lazy'; image.decoding = 'async';
          const href = '/api/result?id=' + encodeURIComponent(result.prompt_id) + '&index=' + i;
          if (i === 0) firstResultHref = href;
          image.src = href; image.alt = 'Сгенерированное изображение';
          image.addEventListener('load', () => { meta.textContent += ` · ${image.naturalWidth}×${image.naturalHeight} px`; }, {once:true});
          const link = document.createElement('a');
          link.href = href; link.download = `${result.filename_base || 'Generated Image'}${i ? ' ' + (i + 1) : ''}.png`; link.textContent = 'Сохранить результат';
          wrapper.append(meta);
          if (job.expanded_prompt) {
            // What the image model actually received after the upsampler; textContent keeps the model output inert.
            const details = document.createElement('details');
            const summary = document.createElement('summary');
            summary.textContent = 'Расширенный промпт (upsampler)';
            const text = document.createElement('pre');
            text.textContent = job.expanded_prompt;
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
          message:job.save_error ? 'Изображение создано, но автосохранение не удалось: '+job.save_error : 'Изображение создано и сохранено.',
          folder:job.saved_count ? job.save_dir : '', onLogs:() => byId('openLogs').click()});
        loadResultHistory();
        break;
      }
      if (Date.now() - started > 60 * 60 * 1000) {reportImageError({message:'Нет результата в течение часа. Проверьте журнал ComfyUI.'});return;}
      if (job.state === 'queued') {
        if (!qwenRealtime || Date.now() - qwenRealtimeUpdated > 5000) qwenRecordStage('Ожидание в очереди ComfyUI');
        updateLiveStatus({phase:'queued', queuePosition: Number.isFinite(job.queue_position) ? job.queue_position : null, queueTotal: Number.isFinite(job.queue_total) ? job.queue_total : null});
      } else {
        if (qwenRealtime && Date.now() - qwenRealtimeUpdated <= 5000) {
          const current = qwenRealtime;
          updateLiveStatus({phase:'running', detail: 'Live этап ComfyUI', progressPct: null,
            node: current.step === null ? current.node : `${current.node} · шаг ${current.step}/${current.total}`});
        } else {
          updateLiveStatus({phase:'running', detail: job.status || 'ComfyUI выполняет задачу', progressPct: (typeof job.progress_pct === 'number') ? job.progress_pct : null, node: job.node || ''});
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
    throw new Error('Seed должен быть целым числом от 0 до 281474976710655.');
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
  catch (error) { byId('seed').setCustomValidity(error.message); }
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
    byId('historyStatus').textContent = result.warning || (rows.length ? `Заданий в истории: ${rows.length}. Показаны последние ${Math.min(20,rows.length)}. Файлы хранятся в выбранных при генерации папках.` : 'Новые сохранённые результаты появятся здесь и останутся после перезапуска. История хранит до 100 заданий.');
    for (const row of rows.slice(0,20)) {
      const card = document.createElement('article'); card.className = 'history-card';
      const heading = document.createElement('strong'); heading.textContent = row.filename_base || 'Сохранённый результат';
      const meta = document.createElement('p'); meta.className = 'preset-hint';
      meta.textContent = `${row.engine} · ${row.quality || ''} · Seed ${row.seed} · ${row.upscale || 1}×`;
      const prompt = document.createElement('p'); prompt.className = 'history-prompt'; prompt.textContent = row.original_prompt || '';
      card.append(heading,meta,prompt);
      const links = document.createElement('div'); links.className = 'history-actions';
      for (const index of row.image_indices || []) {
        const link = document.createElement('a'); link.href = '/api/result?id='+encodeURIComponent(row.prompt_id)+'&index='+index;
        link.textContent = index ? `Открыть результат ${index+1}` : 'Открыть результат'; link.target = '_blank'; link.rel = 'noopener'; links.append(link);
      }
      if (!row.image_indices?.length) { const missing = document.createElement('span'); missing.textContent = 'Файлы сейчас недоступны. Проверьте папку результатов или подключение диска.'; links.append(missing); }
      if (Number.isSafeInteger(row.seed) && row.seed >= 0 && row.seed < 2**48) {
        const seedButton = document.createElement('button'); seedButton.type = 'button'; seedButton.className='secondary slim'; seedButton.textContent='Использовать seed'; seedButton.disabled=busy; seedButton.addEventListener('click',()=>useSeed(row.seed)); links.append(seedButton);
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
  } catch { byId('historyStatus').textContent = 'История сейчас недоступна. Генерация и сохранённые файлы остаются доступными.'; }
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
  logsStatus.textContent = 'Читаем последние записи…';
  try {
    const result = await request('/api/logs?scope=' + encodeURIComponent(logsScope.value));
    if (sequence !== logsSequence || logsPanel.hidden) return;
    logsText = String(result.text || 'Нет записей.');
    logsOutput.textContent = logsText;
    logsStatus.textContent = `Файлов: ${result.files || 0}` +
      (result.truncated ? ' · показаны последние фрагменты' : '') + ' · локально, без отправки в интернет';
  } catch (error) {
    if (sequence !== logsSequence || logsPanel.hidden) return;
    logsText = '';
    logsOutput.textContent = 'Не удалось прочитать логи: ' + error.message;
    logsStatus.textContent = 'Ошибка получения логов';
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
  if (!logsText) { logsStatus.textContent = 'Сначала получи логи.'; return; }
  if (await copyLogsText(logsText)) {
    logsStatus.textContent = 'Логи скопированы в буфер обмена.';
  } else {
    logsStatus.textContent = 'Копирование недоступно: выдели текст журнала вручную (Ctrl+A / Ctrl+C внутри области).';
    logsOutput.focus();
  }
});
logsSaveTxt.addEventListener('click', () => {
  if (!logsText) { logsStatus.textContent = 'Сначала получи логи.'; return; }
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
    logsStatus.textContent = 'TXT передан браузеру для сохранения.';
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
  '41': 'Загрузка исходного изображения',
  '83': 'Загрузка референса',
  '161': 'Загрузка Qwen diffusion model',
  '162': 'Загрузка текстового энкодера',
  '146': 'Загрузка VAE',
  '170': 'Подключение LoRA',
  '145': 'Настройка сэмплинга',
  '152': 'Подготовка CFG',
  '160': 'Масштабирование входного изображения',
  '151': 'Обработка промпта',
  '149': 'Подготовка conditioning',
  '148': 'Подготовка референсов',
  '147': 'Подготовка conditioning',
  '156': 'Кодирование входного изображения (VAE)',
  '169': 'Генерация изображения · KSampler',
  '158': 'Декодирование результата (VAE)',
  '200': 'Загрузка модели Real-ESRGAN',
  '201': 'AI Upscale · первый проход',
  '202': 'AI Upscale · второй проход',
  '9': 'Запись готового файла'
};
function qwenAddStage(text) {
  if (!text || text === qwenLastRecordedStage) return;
  qwenLastRecordedStage = text;
  const date = new Date();
  const clock = date.toLocaleTimeString('ru-RU', {hour12:false});
  qwenStageHistory.push(`[${clock}] ${text}`);
  qwenStageHistory = qwenStageHistory.slice(-8);
  qwenStageEvents.textContent = qwenStageHistory.join('\n');
}
function qwenResetProgressUI() {
  qwenStageHistory = [];
  qwenLastRecordedStage = '';
  qwenLastSamplerStep = -1;
  qwenRealtime = null;
  qwenRealtimeUpdated = 0;
  qwenStagePanel.hidden = false;
  qwenSamplerProgress.hidden = true;
  qwenStageStep.textContent = '';
  qwenStageName.textContent = 'Подготовка запроса';
  qwenStageEvents.textContent = '';
  qwenProgressHint.textContent = 'Показываем только реальные события ComfyUI. Процент шагов — не процент всей генерации.';
  qwenAddStage('Подготовка запроса');
}
function qwenRecordStage(stage, step, maxSteps) {
  qwenStagePanel.hidden = false;
  qwenStageName.textContent = stage;
  if (Number.isFinite(step) && Number.isFinite(maxSteps) && maxSteps > 0) {
    qwenStageStep.textContent = `Шаг ${step}/${maxSteps}`;
    qwenSamplerProgress.hidden = false;
    qwenSamplerProgress.max = maxSteps;
    qwenSamplerProgress.value = Math.max(0, Math.min(maxSteps, step));
    if (step !== qwenLastSamplerStep) {
      qwenLastSamplerStep = step;
      qwenAddStage(`KSampler · шаг ${step}/${maxSteps}`);
    }
  } else {
    qwenStageStep.textContent = '';
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
    qwenRecordStage('ComfyUI начал выполнение');
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
    updateLiveStatus({phase:'running', node: step === null ? label : `${label} · шаг ${step}/${total}`, progressPct:null});
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
      qwenProgressHint.textContent = 'Live-прогресс недоступен: остаётся надёжный опрос состояния задачи каждые 2 секунды.';
    });
    ws.addEventListener('error', () => {
      qwenProgressHint.textContent = 'Нет WebSocket-прогресса; работает HTTP-статус. Результат генерации не затронут.';
    });
  } catch (error) {
    qwenProgressHint.textContent = 'ComfyUI WebSocket не подключился, используется обычный HTTP-статус.';
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
  const search = qwenArchiveSearch.value.toLocaleLowerCase('ru-RU');
  const matches = qwenFavorites.filter(item => item.prompt.toLocaleLowerCase('ru-RU').includes(search));
  qwenArchiveEntries.replaceChildren();
  if (!matches.length) {
    const empty = document.createElement('p');
    empty.className = 'qwen-muted';
    empty.textContent = search ? 'По запросу ничего нет.' : 'Архив пока пуст. Сохрани промпт кнопкой ♡.';
    qwenArchiveEntries.append(empty);
    return;
  }
  for (const item of matches) {
    const wrap = document.createElement('div');
    wrap.className = 'qwen-archive-item';
    const meta = document.createElement('div');
    meta.className = 'qwen-archive-meta';
    meta.textContent = `Сохранён: ${item.created_at}`;
    const pre = document.createElement('pre');
    pre.textContent = item.prompt; // Never inject prompt text as HTML.
    const actions = document.createElement('div');
    actions.className = 'qwen-archive-buttons';
    const append = document.createElement('button');
    append.type = 'button';
    append.className = 'secondary';
    append.textContent = 'Добавить к промпту';
    append.addEventListener('click', () => {
      const target = byId('prompt');
      const separator = target.value.trim() ? '\n\n' : '';
      if ((target.value + separator + item.prompt).length > 5000) {
        qwenArchiveStatus.textContent = 'После добавления получится больше 5000 символов. Сократи текущий промпт.';
        return;
      }
      target.value += separator + item.prompt;
      target.dispatchEvent(new Event('input', {bubbles:true}));
      qwenCloseArchive();
      target.focus();
      target.setSelectionRange(target.value.length, target.value.length);
      qwenFavStatus.textContent = 'Промпт из архива добавлен в конец текста.';
    });
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'qwen-danger';
    del.textContent = 'Удалить из архива';
    del.addEventListener('click', async () => {
      if (!confirm('Удалить этот промпт из архива? Это нельзя отменить.')) return;
      del.disabled = true;
      try {
        const result = await request('/api/favorites', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({action:'delete', id:item.id})
        });
        qwenFavorites = result.entries;
        qwenArchiveStatus.textContent = 'Запись удалена из архива.';
        qwenRenderFavorites();
      } catch (error) {
        del.disabled = false;
        qwenArchiveStatus.textContent = 'Ошибка удаления: ' + error.message;
      }
    });
    actions.append(append,del);
    wrap.append(meta,pre,actions);
    qwenArchiveEntries.append(wrap);
  }
}
qwenFavSaveButton.addEventListener('click', async () => {
  const prompt = byId('prompt').value;
  if (!prompt.trim()) { qwenFavStatus.textContent = 'Сначала введи промпт.'; return; }
  qwenFavSaveButton.disabled = true;
  try {
    const result = await request('/api/favorites', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'add',prompt})
    });
    qwenFavorites = result.entries;
    qwenFavStatus.textContent = result.duplicate ? 'Этот промпт уже есть в архиве.' : '♡ Сохранено в portable-архив.';
  } catch(error) { qwenFavStatus.textContent = 'Ошибка сохранения: ' + error.message; }
  finally { qwenFavSaveButton.disabled = false; }
});
qwenFavOpenButton.addEventListener('click', async () => {
  qwenLastFocus = document.activeElement;
  qwenArchivePanel.hidden = false;
  qwenArchiveStatus.textContent = 'Загружаем архив…';
  qwenArchiveSearch.value = '';
  byId('qwenArchiveClose').focus();
  try {
    await qwenReloadFavorites();
    qwenArchiveStatus.textContent = `Сохранено промптов: ${qwenFavorites.length}`;
  } catch(error) {
    qwenArchiveStatus.textContent = 'Не удалось прочитать архив: ' + error.message;
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
