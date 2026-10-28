'use strict';
(() => {
  // Voice input for every text field: focus a field, press the microphone (or F9) and speak. When you stop speaking the recording ends
  // by itself (or press again), the text is recognized on this computer (Whisper, see /api/speech/*) and inserted at the cursor.
  // While you speak, the words recognized so far appear in the bar (when the video card is free; otherwise only the final text comes).
  const LANG_KEY = 'visualforge.voice.lang.v1';
  const ENHANCE_KEY = 'visualforge.voice.enhance.v1';
  const NO_VOICE_IDS = new Set(['saveDir', 'videoSaveDir', 'musicSaveDir', 'componentsTextTag', 'folderPickerPath']); // paths and tags: dictation makes no sense
  const LANGUAGES = [['ru', 'RU'], ['en', 'EN'], ['uk', 'UK'], ['de', 'DE'], ['fr', 'FR'], ['es', 'ES'], ['auto', 'Авто']];
  const RATE = 16000, PARTIAL_EVERY_MS = 1400, PARTIAL_WINDOW_S = 25, MIN_SAMPLES = RATE * 0.4;
  const Gate = window.VoiceGate;
  let target = null, state = 'idle', availability = null, hideTimer = null, warmedAt = 0;
  let stream = null, context = null, nodes = [], chunks = [], gate = null, tick = null, partialTimer = null, inFlight = false, preview = '', startedAt = 0, speaking = false, partialController = null;
  let bar, button, select, label, meter, meterFill, enhanceBox, enhanceLabel, hint;
  let hintTimer = null, enhanceTimer = null, enhanceRequest = 0, enhanceTask = null, busyKind = '';

  const make = (tag, className, text) => {const node = document.createElement(tag);if (className) node.className = className;if (text !== undefined) node.textContent = text;return node;};
  const remember = value => {try {localStorage.setItem(LANG_KEY, value);} catch {}};
  const recall = () => {try {return localStorage.getItem(LANG_KEY) || 'ru';} catch {return 'ru';}};
  const enhanceWanted = () => {try {return localStorage.getItem(ENHANCE_KEY) === '1';} catch {return false;}};

  function eligible(node) {
    if (!(node instanceof HTMLElement) || node.disabled || node.readOnly || node.dataset.noVoice !== undefined || NO_VOICE_IDS.has(node.id)) return false;
    if (node.closest('#voiceBar, #studioBusy') || node.inputMode === 'numeric') return false;
    if (node instanceof HTMLTextAreaElement) return true;
    return node instanceof HTMLInputElement && ['text', 'search', ''].includes((node.getAttribute('type') || 'text').toLowerCase());
  }

  // ---- the floating bar -------------------------------------------------------------------------------------------
  function build() {
    bar = make('div', 'voice-bar');bar.id = 'voiceBar';bar.hidden = true;bar.dataset.allowBusy = '1';
    button = make('button', 'voice-mic', '🎙');button.type = 'button';button.dataset.allowBusy = '1';
    button.setAttribute('aria-label', 'Голосовой ввод');button.title = 'Голосовой ввод (F9)';
    select = document.createElement('select');select.className = 'voice-lang';select.dataset.allowBusy = '1';select.setAttribute('aria-label', 'Язык речи');
    for (const [value, text] of LANGUAGES) {const option = make('option', '', text);option.value = value;select.append(option);}
    select.value = recall();select.onchange = () => remember(select.value);
    enhanceLabel = make('label', 'voice-enhance');enhanceLabel.dataset.allowBusy = '1';
    enhanceBox = document.createElement('input');enhanceBox.type = 'checkbox';enhanceBox.dataset.allowBusy = '1';enhanceBox.checked = enhanceWanted();
    enhanceBox.setAttribute('aria-label', 'Улучшенное распознавание с Gemma AI');
    enhanceLabel.append(enhanceBox, make('span', 'voice-switch'), make('span', 'voice-enhance-text', 'Gemma AI'));
    enhanceBox.onchange = () => {
      try {localStorage.setItem(ENHANCE_KEY, enhanceBox.checked ? '1' : '0');} catch {}
      clearTimeout(hideTimer);
      if (!enhanceBox.checked) {stopEnhanceNotice();updateEnhance();return;}
      if (!enhanceReady()) {showHint(enhanceReason(), 8000);return;}
      prepareEnhance();
    };
    meter = make('span', 'voice-meter');meterFill = make('i');meter.append(meterFill);meter.hidden = true;
    label = make('span', 'voice-label');label.setAttribute('role', 'status');
    hint = make('div', 'voice-hint');hint.setAttribute('role', 'status');hint.setAttribute('aria-live', 'polite');hint.setAttribute('aria-hidden', 'true');
    bar.append(button, select, enhanceLabel, meter, label, hint);document.body.append(bar);
    updateEnhance();
    // Pressing the bar must not take the focus (and the cursor position) away from the text field.
    bar.addEventListener('mousedown', event => {if (event.target !== select) event.preventDefault();});
    bar.addEventListener('focusin', () => clearTimeout(hideTimer));
    button.onclick = toggle;
  }

  function place() {
    if (!target || bar.hidden) return;
    const rect = target.getBoundingClientRect();
    const height = bar.offsetHeight || 32;
    // Anchored by its right edge, so a long message grows to the left instead of being squeezed against the window edge.
    bar.style.left = 'auto';
    bar.style.right = Math.max(4, window.innerWidth - rect.right) + 'px';
    bar.style.top = Math.max(4, rect.top - height - 4) + 'px';
  }
  // Loading the model takes a while, so it starts as soon as the microphone appears (at most once every four minutes).
  function warm() {
    if (Date.now() - warmedAt < 240000 || (availability && !availability.available)) return;
    warmedAt = Date.now();
    fetch('/api/speech/prepare', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}).catch(() => {});
  }
  const show = () => {
    clearTimeout(hideTimer);bar.hidden = false;place();
    if (!availability) load().then(prepareEnhance);else prepareEnhance();
    warm();
  };
  const hideSoon = () => {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (state === 'idle' && document.activeElement !== target && !bar.contains(document.activeElement)) {
        bar.hidden = true;stopEnhanceNotice();
      }
    }, 250);
  };

  function setState(next, text) {
    state = next;
    bar.dataset.state = next;
    if (next !== 'idle') {clearTimeout(hideTimer);bar.hidden = false;}   // the permission dialog takes the focus away from the field: the bar must stay
    label.textContent = text || '';
    meter.hidden = next !== 'recording';
    button.textContent = next === 'recording' ? '⏹' : next === 'processing' || next === 'starting' ? '…' : '🎙';
    button.disabled = next === 'processing';
    button.title = next === 'recording' ? 'Остановить запись и распознать (F9)' : 'Голосовой ввод (F9)';
    place();
  }
  function flash(text, ms = 4000) {
    label.textContent = text;
    setTimeout(() => {if (state === 'idle' && label.textContent === text) label.textContent = '';}, ms);
  }
  function showRecording() {
    if (state !== 'recording') return;
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    const words = preview ? '«…' + preview.slice(-70) + '»' : speaking ? 'слушаю…' : 'говорите';
    label.textContent = seconds + ' с · ' + words + (speaking ? ' · пауза — и я вставлю текст' : '');
    meterFill.style.width = Math.round((gate ? gate.level : 0) * 100) + '%';
    place();
  }

  // ---- the Gemma switch ---------------------------------------------------------------------------------------------
  const generating = () => !!(window.StudioUI && window.StudioUI.isBusy());
  const enhanceReady = () => !!(availability && availability.enhance && availability.enhance.available);
  const enhanceReason = () => (availability && availability.enhance && availability.enhance.reason) || 'Gemma AI сейчас недоступна: нужна модель в разделе Text.';
  function updateEnhance() {
    const ready = enhanceReady();
    enhanceLabel.classList.toggle('unavailable', !ready);
    enhanceLabel.title = ready
      ? 'Gemma AI — локальный ИИ: после Whisper модель (' + availability.enhance.model + ') расставит знаки и заглавные и исправит ошибки. Первый запуск требует загрузки модели в видеопамять.'
      : 'Улучшенное распознавание недоступно: ' + enhanceReason();
    // Availability may change while the GPU is busy; it must not overwrite the user's saved choice.
  }

  function showHint(text, ms = 0) {
    clearTimeout(hintTimer);hintTimer = null;
    if (hint.textContent !== text) hint.textContent = text;
    // Establish the hidden frame even when the whole bar was just shown.
    if (!hint.classList.contains('visible')) hint.getBoundingClientRect();
    hint.classList.add('visible');hint.setAttribute('aria-hidden', 'false');
    if (ms) hintTimer = setTimeout(hideHint, ms);
  }
  function hideHint() {
    clearTimeout(hintTimer);hintTimer = null;
    hint.classList.remove('visible');hint.setAttribute('aria-hidden', 'true');
  }
  function stopEnhanceNotice() {
    enhanceRequest++;enhanceTask = null;clearTimeout(enhanceTimer);enhanceTimer = null;hideHint();
  }
  function prepareEnhance() {
    if (!enhanceBox.checked || !enhanceReady() || bar.hidden || enhanceTask !== null) return;
    const request = ++enhanceRequest, started = Date.now(), model = availability.enhance.model;
    enhanceTask = request;
    let sent = false;
    const current = () => enhanceTask === request && enhanceRequest === request && enhanceBox.checked && !bar.hidden;
    const fail = message => {
      if (!current()) return;
      enhanceTask = null;showHint(message, 8000);
    };
    const loading = 'Gemma AI загружается… Первый запуск может занять несколько минут.';
    const waiting = 'Gemma AI ждёт освобождения видеокарты…';
    showHint(generating() && busyKind !== 'voice' ? waiting : loading);
    async function poll() {
      try {
        const response = await fetch('/api/text/status', {cache: 'no-store'});
        if (!response.ok) {fail('Не удалось проверить загрузку Gemma AI. Попробуйте включить её снова.');return;}
        const status = await response.json();
        if (!current()) return;
        const jobs = Array.isArray(status.generating) ? status.generating : [];
        const otherBusy = (generating() && busyKind !== 'voice') || jobs.some(id => id !== 'voice-polish');
        if (otherBusy) {
          showHint(waiting);sent = false;
        } else {
          if ((status.loaded || []).some(item => item.name === model) && !jobs.includes('voice-polish')) {
            enhanceTask = null;hideHint();return;
          }
          showHint(loading);
          if (!sent && !jobs.includes('voice-polish')) {
            const prepare = await fetch('/api/speech/prepare', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enhance: true})});
            if (!current()) return;
            if (!prepare.ok || !(await prepare.json()).preparing) {fail('Gemma AI не удалось запустить. Проверьте доступность модели в Text.');return;}
            if (!current()) return;
            sent = true;
          }
        }
        // Loaded models, not elapsed UI timers, decide when the hint disappears.
        if (Date.now() - started > 300000) {fail('Gemma AI пока не готова. Проверьте состояние Text и видеокарты.');return;}
        if (current()) enhanceTimer = setTimeout(poll, 1000);
      } catch {
        fail('Нет связи со студией. Проверьте, запущено ли приложение.');
      }
    }
    poll();
  }

  // ---- availability ------------------------------------------------------------------------------------------------
  async function load() {
    try {
      const response = await fetch('/api/speech/availability', {cache: 'no-store'});
      availability = response.ok ? await response.json() : {available: false, reason: 'Голосовой ввод недоступен.'};
    } catch {availability = {available: false, reason: 'Нет связи со студией.'};}
    updateEnhance();
    return availability;
  }

  // ---- recording -----------------------------------------------------------------------------------------------------
  let microphoneEvent=null, recognitionEvent=null;
  async function start() {
    if (!target || state !== 'idle') return;
    microphoneEvent=window.StudioEvents?.microphoneStart();recognitionEvent=microphoneEvent;
    setState('starting', 'Разрешите доступ к микрофону, если браузер спросит…');
    if (!availability || !availability.available) await load();
    if (!availability.available) {setState('idle', '');flash(availability.reason + ' Нажмите, чтобы открыть «Компоненты».', 8000);label.onclick = () => window.showComponents && window.showComponents();return;}
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !Gate) {setState('idle', '');flash('Браузер не даёт доступ к микрофону.');return;}
    try {stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});}
    catch (error) {setState('idle', '');flash(error && error.name === 'NotAllowedError' ? 'Микрофон запрещён. Разрешите его для этого окна (Edge: Параметры → Разрешения сайтов → Микрофон) и проверьте Параметры Windows → Конфиденциальность → Микрофон.' : 'Микрофон не найден или занят.', 8000);return;}
    try {
      try {context = new (window.AudioContext || window.webkitAudioContext)({sampleRate: RATE});} catch {context = new (window.AudioContext || window.webkitAudioContext)();}
      if (context.state === 'suspended') await context.resume();
      const source = context.createMediaStreamSource(stream);
      const mute = context.createGain();mute.gain.value = 0;mute.connect(context.destination);
      let capture;
      try {
        await context.audioWorklet.addModule('/voice-worklet.js');
        capture = new AudioWorkletNode(context, 'vf-capture');
        capture.port.onmessage = event => onSamples(event.data);
      } catch {
        capture = context.createScriptProcessor(4096, 1, 1);   // older engines, or a worklet that could not be loaded
        capture.onaudioprocess = event => onSamples(new Float32Array(event.inputBuffer.getChannelData(0)));
      }
      source.connect(capture);capture.connect(mute);
      nodes = [source, capture, mute];
    } catch (error) {release('error');setState('idle', '');flash('Не удалось начать запись: ' + (error.message || error), 8000);return;}
    chunks = [];preview = '';speaking = false;inFlight = false;startedAt = Date.now();
    gate = new Gate.SpeechGate(context.sampleRate);
    warmedAt = 0;warm();    // the model must be loading while the person speaks
    tick = setInterval(showRecording, 100);
    partialTimer = setInterval(partial, PARTIAL_EVERY_MS);
    setState('recording', 'говорите');
    showRecording();
  }

  function onSamples(chunk) {
    if (state !== 'recording') return;
    chunks.push(chunk);
    for (const event of gate.push(chunk)) {
      if (event === 'speech-start') speaking = true;
      else if (event === 'stop-silence' || event === 'stop-max') stop('done');
      else if (event === 'stop-no-speech') stop('no-speech');
    }
  }

  function release(eventState='done') {
    window.StudioEvents?.microphoneFinish(microphoneEvent,eventState);microphoneEvent=null;
    clearInterval(tick);clearInterval(partialTimer);tick = partialTimer = null;
    for (const node of nodes) {try {node.disconnect();} catch {}}
    nodes = [];
    if (stream) for (const track of stream.getTracks()) track.stop();
    if (context) context.close().catch(() => {});
    stream = context = null;
  }

  function samples16k(seconds) {
    let all = Gate.concat(chunks);
    if (seconds) all = all.slice(Math.max(0, all.length - Math.round(seconds * context.sampleRate)));
    return Gate.resample(all, context.sampleRate, RATE);
  }

  function stop(reason) {
    if (state !== 'recording') return;
    const audio = reason === 'cancel' ? null : samples16k(0);
    if (partialController) partialController.abort();   // the final recording must not queue behind a preview
    release(reason === 'cancel' ? 'cancelled' : 'done');
    if (reason === 'cancel') {setState('idle', '');return;}
    if (reason === 'no-speech' && !preview) {setState('idle', '');flash('Не услышал речь. Проверьте микрофон и говорите ближе.');return;}
    if (audio.length < MIN_SAMPLES) {setState('idle', '');flash('Слишком короткая запись.');return;}
    finish(audio);
  }

  async function finish(audio) {
    const wanted = enhanceBox.checked && enhanceReady();
    const enhance = wanted && !generating();   // the video card belongs to the running generation
    setState('processing', enhance ? 'Распознаю и улучшаю с Gemma AI…' : 'Распознаю…');
    try {
      const answer = await transcribe(new Blob([Gate.wavBuffer(audio, RATE)], {type: 'audio/wav'}), false, undefined, enhance);
      setState('idle', '');
      if (answer.text) {insert(target, answer.text);flash(answer.note ? 'Готово. ' + answer.note : wanted && !enhance ? 'Готово. Идёт генерация: Gemma AI пропущена, текст без её правки.' : enhance ? 'Готово (Gemma AI)' : 'Готово', answer.note || (wanted && !enhance) ? 9000 : 4000);}
      else flash('Речь не распознана. Говорите ближе к микрофону.');
    } catch (error) {setState('idle', '');flash(error.message || 'Не удалось распознать.', 8000);}
  }

  // Live preview: the recording so far is recognized again and again while the person speaks (only when the model is on the video card).
  async function partial() {
    if (state !== 'recording' || inFlight || !speaking || !chunks.length) return;
    inFlight = true;
    partialController = new AbortController();
    try {
      const answer = await transcribe(new Blob([Gate.wavBuffer(samples16k(PARTIAL_WINDOW_S), RATE)], {type: 'audio/wav'}), true, partialController.signal);
      if (state === 'recording' && answer.text) preview = answer.text;
    } catch {} finally {inFlight = false;}
  }

  async function transcribe(wav, isPartial, signal, enhance) {
    const microphone=await recognitionEvent;
    const headers={'Content-Type':'audio/wav'};
    // Headers keep the technical parent ID out of the existing HTTP request-path log.
    if(microphone)headers['X-VisualForge-Session']=microphone.id;
    let response;
    try {response = await fetch('/api/speech/transcribe?lang=' + encodeURIComponent(select.value) + (isPartial ? '&partial=1' : '') + (enhance ? '&enhance=1' : ''), {method: 'POST', headers, body: wav, signal});}
    catch {throw new Error('Нет связи со студией. Проверьте, запущено ли приложение.');}
    let value;
    try {value = await response.json();} catch {throw new Error('Некорректный ответ службы распознавания.');}
    if (!response.ok) throw new Error(value.error || 'Распознавание недоступно');
    return {text: value.skipped ? '' : String(value.text || '').trim(), note: value.enhance_note || ''};
  }

  // ---- inserting the text ----------------------------------------------------------------------------------------------
  function insert(field, text) {
    if (!field || !text) return;
    const before = field.value.slice(0, field.selectionStart), after = field.value.slice(field.selectionEnd);
    const lead = before && !/[\s(«"]$/.test(before) ? ' ' : '';
    const trail = after && !/^[\s.,;:!?)»"]/.test(after) ? ' ' : '';
    const value = before + lead + text + trail + after;
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), 'value').set;
    setter.call(field, value);
    const caret = (before + lead + text).length;
    field.setSelectionRange(caret, caret);
    field.dispatchEvent(new Event('input', {bubbles: true}));
    field.focus();
  }

  function toggle() {
    if (state === 'recording') stop('done');
    else if (state === 'idle') start();
    else if (state === 'starting') {/* waiting for the permission answer */}
  }

  // ---- wiring ------------------------------------------------------------------------------------------------------------
  build();
  document.addEventListener('focusin', event => {
    if (state !== 'idle') return;
    if (eligible(event.target)) {target = event.target;show();}
  });
  document.addEventListener('focusout', event => {
    // A label click can focus its checkbox despite mousedown.preventDefault(). Keep the whole bar in the focus scope.
    if ((event.target === target || bar.contains(event.target)) && event.relatedTarget !== target && !bar.contains(event.relatedTarget)) hideSoon();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'F9') {
      event.preventDefault();
      if (state === 'idle' && eligible(document.activeElement)) {target = document.activeElement;show();}
      toggle();
    } else if (event.key === 'Escape' && state === 'recording') stop('cancel');   // drop the recording without recognizing it
  });
  // Keep the saved choice while another module owns the GPU; resume warmup when the voice bar is visible.
  window.addEventListener('studio:busy', event => {
    busyKind = event.detail?.kind || '';
    enhanceLabel.classList.toggle('blocked', busyKind !== 'voice');
    if (busyKind !== 'voice') enhanceLabel.title = 'Идёт генерация: Gemma AI загрузится после её окончания. Ваш выбор сохранён.';
  });
  window.addEventListener('studio:idle', event => {
    busyKind = '';enhanceLabel.classList.remove('blocked');updateEnhance();
    const ownWork = event.detail && event.detail.kind === 'voice';   // Gemma's own loading or editing must not trigger another load
    if (!ownWork) prepareEnhance();
  });
  window.addEventListener('resize', place);
  window.addEventListener('scroll', place, true);
  window.VoiceInput = {insert, transcribe, load, finish, get state() {return state;}};
})();
