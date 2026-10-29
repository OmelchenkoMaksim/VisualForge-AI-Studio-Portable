/* Music owns its draft and job state; image/video prompts stay in their own controls. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const fields = ['Engine','Caption','Lyrics','Mode','Duration','Bpm','Language','Seed','Planner'];
  const draftKey = 'visualforge.music.draft.v1';
  let active = null, selected = '', playing = '', available = false, timer = null, polling = false, profiles=[];
  const defaultGuide=StudioI18n.t("music.default_guide_start_genre_mood_add_2_3");
  function capabilities() {
    const profile=profiles.find(row=>row.id===el('musicEngine').value);
    return profile?.capabilities || {mode:true,lyrics:true,bpm:true,language:true,planner:!!profile?.planner};
  }
  function configureEngine() {
    const profile=profiles.find(row=>row.id===el('musicEngine').value);
    if(!profile)return;
    available=profile.available;
    StudioI18n.bind(el('musicAvailability'), "textContent", () => (available ? profile.name+StudioI18n.t("music.engine_availability_local_execution_suffix")+profile.license : StudioI18n.remote(profile.reason)), true);
    StudioI18n.bind(el('musicRequirements'), "textContent", () => (profile.requirements), true);
    StudioI18n.bind(el('musicEvidence'), "textContent", () => (profile.evidence || StudioI18n.t("music.evidence_gpu_has_not_been_tested_yet")), true);
    StudioI18n.bind(el('musicModelLimitations'), "textContent", () => (profile.limitations || ''), true);
    StudioI18n.bind(el('musicPromptGuide'), "textContent", () => (StudioI18n.remote(profile.prompt_guide) || defaultGuide), true);
    el('musicDuration').min=profile.min_duration;el('musicDuration').max=profile.max_duration;
    el('musicDuration').value=Math.max(profile.min_duration,Math.min(profile.max_duration,Number(el('musicDuration').value)||30));
    StudioI18n.bind(el('musicDurationLabel'), "textContent", () => (profile.id==='minimax-music3-int8'?StudioI18n.t("music.duration_maximum_seconds"):StudioI18n.t("music.generation_duration_seconds_field_label")), true);
    StudioI18n.bind(el('musicDurationHint'), "textContent", () => (profile.id==='minimax-music3-int8'?StudioI18n.t("music.duration_hint_mini_max_may_end_track_before"):(profile.id==='stable-audio-3-medium'?StudioI18n.t("music.duration_hint_stable_audio_10_120_seconds_longer"):'')), true);
    // Fields an engine does not use are hidden, not merely disabled: Stable Audio has no lyrics, tempo, language or planner.
    const caps=capabilities();
    el('musicModeBlock').hidden=!caps.mode;el('musicBpmBlock').hidden=!caps.bpm;el('musicLanguageBlock').hidden=!caps.language;
    el('musicPlannerBlock').hidden=!caps.planner;el('musicPlannerHint').hidden=!caps.planner;
    el('musicPlanner').disabled=!profile.planner;
    StudioI18n.bind(el('musicPlannerHint'), "textContent", () => (profile.planner?StudioI18n.t("music.planner_hint_ace_step_helps_structure_track_takes"):StudioI18n.t("music.planner_hint_mini_max_music3_uses_own_musical")), true);
    buttons();header();
  }
  async function api(route, data) {
    const response = await fetch('/api/music/' + route, data === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(StudioI18n.remote(result.error) || StudioI18n.t("music.engine_header_service_unavailable_status"));
    return result;
  }
  function draft() {
    const value = {};
    for (const key of fields) value[key] = key === 'Planner' ? el('music'+key).checked : el('music'+key).value;
    try { localStorage.setItem(draftKey, JSON.stringify(value)); } catch {}
  }
  function header() {
    const profile = profiles.find(row => row.id === el('musicEngine').value);
    StudioUI.setHeader('music', {model:profile?.name || 'Music',
      description:!capabilities().mode ? StudioI18n.t("music.header_music_sounds_description") : (el('musicMode').value === 'song' ? StudioI18n.t("music.header_create_song_vocals_lyrics") : StudioI18n.t("music.header_generate_instrumental_music_description")),
      tone:profile ? (available ? 'good' : 'bad') : 'muted',
      status:profile ? (available ? StudioI18n.t("music.header_music_engine_model_files_available_gpu") : StudioI18n.remote(profile.reason) || StudioI18n.t("music.header_selected_model_unavailable")) : StudioI18n.t("music.header_checking_music_models")});
  }
  function modes() {
    header();
    const song = capabilities().lyrics && el('musicMode').value === 'song';
    el('musicLyricsBlock').hidden = !song;
    el('musicLyrics').required = song;
    el('musicLanguage').disabled = !song;
  }
  function restore(value) {
    for (const key of fields) if (Object.hasOwn(value, key)) {
      if (key === 'Planner') el('music'+key).checked = value[key];
      else el('music'+key).value = value[key];
    }
    modes();configureEngine();
  }
  function buttons() {
    el('musicGenerate').disabled = !available || !!active;
    el('musicCancel').disabled = !active;
  }
  function show(row) {
    selected = row.id; playing = row.id;
    const url = '/api/music/result?id=' + encodeURIComponent(row.id);
    el('musicPlayer').src = url; el('musicPlayer').hidden = false;
    el('musicDownload').href = url; el('musicDownload').download = row.filename || 'Generated Music.wav';
    el('musicDownload').hidden = false;
    const meta = row.metadata;
    StudioI18n.bind(el('musicResultMeta'), "textContent", () => (meta ? StudioI18n.t("music.meta_s_k_hz_seed", [meta.duration, meta.sample_rate/1000, meta.channels === 2 ? StudioI18n.t("music.output_audio_stereo_channel_label") : meta.channels+StudioI18n.t("music.output_audio_channel_count_suffix"), row.seed]) : ''), true);
    StudioI18n.bind(el('musicSavedPath'), "textContent", () => (StudioI18n.t("music.output_saved_file_path_prefix") + row.saved_path), true);
  }
  async function logs(id = selected) {
    if (typeof id !== 'string') id=selected;
    if (!id) { StudioI18n.bind(el('musicLogs'), "textContent", () => (StudioI18n.t("music.logs_task_has_not_started_yet_error")), false); return; }
    try { StudioI18n.value(el('musicLogs'), "textContent", ((await api('logs?id='+encodeURIComponent(id))).text), false); }
    catch (error) { StudioI18n.bind(el('musicLogs'), "textContent", () => (StudioI18n.remote(error.message)), false); }
  }
  async function history() {
    const value = await api('history');
    el('musicHistory').replaceChildren();
    if (!value.entries.length) StudioI18n.bind(el('musicHistory'), "textContent", () => (StudioI18n.t("music.history_generated_tracks_appear_here")), true);
    for (const row of value.entries.slice(0, 30)) {
      const card = document.createElement('div'); card.className = 'music-history-card';
      const title = document.createElement('p');
      StudioI18n.bind(title, "textContent", () => (StudioI18n.t("music.history_s_seed", [profiles.find(p=>p.id===row.engine)?.name || row.engine || 'ACE-Step 1.5 Turbo', {song:StudioI18n.t("music.generation_song_mode_label"),audio:StudioI18n.t("music.history_sound_music")}[row.mode] || StudioI18n.t("music.generation_instrumental_mode_label"), row.duration, row.seed, {done:StudioI18n.t("music.generation_completed_status_label"),error:StudioI18n.t("music.generation_failed_status_label"),cancelled:StudioI18n.t("music.generation_cancelled_status_label")}[row.state] || row.state])), true);
      const caption = document.createElement('p'); StudioI18n.bind(caption, "textContent", () => (row.caption), true);
      const reuse = document.createElement('button'); reuse.type='button'; reuse.className='secondary'; StudioI18n.bind(reuse, "textContent", () => (StudioI18n.t("music.reuse_restore_settings")), true);
      reuse.onclick = () => {
        restore({Engine:row.engine || 'ace-step-15-turbo',Caption:row.caption,Lyrics:row.lyrics,Mode:row.mode === 'audio' ? 'instrumental' : row.mode,Duration:row.duration,Bpm:row.bpm ?? '',Language:row.language,Seed:row.seed,Planner:row.planner}); draft();
      };
      const journal = document.createElement('button'); journal.type='button'; journal.className='secondary'; StudioI18n.bind(journal, "textContent", () => (StudioI18n.t("music.journal_task_log")), true);
      journal.onclick = () => { selected=row.id; logs(); };
      card.append(title,caption,reuse,journal);
      if (row.state === 'done') {
        const play = document.createElement('button'); play.type='button'; play.className='secondary'; StudioI18n.bind(play, "textContent", () => (StudioI18n.t("music.history_track_listen_button_label")), true);
        play.onclick = () => show(row); card.append(play);
      }
      if (StudioI18n.remote(row.error)) { const error = document.createElement('p'); StudioI18n.bind(error, "textContent", () => (StudioI18n.remote(row.error)), true); card.append(error); }
      card.append(StudioUI.deleteGenerationButton('Music', row.id, async () => {
        if (playing === row.id) {
          playing='';
          el('musicPlayer').pause(); el('musicPlayer').removeAttribute('src'); el('musicPlayer').load(); el('musicPlayer').hidden=true;
          el('musicDownload').removeAttribute('href'); el('musicDownload').hidden=true;
          StudioI18n.bind(el('musicResultMeta'), "textContent", () => (''), true); StudioI18n.bind(el('musicSavedPath'), "textContent", () => (''), true);
        }
        if (selected === row.id) { selected=''; StudioI18n.bind(el('musicLogs'), "textContent", () => (StudioI18n.t("music.logs_generation_deleted")), false); }
        await history();
      }, () => {
        if (playing === row.id) {el('musicPlayer').pause(); el('musicPlayer').removeAttribute('src'); el('musicPlayer').load();}
      }));
      if (row.generation) card.append(StudioUI.generationInfo(row.generation));
      el('musicHistory').append(card);
    }
  }
  async function poll() {
    if (!active || polling) return;
    polling = true;
    try {
      const row = await api('status?id='+encodeURIComponent(active.id));
      StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.t("music.poll_elapsed_s", [StudioI18n.remote(row.phase), row.elapsed_sec])), true);
      if (['done','error','cancelled'].includes(row.state)) {
        active = null; clearInterval(timer); timer = null; buttons();
        if (row.state === 'done') show(row);
        if (row.state !== 'cancelled') StudioUI.notify({module:'Music',id:row.id,error:row.state==='error',
          message:StudioI18n.remote(row.error) || StudioI18n.t("music.poll_track_created_saved"),folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):'',
          onLogs:()=>StudioUI.showLogs('Music',row.id,logs)});
        if (StudioI18n.remote(row.error)) StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.remote(row.error)), true);
        await Promise.all([history(), logs()]);
      }
    } catch (error) { StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.t("music.poll_no_response_music")+StudioI18n.remote(error.message)+StudioI18n.t("music.poll_checks_continue")), true); }
    finally { polling = false; }
  }
  async function refresh() {
    try {
      const status = await api('availability');
      const wanted=el('musicEngine').value;
      profiles=status.engines || [status.engine];
      el('musicEngine').replaceChildren(...profiles.map(profile=>{
        const option=StudioI18n.option(() => (StudioUI.modelLabel(profile.id,profile.name)+(profile.available?'':StudioI18n.t("music.engine_selector_unavailable_option_suffix"))), profile.id);
        StudioI18n.bind(option, "title", () => (StudioUI.modelSource(profile.id)), true);return option;
      }));
      el('musicEngine').value=profiles.some(p=>p.id===wanted)?wanted:profiles[0].id;
      configureEngine();
      const [settings, current] = await Promise.all([api('settings'),api('active')]);
      el('musicSaveDir').value = settings.save_dir;
      StudioI18n.bind(el('musicOutputLocation'), "textContent", () => (StudioI18n.t("music.output_location_autosave")+settings.save_dir), true);
      if (current.id && !['done','error','cancelled'].includes(current.state)) {
        active = current; selected = current.id;
        if (!timer) timer = setInterval(poll, 2000);
        poll();
      } else if (!active) StudioI18n.bind(el('musicStatus'), "textContent", () => (available ? StudioI18n.t("music.refresh_ready_generate") : StudioI18n.remote(status.reason)), true);
      buttons(); await history();
    } catch (error) { StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.remote(error.message)), true);StudioUI.setHeader('music', {tone:'bad',status:StudioI18n.remote(error.message)}); }
  }
  el('musicTab').onclick = () => {
    for (const name of ['image','video','music']) {
      el(name+'Workspace').hidden = name !== 'music';
      el(name+'Tab').setAttribute('aria-selected', String(name === 'music'));
    }
    if (el('textWorkspace')) el('textWorkspace').hidden = true;
    if (el('textTab')) el('textTab').setAttribute('aria-selected', 'false');
    document.body.dataset.studioModule='music'; refresh();
  };
  for (const key of fields) el('music'+key).addEventListener('input', () => { modes(); if(key==='Engine'){configureEngine();if(!active)StudioI18n.bind(el('musicStatus'), "textContent", () => (available?StudioI18n.t("music.refresh_ready_generate"):StudioI18n.t("music.header_selected_model_unavailable")), true);} draft(); });
  try { restore(JSON.parse(localStorage.getItem(draftKey) || '{}')); } catch { modes(); }
  el('musicForm').onsubmit = async event => {
    event.preventDefault(); if (active) return;
    el('musicGenerate').disabled = true;
    StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.t("music.generation_preparing_gpu_memory_status")), true);
    try {
      active = await api('generate', {engine:el('musicEngine').value, caption:el('musicCaption').value,
        lyrics:el('musicLyrics').value,mode:el('musicMode').value,duration:Number(el('musicDuration').value),
        bpm:el('musicBpm').value,language:el('musicLanguage').value,seed:el('musicSeed').value,planner:el('musicPlanner').disabled?false:el('musicPlanner').checked});
      selected=active.id; buttons(); timer=setInterval(poll,2000); poll();
    } catch (error) { StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.remote(error.message)), true); buttons(); StudioUI.notify({module:'Music',error:true,message:StudioI18n.remote(error.message),onLogs:()=>StudioUI.showLogs('Music','',logs)}); }
  };
  el('musicCancel').onclick = async () => {
    if (!active) return;
    try { await api('cancel',{id:active.id,token:active.token}); StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.t("music.generation_cancellation_requested_status")), true); }
    catch (error) { StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.remote(error.message)), true); }
  };
  async function saveFolder(path) {
    const result=await api('settings',{save_dir:path});
    el('musicSaveDir').value=result.save_dir;StudioI18n.bind(el('musicOutputLocation'), "textContent", () => (StudioI18n.t("music.output_location_autosave")+result.save_dir), true);
  }
  el('musicSaveSettings').onclick=async()=>{try{await saveFolder(el('musicSaveDir').value);}catch(e){StudioI18n.bind(el('musicOutputLocation'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('musicChooseSaveDir').onclick=()=>window.chooseStudioFolder(el('musicSaveDir').value,saveFolder,el('musicChooseSaveDir'));
  el('musicOpenSaveDir').onclick=async()=>{try{await StudioUI.openFolder(el('musicSaveDir').value);}catch(e){StudioI18n.bind(el('musicOutputLocation'), "textContent", () => (StudioI18n.remote(e.message)), true);}};
  el('musicExample').onclick = () => {el('musicCaption').value='Warm cinematic ambient, gentle piano, soft strings, slow evolving melody, peaceful atmosphere, spacious stereo production.';draft();};
  el('musicRefreshLogs').onclick=logs;
  el('musicCopyLogs').onclick=async()=>{try{await navigator.clipboard.writeText(el('musicLogs').textContent);}catch{StudioI18n.bind(el('musicStatus'), "textContent", () => (StudioI18n.t("music.task_log_clipboard_copy_failed_hint")), true);}};
})();
