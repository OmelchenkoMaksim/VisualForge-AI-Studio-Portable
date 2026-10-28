/* Voiceover owns its draft and job state; the other sections keep their own controls. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const draftKey = 'visualforge.voiceover.draft.v1';
  const fields = ['Language', 'Format', 'Seed', 'Dictionary', 'Title'];
  const others = ['imageTab', 'videoTab', 'musicTab', 'textTab', 'componentsTab'];
  const workspaces = ['imageWorkspace', 'videoWorkspace', 'musicWorkspace', 'textWorkspace', 'componentsWorkspace'];
  let active = null, selected = '', playing = '', available = false, timer = null, polling = false, engine = null, engines = [], savedEngine = '', prepared = null, voices = [];

  async function api(route, data) {
    const response = await fetch('/api/voiceover/' + route, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(StudioI18n.remote(result.error) || StudioI18n.t("voiceover.api_service_unavailable_error"));
    return result;
  }
  function say(fn) { StudioI18n.bind(el('voiceoverStatus'), 'textContent', fn, true); }
  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error(StudioI18n.t("voiceover.file_read_failed_error")));
      reader.readAsDataURL(file);
    });
  }
  function duration(seconds) {
    const total = Math.max(0, Math.round(seconds));
    if (total >= 3600) return StudioI18n.t("voiceover.duration_hours_minutes_template", [Math.floor(total / 3600), Math.floor(total % 3600 / 60)]);
    if (total >= 60) return StudioI18n.t("voiceover.duration_minutes_seconds_template", [Math.floor(total / 60), total % 60]);
    return StudioI18n.t("voiceover.duration_seconds_template", [total]);
  }
  function draft() {
    const value = {};
    for (const key of fields) value[key] = el('voiceover' + key).value;
    value.Engine = el('voiceoverEngine').value || savedEngine;
    try { localStorage.setItem(draftKey, JSON.stringify(value)); } catch {}
  }
  function restore() {
    try {
      const value = JSON.parse(localStorage.getItem(draftKey) || '{}');
      for (const key of fields) if (typeof value[key] === 'string') el('voiceover' + key).value = value[key];
      savedEngine = typeof value.Engine === 'string' ? value.Engine : '';  // the engine list arrives from the server later
    } catch {}
  }
  function chooseEngine() {
    engine = engines.find(item => item.id === el('voiceoverEngine').value) || engines[0] || null;
    available = !!engine && engine.available;
    if (!engine) return;
    StudioI18n.bind(el('voiceoverAvailability'), 'textContent', () => available ? StudioI18n.t("voiceover.engine_license_line_template", [engine.name, engine.license]) : StudioI18n.remote(engine.reason), true);
    StudioI18n.bind(el('voiceoverEngineInfo'), 'textContent', () => StudioI18n.remote(engine.requirements), true);
    header(); buttons();
  }
  function header() {
    StudioUI.setHeader('voiceover', {model: engine?.name || 'Voiceover',
      description: StudioI18n.t("voiceover.header_description_voiceover_label"),
      tone: engine ? (available ? 'good' : 'bad') : 'muted',
      status: engine ? (available ? StudioI18n.t("voiceover.header_status_ready_label") : StudioI18n.remote(engine.reason) || StudioI18n.t("voiceover.header_status_unavailable_label")) : StudioI18n.t("settings.studio_ui_headers_checking_voiceover_models")});
  }
  function buttons() {
    el('voiceoverGenerate').disabled = !available || !!active || !prepared || !el('voiceoverVoice').value;
    el('voiceoverCancel').disabled = !active;
    el('voiceoverVoiceDelete').disabled = !el('voiceoverVoice').value;
  }
  function renderSummary() {
    el('voiceoverSummary').hidden = !prepared;
    if (!prepared) return;
    const changes = prepared.normalization || {places: 0, examples: []};
    el('voiceoverExamples').hidden = !changes.places;
    StudioI18n.bind(el('voiceoverExamplesSummary'), 'textContent', () => StudioI18n.t("voiceover.normalize_examples_summary_template", [changes.places]), true);
    el('voiceoverExamplesList').replaceChildren(...changes.examples.map(example => {
      const item = document.createElement('li');
      StudioI18n.bind(item, 'textContent', () => StudioI18n.t("voiceover.normalize_example_template", [example.before, example.after]), true);
      return item;
    }));
    StudioI18n.bind(el('voiceoverTotals'), 'textContent', () => StudioI18n.t("voiceover.summary_totals_template_label", [prepared.chars, prepared.fragments, duration(prepared.estimate_sec)]), true);
    el('voiceoverChapters').replaceChildren(...prepared.chapters.map(row => {
      const item = document.createElement('li');
      StudioI18n.bind(item, 'textContent', () => StudioI18n.t("voiceover.summary_chapter_row_template_label", [row.title, row.chars, row.fragments]), true);
      return item;
    }));
  }
  function renderVoices(wanted) {
    const select = el('voiceoverVoice');
    const keep = wanted || select.value;
    if (!voices.length) {
      select.replaceChildren(StudioI18n.option(() => StudioI18n.t("voiceover.voice_none_option_label"), ''));
    } else {
      select.replaceChildren(...voices.map(voice => StudioI18n.option(() => StudioI18n.t("voiceover.voice_duration_template_label", [voice.name, voice.duration]), voice.id)));
      select.value = voices.some(voice => voice.id === keep) ? keep : voices[0].id;
    }
    voicePreview();
    buttons();
  }
  function voicePreview() {
    const id = el('voiceoverVoice').value;
    const player = el('voiceoverVoicePlayer');
    if (!id) { player.pause(); player.removeAttribute('src'); player.hidden = true; return; }
    player.src = '/api/voiceover/voice-audio?id=' + encodeURIComponent(id);
    player.hidden = false;
  }
  function renderReport(row) {
    const check = row.verification || {}, list = row.flagged || [];
    el('voiceoverVerification').hidden = !['done', 'skipped'].includes(check.state);
    StudioI18n.bind(el('voiceoverVerification'), 'textContent', () => check.state === 'done'
      ? StudioI18n.t("voiceover.verification_summary_template", [check.checked, check.repaired, list.length]) : StudioI18n.remote(check.reason || ''), true);
    el('voiceoverFlagged').hidden = !list.length;
    StudioI18n.bind(el('voiceoverFlaggedSummary'), 'textContent', () => StudioI18n.t("voiceover.flagged_summary_template", [list.length]), true);
    el('voiceoverFlaggedList').replaceChildren(...list.map(item => {
      const card = document.createElement('div'); card.className = 'voiceover-history-card';
      const title = document.createElement('p');
      StudioI18n.bind(title, 'textContent', () => item.wrong == null ? item.chapter : StudioI18n.t("voiceover.flagged_row_template", [item.chapter, item.wrong, item.words]), true);
      const text = document.createElement('p'); StudioI18n.bind(text, 'textContent', () => StudioI18n.t("voiceover.flagged_text_template", [item.text]), true);
      card.append(title, text);
      if (item.heard != null) {
        const heard = document.createElement('p');
        StudioI18n.bind(heard, 'textContent', () => StudioI18n.t("voiceover.flagged_heard_template", [item.heard || StudioI18n.t("voiceover.flagged_nothing_heard_label")]), true);
        card.append(heard);
      }
      if ((item.suspects || []).length) {
        // The words recognition did not give back: one click starts a dictionary line «word=» for the person to complete.
        const line = document.createElement('p');
        StudioI18n.bind(line, 'textContent', () => StudioI18n.t("voiceover.flagged_suspects_label"), true);
        for (const word of item.suspects) {
          const add = document.createElement('button'); add.type = 'button'; add.className = 'secondary'; add.textContent = word;
          add.onclick = () => {
            const box = el('voiceoverDictionary');
            if (!box.value.split('\n').some(entry => entry.startsWith(word + '='))) box.value = (box.value.trimEnd() ? box.value.trimEnd() + '\n' : '') + word + '=';
            box.dispatchEvent(new Event('input', {bubbles: true})); box.scrollIntoView({block: 'center'}); box.focus();
          };
          line.append(' ', add);
        }
        card.append(line);
      }
      const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none';
      audio.src = '/api/voiceover/fragment?id=' + encodeURIComponent(row.id) + '&key=' + encodeURIComponent(item.key);
      card.append(audio);
      return card;
    }));
  }
  function show(row) {
    selected = row.id; playing = row.id;
    const url = '/api/voiceover/result?id=' + encodeURIComponent(row.id);
    el('voiceoverPlayer').src = url; el('voiceoverPlayer').hidden = false;
    el('voiceoverDownload').href = url; el('voiceoverDownload').download = row.filename || 'Voiceover';
    el('voiceoverDownload').hidden = false;
    StudioI18n.bind(el('voiceoverResultMeta'), 'textContent', () => StudioI18n.t("voiceover.result_meta_template", [duration(row.duration_sec || 0), String(row.format || '').toUpperCase(), (row.chapters || []).length]), true);
    StudioI18n.bind(el('voiceoverSavedPath'), 'textContent', () => StudioI18n.t("voiceover.result_saved_path_prefix") + row.saved_path, true);
    renderReport(row);
  }
  async function logs(id = selected) {
    if (typeof id !== 'string') id = selected;
    if (!id) { StudioI18n.bind(el('voiceoverLogs'), 'textContent', () => StudioI18n.t("voiceover.logs_select_task_hint_label"), false); return; }
    try { StudioI18n.value(el('voiceoverLogs'), 'textContent', (await api('logs?id=' + encodeURIComponent(id))).text, false); }
    catch (error) { StudioI18n.bind(el('voiceoverLogs'), 'textContent', () => StudioI18n.remote(error.message), false); }
  }
  async function history() {
    const value = await api('history');
    el('voiceoverHistory').replaceChildren();
    if (!value.entries.length) StudioI18n.bind(el('voiceoverHistory'), 'textContent', () => StudioI18n.t("voiceover.history_empty_label"), true);
    const states = () => ({done: StudioI18n.t("voiceover.history_state_done_label"), error: StudioI18n.t("voiceover.history_state_failed_label"), cancelled: StudioI18n.t("voiceover.history_state_cancelled_label")});
    for (const row of value.entries.slice(0, 30)) {
      const card = document.createElement('div'); card.className = 'voiceover-history-card';
      const title = document.createElement('p');
      StudioI18n.bind(title, 'textContent', () => StudioI18n.t("voiceover.history_row_template", [row.title, row.voice_name, row.seed, states()[row.state] || row.state]), true);
      card.append(title);
      if (row.state === 'done') {
        const play = document.createElement('button'); play.type = 'button'; play.className = 'secondary';
        StudioI18n.bind(play, 'textContent', () => StudioI18n.t("voiceover.history_listen_button_label"), true);
        play.onclick = () => show(row); card.append(play);
      }
      const journal = document.createElement('button'); journal.type = 'button'; journal.className = 'secondary';
      StudioI18n.bind(journal, 'textContent', () => StudioI18n.t("voiceover.history_log_button_label"), true);
      journal.onclick = () => { selected = row.id; logs(); }; card.append(journal);
      if (row.flagged && row.flagged.length) {
        const flagged = document.createElement('p');
        StudioI18n.bind(flagged, 'textContent', () => StudioI18n.t("voiceover.history_flagged_template", [row.flagged.length]), true);
        card.append(flagged);
      }
      if (StudioI18n.remote(row.error)) { const error = document.createElement('p'); StudioI18n.bind(error, 'textContent', () => StudioI18n.remote(row.error), true); card.append(error); }
      card.append(StudioUI.deleteGenerationButton('Voiceover', row.id, async () => {
        if (playing === row.id) {
          playing = '';
          el('voiceoverPlayer').pause(); el('voiceoverPlayer').removeAttribute('src'); el('voiceoverPlayer').load(); el('voiceoverPlayer').hidden = true;
          el('voiceoverDownload').removeAttribute('href'); el('voiceoverDownload').hidden = true;
          StudioI18n.bind(el('voiceoverResultMeta'), 'textContent', () => '', true); StudioI18n.bind(el('voiceoverSavedPath'), 'textContent', () => '', true);
          renderReport({});
        }
        if (selected === row.id) { selected = ''; StudioI18n.bind(el('voiceoverLogs'), 'textContent', () => StudioI18n.t("voiceover.logs_generation_deleted_label"), false); }
        await history();
      }, () => {
        if (playing === row.id) { el('voiceoverPlayer').pause(); el('voiceoverPlayer').removeAttribute('src'); el('voiceoverPlayer').load(); }
      }));
      if (row.generation) card.append(StudioUI.generationInfo(row.generation));
      el('voiceoverHistory').append(card);
    }
  }
  function progressText(row) {
    return StudioI18n.t("voiceover.status_progress_template_label", [StudioI18n.remote(row.phase), row.done || 0, row.total || 0, Math.round(row.elapsed_sec || 0)]);
  }
  async function poll() {
    if (!active || polling) return;
    polling = true;
    try {
      const row = await api('status?id=' + encodeURIComponent(active.id));
      say(() => progressText(row));
      el('voiceoverProgress').hidden = false;
      el('voiceoverProgress').value = row.total ? Math.min(1, (row.done || 0) / row.total) : 0;
      if (['done', 'error', 'cancelled'].includes(row.state)) {
        active = null; clearInterval(timer); timer = null; el('voiceoverProgress').hidden = true; buttons();
        if (row.state === 'done') show(row);
        if (row.state !== 'cancelled') StudioUI.notify({module: 'Voiceover', id: row.id, error: row.state === 'error',
          message: StudioI18n.remote(row.error) || StudioI18n.t("voiceover.notify_done_message_label"), folder: row.state === 'done' ? StudioUI.parentFolder(row.saved_path) : '',
          onLogs: () => StudioUI.showLogs('Voiceover', row.id, logs)});
        if (StudioI18n.remote(row.error)) say(() => StudioI18n.remote(row.error));
        else if (row.state === 'cancelled') say(() => StudioI18n.remote(row.phase));
        await Promise.all([history(), logs()]);
      }
    } catch (error) { say(() => StudioI18n.t("voiceover.status_no_response_template_label", [StudioI18n.remote(error.message)])); }
    finally { polling = false; }
  }
  async function refresh() {
    try {
      const status = await api('availability');
      engines = status.engines || [status.engine];
      const wanted = el('voiceoverEngine').value || savedEngine;
      el('voiceoverEngine').replaceChildren(...engines.map(item => StudioI18n.option(() => item.name + (item.available ? '' : StudioI18n.t("voiceover.engine_unavailable_suffix")), item.id)));
      el('voiceoverEngine').value = engines.some(item => item.id === wanted) ? wanted : (engines.find(item => item.available) || engines[0]).id;
      const checking = status.verification && status.verification.available;
      if (checking && el('voiceoverVerify').disabled) el('voiceoverVerify').checked = true;  // the model was installed meanwhile
      el('voiceoverVerify').disabled = !checking;
      if (!checking) el('voiceoverVerify').checked = false;
      StudioI18n.bind(el('voiceoverVerifyHint'), 'textContent', () => checking ? StudioI18n.t("voiceover.verify_hint_paragraph_label") : StudioI18n.t("voiceover.verify_unavailable_hint_label"), true);
      chooseEngine();
      voices = (await api('voices')).voices;
      renderVoices();
      const [settings, current] = await Promise.all([api('settings'), api('active')]);
      el('voiceoverSaveDir').value = settings.save_dir;
      StudioI18n.bind(el('voiceoverOutputLocation'), 'textContent', () => StudioI18n.t("voiceover.save_dir_location_prefix") + settings.save_dir, true);
      if (current.id && !['done', 'error', 'cancelled'].includes(current.state)) {
        active = current; selected = current.id;
        if (!timer) timer = setInterval(poll, 2000);
        poll();
      } else if (!active) say(() => available ? StudioI18n.t("voiceover.status_ready_label") : StudioI18n.remote(engine.reason));
      buttons(); await history();
    } catch (error) { say(() => StudioI18n.remote(error.message)); StudioUI.setHeader('voiceover', {tone: 'bad', status: StudioI18n.remote(error.message)}); }
  }
  function replacements() {
    return el('voiceoverDictionary').value.split('\n').map(line => {
      const index = line.indexOf('=');
      return index > 0 ? [line.slice(0, index).trim(), line.slice(index + 1).trim()] : null;
    }).filter(Boolean);
  }

  el('voiceoverTab').onclick = () => {
    for (const name of workspaces) if (el(name)) el(name).hidden = true;
    for (const name of others) if (el(name)) el(name).setAttribute('aria-selected', 'false');
    el('voiceoverWorkspace').hidden = false;
    el('voiceoverTab').setAttribute('aria-selected', 'true');
    document.body.dataset.studioModule = 'voiceover'; refresh();
  };
  // Every other section hides its own siblings by name; Voiceover joins them from here without editing each of them.
  for (const name of others) if (el(name)) el(name).addEventListener('click', () => {
    el('voiceoverWorkspace').hidden = true; el('voiceoverTab').setAttribute('aria-selected', 'false');
  });
  for (const key of fields) el('voiceover' + key).addEventListener('input', draft);
  restore();
  el('voiceoverEngine').addEventListener('change', () => { chooseEngine(); draft(); if (!active) say(() => available ? StudioI18n.t("voiceover.status_ready_label") : StudioI18n.remote(engine.reason)); });
  el('voiceoverVoice').addEventListener('change', () => { voicePreview(); buttons(); });
  el('voiceoverText').addEventListener('input', () => { prepared = null; renderSummary(); buttons(); });
  el('voiceoverFile').addEventListener('change', () => { prepared = null; renderSummary(); buttons(); });
  el('voiceoverPrepare').onclick = async () => {
    el('voiceoverPrepare').disabled = true;
    say(() => StudioI18n.t("voiceover.status_preparing_label"));
    try {
      const file = el('voiceoverFile').files[0];
      const body = {title: el('voiceoverTitle').value};
      if (file) body.file = {name: file.name, data: await readFile(file)};
      else body.text = el('voiceoverText').value;
      prepared = await api('prepare', body);
      if (!el('voiceoverTitle').value) { el('voiceoverTitle').value = prepared.title; draft(); }
      renderSummary();
      say(() => StudioI18n.t("voiceover.status_prepared_label"));
    } catch (error) { prepared = null; renderSummary(); say(() => StudioI18n.remote(error.message)); }
    finally { el('voiceoverPrepare').disabled = false; buttons(); }
  };
  el('voiceoverVoiceAdd').onclick = async () => {
    const file = el('voiceoverVoiceFile').files[0];
    if (!file) { say(() => StudioI18n.t("voiceover.voice_file_missing_error_label")); return; }
    el('voiceoverVoiceAdd').disabled = true;
    try {
      const added = await api('voice-add', {name: el('voiceoverVoiceName').value, consent: el('voiceoverVoiceConsent').checked,
        transcript: el('voiceoverVoiceTranscript').value, audio: await readFile(file)});
      voices = (await api('voices')).voices;
      renderVoices(added.id);
      el('voiceoverVoiceName').value = ''; el('voiceoverVoiceFile').value = ''; el('voiceoverVoiceTranscript').value = ''; el('voiceoverVoiceConsent').checked = false;
      say(() => StudioI18n.t("voiceover.voice_saved_status_label"));
    } catch (error) { say(() => StudioI18n.remote(error.message)); }
    finally { el('voiceoverVoiceAdd').disabled = false; buttons(); }
  };
  el('voiceoverVoiceDelete').onclick = async () => {
    const id = el('voiceoverVoice').value;
    if (!id || !window.confirm(StudioI18n.t("voiceover.voice_delete_confirm_question"))) return;
    try {
      await api('voice-delete', {id});
      voices = (await api('voices')).voices; renderVoices();
    } catch (error) { say(() => StudioI18n.remote(error.message)); }
  };
  el('voiceoverForm').onsubmit = async event => {
    event.preventDefault(); if (active || !prepared) return;
    el('voiceoverGenerate').disabled = true;
    say(() => StudioI18n.t("voiceover.status_starting_label"));
    try {
      active = await api('generate', {engine: el('voiceoverEngine').value, book_id: prepared.book_id, voice_id: el('voiceoverVoice').value, language: el('voiceoverLanguage').value,
        format: el('voiceoverFormat').value, seed: el('voiceoverSeed').value, replacements: replacements(), verify: el('voiceoverVerify').checked,
        normalize: el('voiceoverNormalize').checked, dictionary: el('voiceoverBuiltin').checked});
      selected = active.id; buttons(); timer = setInterval(poll, 2000); poll();
    } catch (error) {
      say(() => StudioI18n.remote(error.message)); buttons();
      StudioUI.notify({module: 'Voiceover', error: true, message: StudioI18n.remote(error.message), onLogs: () => StudioUI.showLogs('Voiceover', '', logs)});
    }
  };
  el('voiceoverCancel').onclick = async () => {
    if (!active) return;
    try { await api('cancel', {id: active.id, token: active.token}); say(() => StudioI18n.t("voiceover.status_cancel_requested_label")); }
    catch (error) { say(() => StudioI18n.remote(error.message)); }
  };
  async function saveFolder(path) {
    const result = await api('settings', {save_dir: path});
    el('voiceoverSaveDir').value = result.save_dir;
    StudioI18n.bind(el('voiceoverOutputLocation'), 'textContent', () => StudioI18n.t("voiceover.save_dir_location_prefix") + result.save_dir, true);
  }
  el('voiceoverSaveSettings').onclick = async () => { try { await saveFolder(el('voiceoverSaveDir').value); } catch (e) { StudioI18n.bind(el('voiceoverOutputLocation'), 'textContent', () => StudioI18n.remote(e.message), true); } };
  el('voiceoverChooseSaveDir').onclick = () => window.chooseStudioFolder(el('voiceoverSaveDir').value, saveFolder, el('voiceoverChooseSaveDir'));
  el('voiceoverOpenSaveDir').onclick = async () => { try { await StudioUI.openFolder(el('voiceoverSaveDir').value); } catch (e) { StudioI18n.bind(el('voiceoverOutputLocation'), 'textContent', () => StudioI18n.remote(e.message), true); } };
  el('voiceoverClearCache').onclick = async () => {
    try {
      const freed = await api('clear-cache', {});
      StudioI18n.bind(el('voiceoverCacheStatus'), 'textContent', () => StudioI18n.t("voiceover.cache_cleared_template", [(freed.freed_bytes / 1048576).toFixed(0)]), true);
    } catch (error) { StudioI18n.bind(el('voiceoverCacheStatus'), 'textContent', () => StudioI18n.remote(error.message), true); }
  };
  el('voiceoverRefreshLogs').onclick = logs;
  el('voiceoverCopyLogs').onclick = async () => { try { await navigator.clipboard.writeText(el('voiceoverLogs').textContent); } catch { say(() => StudioI18n.t("voiceover.logs_clipboard_copy_failed_label")); } };
})();
