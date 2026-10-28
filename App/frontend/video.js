'use strict';
(() => {
  const el = id => document.getElementById(id);
  let available = false, active = null, source = null, end = null, last = null;
  let polling = false;
  let timer = null, speechReference = null, speechAudio = null;
  const characters=[];
  let profiles=[], selectedEngine='freevideo-h3-int8', engineDrafts={};
  const originalHints=Object.fromEntries(['videoQualityHelp','videoProfileHint','videoPromptGuide'].map(id=>[id,el(id).textContent]));
  const isWan=()=>el('videoEngine').value.startsWith('wan22-');
  const isLtx=()=>el('videoEngine').value==='ltx25-distilled';
  const isProfiled=()=>isWan() || isLtx();
  const isS2V=()=>el('videoEngine').value==='wan22-s2v-14b';
  const engineName=id=>profiles.find(p=>p.id===id)?.name || ({'wan22-ti2v-5b':'Wan 2.2 TI2V-5B','wan22-s2v-14b':'Wan 2.2 S2V-14B'})[id] || 'FreeVideo';
  function rememberEngine(){engineDrafts[selectedEngine]={mode:el('videoMode').value,quality:el('videoQuality').value,frames:el('videoDuration').value,resolution:el('videoResolution').value};}
  function configureEngine(){
    selectedEngine=el('videoEngine').value;
    const profile=profiles.find(p=>p.id===selectedEngine);if(!profile)return;
    const saved=engineDrafts[selectedEngine] || {};
    for(const option of el('videoMode').options)option.disabled=!profile.modes.includes(option.value);
    el('videoMode').value=profile.modes.includes(saved.mode)?saved.mode:profile.modes[0];
    const plural=n=>n%10===1 && n%100!==11?'кадр':n%10>=2 && n%10<=4 && !(n%100>=12 && n%100<=14)?'кадра':'кадров';
    el('videoDuration').replaceChildren(...profile.frames.map(n=>new Option(profile.duration_labels?.[n] || (n===1440?'1 минута · 6 сцен · 1440 кадров':`${(n/profile.fps).toLocaleString('ru-RU',{maximumFractionDigits:4})} с · ${n} ${plural(n)}`),String(n))));
    if(profile.frames.includes(Number(saved.frames)))el('videoDuration').value=saved.frames;
    else if(isWan() && !isS2V())el('videoDuration').value='81';
    el('videoResolution').replaceChildren(...profile.resolutions.map(([w,h])=>new Option(`${w} × ${h}`,`${w}x${h}`)));
    if([...el('videoResolution').options].some(o=>o.value===saved.resolution))el('videoResolution').value=saved.resolution;
    else if(isWan() && !isS2V())el('videoResolution').value='832x480';
    const names={preview:'Предпросмотр',standard:'Стандартное',high:'Высокое'};
    const h3={preview:'8 шагов',standard:'8 + 3 шага',high:'16 шагов'};
    el('videoQuality').replaceChildren(...profile.qualities.map(q=>new Option(profile.quality_labels?.[q] || `${names[q]} · ${profile.steps?profile.steps[q]+' шагов':h3[q]}`,q)));
    el('videoQuality').value=profile.qualities.includes(saved.quality)?saved.quality:(isProfiled()?'standard':'preview');
    el('videoQuality').previousElementSibling.title=isProfiled()?'Шаг — одно уточнение результата моделью. Больше шагов требует больше времени.':'Шаг — одно уточнение результата моделью. 8 + 3 — основной проход и дополнительное уточнение.';
    el('videoQualityHelp').textContent=isLtx()?'Быстрое: 8 шагов, один проход, облегчённое декодирование. Два прохода: 8 шагов в половинном размере, обученное увеличение и 3 шага уточнения; затем более детальное декодирование. Для длинного ролика выбранные шаги выполняются для каждого фрагмента.':isS2V()?'20 последовательных уточнений сцены моделью. Пока доступен один профиль качества.':isWan()?'Шаг — одно уточнение сцены. Начните со стандартных 20 шагов; 12 быстрее, 30 требует больше времени.':originalHints.videoQualityHelp;
    el('videoProfileHint').textContent=isLtx()?'24 FPS. Начните с 768 × 512 и 49 кадров (2,0417 с). Видео и звук создаются совместно. 15/30/60 с — продолжение одной сцены; на стыках возможны изменения деталей и звука. Доступны горизонтальные и вертикальные профили. Экспорт MP4 / H.264 + AAC.':isS2V()?'16 FPS — 16 кадров за секунду. Короткий профиль: 512 × 512, 77 кадров (4,8125 с), 20 шагов. Экспорт MP4 / H.264 со звуком речи.':isWan()?'24 FPS — 24 кадра за секунду. Начните с 832 × 480, 81 кадр (3,375 с), 20 шагов. На 512 × 288 в пробе были сильные искажения формы. 121 кадр проверен на сцене с движением; остальные сочетания отдельно не проверены. Экспорт MP4 / H.264, без звука.':originalHints.videoProfileHint;
    el('videoPromptGuide').textContent=isLtx()?'Опишите сцену, движение камеры и желаемый звук одним связным текстом. Например: A close-up of a small brass bell swinging gently in a quiet garden. The bell rings clearly, birds chirp softly in the background. Locked camera, natural daylight. Реплики пишите в кавычках; правильность произношения не гарантируется.':isS2V()?'Опишите персонажа, выражение лица и спокойное движение. Слова реплики вводятся отдельно. Пример описания: The person speaks naturally to the camera, subtle head movement, clear face, stable lighting, fixed camera.':isWan()?'Для Wan начните с простого английского описания сцены, одного движения и камеры. При исходном изображении опишите желаемое движение. Для заметного действия: A small yellow toy robot raises its right arm and waves its hand. Locked camera, one continuous shot.':originalHints.videoPromptGuide;
    available=profile.available;
    el('videoAvailability').textContent=profile.available?`${profile.name} · ${profile.verification?.summary_ru || (isProfiled()?'Локальная генерация ещё не проверена.':'Проверено на RTX 5070 Ti 16 ГБ.')}`:profile.reason;
    el('videoModelDetails').textContent=[profile.release_date?`Выпуск модели: ${profile.release_date}.`:'',profile.requirements,profile.limitations].filter(Boolean).join(' ');
    el('videoGenerate').disabled=!available || !!active;
    modes();
  }
  function addCharacter() {
    if(characters.length>=3)return;
    const person={name:'',image:null,voice_audio:null,speech_text:'',language:'Russian'};
    const card=document.createElement('div');card.className='video-character';card.tabIndex=0;
    const title=document.createElement('label');title.textContent='Имя персонажа';
    const name=document.createElement('input');name.type='text';name.maxLength=60;name.placeholder='Например: Алиса';name.oninput=()=>person.name=name.value;
    const photoLabel=document.createElement('label');photoLabel.textContent='Изображение · можно вставить Ctrl+V';
    const photo=document.createElement('input');photo.type='file';photo.accept='image/*';
    const preview=document.createElement('img');preview.className='video-frame';preview.hidden=true;preview.alt='Изображение персонажа';
    const load=async file=>{try{person.image=await toFrame(file,1024);preview.src=person.image;preview.hidden=false;}catch(e){el('videoStatus').textContent=e.message;}};
    photo.onchange=()=>{if(photo.files[0])load(photo.files[0]);};
    card.addEventListener('paste',event=>{const file=[...(event.clipboardData?.files || [])].find(f=>f.type.startsWith('image/'));if(file){event.preventDefault();load(file);}});
    const voiceLabel=document.createElement('label');voiceLabel.textContent='Образец голоса · необязательно';
    const voice=document.createElement('input');voice.type='file';voice.accept='audio/*,.wav,.mp3,.flac,.m4a,.ogg';
    voice.onchange=()=>{const file=voice.files[0];person.voice_audio=null;if(!file)return;if(file.size>8*1024*1024){el('videoStatus').textContent='Образец голоса больше 8 МиБ';return;}const reader=new FileReader();reader.onload=()=>{person.voice_audio=String(reader.result).replace(/^data:[^;]*;/,'data:audio/wav;');};reader.readAsDataURL(file);};
    const textLabel=document.createElement('label');textLabel.textContent='Текст реплики этим голосом';
    const text=document.createElement('textarea');text.rows=3;text.maxLength=1000;text.placeholder='Введите то, что должен сказать персонаж';text.oninput=()=>person.speech_text=text.value;
    const language=document.createElement('select');language.setAttribute('aria-label','Язык реплики');for(const [value,label] of [['Russian','Русский'],['English','English'],['Auto','Определить язык']]){const option=new Option(label,value);language.add(option);}language.onchange=()=>person.language=language.value;
    const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Удалить персонажа';remove.onclick=()=>{characters.splice(characters.indexOf(person),1);card.remove();el('videoAddCharacter').disabled=false;};
    const prefix='videoCharacter-'+crypto.randomUUID();
    for(const [label,input,suffix] of [[title,name,'name'],[photoLabel,photo,'image'],[voiceLabel,voice,'voice'],[textLabel,text,'text']]){input.id=prefix+'-'+suffix;label.htmlFor=input.id;}
    card.append(title,name,photoLabel,photo,preview,voiceLabel,voice,textLabel,text,language,remove);
    characters.push(person);el('videoCharacters').append(card);el('videoAddCharacter').disabled=characters.length>=3;
  }
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/video/' + route, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
    catch {throw new Error('Нет связи со студией. Проверьте, запущено ли приложение.');}
    let value;
    try {value = await response.json();} catch {throw new Error('Некорректный ответ Video-сервиса. Откройте логи Video.');}
    if (!response.ok) throw new Error(value.error || value.reason || 'Video недоступен');
    return value;
  };
  function module(name) {
    if (el('musicWorkspace')) el('musicWorkspace').hidden = true;
    if (el('musicTab')) el('musicTab').setAttribute('aria-selected', 'false');
    if (el('textWorkspace')) el('textWorkspace').hidden = true;
    if (el('textTab')) el('textTab').setAttribute('aria-selected', 'false');
    el('imageWorkspace').hidden = name !== 'image'; el('videoWorkspace').hidden = name !== 'video';
    document.body.dataset.studioModule = name;
    el('imageTab').setAttribute('aria-selected', String(name === 'image'));
    el('videoTab').setAttribute('aria-selected', String(name === 'video'));
    if (name === 'video') refresh();
  }
  function header() {
    const profile = profiles.find(row => row.id === el('videoEngine').value);
    const descriptions = {t2v:'Генерация видео по текстовому описанию', i2v:'Оживление исходного изображения',
      first_last:'Генерация видео между первым и последним кадром', characters:'Генерация видео с персонажами по референсам',
      s2v:'Говорящий персонаж по изображению и речи'};
    StudioUI.setHeader('video', {model:profile?.name || 'Video',
      description:descriptions[el('videoMode').value] || 'Локальная генерация видео',
      tone:profile ? (available ? 'good' : 'bad') : 'muted',
      status:profile ? (available ? 'Video: файлы движка и модели доступны. GPU-проверка выполняется при генерации.' : profile.reason || 'Выбранная модель недоступна.') : 'Проверка моделей Video…'});
  }
  function modes() {
    header();
    const mode = el('videoMode').value;
    el('videoSourceBlock').hidden = !['i2v','first_last','s2v'].includes(mode);
    el('videoSpeechBlock').hidden=mode!=='s2v';
    el('videoCloneBlock').hidden=el('videoSpeechMode').value!=='clone';
    el('videoReadySpeechBlock').hidden=el('videoSpeechMode').value!=='audio';
    el('videoEndBlock').hidden = mode !== 'first_last';
    el('videoCharacterBlock').hidden=mode!=='characters';
    if(mode==='characters' && !characters.length)addCharacter();
    if(isProfiled()){for(const option of [...el('videoDuration').options,...el('videoQuality').options])option.disabled=false;return;}
    for(const option of el('videoDuration').options)option.disabled=!['t2v','characters'].includes(mode) && option.value!=='39';
    if(!['t2v','characters'].includes(mode))el('videoDuration').value='39';
    const previewOnly=mode!=='t2v' || el('videoDuration').value!=='39';
    for(const option of el('videoQuality').options)option.disabled=previewOnly && option.value!=='preview';
    if(previewOnly)el('videoQuality').value='preview';
  }
  function draft() {
    rememberEngine();
    try {localStorage.setItem('visualforge.video.draft.v1',JSON.stringify({speech:{mode:el('videoSpeechMode').value,text:el('videoSpeechText').value,language:el('videoSpeechLanguage').value},engine:selectedEngine,engineDrafts,prompt:el('videoPrompt').value,seed:el('videoSeed').value,mode:el('videoMode').value,quality:el('videoQuality').value,frames:el('videoDuration').value}));} catch {}
  }
  async function toFrame(blob,maxSide=8192) {
    if (blob.size > 36*1024*1024) throw new Error('Исходный кадр больше 36 МиБ');
    const url = URL.createObjectURL(blob);
    try {
      const image = new Image(); image.src=url; await image.decode();
      if (image.naturalWidth > 8192 || image.naturalHeight > 8192) throw new Error('Кадр больше 8192 пикселей по стороне');
      const scale=Math.min(1,maxSide/Math.max(image.naturalWidth,image.naturalHeight));
      const canvas = document.createElement('canvas'); canvas.width=Math.max(1,Math.round(image.naturalWidth*scale)); canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
      const context=canvas.getContext('2d'); context.fillStyle='#eee'; context.fillRect(0,0,canvas.width,canvas.height); context.drawImage(image,0,0,canvas.width,canvas.height);
      return canvas.toDataURL('image/png');
    } finally {URL.revokeObjectURL(url);}
  }
  async function inputFrame(file, which) {
    if(!file)return;
    const value=await toFrame(file);
    if (which==='source') {source=value;el('videoSourcePreview').src=value;el('videoSourcePreview').hidden=false;}
    else {end=value;el('videoEndPreview').src=value;el('videoEndPreview').hidden=false;}
  }
  async function history() {
    const data=await api('history'); el('videoHistory').replaceChildren();
    for (const row of data.entries.slice(0,20)) {
      const card=document.createElement('div');card.className='video-history-card';
      const title=document.createElement('p');title.textContent=`${engineName(row.engine)} · ${({t2v:'По тексту',i2v:'По изображению',first_last:'Между кадрами',characters:'Персонажи',s2v:'Говорящий персонаж'})[row.mode] || row.mode} · ${({preview:'Предпросмотр',standard:'Стандартное',high:'Высокое'})[row.quality] || row.quality} · Seed ${row.seed} · ${row.state === 'done' ? 'Готово' : row.state === 'cancelled' ? 'Отменено' : 'Ошибка'}`;
      const prompt=document.createElement('p');prompt.textContent=(row.mix_summary?row.mix_summary+' · ':'')+row.original_prompt;
      const reuse=document.createElement('button');reuse.type='button';reuse.className='secondary';reuse.textContent='Вернуть промпт и seed';reuse.onclick=()=>{el('videoPrompt').value=row.original_prompt;el('videoSeed').value=row.seed;draft();};
      card.append(title,prompt,reuse);
      if(row.state==='done') {const play=document.createElement('button');play.type='button';play.className='secondary';play.textContent='Открыть видео';play.onclick=()=>show(row);card.append(play);}
      card.append(StudioUI.deleteGenerationButton('Video', row.id, async () => {
        if (last?.id === row.id) {
          last=null; el('videoPlayer').pause(); el('videoPlayer').removeAttribute('src'); el('videoPlayer').load(); el('videoPlayer').hidden=true;
          el('videoDownload').removeAttribute('href'); el('videoDownload').hidden=true;
          el('videoResultMeta').textContent=''; el('videoSavedPath').textContent='';
          exportSourceId=''; ++previewSeq; ++mixSeq; el('videoExportBlock').hidden=true; el('videoMixBlock').hidden=true;
        }
        el('videoLogs').textContent='Генерация удалена.';
        await history();
      }, () => {
        if (last?.id === row.id) {el('videoPlayer').pause(); el('videoPlayer').removeAttribute('src'); el('videoPlayer').load();}
      }));
      if (row.generation) card.append(StudioUI.generationInfo(row.generation));
      el('videoHistory').append(card);
    }
  }
  function show(row) {
    last=row;const url='/api/video/result?id='+encodeURIComponent(row.id);
    el('videoPlayer').src=url;el('videoPlayer').hidden=false;
    el('videoDownload').href=url;el('videoDownload').download=row.filename || 'Generated Video.mp4';el('videoDownload').hidden=false;
    el('videoResultMeta').textContent=row.metadata ? `${engineName(row.engine)} · ${row.metadata.width}×${row.metadata.height} · Кадры: ${row.metadata.frames} · ${row.metadata.duration} с · ${row.metadata.has_audio ? 'Есть аудиопоток' : 'Без аудиопотока'} · Seed ${row.seed}` : '';
    el('videoSavedPath').textContent=row.saved_path ? 'Сохранено: '+row.saved_path : '';
    exportOpen(row);mixOpen(row);
  }
  async function logs(id) {if(!id){el('videoLogs').textContent='Задача ещё не была запущена. Подробности ошибки показаны в уведомлении.';return;}try {el('videoLogs').textContent=(await api('logs?id='+encodeURIComponent(id))).text;} catch(error) {el('videoLogs').textContent=error.message;}}
  async function poll() {
    if(!active || polling)return;
    polling=true;
    try {
      const row=await api('status?id='+encodeURIComponent(active.id));
      el('videoStatus').textContent=`${engineName(row.engine)} · ${row.continuation_part ? 'Продолжение '+row.continuation_part+' / '+row.continuation_parts+' · ' : ''}${row.scene ? 'Сцена '+row.scene+' / '+row.scenes+' · ' : ''}${row.phase} · прошло ${row.elapsed_sec} с`;
      if (['done','error','cancelled'].includes(row.state)) {
        clearInterval(timer);timer=null;
        active=null;
        el('videoGenerate').disabled=!available;el('videoCancel').disabled=true;
        if(typeof engineReady==='function')el('generate').disabled=!engineReady();
        if(row.state==='done')show(row);
        if(row.state!=='cancelled')StudioUI.notify({module:'Video',id:row.id,error:row.state==='error',
          message:row.error || 'Генерация и монтаж завершены. Видео сохранено.',folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):'',
          onLogs:() => StudioUI.showLogs('Video',row.id,logs)});
        if(row.error)el('videoStatus').textContent=row.error;
        await Promise.all([history(),logs(row.id)]);
      }
    } catch(error) {el('videoStatus').textContent=error.message;}
    finally {polling=false;}
  }
  async function refresh() {
    try {
      const status=await api('availability');
      if(profiles.length)rememberEngine();
      profiles=status.engines || [];exportInit(status.delivery);
      el('videoEngine').replaceChildren(...profiles.map(p=>new Option(StudioUI.modelLabel(p.id,p.name),p.id)));
      el('videoEngine').value=profiles.some(p=>p.id===selectedEngine)?selectedEngine:profiles[0]?.id;
      el('videoEngine').disabled=false;configureEngine();
      el('videoVoiceAvailability').textContent=status.voice_available ? 'Локальная озвучка по образцу: Qwen3-TTS, готова.' : 'Модуль голоса по образцу пока не установлен.';
      el('videoGenerate').disabled=!available || !!active;
      if(status.available) {
        el('videoSaveDir').value=(await api('settings')).save_dir;await history();await prompts();
        const exporting=(await api('export-status')).export;
        if(exporting && !exportJob && !mixJob)(exporting.kind==='mix'?attachMix:attachExport)(exporting);
        const running=await api('active');
        if(running.job && !active) {
          active=running.job;el('videoGenerate').disabled=true;el('videoCancel').disabled=false;el('generate').disabled=true;
          if(!timer)timer=setInterval(poll,1000);poll();
        }
      }
    } catch(error) {el('videoAvailability').textContent=error.message;available=false;el('videoGenerate').disabled=true;StudioUI.setHeader('video', {tone:'bad',status:error.message});}
  }
  async function prompts() {
    const data=await api('prompts');el('videoPrompts').replaceChildren();
    for(const entry of data.entries) {
      const card=document.createElement('div');card.className='video-history-card';const text=document.createElement('p');text.textContent=entry.prompt;
      const use=document.createElement('button');use.type='button';use.className='secondary';use.textContent='Добавить к промпту';use.onclick=()=>{el('videoPrompt').value=[el('videoPrompt').value,entry.prompt].filter(Boolean).join('\n\n');draft();};
      const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Удалить';remove.onclick=async()=>{await api('prompts',{action:'delete',id:entry.id});await prompts();};card.append(text,use,remove);el('videoPrompts').append(card);
    }
  }
  // Export for social networks. The plan (frame placement, notes) is always computed by the server with the same code the export uses.
  let exportJob=null, exportTimer=null, exportPolling=false, exportSourceId=null, exportPlan=null, previewSeq=0, thumb=null;
  const EXPORT_KEY='visualforge.video.export.v1';
  const fmtNum=(n,digits=2)=>Number(n).toLocaleString('ru-RU',{maximumFractionDigits:digits});
  const fmtFps=value=>{const [a,b=1]=String(value).split('/').map(Number);return fmtNum(a/b);};
  const listItem=(text,className)=>{const item=document.createElement('li');item.textContent=text;if(className)item.className=className;return item;};
  function exportDraft(){try{localStorage.setItem(EXPORT_KEY,JSON.stringify({preset:el('videoExportPreset').value,audio:el('videoExportAudio').value}));}catch{}}
  function exportInit(info){
    let saved={};try{saved=JSON.parse(localStorage.getItem(EXPORT_KEY) || '{}');}catch{}
    const keep=el('videoExportPreset').value || saved.preset;
    el('videoExportPreset').replaceChildren(...(info?.presets || []).map(p=>new Option(`${p.label} · ${p.width} × ${p.height}`,p.id)));
    if([...el('videoExportPreset').options].some(o=>o.value===keep))el('videoExportPreset').value=keep;
    if(!el('videoExportAudio').dataset.restored){if(['keep','fill','require'].includes(saved.audio))el('videoExportAudio').value=saved.audio;el('videoExportAudio').dataset.restored='1';}
  }
  function drawExport(plan){
    const canvas=el('videoExportCanvas'),{width:tw,height:th}=plan.target,scale=Math.min(240/tw,300/th);
    canvas.width=Math.max(2,Math.round(tw*scale));canvas.height=Math.max(2,Math.round(th*scale));
    const g=canvas.getContext('2d'),p=plan.picture,x=p.x*scale,y=p.y*scale,w=p.width*scale,h=p.height*scale;
    g.fillStyle='#000';g.fillRect(0,0,canvas.width,canvas.height);
    // A separate, muted video seeked to a representative moment: the main player may not have painted its first frame yet.
    if(thumb && thumb.readyState>=2 && !thumb.seeking){try{g.drawImage(thumb,x,y,w,h);}catch{}}
    else{g.fillStyle='#2b3550';g.fillRect(x,y,w,h);}
    g.strokeStyle='#7968f4';g.lineWidth=1;g.strokeRect(x+.5,y+.5,Math.max(1,w-1),Math.max(1,h-1));
  }
  function exportFacts(plan){
    const p=plan.picture,s=plan.source,t=plan.target;
    const bars=[p.y>0?`${p.y} пикс. сверху и снизу`:'',p.x>0?`${p.x} пикс. слева и справа`:''].filter(Boolean);
    return [
      `Исходник: ${s.width} × ${s.height}, ${fmtFps(s.fps)} fps, ${fmtNum(s.duration,3)} с`,
      `Результат: ${t.width} × ${t.height} (${plan.aspect}), ${t.fps} fps, MP4 H.264`,
      `Кадр в поле: ${p.width} × ${p.height}` + (bars.length?`, чёрные поля: ${bars.join(', ')}`:', без полей'),
      p.scaled_up?`Масштаб ×${fmtNum(p.scale_factor)}: кадр увеличивается, деталей не прибавляется`:p.scale_factor<1?`Масштаб ×${fmtNum(p.scale_factor)}: кадр уменьшается`:'Масштаб без изменения',
      plan.frame_rate_conversion==='none'?'Частота кадров не меняется':`Частота кадров: ${fmtFps(s.fps)} → ${t.fps} fps; кадры повторяются или пропускаются, нового движения нет`,
      ({source:'Звук: из ролика',silence:'Звук: добавится тихая дорожка',none:'Звука в результате не будет'})[plan.audio_origin]
    ];
  }
  async function updateExportPreview(){
    if(!exportSourceId)return;
    const seq=++previewSeq;el('videoExportStart').disabled=true;
    try{
      const plan=await api(`export-preview?id=${encodeURIComponent(exportSourceId)}&preset=${encodeURIComponent(el('videoExportPreset').value)}&audio=${encodeURIComponent(el('videoExportAudio').value)}`);
      if(seq!==previewSeq)return;
      exportPlan=plan;drawExport(plan);
      el('videoExportFacts').replaceChildren(...exportFacts(plan).map(text=>listItem(text)),...(plan.blocked?[listItem(plan.blocked,'export-blocked')]:[]));
      el('videoExportStart').disabled=!!plan.blocked || !!exportJob || !!mixJob;
    }catch(error){
      if(seq!==previewSeq)return;
      exportPlan=null;el('videoExportFacts').replaceChildren(listItem(error.message,'export-blocked'));
    }
  }
  function loadThumb(id){
    if(thumb){thumb.removeAttribute('src');thumb.load();}
    thumb=document.createElement('video');thumb.muted=true;thumb.preload='auto';thumb.src='/api/video/result?id='+encodeURIComponent(id);
    thumb.addEventListener('loadedmetadata',()=>{thumb.currentTime=Math.min((thumb.duration || 0)/3,2);},{once:true});
    thumb.addEventListener('seeked',()=>{if(exportPlan)drawExport(exportPlan);});
  }
  function exportOpen(row){exportSourceId=row.id;el('videoExportBlock').hidden=false;loadThumb(row.id);updateExportPreview();}
  function attachExport(row){
    exportJob=row;el('videoExportStart').disabled=true;el('videoExportCancel').disabled=false;el('videoExportProgress').hidden=false;
    if(!exportTimer)exportTimer=setInterval(pollExport,700);pollExport();
  }
  async function pollExport(){
    if(!exportJob || exportPolling)return;
    exportPolling=true;
    try{
      const row=await api('export-status?id='+encodeURIComponent(exportJob.id));
      exportJob=row;el('videoExportProgress').value=row.progress || 0;
      el('videoExportStatus').textContent=`${row.label} · ${row.phase} · прошло ${row.elapsed_sec} с`;
      if(row.state!=='running'){
        clearInterval(exportTimer);exportTimer=null;exportJob=null;
        el('videoExportCancel').disabled=true;el('videoExportProgress').hidden=true;
        if(row.state==='done')el('videoExportStatus').textContent=`Готово: ${row.saved_path}`;
        else if(row.state==='cancelled')el('videoExportStatus').textContent='Экспорт отменён. Частичный файл удалён.';
        else el('videoExportStatus').textContent=row.error || 'Экспорт не удался';
        if(row.state!=='cancelled')StudioUI.notify({module:'Video',id:row.id,error:row.state==='error',
          message:row.state==='error'?row.error:`Экспорт «${row.label}» завершён: ${row.filename}`,folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):''});
        updateExportPreview();updateMixPreview();
      }
    }catch(error){el('videoExportStatus').textContent=error.message;}
    finally{exportPolling=false;}
  }
  el('videoExportStart').onclick=async()=>{
    if(!exportSourceId || exportJob || mixJob)return;
    el('videoExportStart').disabled=true;el('videoMixStart').disabled=true;el('videoExportStatus').textContent='Запуск экспорта…';el('videoExportProgress').value=0;
    try{attachExport(await api('export',{id:exportSourceId,preset:el('videoExportPreset').value,audio:el('videoExportAudio').value}));}
    catch(error){el('videoExportStatus').textContent=error.message;StudioUI.notify({module:'Video',error:true,message:error.message});updateExportPreview();}
  };
  el('videoExportCancel').onclick=async()=>{
    if(!exportJob)return;el('videoExportCancel').disabled=true;
    try{await api('export-cancel',{id:exportJob.id,cancel_token:exportJob.cancel_token});}catch(error){el('videoExportStatus').textContent=error.message;el('videoExportCancel').disabled=false;}
  };
  el('videoExportPreset').onchange=()=>{exportDraft();updateExportPreview();};
  el('videoExportAudio').onchange=()=>{exportDraft();updateExportPreview();};
  // Music and sound. The server plans the mix with the same code that performs it; the browser only draws the plan.
  let mixJob=null, mixTimer=null, mixPolling=false, mixPlan=null, mixSeq=0, mixDebounce=null, musicList=[], clipHasAudio=false;
  const mixTracks=[];
  const mixNumber=(input,fallback)=>{const value=Number(input.value);return input.value.trim()!=='' && Number.isFinite(value)?value:fallback;};
  const scheduleMix=()=>{clearTimeout(mixDebounce);mixDebounce=setTimeout(updateMixPreview,250);};
  async function loadMusic(){
    try{
      const data=await (await fetch('/api/music/history')).json();
      musicList=(data.entries || []).filter(e=>e.state==='done' && e.saved_path).map(e=>({id:e.id,label:`${String(e.caption || 'Music').slice(0,45)} · ${fmtNum(e.metadata?.duration || e.duration || 0,1)} с`}));
    }catch{musicList=[];}
    for(const track of mixTracks)track.fill();
    scheduleMix();
  }
  function addMixTrack(){
    if(mixTracks.length>=3)return;
    const uid='mix-'+crypto.randomUUID(),card=document.createElement('div');card.className='video-character mix-track';
    const field=(text,id,attrs)=>{const label=document.createElement('label');label.htmlFor=uid+'-'+id;label.textContent=text;const input=document.createElement('input');input.id=uid+'-'+id;Object.assign(input,attrs);input.oninput=input.onchange=scheduleMix;return {label,input};};
    const musicLabel=document.createElement('label');musicLabel.htmlFor=uid+'-music';musicLabel.textContent=`Дорожка ${mixTracks.length+1}: музыка из раздела Music`;
    const select=document.createElement('select');select.id=uid+'-music';select.onchange=scheduleMix;
    const start=field('Старт, с','start',{type:'number',min:'0',max:'3600',step:'0.1',value:'0'});
    const gain=field('Громкость, дБ','gain',{type:'number',min:'-40',max:'12',step:'1',value:clipHasAudio?'-12':'-6'});
    const fadeIn=field('Нарастание, с','fadein',{type:'number',min:'0',max:'30',step:'0.1',value:'0.3'});
    const fadeOut=field('Затухание, с','fadeout',{type:'number',min:'0',max:'30',step:'0.1',value:'1.2'});
    const loop=field('Повторять, пока не кончится ролик','loop',{type:'checkbox'});loop.label.className='mix-check';loop.label.prepend(loop.input);
    const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Убрать дорожку';
    const row=document.createElement('div');row.className='mix-row';row.append(...[start,gain,fadeIn,fadeOut].flatMap(f=>[f.label,f.input]));
    const track={
      fill(){const keep=select.value;select.replaceChildren(new Option(musicList.length?'Выберите музыку…':'Нет готовой музыки: создайте её в разделе Music',''),...musicList.map(m=>new Option(m.label,m.id)));if(musicList.some(m=>m.id===keep))select.value=keep;},
      read(){return {music_id:select.value,label:select.selectedOptions[0]?.textContent.slice(0,60) || 'Music',start:mixNumber(start.input,0),gain_db:mixNumber(gain.input,-12),fade_in:mixNumber(fadeIn.input,0),fade_out:mixNumber(fadeOut.input,0),loop:loop.input.checked};}
    };
    remove.onclick=()=>{mixTracks.splice(mixTracks.indexOf(track),1);card.remove();el('videoMixAdd').disabled=false;scheduleMix();};
    card.append(musicLabel,select,row,loop.label,remove);el('videoMixTracks').append(card);mixTracks.push(track);track.fill();el('videoMixAdd').disabled=mixTracks.length>=3;
    if(mixTracks.length===1 && musicList.length)select.value=musicList[0].id;
    scheduleMix();
  }
  function mixRequest(){
    if(!exportSourceId || !mixTracks.length)return null;
    const tracks=mixTracks.map(t=>t.read());
    if(tracks.some(t=>!t.music_id))return null;
    return {id:exportSourceId,native:{mode:el('videoMixNative').value,gain_db:mixNumber(el('videoMixNativeGain'),0)},tracks,ducking:el('videoMixDucking').checked};
  }
  function drawMix(plan){
    const canvas=el('videoMixCanvas'),lanes=plan.lanes,rowH=26,top=6,width=480,height=top+lanes.length*rowH+22;
    canvas.width=width;canvas.height=height;
    const g=canvas.getContext('2d'),scale=(width-12)/Math.max(plan.duration,.1),x=t=>6+t*scale,colors={video:'#3b4256',native:'#2f7fd0',music:'#7968f4'};
    g.fillStyle='#0b1018';g.fillRect(0,0,width,height);g.font='12px sans-serif';g.textBaseline='middle';
    lanes.forEach((lane,i)=>{
      const y=top+i*rowH,h=rowH-6,x0=x(lane.start),w=Math.max(2,(lane.end-lane.start)*scale);
      g.globalAlpha=lane.kind==='music'?Math.min(1,Math.max(.4,1+lane.gain_db/30)):1;g.fillStyle=colors[lane.kind];g.fillRect(x0,y,w,h);g.globalAlpha=1;
      if(lane.kind==='music'){
        g.fillStyle='rgba(0,0,0,.5)';
        if(lane.fade_in){g.beginPath();g.moveTo(x0,y);g.lineTo(x0+lane.fade_in*scale,y);g.lineTo(x0,y+h);g.fill();}
        if(lane.fade_out){g.beginPath();g.moveTo(x0+w,y);g.lineTo(x0+w-lane.fade_out*scale,y);g.lineTo(x0+w,y+h);g.fill();}
      }
      g.fillStyle='#fff';g.fillText(lane.gain_db===null?lane.label:`${lane.label} · ${fmtNum(lane.gain_db,1)} дБ`,x0+6,y+h/2,Math.max(20,w-10));
    });
    const step=plan.duration<=12?1:plan.duration<=40?5:10,axis=top+lanes.length*rowH;
    g.fillStyle='#9fb0c8';g.strokeStyle='#526077';g.textBaseline='top';
    for(let t=0;t<=plan.duration+1e-6;t+=step){g.beginPath();g.moveTo(x(t),axis-4);g.lineTo(x(t),axis+2);g.stroke();g.fillText(`${t} с`,x(t)-8,axis+4);}
  }
  async function updateMixPreview(){
    const seq=++mixSeq,body=mixRequest();el('videoMixStart').disabled=true;
    if(!body){
      mixPlan=null;el('videoMixFacts').replaceChildren(listItem(mixTracks.length?'Выберите музыку для каждой дорожки':'Добавьте хотя бы одну дорожку музыки'));
      el('videoMixCanvas').width=1;el('videoMixCanvas').height=1;return;
    }
    try{
      const plan=await api('mix-preview',body);
      if(seq!==mixSeq)return;
      mixPlan=plan;drawMix(plan);
      el('videoMixFacts').replaceChildren(listItem(`Длительность ролика: ${fmtNum(plan.duration,2)} с; ${plan.native_in?'звук ролика сохраняется':'звука ролика в итоге не будет'}`),...plan.notes.map(t=>listItem(t)),...(plan.blocked?[listItem(plan.blocked,'export-blocked')]:[]));
      el('videoMixStart').disabled=!!plan.blocked || !!mixJob || !!exportJob;
    }catch(error){if(seq!==mixSeq)return;mixPlan=null;el('videoMixFacts').replaceChildren(listItem(error.message,'export-blocked'));}
  }
  function mixOpen(row){
    clipHasAudio=!!row.metadata?.has_audio;
    el('videoMixBlock').hidden=false;el('videoMixNativeBlock').hidden=!clipHasAudio;
    el('videoMixDucking').disabled=!clipHasAudio;if(!clipHasAudio)el('videoMixDucking').checked=false;
    el('videoMixDucking').title=clipHasAudio?'':'В этом ролике нет собственного звука: приглушать нечего';
    loadMusic();
  }
  function attachMix(row){
    mixJob=row;el('videoMixStart').disabled=true;el('videoExportStart').disabled=true;el('videoMixCancel').disabled=false;el('videoMixProgress').hidden=false;
    if(!mixTimer)mixTimer=setInterval(pollMix,600);pollMix();
  }
  async function pollMix(){
    if(!mixJob || mixPolling)return;
    mixPolling=true;
    try{
      const row=await api('export-status?id='+encodeURIComponent(mixJob.id));
      mixJob=row;el('videoMixProgress').value=row.progress || 0;
      el('videoMixStatus').textContent=`${row.phase} · прошло ${row.elapsed_sec} с`;
      if(row.state!=='running'){
        clearInterval(mixTimer);mixTimer=null;mixJob=null;el('videoMixCancel').disabled=true;el('videoMixProgress').hidden=true;
        if(row.state==='done')el('videoMixStatus').textContent=`Готово: ${row.saved_path}`;
        else if(row.state==='cancelled')el('videoMixStatus').textContent='Микширование отменено. Частичный файл удалён.';
        else el('videoMixStatus').textContent=row.error || 'Микширование не удалось';
        if(row.state!=='cancelled')StudioUI.notify({module:'Video',id:row.id,error:row.state==='error',
          message:row.state==='error'?row.error:`Музыка добавлена: ${row.filename}`,folder:row.state==='done'?StudioUI.parentFolder(row.saved_path):''});
        if(row.state==='done' && row.result_id){await history();show(await api('status?id='+encodeURIComponent(row.result_id)));}
        else updateMixPreview();
        updateExportPreview();
      }
    }catch(error){el('videoMixStatus').textContent=error.message;}
    finally{mixPolling=false;}
  }
  el('videoMixStart').onclick=async()=>{
    const body=mixRequest();if(!body || mixJob || exportJob)return;
    el('videoMixStart').disabled=true;el('videoMixStatus').textContent='Запуск микширования…';el('videoMixProgress').value=0;
    try{attachMix(await api('mix',body));}
    catch(error){el('videoMixStatus').textContent=error.message;StudioUI.notify({module:'Video',error:true,message:error.message});updateMixPreview();}
  };
  el('videoMixCancel').onclick=async()=>{
    if(!mixJob)return;el('videoMixCancel').disabled=true;
    try{await api('export-cancel',{id:mixJob.id,cancel_token:mixJob.cancel_token});}catch(error){el('videoMixStatus').textContent=error.message;el('videoMixCancel').disabled=false;}
  };
  el('videoMixAdd').onclick=addMixTrack;el('videoMixRefresh').onclick=loadMusic;
  el('videoMixNative').onchange=el('videoMixNativeGain').oninput=el('videoMixDucking').onchange=scheduleMix;
  el('imageTab').onclick=()=>module('image');el('videoTab').onclick=()=>module('video');
  el('videoEngine').onchange=()=>{rememberEngine();configureEngine();draft();if(!available)window.repairStudioModel?.('video',selectedEngine);};
  el('videoResolution').onchange=draft;
  el('videoMode').onchange=()=>{modes();draft();};
  el('videoAddCharacter').onclick=addCharacter;
  el('videoDuration').onchange=()=>{modes();draft();};
  el('videoPrompt').oninput=draft;el('videoSeed').oninput=draft;el('videoQuality').onchange=draft;
  async function readSpeech(file){
    if(!file)return null;if(file.size>8*1024*1024)throw new Error('Аудиофайл больше 8 МиБ');
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).replace(/^data:[^;]*;/,'data:audio/wav;'));reader.onerror=()=>reject(new Error('Не удалось прочитать аудиофайл'));reader.readAsDataURL(file);});
  }
  el('videoVoiceSample').onchange=async()=>{speechReference=null;try{speechReference=await readSpeech(el('videoVoiceSample').files[0]);}catch(e){el('videoStatus').textContent=e.message;}};
  el('videoReadySpeech').onchange=async()=>{speechAudio=null;try{speechAudio=await readSpeech(el('videoReadySpeech').files[0]);}catch(e){el('videoStatus').textContent=e.message;}};
  el('videoSpeechMode').onchange=()=>{modes();draft();};el('videoSpeechText').oninput=draft;el('videoSpeechLanguage').onchange=draft;
  el('videoSource').onchange=async()=>{try{await inputFrame(el('videoSource').files[0],'source');}catch(e){el('videoStatus').textContent=e.message;}};
  el('videoEnd').onchange=async()=>{try{await inputFrame(el('videoEnd').files[0],'end');}catch(e){el('videoStatus').textContent=e.message;}};
  function generationRequest() {
    const data={engine:el('videoEngine').value,mode:el('videoMode').value,prompt:el('videoPrompt').value,quality:el('videoQuality').value,seed:el('videoSeed').value,width:Number(el('videoResolution').value.split('x')[0]),height:Number(el('videoResolution').value.split('x')[1]),frames:Number(el('videoDuration').value),fps:profiles.find(p=>p.id===el('videoEngine').value)?.fps || 24};
    if (['i2v','first_last','s2v'].includes(data.mode)) {
      if (!source) throw new Error('Добавьте исходный кадр');
      data.source_image=source;
    }
    if (data.mode==='s2v') {
      data.speech_mode=el('videoSpeechMode').value;
      if (data.speech_mode==='clone') {
        if (!speechReference) throw new Error('Добавьте образец голоса');
        if (!el('videoSpeechText').value.trim()) throw new Error('Введите короткую реплику');
        data.voice_audio=speechReference;
        data.speech_text=el('videoSpeechText').value;
        data.language=el('videoSpeechLanguage').value;
      } else {
        if (!speechAudio) throw new Error('Добавьте готовую запись речи');
        data.source_audio=speechAudio;
      }
    }
    if (data.mode==='first_last') {
      if (!end) throw new Error('Добавьте конечный кадр');
      data.end_image=end;
    }
    if (data.mode==='characters') {
      if (!characters.length || characters.some(p=>!p.image)) throw new Error('Добавьте изображение каждого персонажа');
      if (characters.some(p=>p.speech_text.trim() && !p.voice_audio)) throw new Error('Добавьте образец голоса для каждой реплики');
      data.characters=characters;
    }
    return data;
  }
  el('videoForm').onsubmit=async event=>{
    event.preventDefault();if(active)return;
    el('videoGenerate').disabled=true;el('videoStatus').textContent='Проверка входных данных и свободной GPU…';
    try {
      const data=generationRequest();
      active=await api('generate',data);el('videoSeed').value=active.seed;draft();el('videoCancel').disabled=false;el('generate').disabled=true;
      timer=setInterval(poll,1000);poll();
    } catch(error) {el('videoStatus').textContent=error.message;el('videoGenerate').disabled=!available;StudioUI.notify({module:'Video',error:true,message:error.message,onLogs:()=>StudioUI.showLogs('Video','',logs)});}
  };
  el('videoCancel').onclick=async()=>{if(!active)return;el('videoCancel').disabled=true;try{await api('cancel',{id:active.id,cancel_token:active.cancel_token});}catch(e){el('videoStatus').textContent=e.message;el('videoCancel').disabled=false;}};
  async function saveFolder(path) {
    const result=await api('settings',{save_dir:path});
    el('videoSaveDir').value=result.save_dir;
    el('videoStatus').textContent='Папка Video сохранена.';
  }
  el('videoSaveSettings').onclick=async()=>{try{await saveFolder(el('videoSaveDir').value);}catch(e){el('videoStatus').textContent=e.message;}};
  el('videoChooseSaveDir').onclick=()=>window.chooseStudioFolder(el('videoSaveDir').value,saveFolder,el('videoChooseSaveDir'));
  el('videoOpenSaveDir').onclick=async()=>{try{await StudioUI.openFolder(el('videoSaveDir').value);}catch(e){el('videoStatus').textContent=e.message;}};
  el('videoCopySaveDir').onclick=()=>navigator.clipboard.writeText(el('videoSaveDir').value);
  el('videoSavePrompt').onclick=async()=>{try{await api('prompts',{action:'add',prompt:el('videoPrompt').value});await prompts();}catch(e){el('videoStatus').textContent=e.message;}};
  el('videoRefreshLogs').onclick=()=>logs(active?.id || last?.id || '');
  el('videoCopyLogs').onclick=()=>navigator.clipboard.writeText(el('videoLogs').textContent);
  window.addEventListener('beforeunload',event=>{if(active || exportJob || mixJob){event.preventDefault();event.returnValue='';}});
  async function fetchSourceImage(url) {
    const response=await fetch(url);
    if (!response.ok) throw new Error('Исходное изображение недоступно');
    return response.blob();
  }
  window.addEventListener('visualforge:use-image-in-video',async event=>{
    try{await inputFrame(await fetchSourceImage(event.detail.url),'source');if(!el('videoPrompt').value)el('videoPrompt').value=event.detail.prompt || '';el('videoMode').value='i2v';modes();draft();module('video');}catch(error){el('videoStatus').textContent=error.message;module('video');}
  });
  function imageActions() {
    for(const container of [el('images'),el('historyCards')])if(container)for(const link of container.querySelectorAll('a[href^="/api/result"]')){
      if(link.dataset.videoAction)continue;link.dataset.videoAction='1';const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Использовать в Video';button.onclick=()=>window.dispatchEvent(new CustomEvent('visualforge:use-image-in-video',{detail:{url:link.href}}));link.parentElement.append(button);
    }
  }
  for(const id of ['images','historyCards'])if(el(id))new MutationObserver(imageActions).observe(el(id),{childList:true,subtree:true});
  try{const value=JSON.parse(localStorage.getItem('visualforge.video.draft.v1') || '{}');engineDrafts=value.engineDrafts || {};if(value.speech){el('videoSpeechText').value=value.speech.text || '';if(['clone','audio'].includes(value.speech.mode))el('videoSpeechMode').value=value.speech.mode;if(['Russian','English','Auto'].includes(value.speech.language))el('videoSpeechLanguage').value=value.speech.language;}selectedEngine=value.engine || 'freevideo-h3-int8';el('videoEngine').add(new Option('Wan 2.2 TI2V-5B','wan22-ti2v-5b'));el('videoEngine').add(new Option('Wan 2.2 S2V-14B','wan22-s2v-14b'));el('videoEngine').value=selectedEngine;el('videoPrompt').value=value.prompt || '';el('videoSeed').value=value.seed || '';if(['t2v','i2v','first_last','characters','s2v'].includes(value.mode))el('videoMode').value=value.mode;if(['preview','standard','high'].includes(value.quality))el('videoQuality').value=value.quality;if(['39','243','1440'].includes(value.frames))el('videoDuration').value=value.frames;}catch{}
  if(!engineDrafts[selectedEngine])rememberEngine();
  modes();imageActions();
})();
