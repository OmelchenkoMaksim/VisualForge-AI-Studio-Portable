'use strict';
(() => {
  // Gemma is a required component: the scenario writer of the Video section, reading of screenshots and voice and the dictation
  // editor all use it. This window appears whenever no usable Gemma is installed (and from the Components tab), offers the
  // download with its mirrors, and lets the person point at a GGUF file on disk when no source is reachable.
  const el = id => document.getElementById(id);
  const dialog = el('gemmaDialog');
  if (!dialog) return;
  const SKIP_KEY = 'vf-gemma-skip';
  let state = null, request = null, deadline = 0, timer = null, busy = false, manual = false, lastRunning = false, polling = false;
  const fmt = (n, digits = 1) => Number(n).toLocaleString(StudioI18n.locale, {maximumFractionDigits: digits});
  const gb = bytes => bytes >= 1e9 ? fmt(bytes / 1e9) + StudioI18n.t('components.memory_gigabytes_unit_suffix') : fmt(bytes / 1e6, 0) + StudioI18n.t('components.memory_megabytes_unit_suffix');
  const clock = seconds => {seconds = Math.max(0, Math.round(seconds));return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');};
  const text = (node, read) => StudioI18n.bind(node, 'textContent', read, true);
  const skipped = () => {try {return sessionStorage.getItem(SKIP_KEY) === '1';} catch {return false;}};
  const remember = () => {try {sessionStorage.setItem(SKIP_KEY, '1');} catch {}};
  async function api(route, data) {
    let response;
    try {response = await fetch('/api/components' + route, data === undefined ? {cache: 'no-store'} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error(StudioI18n.t('components.api_request_connect_studio_check_application_running_unavailable'));}
    let value;
    try {value = await response.json();} catch {throw new Error(StudioI18n.t('components.api_request_invalid_installer_response'));}
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || StudioI18n.t('components.api_request_installer_unavailable'));
    return value;
  }
  const group = () => (state?.required || []).find(item => item.id === 'gemma');
  const missing = () => !!group() && !group().satisfied;
  const showError = message => text(el('gemmaError'), () => StudioI18n.remote(message) || '');

  function fillOptions(g) {
    const select = el('gemmaSize');
    const keep = select.value;
    if (select.options.length !== g.options.length) {
      select.replaceChildren(...g.options.map(option => {
        const node = document.createElement('option');
        node.value = option.id;
        StudioI18n.bind(node, 'textContent', () => StudioI18n.t('components.gemma_option_label_template', [StudioI18n.remote(option.title), gb(option.download_bytes)]), true);
        return node;
      }));
      select.value = g.options.some(option => option.id === keep) ? keep : g.default;
    }
    const chosen = g.options.find(option => option.id === select.value) || g.options[0];
    const hosts = [...new Set(chosen.sources)];
    text(el('gemmaSources'), () => hosts.length ? StudioI18n.t('components.gemma_sources_line_template', [hosts.join(' → ')]) : '');
  }

  function renderProgress(install) {
    const box = el('gemmaProgress');
    box.hidden = !install.running;
    if (!install.running) return;
    const c = install.current, p = install.progress || {};
    text(el('gemmaCurrent'), () => c ? `[${c.index}/${c.total}] ${StudioI18n.remote(c.title)}` : StudioI18n.t('components.gemma_progress_preparing_status'));
    const bar = el('gemmaBar');
    if (p.name && p.total) {
      bar.max = p.total;bar.value = p.done;
      const eta = p.speed > 1 ? StudioI18n.t('components.download_progress_remaining_time_prefix') + clock((p.total - p.done) / p.speed) : '';
      text(el('gemmaLine'), () => StudioI18n.t('components.line_mb_s', [p.name, fmt(100 * p.done / p.total, 0), gb(p.done), gb(p.total), fmt((p.speed || 0) / 1e6), eta]));
    } else {
      bar.removeAttribute('value');
      text(el('gemmaLine'), () => (install.events || []).map(e => e.text || '').filter(Boolean).slice(-1)[0] || StudioI18n.t('components.line_extracting_configuring'));
    }
  }

  function render() {
    const g = group();
    if (!g || !state) return;
    const install = state.install;
    fillOptions(g);
    renderProgress(install);
    const asking = request && request.purpose === 'model';
    el('gemmaDownload').hidden = !!asking;
    el('gemmaStart').disabled = busy || install.running;
    el('gemmaImport').disabled = busy || (install.running && !asking);
    el('gemmaCancel').hidden = !install.running || !!asking;
    const done = !install.running && !asking && g.satisfied && manual;
    el('gemmaFailure').hidden = !asking && !done;
    if (asking) {
      el('gemmaLocal').open = true;
      const seconds = Math.max(0, Math.round((deadline - performance.now()) / 1000));
      text(el('gemmaFailure'), () => `${StudioI18n.t('components.gemma_failed_sources_title_label')}. ` + StudioI18n.t('components.gemma_failed_sources_help_template', [(request.reasons || []).join('; '), seconds]));
    } else if (done) text(el('gemmaFailure'), () => StudioI18n.t('components.gemma_done_status_text'));
    text(el('gemmaSkip'), () => StudioI18n.t(g.satisfied ? 'components.gemma_close_button_label' : 'components.gemma_skip_button_label'));
    // Only the last run's failures that concern Gemma (its options, a local import, the Ollama runtime) belong in this window;
    // the results of an earlier, unrelated or cancelled run stay in the Components tab.
    const mine = id => id === 'text.runtime' || id.startsWith('text.local.') || g.options.some(option => option.id === id);
    const failed = install.results ? Object.entries(install.results).filter(([id, value]) => value.startsWith('failed') && mine(id)) : [];
    showError(install.running || asking ? '' : install.error || failed.map(([, value]) => value).join('\n'));
  }

  function open() {
    if (dialog.open) return;
    if (document.querySelector('dialog[open]')) return;
    dialog.showModal();
    clearInterval(timer);
    timer = setInterval(poll, 1000);
  }
  function close() {
    if (dialog.open) dialog.close();
    clearInterval(timer);timer = null;manual = false;request = null;
  }

  // The cheap /recovery answer is polled all the time; the full component state only while the window is open, an installation runs
  // or has just ended, or on the first call (`force`).
  async function poll(force) {
    if (polling) return;
    polling = true;
    try {
      const info = await api('/recovery');
      const asked = info.request && info.request.purpose === 'model' && info.request.remaining_seconds > 0 ? info.request : null;
      if (asked && (!request || request.token !== asked.token)) deadline = performance.now() + asked.remaining_seconds * 1000;
      request = asked;
      const changed = info.running !== lastRunning;
      lastRunning = info.running;
      if (!(force === true || changed || info.running || asked || dialog.open || !state)) return;
      state = await api('');
      if (changed && !info.running) document.dispatchEvent(new Event('studio:models-changed'));
      if (asked) {open();render();return;}
      if (!state.install.running && !missing() && !manual) {close();return;}
      if (dialog.open) render();
      else if (missing() && !skipped() && !manual) {resetOptions();open();render();}
    } catch (error) {if (dialog.open) showError(error.message);}
    finally {polling = false;}
  }
  const resetOptions = () => {el('gemmaSize').replaceChildren();};

  el('gemmaStart').onclick = async () => {
    busy = true;showError('');render();
    try {await api('/install', {models: [el('gemmaSize').value]});}
    catch (error) {showError(error.message);}
    busy = false;await poll();
  };
  el('gemmaImport').onclick = async () => {
    const path = el('gemmaFile').value.trim(), projector = el('gemmaProjector').value.trim();
    if (!path) {showError(StudioI18n.t('components.routes_invalid_local_model'));return;}
    busy = true;showError('');
    try {
      if (request && request.purpose === 'model') await api('/recovery-answer', {token: request.token, kind: 'file', value: path, extra: projector});
      else await api('/install', {local_gemma: {path, projector}});
    } catch (error) {showError(error.message);}
    busy = false;await poll();
  };
  el('gemmaCancel').onclick = async () => {el('gemmaCancel').disabled = true;try {await api('/cancel', {});} catch {}el('gemmaCancel').disabled = false;};
  async function skip() {
    if (request && request.purpose === 'model') {
      try {await api('/recovery-answer', {token: request.token, kind: 'skip', value: ''});} catch {}
    } else remember();
    close();
  }
  el('gemmaSkip').onclick = skip;
  dialog.addEventListener('cancel', event => {event.preventDefault();skip();});
  el('gemmaSize').onchange = () => {if (group()) fillOptions(group());};
  for (const [button, input, kind] of [['gemmaBrowseFile', 'gemmaFile', 'model'], ['gemmaBrowseProjector', 'gemmaProjector', 'projector']]) {
    el(button).onclick = async () => {
      el(button).disabled = true;
      try {const result = await api('/pick-file', {kind});if (result.path) el(input).value = result.path;}
      catch (error) {showError(error.message);}
      finally {el(button).disabled = false;}
    };
  }

  // Called by the Components tab: `local` opens the window with the file section expanded even when a Gemma is installed.
  window.showGemmaDialog = async (options = {}) => {
    manual = true;
    try {state = await api('');} catch (error) {showError(error.message);}
    resetOptions();
    el('gemmaLocal').open = !!options.local;
    open();render();
  };
  // Check once after the page loads, then keep watching only while something is missing or being installed.
  setTimeout(() => poll(true), 600);
  document.addEventListener('studio:models-changed', () => setTimeout(() => poll(true), 300));
  setInterval(poll, 2000);
})();
