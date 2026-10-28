/* Offline completion feedback and shared presentation metadata. */
(() => {
  'use strict';
  const releases = {
    'qwen-edit-2511': ['23.12.2025', StudioI18n.t("settings.studio_ui_releases_model_release"), 'https://github.com/QwenLM/Qwen-Image'],
    'qwen-image-21-uc-fp8': ['23.09.2026', StudioI18n.t("settings.studio_ui_releases_fp8_publication"), 'https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized/commits/main'],
    'flux3-klein-int8': ['17.10.2026', StudioI18n.t("settings.studio_ui_releases_comfy_package_update"), 'https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy/commits/main'],
    'z-image-turbo-int8': ['23.09.2026', StudioI18n.t("settings.studio_ui_releases_comfy_package_update"), 'https://huggingface.co/Comfy-Org/Z-Image-Turbo/commits/main'],
    'stable-audio-3-medium': ['23.09.2026', StudioI18n.t("settings.studio_ui_releases_comfy_package_publication"), 'https://huggingface.co/Comfy-Org/stable-audio-3/commits/main'],
    'minimax-music3-int8': ['13.08.2026', StudioI18n.t("settings.studio_ui_releases_model_release"), 'https://blog.comfy.org/p/minimax-music-3-state-of-the-art'],
    'ace-step-15-turbo': ['23.01.2026', StudioI18n.t("settings.studio_ui_releases_weights_publication"), 'https://huggingface.co/ACE-Step/Ace-Step1.5/commits/main'],
    'freevideo-h3-int8': ['13.09.2026', StudioI18n.t("settings.studio_ui_releases_vdn_h3_edge_weights_publication"), 'https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/commits/main'],
    'wan22-ti2v-5b': ['28.07.2025', StudioI18n.t("settings.studio_ui_releases_model_release"), 'https://github.com/Wan-Video/Wan2.2'],
    'wan22-s2v-14b': ['26.08.2025', StudioI18n.t("settings.studio_ui_releases_model_release"), 'https://github.com/Wan-Video/Wan2.2'],
    'ltx25-distilled': ['11.08.2026', StudioI18n.t("settings.studio_ui_releases_model_release"), 'https://github.com/Lightricks/LTX-2/releases/tag/v1.2.0'],
    'text-qwen38-27b': ['14.08.2026', StudioI18n.t("settings.studio_ui_releases_base_model_release"), 'https://github.com/QwenLM/Qwen3.8#news'],
    'text-qwen36-35b-a3b': ['16.04.2026', StudioI18n.t("settings.studio_ui_releases_base_model_release"), 'https://github.com/QwenLM/Qwen3.8#news'],
    'text-gemma4-26b-a4b': ['31.03.2026', StudioI18n.t("settings.studio_ui_releases_base_model_release"), 'https://ai.google.dev/gemma/docs/releases#march_31_2026']
  };
  // Keep the existing action buttons so module navigation and log handlers retain their ownership.
  const settingsToggle = document.getElementById('studioSettingsToggle');
  const settingsMenu = document.getElementById('studioSettingsMenu');
  const settingsHost = settingsToggle.closest('.studio-settings');
  const settingsItems = [...settingsMenu.querySelectorAll('button')];
  function closeSettings(restoreFocus = false) {
    settingsMenu.hidden = true;
    settingsToggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) settingsToggle.focus();
  }
  function openSettings() {
    settingsMenu.hidden = false;
    settingsToggle.setAttribute('aria-expanded', 'true');
  }
  settingsToggle.addEventListener('click', () => {
    if (settingsMenu.hidden) openSettings(); else closeSettings();
  });
  settingsMenu.addEventListener('click', event => {
    if (event.target.closest('button')) closeSettings();
  });
  document.addEventListener('click', event => {
    if (!settingsHost.contains(event.target)) closeSettings();
  });
  settingsHost.addEventListener('focusout', event => {
    if (!settingsHost.contains(event.relatedTarget)) closeSettings();
  });
  settingsHost.addEventListener('keydown', event => {
    // Native selects own arrow/Home/End keys; menu navigation must not steal language selection.
    if (event.target.tagName === 'SELECT' && event.key !== 'Escape') return;
    if (event.key === 'Escape' && !settingsMenu.hidden) {
      event.preventDefault();event.stopPropagation();closeSettings(true);
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();openSettings();
      const index = settingsItems.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? settingsItems.length - 1 :
        index < 0 ? (event.key === 'ArrowUp' ? settingsItems.length - 1 : 0) :
        (index + (event.key === 'ArrowUp' ? -1 : 1) + settingsItems.length) % settingsItems.length;
      settingsItems[next].focus();
    }
  });
  // Cache each module separately: a late Image preflight must not overwrite the active tab's header.
  const headers = {
    image: {model: '', description: document.getElementById('subtitle').textContent, status: document.getElementById('preflight').textContent, tone: 'muted'},
    video: {model: 'Video', description: StudioI18n.t("settings.studio_ui_headers_description_local_video_generation"), status: StudioI18n.t("settings.studio_ui_headers_checking_video_models"), tone: 'muted'},
    music: {model: 'Music', description: StudioI18n.t("settings.studio_ui_headers_description_local_music_song_generation"), status: StudioI18n.t("settings.studio_ui_headers_checking_music_models"), tone: 'muted'},
    text: {model: 'Text', description: StudioI18n.t("settings.studio_ui_headers_description_local_text_assistant"), status: StudioI18n.t("settings.studio_ui_headers_checking_text_models"), tone: 'muted'}
  };
  function renderHeader() {
    const state = headers[document.body.dataset.studioModule || 'image'];
    if (!state) return;
    // The compact top bar already shows the section in the rail, so the context starts directly with the model name.
    StudioI18n.bind(document.getElementById('engineEyebrow'), "textContent", () => (state.model || 'Local AI'), true);
    StudioI18n.bind(document.getElementById('subtitle'), "textContent", () => (StudioI18n.remote(state.description)), true);
    const readiness = document.getElementById('preflight');
    StudioI18n.bind(readiness, "textContent", () => (state.status), true);
    readiness.className = 'status ' + state.tone;
  }
  function setHeader(module, state) {
    if (!headers[module]) return;
    Object.assign(headers[module], state);
    if ((document.body.dataset.studioModule || 'image') === module) renderHeader();
  }
  new MutationObserver(renderHeader).observe(document.body, {attributes:true, attributeFilter:['data-studio-module']});
  const seen = new Set();
  // Melodies, volume and the audio unlock live in sounds.js; the user picks them in the gear menu under «Звуки».
  function sound(error) {
    try { window.StudioSounds?.play(error ? 'error' : 'success'); } catch {}
  }
  async function openFolder(path) {
    const response = await fetch('/api/open-output-dir', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path})});
    const result = await response.json();
    if (!response.ok) throw new Error(StudioI18n.remote(result.error) || StudioI18n.t("settings.open_folder_failed"));
    return result;
  }
  function notify({module, id, error = false, message = '', folder = '', onLogs}) {
    const key = id && `${module}:${id}:${error ? 'error' : 'done'}`;
    if (key && seen.has(key)) return;
    if (key) seen.add(key);
    let host = document.getElementById('studioNotifications');
    if (!host) {
      host = document.createElement('section'); host.id = 'studioNotifications';
      StudioI18n.bind(host, "@aria-label", () => (StudioI18n.t("settings.host_studio_notifications")), true); document.body.append(host);
    }
    const card = document.createElement('div'); card.className = 'studio-notice' + (error ? ' studio-notice-error' : '');
    if (id) card.dataset.jobKey = module + ':' + id;
    card.setAttribute('role', error ? 'alert' : 'status');
    const title = document.createElement('strong');
    StudioI18n.bind(title, "textContent", () => (module + (error ? StudioI18n.t("settings.generation_notification_error_title_suffix") : StudioI18n.t("settings.notify_completed_successfully"))), true);
    const body = document.createElement('p'); StudioI18n.bind(body, "textContent", () => (message || (error ? StudioI18n.t("settings.notify_details_available_log") : StudioI18n.t("settings.notify_result_saved"))), true);
    card.append(title, body);
    const action = (label, callback) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; StudioI18n.bind(button, "textContent", () => (label), true);
      button.onclick = async () => {
        button.disabled = true;
        try { await callback(); } catch (e) { StudioI18n.bind(body, "textContent", () => (StudioI18n.remote(e.message)), true); } finally { button.disabled = false; }
      };
      card.append(button);
    };
    if (folder) action(StudioI18n.t("settings.notify_open_folder"), () => openFolder(folder));
    if (onLogs) action(StudioI18n.t("settings.notify_show_log"), onLogs);
    action(StudioI18n.t("settings.dialog_close_button_label"), () => card.remove());
    host.append(card);
    sound(error);
  }
  function generationInfo(record) {
    const details = document.createElement('details'); details.className = 'generation-info';
    const summary = document.createElement('summary');
    StudioI18n.bind(summary, "textContent", () => (StudioI18n.t("settings.summary_generation_data") + record.type + ' · ' + record.id.slice(0, 12) + '…'), true);
    details.append(summary);
    const list = document.createElement('dl');
    const date = () => record.started_at ? new Date(record.started_at).toLocaleString(StudioI18n.locale) : StudioI18n.t("settings.metadata_unknown_value_label");
    const duration = () => record.duration_sec === null ? StudioI18n.t("settings.generation_info_not_finished_yet_duration_unknown") : record.duration_sec.toLocaleString(StudioI18n.locale) + StudioI18n.t("settings.duration_seconds_unit_suffix");
    for (const [label, read] of [[()=>StudioI18n.t("settings.generation_identity_type_field_label"), ()=>record.type], [()=>'ID', ()=>record.id], [()=>StudioI18n.t("settings.generation_identity_started_field_label"), date], [()=>StudioI18n.t("settings.generation_identity_duration_field_label"), duration]]) {
      const key = document.createElement('dt'), text = document.createElement('dd');
      StudioI18n.bind(key, "textContent", label, false); StudioI18n.bind(text, "textContent", read, false); list.append(key, text);
    }
    const copy = document.createElement('button'); copy.type='button'; copy.className='secondary slim'; StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("settings.generation_identity_copy_button_label")), true);
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(JSON.stringify(record, null, 2)); StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("settings.clipboard_copy_success_feedback")), true); }
      catch { StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("settings.clipboard_copy_failed_feedback")), true); }
    };
    details.append(list, copy);
    return details;
  }
  function deleteGenerationButton(module, id, afterDelete, beforeDelete = () => {}) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'secondary slim'; StudioI18n.bind(button, "textContent", () => (StudioI18n.t("settings.generation_delete_button_label")), true);
    StudioI18n.bind(button, "title", () => (StudioI18n.t("settings.button_delete_generation_output_metadata_separate_task")), true);
    const reportFailure = message => notify({module, error:true, message:StudioI18n.t("settings.delete_generation_button_complete_deletion_failed") + message});
    button.onclick = async () => {
      if (button.disabled || !window.confirm(StudioI18n.t("settings.button_permanently_delete_generation_history_entry_output"))) return;
      button.disabled = true;
      try {
        // Release range requests before deleting a playing file: Windows otherwise keeps its handle open.
        await beforeDelete();
        const url = module === 'Image' ? '/api/history/delete' : '/api/' + module.toLowerCase() + '/delete';
        const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})});
        const value = await response.json();
        if (!response.ok || !value?.deleted) {reportFailure(StudioI18n.remote(value?.error) || StudioI18n.t("settings.button_deletion_not_confirmed"));return;}
        for (const card of document.querySelectorAll('.studio-notice')) if (card.dataset.jobKey === module + ':' + id) card.remove();
        await afterDelete();
      } catch (error) {
        reportFailure(StudioI18n.remote(error.message));
      } finally { button.disabled = false; }
    };
    return button;
  }
  function showLogs(module, id, load) {
    document.getElementById(module.toLowerCase() + 'Tab')?.click();
    const output = document.getElementById(module.toLowerCase() + 'Logs');
    if (output) {
      output.closest('details').open = true;
      output.scrollIntoView({behavior:'smooth', block:'center'});
    }
    return load(id);
  }
  for (const option of document.querySelectorAll('#engineSelect option, #videoEngine option, #musicEngine option')) {
    const data=releases[option.value];
    if (data) { StudioI18n.append(option, "textContent", () => (` · ${data[1]}: ${data[0]}`), true); StudioI18n.bind(option, "title", () => (data[2]), true); }
  }
  // ---- while any module generates, everything that would start or disturb a job is inert ----------------------------------
  // Tabs, engine and mode choices, settings and typing in prompt fields stay free; the buttons below (starting a generation or an export,
  // changing the Text chat or the save folder under a running job, installing components) are dimmed and ignored, and so are the forms
  // (Enter in a field) and the chat's Enter-to-send. The bridge knows who is working (/api/busy covers Image, Video, Music and Text), so
  // this also holds after a reload and on other tabs. The banner's button stops whatever runs, wherever it was started.
  const BLOCKED = '#generate, #variant, #videoGenerate, #musicGenerate, #textSend, #textRegenerate, #textNewChat, #textRename, #textDelete, #textUnload, #textSaveSettings, #textChats button, #videoMixStart, #videoExportStart, #saveSaveDir, #chooseSaveDir, #useInternalResults, #videoChooseSaveDir, #videoSaveSettings, #musicChooseSaveDir, #musicSaveSettings, #componentsStart, #componentsModules .components-model-actions button, #componentRemoveConfirm';
  const START_FORMS = '#form, #videoForm, #musicForm, #textForm';
  const ALLOWED = '[data-allow-busy], #studioNotifications *, #studioBusy *';
  let locked = false, lockKind = '', banner = null, banner_text = null, banner_stop = null, polling = false;
  const isBlocked = target => {
    if (!locked || !(target instanceof Element)) return false;
    const control = target.closest(BLOCKED);
    return !!control && !control.closest(ALLOWED);
  };
  const swallow = event => {if (isBlocked(event.target)) {event.preventDefault();event.stopImmediatePropagation();}};
  for (const type of ['mousedown', 'click', 'dblclick']) document.addEventListener(type, swallow, true);
  document.addEventListener('submit', event => {if (locked && event.target instanceof Element && event.target.matches(START_FORMS)) {event.preventDefault();event.stopImmediatePropagation();}}, true);
  document.addEventListener('keydown', event => {   // Enter sends the chat message: that is starting a generation too
    if (locked && event.key === 'Enter' && !event.shiftKey && event.target instanceof Element && event.target.id === 'textInput') {event.preventDefault();event.stopImmediatePropagation();}
  }, true);
  function ensureBanner() {
    if (banner) return;
    banner = document.createElement('div');banner.id = 'studioBusy';banner.setAttribute('role', 'status');banner.hidden = true;
    banner_text = document.createElement('span');
    banner_stop = document.createElement('button');banner_stop.type = 'button';banner_stop.id = 'studioBusyStop';banner_stop.dataset.allowBusy = '1';StudioI18n.bind(banner_stop, "textContent", () => (StudioI18n.t("settings.banner_stop_generation")), true);
    banner_stop.onclick = async () => {
      banner_stop.disabled = true;StudioI18n.bind(banner_stop, "textContent", () => (StudioI18n.t("settings.banner_stop_stopping")), true);
      try {await fetch('/api/stop-generation', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});} catch {}
      for (const delay of [300, 1200, 3000]) setTimeout(poll, delay);
    };
    banner.append(banner_text, banner_stop);document.body.prepend(banner);
  }
  function setLocked(busy, labels, kind = '') {
    ensureBanner();
    if (busy !== locked || kind !== lockKind) {
      const was = locked, wasKind = lockKind;
      locked = busy;lockKind = busy ? kind : '';document.body.classList.toggle('studio-busy', busy);banner.hidden = !busy;
      if (!busy) {banner_stop.disabled = false;StudioI18n.bind(banner_stop, "textContent", () => (StudioI18n.t("settings.banner_stop_generation")), true);}
      if (busy !== was) window.dispatchEvent(new CustomEvent(busy ? 'studio:busy' : 'studio:idle', {detail: {kind: busy ? kind : wasKind, labels}}));
    }
    if (busy) StudioI18n.bind(banner_text, "textContent", () => (kind === 'voice'
      ? StudioI18n.t("settings.banner_gemma_working_voice_input_loading_editing")
      : StudioI18n.t("settings.banner_generation_running_tabs_model_selection_text", [labels.map(label=>StudioI18n.remote(label)).join(', ')])), true);
  }
  let again = false;
  async function poll() {
    if (polling) {again = true;return;}  // a look is already under way: look once more right after it
    polling = true;
    try {
      const response = await fetch('/api/busy', {cache: 'no-store'});
      if (response.ok) {const state = await response.json();setLocked(!!state.busy, state.labels || [], state.kind || '');}
    } catch {} finally {polling = false;if (again) {again = false;poll();}}
  }
  // Something may have just started (or ended): look again shortly after any click or submit.
  for (const type of ['click', 'submit']) document.addEventListener(type, () => {for (const delay of [250, 1500]) setTimeout(poll, delay);});
  document.addEventListener('visibilitychange', () => {if (!document.hidden) poll();});
  setInterval(() => {if (!document.hidden) poll();}, 1200);
  poll();
  window.StudioUI = {
    notify, openFolder, showLogs, setHeader, deleteGenerationButton, generationInfo, isBusy: () => locked, refreshBusy: poll,
    parentFolder: path => { const text=String(path || ''); const index=Math.max(text.lastIndexOf('\\'),text.lastIndexOf('/')); return index<0?'':text.slice(0, index<=2?index+1:index); },
    // Engine names come from the server in Russian source form (e.g. "Wan 2.2 S2V-14B · речь → видео"); translate
    // them here, otherwise every selector that uses this label stays Russian after switching to English.
    modelLabel: (id, name) => { const label = window.StudioI18n ? StudioI18n.remote(name) : name; return releases[id] ? `${label} · ${releases[id][1]}: ${releases[id][0]}` : label; },
    modelSource: id => releases[id]?.[2] || ''
  };
})();
