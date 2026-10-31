'use strict';
(() => {
  // The Components tab: what is installed, and downloading missing models (the programs are installed by Setup).
  const el = id => document.getElementById(id);
  const WORKSPACES = ['image', 'video', 'music', 'text', 'voiceover'];
  let state = null, selected = new Set(), timer = null, busyLocal = false;
  const fmt = (n, digits = 1) => Number(n).toLocaleString(StudioI18n.locale, {maximumFractionDigits: digits});
  const gb = bytes => bytes >= 1e9 ? fmt(bytes / 1e9) + StudioI18n.t("components.memory_gigabytes_unit_suffix") : fmt(bytes / 1e6, 0) + StudioI18n.t("components.memory_megabytes_unit_suffix");
  const clock = seconds => {seconds = Math.max(0, Math.round(seconds));const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');};
  const STATE_TEXT = {installed: StudioI18n.t("components.component_installation_installed_label"), partial: StudioI18n.t("components.state_partial_partially_downloaded"), missing: StudioI18n.t("components.component_installation_missing_label")};
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/components' + route, data === undefined ? {cache: 'no-store'} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error(StudioI18n.t("components.api_request_connect_studio_check_application_running_unavailable"));}
    let value;
    try {value = await response.json();} catch {throw new Error(StudioI18n.t("components.api_request_invalid_installer_response"));}
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || StudioI18n.t("components.api_request_installer_unavailable"));
    return value;
  };
  const make = (tag, className, text) => {const node = document.createElement(tag);if (className) node.className = className;if (text !== undefined) StudioI18n.bind(node, "textContent", () => (typeof text==='function'?text():text), true);return node;};

  function render() {
    const root = el('componentsModules');
    StudioI18n.bind(root, "textContent", () => (''), true);
    if (!state) return;
    const missingSoftware = state.missing_software || [];
    const banner = el('componentsSoftware');
    banner.hidden = !missingSoftware.length;
    StudioI18n.bind(banner, "textContent", () => (''), true);
    if (missingSoftware.length) {
      const titles = state.modules.flatMap(m => m.components).filter(c => missingSoftware.includes(c.id)).map(c => StudioI18n.remote(c.title));
      const fix = make('button', 'secondary', StudioI18n.t("components.missing_software_install_button_label"));
      fix.type = 'button';fix.disabled = state.install.running;fix.onclick = start;
      banner.append(make('p', '', StudioI18n.t("components.software_missing_warning_prefix") + titles.join('; ') + StudioI18n.t("components.software_missing_sections_unavailable_warning_suffix")), fix);
    }
    for (const module of state.modules) {
      const models = module.components.filter(c => c.kind === 'model');
      const software = module.components.filter(c => c.kind === 'software');
      if (!models.length && !software.length) continue;
      const card = make('section', 'components-card');
      card.append(make('h3', '', StudioI18n.remote(module.title)));
      if (module.summary) card.append(make('p', 'small', module.summary));
      for (const row of software) {
        const line = make('div', 'components-row');
        line.append(make('span', 'components-check', row.state === 'installed' ? '✓' : '·'), make('span', 'components-title', StudioI18n.remote(row.title)), make('span', 'small', gb(row.disk_bytes) + ' · ' + STATE_TEXT[row.state] + StudioI18n.t("components.software_card_component_kind_suffix")));
        card.append(line);
      }
      for (const row of models) {
        const line = make('div', 'components-row');
        const box = document.createElement('input');
        box.type = 'checkbox';box.value = row.id;
        box.disabled = row.state === 'installed' || state.install.running;
        box.checked = row.state === 'installed' || selected.has(row.id);
        box.onchange = () => {box.checked ? selected.add(row.id) : selected.delete(row.id);summary();};
        const text = make('span', 'components-title', StudioI18n.remote(row.title));
        StudioI18n.bind(box, "@aria-label", () => (StudioI18n.t("components.model_selection_download_accessibility_prefix") + StudioI18n.remote(row.title)), true);
        const info = make('span', 'small', (row.state === 'installed' ? gb(row.download_bytes) : gb(row.remaining_bytes || row.download_bytes)) + ' · ' + STATE_TEXT[row.state]);
        line.append(box, text, info);
        if (row.enabled === false) StudioI18n.append(info, "textContent", () => (StudioI18n.t("components.model_card_disabled_status_suffix")), true);
        if (row.required_group) StudioI18n.append(info, "textContent", () => (StudioI18n.t("components.gemma_required_badge_suffix")), true);
        if (StudioI18n.remote(row.description) || StudioI18n.remote(row.note) || row.license) {
          const detail = make('span', 'components-detail small', () => [StudioI18n.remote(row.description), StudioI18n.remote(row.note), row.license ? StudioI18n.t("components.model_card_license_information_prefix") + row.license : ''].filter(Boolean).join(' '));
          line.append(detail);
        }
        if (row.removable && row.state !== 'installed' && !row.local_only) {
          const repair = make('button', 'secondary', StudioI18n.t("components.model_card_install_repair_button_label"));
          repair.type = 'button';repair.disabled = state.install.running || busyLocal;
          repair.onclick = () => repairModel(row.id);
          const actions = make('div', 'components-model-actions');actions.append(repair);line.append(actions);
        }
        if (row.removable && row.state !== 'missing') {
          const actions = make('div', 'components-model-actions');
          const toggle = make('button', 'secondary', row.enabled === false ? StudioI18n.t("components.model_card_enable_button_label") : StudioI18n.t("components.model_card_disable_button_label"));
          toggle.type = 'button';StudioI18n.bind(toggle, "@aria-label", () => (toggle.textContent + ': ' + StudioI18n.remote(row.title)), true);
          toggle.disabled = state.install.running || busyLocal;
          toggle.onclick = async () => {
            toggle.disabled = true;
            try {
              const result = await api('/model-toggle', {id:row.id, enabled:row.enabled === false});
              StudioI18n.bind(el('componentsModelStatus'), "textContent", () => (result.repairing ? StudioI18n.t("components.model_repairing_dependencies") : row.enabled === false ? StudioI18n.t("components.model_enable_success_status") : StudioI18n.t("components.model_disabled_files_preserved")), true);
              document.dispatchEvent(new Event('studio:models-changed'));await refresh();
            } catch (error) {StudioI18n.bind(el('componentsError'), "textContent", () => (StudioI18n.remote(error.message)), true);toggle.disabled = false;}
          };
          const remove = make('button', 'secondary', StudioI18n.t("components.model_card_delete_button_label"));
          remove.type = 'button';StudioI18n.bind(remove, "@aria-label", () => (StudioI18n.t("components.model_card_delete_accessibility_prefix") + StudioI18n.remote(row.title)), true);
          remove.disabled = state.install.running || busyLocal;
          remove.onclick = () => prepareRemoval(row);
          actions.append(toggle, remove);line.append(actions);
        }
        card.append(line);
      }
      if (module.id === 'text') {
        for (const group of state.required || []) {
          const box = make('div', 'components-required');
          box.append(make('p', group.satisfied ? 'small' : 'small components-required-missing', () => group.satisfied
            ? StudioI18n.t("components.gemma_card_installed_status_template", [group.installed.join(', ')]) : StudioI18n.t("components.gemma_card_missing_status_text")));
          const actions = make('div', 'components-model-actions');
          if (!group.satisfied) {
            const install = make('button', '', StudioI18n.t("components.gemma_card_open_button_label"));
            install.type = 'button';install.onclick = () => window.showGemmaDialog();actions.append(install);
          }
          const local = make('button', 'secondary', StudioI18n.t("components.gemma_card_local_button_label"));
          local.type = 'button';local.onclick = () => window.showGemmaDialog({local: true});actions.append(local);
          box.append(actions);card.append(box);
        }
        const form = make('div', 'components-text');
        const input = document.createElement('input');
        input.id = 'componentsTextTag';StudioI18n.bind(input, "placeholder", () => (StudioI18n.t("components.input_another_ollama_model_e_g_qwen3")), true);input.autocomplete = 'off';input.spellcheck = false;
        StudioI18n.bind(input, "@aria-label", () => (StudioI18n.t("components.input_model_name_ollama_registry")), true);
        form.append(input, make('span', 'small', StudioI18n.t("components.custom_ollama_model_source_hint")));
        card.append(form);
      }
      root.append(card);
    }
    summary();
  }

  let removal = null, removalTimer = null;
  const dialog = el('componentRemoveDialog');
  function updateRemoval() {
    if (!removal) return;
    const remaining = Math.max(0, Math.ceil((removal.ready - performance.now()) / 1000));
    StudioI18n.bind(el('componentRemoveWait'), "textContent", () => (remaining ? StudioI18n.t("components.component_remove_wait_confirmation_available_s", [remaining]) : StudioI18n.t("components.component_remove_wait_type_delete_everything")), true);
    el('componentRemoveInput').disabled = remaining > 0;
    el('componentRemoveConfirm').disabled = remaining > 0 || el('componentRemoveInput').value !== 'REMOVE';
  }
  async function prepareRemoval(row) {
    try {
      const plan = await api('/model-remove-plan', {id:row.id});
      removal = {...plan, ready:performance.now() + plan.wait_seconds * 1000};
      StudioI18n.bind(el('componentRemoveName'), "textContent", () => (StudioI18n.remote(row.title)), true);
      StudioI18n.bind(el('componentRemoveSummary'), "textContent", () => (StudioI18n.t("components.component_remove_summary_files_size_deletion_irreversible", [StudioI18n.remote(plan.note), plan.files, gb(plan.bytes)])), true);
      StudioI18n.bind(el('componentRemoveFiles'), "textContent", () => (plan.examples.join('\n')), false);
      el('componentRemoveInput').value = '';StudioI18n.bind(el('componentRemoveError'), "textContent", () => (''), true);
      updateRemoval();dialog.showModal();
      clearInterval(removalTimer);removalTimer = setInterval(updateRemoval, 200);
    } catch (error) {StudioI18n.bind(el('componentsError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
  }
  el('componentRemoveInput').addEventListener('input', updateRemoval);
  el('componentRemoveCancel').onclick = () => dialog.close();
  dialog.addEventListener('close', () => {clearInterval(removalTimer);removalTimer = null;removal = null;});
  el('componentRemoveConfirm').onclick = async () => {
    if (!removal) return;
    clearInterval(removalTimer);el('componentRemoveConfirm').disabled = true;el('componentRemoveCancel').disabled = true;
    StudioI18n.bind(el('componentRemoveWait'), "textContent", () => (StudioI18n.t("components.component_remove_wait_deleting_model_own_cache")), true);
    try {
      const result = await api('/model-remove', {token:removal.token, confirmation:el('componentRemoveInput').value});
      dialog.close();StudioI18n.bind(el('componentsModelStatus'), "textContent", () => (StudioI18n.t("components.model_deleted_freed_shared_dependencies_preserved", [gb(result.bytes)])), true);
      document.dispatchEvent(new Event('studio:models-changed'));await refresh();
    } catch (error) {StudioI18n.bind(el('componentRemoveError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
    finally {el('componentRemoveCancel').disabled = false;}
  };

  function summary() {
    if (!state) return;
    const rows = state.modules.flatMap(m => m.components).filter(c => selected.has(c.id) && c.state !== 'installed');
    const need = rows.reduce((total, c) => total + (c.remaining_bytes || c.download_bytes), 0);
    const tag = el('componentsTextTag') ? el('componentsTextTag').value.trim() : '';
    StudioI18n.bind(el('componentsTotal'), "textContent", () => (rows.length || tag
      ? StudioI18n.t("components.total_selected_about_download_free_disk_space", [rows.length + (tag ? 1 : 0), gb(need), tag ? StudioI18n.t("components.download_summary_custom_model_suffix") + tag : '', state.free_bytes ? gb(state.free_bytes) : StudioI18n.t("components.metadata_unknown_value_label")])
      : StudioI18n.t("components.total_select_models_download_installed_checked_cannot")), true);
    el('componentsStart').disabled = busyLocal || state.install.running || !(rows.length || tag);
  }

  function renderProgress() {
    const install = state ? state.install : null;
    const box = el('componentsProgress');
    const show = !!install && (install.running || install.results || StudioI18n.remote(install.error));
    box.hidden = !show;
    el('componentsCancel').hidden = !(install && install.running);
    if (!show) return;
    const title = el('componentsCurrent');
    const bar = el('componentsBar'), line = el('componentsFile'), log = el('componentsLog');
    if (install.running) {
      const c = install.current;
      StudioI18n.bind(title, "textContent", () => (c ? `[${c.index}/${c.total}] ${StudioI18n.remote(c.title)}` : StudioI18n.t("components.installation_progress_preparing_status")), true);
      const p = install.progress || {};
      if (p.name && p.total) {
        bar.max = p.total;bar.value = p.done;
        const eta = p.speed > 1 ? StudioI18n.t("components.download_progress_remaining_time_prefix") + clock((p.total - p.done) / p.speed) : '';
        StudioI18n.bind(line, "textContent", () => (StudioI18n.t("components.line_mb_s", [p.name, fmt(100 * p.done / p.total, 0), gb(p.done), gb(p.total), fmt((p.speed || 0) / 1e6), eta])), true);
      } else {bar.removeAttribute('value');StudioI18n.bind(line, "textContent", () => (StudioI18n.t("components.line_extracting_configuring")), true);}
    } else {
      bar.max = 1;bar.value = 1;
      const failed = install.results ? Object.entries(install.results).filter(([, v]) => v.startsWith('failed')) : [];
      StudioI18n.bind(title, "textContent", () => (StudioI18n.remote(install.error) ? StudioI18n.t("components.generation_stopped_status_label") : failed.length ? StudioI18n.t("components.progress_finished_errors") : StudioI18n.t("components.generation_completed_status_label")), true);
      StudioI18n.bind(line, "textContent", () => (StudioI18n.remote(install.error) || failed.map(([k, v]) => `${k}: ${v}`).join('\n') || StudioI18n.t("components.line_all_selected_components_installed_no_restart")), true);
    }
    StudioI18n.bind(log, "textContent", () => ((install.events || []).map(e => e.type === 'component_done' ? '✓ ' + e.id : e.type === 'component_failed' ? '✗ ' + e.id + ': ' + StudioI18n.remote(e.error) : e.type === 'download_start' ? StudioI18n.t("components.installation_log_download_started_prefix") + e.name : e.text || '').filter(Boolean).slice(-6).join('\n')), true);
  }

  async function refresh() {
    try {
      const wasInstalling = state?.install.running;
      state = await api('');
      if (wasInstalling && !state.install.running) document.dispatchEvent(new Event('studio:models-changed'));
      StudioI18n.bind(el('componentsError'), "textContent", () => (''), true);
      if (!state.install.running) for (const id of [...selected]) {
        const row = state.modules.flatMap(m => m.components).find(c => c.id === id);
        if (!row || row.state === 'installed') selected.delete(id);
      }
      if (!el('componentsWorkspace').dataset.rendered || !state.install.running || !el('componentsModules').children.length) {render();el('componentsWorkspace').dataset.rendered = '1';}
      renderProgress();summary();
      if (state.install.running && !timer) timer = setInterval(refresh, 1000);
      if (!state.install.running && timer) {clearInterval(timer);timer = null;render();}
    } catch (error) {StudioI18n.bind(el('componentsError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
  }

  async function start() {
    busyLocal = true;el('componentsStart').disabled = true;
    try {
      const tag = el('componentsTextTag') ? el('componentsTextTag').value.trim() : '';
      await api('/install', {models: [...selected], text_models: tag ? [tag] : []});
      selected.clear();if (el('componentsTextTag')) el('componentsTextTag').value = '';
      StudioI18n.bind(el('componentsError'), "textContent", () => (''), true);
    } catch (error) {StudioI18n.bind(el('componentsError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
    busyLocal = false;await refresh();
  }

  function show() {
    for (const name of WORKSPACES) {el(name + 'Workspace').hidden = true;el(name + 'Tab').setAttribute('aria-selected', 'false');}
    el('componentsWorkspace').hidden = false;el('componentsTab').setAttribute('aria-selected', 'true');
    document.body.dataset.studioModule = 'components';
    refresh();
  }
  function hide() {
    el('componentsWorkspace').hidden = true;el('componentsTab').setAttribute('aria-selected', 'false');
    if (timer) {clearInterval(timer);timer = null;}
  }
  el('componentsTab').onclick = show;
  for (const name of WORKSPACES) el(name + 'Tab').addEventListener('click', hide);
  el('componentsStart').onclick = start;
  el('componentsCancel').onclick = async () => {el('componentsCancel').disabled = true;try {await api('/cancel', {});} catch {}el('componentsCancel').disabled = false;};
  el('componentsRefresh').onclick = refresh;
  document.addEventListener('input', event => {if (event.target && event.target.id === 'componentsTextTag') summary();});
  let recoveryRequest = null, recoveryDeadline = 0, recoveryPolling = false, lastRunning = false;
  const recoveryDialog = el('componentRecoveryDialog');
  function recoveryTick() {
    if (!recoveryRequest) return;
    const seconds = Math.max(0, Math.ceil((recoveryDeadline - performance.now()) / 1000));
    StudioI18n.bind(el('componentRecoveryWait'), "textContent", () => (StudioI18n.t("components.component_recovery_wait_s_remaining_installer_skip_file", [seconds])), true);
    if (!seconds) {recoveryDialog.close();recoveryRequest = null;}
  }
  async function recoveryPoll() {
    if (recoveryPolling) return;
    recoveryPolling = true;
    try {
      const info = await api('/recovery');
      if (lastRunning && !info.running) {document.dispatchEvent(new Event('studio:models-changed'));if (!el('componentsWorkspace').hidden) refresh();}
      lastRunning = info.running;
      const request = info.request;
      // A question about a model (every mirror failed) is asked in the Gemma window (gemma-gate.js), which also takes the file path.
      if (!request || request.remaining_seconds <= 0 || request.purpose === 'model') {if (recoveryDialog.open) recoveryDialog.close();recoveryRequest = null;return;}
      if (request.token !== recoveryRequest?.token) {
        if (document.querySelector('dialog[open]') && !recoveryDialog.open) return;
        recoveryRequest = request;recoveryDeadline = performance.now() + request.remaining_seconds * 1000;
        StudioI18n.bind(el('componentRecoveryName'), "textContent", () => (`${StudioI18n.remote(request.title)}: ${request.name} (${gb(request.bytes)})`), false);
        el('componentRecoveryUrl').value = '';el('componentRecoveryPath').value = '';StudioI18n.bind(el('componentRecoveryError'), "textContent", () => (''), true);
        recoveryDialog.showModal();recoveryTick();
      }
    } catch {} finally {recoveryPolling = false;}
  }
  async function recoveryAnswer(kind) {
    if (!recoveryRequest) return;
    const token = recoveryRequest.token;
    try {
      await api('/recovery-answer', {token, kind, value:kind === 'url' ? el('componentRecoveryUrl').value : kind === 'file' ? el('componentRecoveryPath').value : ''});
      if (recoveryRequest?.token === token) {recoveryDialog.close();recoveryRequest = null;}
    } catch (error) {StudioI18n.bind(el('componentRecoveryError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
  }
  el('componentRecoveryDownload').onclick = () => recoveryAnswer('url');
  el('componentRecoveryImport').onclick = () => recoveryAnswer('file');
  el('componentRecoverySkip').onclick = () => recoveryAnswer('skip');
  recoveryDialog.addEventListener('cancel', event => {event.preventDefault();recoveryAnswer('skip');});
  el('componentRecoveryBrowse').onclick = async () => {
    if (!recoveryRequest) return;
    const token = recoveryRequest.token;
    el('componentRecoveryBrowse').disabled = true;
    try {
      const result = await api('/recovery-file', {token});
      if (result.path && recoveryRequest?.token === token) {el('componentRecoveryPath').value = result.path;await recoveryAnswer('file');}
    } catch (error) {StudioI18n.bind(el('componentRecoveryError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
    finally {el('componentRecoveryBrowse').disabled = false;}
  };
  async function repairModel(id) {
    try {await api('/model-toggle', {id, enabled:true});await refresh();await recoveryPoll();}
    catch (error) {StudioI18n.bind(el('componentsError'), "textContent", () => (StudioI18n.remote(error.message)), true);}
  }
  const componentIds = {'image:qwen-edit-2511':'image.model.qwen-edit-2511','image:qwen-image-21-uc-fp8':'image.model.qwen-image-21-uc','image:z-image-turbo-int8':'image.model.z-image-turbo','image:flux3-klein-int8':'image.model.flux3-klein',
    'video:freevideo-h3-int8':'video.model.h3','video:wan22-ti2v-5b':'video.model.wan-ti2v-5b','video:wan22-s2v-14b':'video.model.wan-s2v-14b','video:ltx25-distilled':'video.model.ltx25','video:wan-animate2':'video.model.wan-animate2','video:scail2':'video.model.scail2',
    'music:ace-step-15-turbo':'music.model.ace-step','music:minimax-music3-int8':'music.model.minimax-music3','music:stable-audio-3-medium':'music.model.stable-audio-3'};
  window.repairStudioModel = (module, id) => {const component = componentIds[module + ':' + id];if (component) repairModel(component);};
  el('musicEngine').addEventListener('change', () => {
    const id=componentIds['music:'+el('musicEngine').value];
    const component=state?.modules.flatMap(module=>module.components).find(row=>row.id===id);
    // Repair follows component state, never a translated option label.
    if(component&&(component.state!=='installed'||component.enabled===false)) window.repairStudioModel('music',el('musicEngine').value);
  });
  setInterval(recoveryPoll, 1000);setInterval(recoveryTick, 200);recoveryPoll();
  // Other tabs link here: window.showComponents()
  window.showComponents = show;
})();
