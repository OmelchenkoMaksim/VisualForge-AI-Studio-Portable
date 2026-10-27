/* Music owns its draft and job state; image/video prompts stay in their own controls. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const fields = ['Engine','Caption','Lyrics','Mode','Duration','Bpm','Language','Seed','Planner'];
  const draftKey = 'visualforge.music.draft.v1';
  let active = null, selected = '', available = false, timer = null, polling = false, profiles=[];
  const defaultGuide='Начните с жанра и настроения, добавьте 2–3 главных инструмента, темп и характер звучания. Лучше использовать конкретное английское описание стиля; слова песни можно написать по-русски. Для инструментала не просите вокал в описании.';
  function capabilities() {
    const profile=profiles.find(row=>row.id===el('musicEngine').value);
    return profile?.capabilities || {mode:true,lyrics:true,bpm:true,language:true,planner:!!profile?.planner};
  }
  function configureEngine() {
    const profile=profiles.find(row=>row.id===el('musicEngine').value);
    if(!profile)return;
    available=profile.available;
    el('musicAvailability').textContent=available ? profile.name+' · локально · '+profile.license : profile.reason;
    el('musicRequirements').textContent=profile.requirements;
    el('musicEvidence').textContent=profile.evidence || 'Работа на этой GPU ещё не проверена.';
    el('musicModelLimitations').textContent=profile.limitations || '';
    el('musicPromptGuide').textContent=profile.prompt_guide || defaultGuide;
    el('musicDuration').min=profile.min_duration;el('musicDuration').max=profile.max_duration;
    el('musicDuration').value=Math.max(profile.min_duration,Math.min(profile.max_duration,Number(el('musicDuration').value)||30));
    el('musicDurationLabel').textContent=profile.id==='minimax-music3-int8'?'Максимальная длительность, секунды':'Длительность, секунды';
    el('musicDurationHint').textContent=profile.id==='minimax-music3-int8'?'MiniMax может завершить композицию раньше указанного предела. Начальный диапазон: 10–60 секунд.':(profile.id==='stable-audio-3-medium'?'Stable Audio: 10–120 секунд. Более длинные клипы пока не проверялись на этой видеокарте.':'');
    // Fields an engine does not use are hidden, not merely disabled: Stable Audio has no lyrics, tempo, language or planner.
    const caps=capabilities();
    el('musicModeBlock').hidden=!caps.mode;el('musicBpmBlock').hidden=!caps.bpm;el('musicLanguageBlock').hidden=!caps.language;
    el('musicPlannerBlock').hidden=!caps.planner;el('musicPlannerHint').hidden=!caps.planner;
    el('musicPlanner').disabled=!profile.planner;
    el('musicPlannerHint').textContent=profile.planner?'Планировщик ACE-Step помогает выстроить композицию и требует дополнительного времени.':'MiniMax Music3 использует собственную музыкальную структуру; отдельный планировщик ACE-Step здесь не применяется.';
    buttons();header();
  }
  async function api(route, data) {
    const response = await fetch('/api/music/' + route, data === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Music недоступен');
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
      description:!capabilities().mode ? 'Музыка и звуки по описанию' : (el('musicMode').value === 'song' ? 'Создание песни с вокалом по вашему тексту' : 'Генерация инструментальной музыки по описанию'),
      tone:profile ? (available ? 'good' : 'bad') : 'muted',
      status:profile ? (available ? 'Music: файлы движка и модели доступны. GPU-проверка выполняется при генерации.' : profile.reason || 'Выбранная модель недоступна.') : 'Проверка моделей Music…'});
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
    selected = row.id;
    const url = '/api/music/result?id=' + encodeURIComponent(row.id);
    el('musicPlayer').src = url; el('musicPlayer').hidden = false;
    el('musicDownload').href = url; el('musicDownload').download = row.filename || 'Generated Music.wav';
    el('musicDownload').hidden = false;
    const meta = row.metadata;
    el('musicResultMeta').textContent = meta ? `${meta.duration} с · ${meta.sample_rate/1000} кГц · ${meta.channels === 2 ? 'стерео' : meta.channels+' каналов'} · Seed ${row.seed}` : '';
    el('musicSavedPath').textContent = 'Сохранено: ' + row.saved_path;
  }
  async function logs(id = selected) {
    if (typeof id !== 'string') id=selected;
    if (!id) { el('musicLogs').textContent='Задача ещё не была запущена. Подробности ошибки показаны в уведомлении.'; return; }
    try { el('musicLogs').textContent = (await api('logs?id='+encodeURIComponent(id))).text; }
    catch (error) { el('musicLogs').textContent = error.message; }
  }
  async function history() {
    const value = await api('history');
    el('musicHistory').replaceChildren();
    if (!value.entries.length) el('musicHistory').textContent = 'Здесь появятся созданные композиции.';
    for (const row of value.entries.slice(0, 30)) {
      const card = document.createElement('div'); card.className = 'music-history-card';
      const title = document.createElement('p');
      title.textContent = `${profiles.find(p=>p.id===row.engine)?.name || row.engine || 'ACE-Step 1.5 Turbo'} · ${{song:'Песня',audio:'Звук / музыка'}[row.mode] || 'Инструментал'} · ${row.duration} с · Seed ${row.seed} · ${{done:'Готово',error:'Ошибка',cancelled:'Отменено'}[row.state] || row.state}`;
      const caption = document.createElement('p'); caption.textContent = row.caption;
      const reuse = document.createElement('button'); reuse.type='button'; reuse.className='secondary'; reuse.textContent='Вернуть настройки';
      reuse.onclick = () => {
        restore({Engine:row.engine || 'ace-step-15-turbo',Caption:row.caption,Lyrics:row.lyrics,Mode:row.mode === 'audio' ? 'instrumental' : row.mode,Duration:row.duration,Bpm:row.bpm ?? '',Language:row.language,Seed:row.seed,Planner:row.planner}); draft();
      };
      const journal = document.createElement('button'); journal.type='button'; journal.className='secondary'; journal.textContent='Лог задачи';
      journal.onclick = () => { selected=row.id; logs(); };
      card.append(title,caption,reuse,journal);
      if (row.state === 'done') {
        const play = document.createElement('button'); play.type='button'; play.className='secondary'; play.textContent='Прослушать';
        play.onclick = () => show(row); card.append(play);
      }
      if (row.error) { const error = document.createElement('p'); error.textContent = row.error; card.append(error); }
      el('musicHistory').append(card);
    }
  }
  async function poll() {
    if (!active || polling) return;
    polling = true;
    try {
      const row = await api('status?id='+encodeURIComponent(active.id));
      el('musicStatus').textContent = `${row.phase} · прошло ${row.elapsed_sec} с`;
      if (['done','error','cancelled'].includes(row.state)) {
        active = null; clearInterval(timer); timer = null; buttons();
        if (row.state === 'done') show(row);
        if (row.state !== 'cancelled') StudioUI.notify({module:'Music',id:row.id,error:row.state==='error',
          message:row.error || 'Композиция создана и сохранена.',folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):'',
          onLogs:()=>StudioUI.showLogs('Music',row.id,logs)});
        if (row.error) el('musicStatus').textContent = row.error;
        await Promise.all([history(), logs()]);
      }
    } catch (error) { el('musicStatus').textContent = 'Нет ответа Music: '+error.message+'. Проверка продолжится.'; }
    finally { polling = false; }
  }
  async function refresh() {
    try {
      const status = await api('availability');
      const wanted=el('musicEngine').value;
      profiles=status.engines || [status.engine];
      el('musicEngine').replaceChildren(...profiles.map(profile=>{
        const option=new Option(StudioUI.modelLabel(profile.id,profile.name)+(profile.available?'':' · недоступна'),profile.id);
        option.title=StudioUI.modelSource(profile.id);return option;
      }));
      el('musicEngine').value=profiles.some(p=>p.id===wanted)?wanted:profiles[0].id;
      configureEngine();
      const [settings, current] = await Promise.all([api('settings'),api('active')]);
      el('musicSaveDir').value = settings.save_dir;
      el('musicOutputLocation').textContent = 'Автосохранение: '+settings.save_dir;
      if (current.id && !['done','error','cancelled'].includes(current.state)) {
        active = current; selected = current.id;
        if (!timer) timer = setInterval(poll, 2000);
        poll();
      } else if (!active) el('musicStatus').textContent = available ? 'Готово к генерации.' : status.reason;
      buttons(); await history();
    } catch (error) { el('musicStatus').textContent = error.message;StudioUI.setHeader('music', {tone:'bad',status:error.message}); }
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
  for (const key of fields) el('music'+key).addEventListener('input', () => { modes(); if(key==='Engine'){configureEngine();if(!active)el('musicStatus').textContent=available?'Готово к генерации.':'Выбранная модель недоступна.';} draft(); });
  try { restore(JSON.parse(localStorage.getItem(draftKey) || '{}')); } catch { modes(); }
  el('musicForm').onsubmit = async event => {
    event.preventDefault(); if (active) return;
    el('musicGenerate').disabled = true;
    el('musicStatus').textContent = 'Подготовка Music и освобождение видеопамяти…';
    try {
      active = await api('generate', {engine:el('musicEngine').value, caption:el('musicCaption').value,
        lyrics:el('musicLyrics').value,mode:el('musicMode').value,duration:Number(el('musicDuration').value),
        bpm:el('musicBpm').value,language:el('musicLanguage').value,seed:el('musicSeed').value,planner:el('musicPlanner').disabled?false:el('musicPlanner').checked});
      selected=active.id; buttons(); timer=setInterval(poll,2000); poll();
    } catch (error) { el('musicStatus').textContent=error.message; buttons(); StudioUI.notify({module:'Music',error:true,message:error.message,onLogs:()=>StudioUI.showLogs('Music','',logs)}); }
  };
  el('musicCancel').onclick = async () => {
    if (!active) return;
    try { await api('cancel',{id:active.id,token:active.token}); el('musicStatus').textContent='Отмена Music…'; }
    catch (error) { el('musicStatus').textContent=error.message; }
  };
  async function saveFolder(path) {
    const result=await api('settings',{save_dir:path});
    el('musicSaveDir').value=result.save_dir;el('musicOutputLocation').textContent='Автосохранение: '+result.save_dir;
  }
  el('musicSaveSettings').onclick=async()=>{try{await saveFolder(el('musicSaveDir').value);}catch(e){el('musicOutputLocation').textContent=e.message;}};
  el('musicChooseSaveDir').onclick=()=>window.chooseStudioFolder(el('musicSaveDir').value,saveFolder,el('musicChooseSaveDir'));
  el('musicOpenSaveDir').onclick=async()=>{try{await StudioUI.openFolder(el('musicSaveDir').value);}catch(e){el('musicOutputLocation').textContent=e.message;}};
  el('musicExample').onclick = () => {el('musicCaption').value='Warm cinematic ambient, gentle piano, soft strings, slow evolving melody, peaceful atmosphere, spacious stereo production.';draft();};
  el('musicRefreshLogs').onclick=logs;
  el('musicCopyLogs').onclick=async()=>{try{await navigator.clipboard.writeText(el('musicLogs').textContent);}catch{el('musicStatus').textContent='Не удалось скопировать журнал. Выделите текст вручную.';}};
})();
