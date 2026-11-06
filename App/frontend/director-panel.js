'use strict';
// The Director: the entry "Режиссёр" of the Video engine list (docs/VisualForge-Director.md). The person attaches materials (pictures of heroes, places and objects, reference videos,
// songs, voices; everything is optional), says how long the film is and how many scenes it has, and describes the film; Gemma writes a plan, the person may edit it, and the studio
// shoots the film scene after scene. The video panel shows this panel instead of its own form while the Director is the chosen engine (event `visualforge:director-engine`) and opens the
// finished film from the Video history.
(() => {
  const root = document.getElementById('directorPanel');
  if (!root) return;
  const MIN_SECONDS = 5, MAX_SECONDS = 900, MIN_SCENES = 2, SCENES_PER_MINUTE = 5;
  // The same rule as `scene_range` in App/server/director/plan_rules.py: two scenes below a minute (level 0), then from one for every minute (a scene lasts a minute at most) up to five.
  // How long a scene is when nobody chose the number of scenes: a scene of about this many seconds (the advice on running time counts the same). The least number of scenes (a minute each)
  // would make a film of ten minutes ten long takes.
  const SCENE_PACE = 15;
  let scenesTouched = false;
  const sceneRange = seconds => seconds < 60 ? [MIN_SCENES, MIN_SCENES] : [Math.max(MIN_SCENES, Math.ceil(seconds / 60)), Math.max(MIN_SCENES, Math.floor(seconds * SCENES_PER_MINUTE / 60))];
  const DRAFT_KEY = 'visualforge.director.draft.v1', CONSENT_KEY = 'visualforge.director.consent.v1', ULTRA_KEY = 'visualforge.director.ultra.v1';
  const TRANSITIONS = ['cut', 'fade', 'dissolve', 'fadeblack', 'wipeleft', 'slideright', 'circleopen', 'smoothleft'];
  // The reasons of the advice on running time that the server can give (`ADVICE_CODES` in plan_rules.py): each has a sentence `director.panel_advice_<code>`.
  const ADVICE = ['baseline', 'places', 'voices', 'songs', 'song_excerpt'];
  const FINISHED = ['done', 'error', 'cancelled', 'interrupted'];
  const IMAGE_TYPE = /\.(jpe?g|png|webp|gif|bmp|heic|heif|avif|tiff?|psd|tga|ico)$/i, VIDEO_TYPE = /\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|mts|ts)$/i, AUDIO_TYPE = /\.(mp3|wav|flac|m4a|aac|ogg|opus|wma|aiff?|amr)$/i;
  const ACCEPT = {photo: 'image/*,.heic,.heif,.avif,.tif,.tiff,.psd,.tga,.ico,.bmp,.gif,.webp', video: 'video/*,.mkv,.avi,.mov,.wmv,.flv,.webm,.m4v,.mts,.ts',
                  audio: 'audio/*,.mp3,.wav,.flac,.m4a,.aac,.ogg,.opus,.wma,.aif,.aiff', voice: 'audio/*,.mp3,.wav,.flac,.m4a,.aac,.ogg,.opus,.wma,.aif,.aiff'};
  let limits = {max_video_bytes: 2 * 1024 ** 3, groups: []}, draft = '', items = [], advice = null, plan = null, run = null, timer = null, working = false, view = {}, announced = '';
  let available = true, speechReady = true;
  const pending = [];

  const say = (node, key, values) => {StudioI18n.bind(node, 'textContent', () => StudioI18n.t(key, values || []), true); return node;};
  const h = (tag, attrs = {}, ...kids) => {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {if (name === 'class') node.className = value; else if (name in node) node[name] = value; else node.setAttribute(name, value);}
    node.append(...kids);
    return node;
  };
  const api = async (route, body) => {
    let response, value;
    try {response = await fetch('/api/director/' + route, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});}
    catch {throw new Error(StudioI18n.t('director.panel_studio_unreachable'));}
    try {value = await response.json();} catch {throw new Error(StudioI18n.t('director.panel_bad_answer'));}
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || StudioI18n.t('director.panel_bad_answer'));
    return value;
  };
  const clock = seconds => {
    const whole = Math.round(seconds), hours = Math.floor(whole / 3600), minutes = Math.floor(whole % 3600 / 60), rest = String(whole % 60).padStart(2, '0');
    return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
  };
  const language = () => String(StudioI18n.locale || 'ru').startsWith('en') ? 'English' : 'Russian';
  // The language the film is described in: Russian when the words have at least as many Cyrillic letters as Latin ones (the rule of `idea_language` in director/plan.py). The voices speak it.
  const ideaLanguage = text => {const cyrillic = (text.match(/[А-Яа-яЁё]/g) || []).length, latin = (text.match(/[A-Za-z]/g) || []).length; return cyrillic && cyrillic >= latin ? 'Russian' : 'English';};

  // ---------- names of the slots ----------
  // The first attachment of a group, the second and the others have their own names ("Диктор", "Диктор 2"; "Главный герой", "Второй главный герой", "Персонаж 3").
  const slotName = (group, position) => StudioI18n.t(`director.slot_${group}_${position === 1 ? 'first' : position === 2 ? 'second' : 'other'}`, [position]);
  const positionOf = item => items.filter(row => row.group === item.group).findIndex(row => row.number === item.number) + 1;
  const titleOf = item => slotName(item.group, positionOf(item)) + (item.label ? ' · ' + item.label : '');
  const groupRow = id => limits.groups.find(row => row.id === id);

  // ---------- the markup ----------
  const intro = say(h('p', {class: 'muted'}), 'director.panel_intro_text');
  const materialsHint = say(h('p', {class: 'preset-hint'}), 'director.panel_materials_hint');
  const materialsBox = h('div', {class: 'director-materials'});
  const materialsStatus = h('p', {class: 'status', role: 'status'});
  const consent = h('input', {type: 'checkbox', id: 'directorConsent'});
  try {consent.checked = localStorage.getItem(CONSENT_KEY) === '1';} catch {/* an unremembered consent is only asked again */}
  const secondsInput = h('input', {id: 'directorSeconds', type: 'number', min: MIN_SECONDS, max: MAX_SECONDS, step: 1, value: 10});
  const secondsText = h('span', {class: 'muted'});
  const scenesInput = h('input', {id: 'directorScenes', type: 'number', min: MIN_SCENES, step: 1, value: MIN_SCENES});
  const orientation = h('select', {id: 'directorOrientation'});
  const quality = h('select', {id: 'directorQuality'});
  // Ultra (on unless the person turned it off) asks the planner and the production line for the best quality; off is two or three times faster. The server's `PROFILES` say what each means.
  const ultra = h('input', {type: 'checkbox', id: 'directorUltra', checked: true});
  try {ultra.checked = localStorage.getItem(ULTRA_KEY) !== '0';} catch {/* an unremembered choice is the default: on */}
  const ultraHint = say(h('p', {class: 'preset-hint'}), 'director.panel_ultra_hint');
  const sceneHint = h('p', {class: 'preset-hint', id: 'directorSceneHint'});
  const adviceBox = h('div', {class: 'director-advice', id: 'directorAdvice', hidden: true});
  const promptBox = h('textarea', {id: 'directorPrompt', rows: 7, maxLength: 5000});
  const planButton = say(h('button', {id: 'directorPlan', type: 'button'}), 'director.panel_make_plan');
  const planStatus = h('p', {class: 'status', role: 'status', 'aria-live': 'polite'});
  const planBox = h('div', {class: 'director-plan', hidden: true});
  const runBox = h('div', {class: 'director-run', hidden: true});
  const field = (key, input, extra) => h('label', {class: 'director-field'}, say(h('span'), key), input, ...(extra ? [extra] : []));
  const option = (value, key) => {const node = h('option', {value}); say(node, key); return node;};
  orientation.append(option('', 'director.panel_auto_choice'), option('landscape', 'director.panel_landscape_choice'), option('portrait', 'director.panel_portrait_choice'));
  quality.append(option('', 'director.panel_auto_choice'), option('draft', 'director.panel_quality_draft'), option('standard', 'director.panel_quality_standard'), option('max', 'director.panel_quality_max'));
  root.append(
    intro,
    say(h('h3'), 'director.panel_materials_title'), materialsHint, materialsBox, materialsStatus,
    say(h('h3'), 'director.panel_film_title'),
    h('div', {class: 'director-fields'}, field('director.panel_seconds_label', secondsInput, secondsText), field('director.panel_scenes_label', scenesInput), field('director.panel_format_label', orientation), field('director.panel_quality_label', quality)),
    h('label', {class: 'director-check director-ultra'}, ultra, say(h('strong'), 'director.panel_ultra_title')), ultraHint,
    sceneHint, adviceBox,
    h('label', {class: 'director-field wide'}, say(h('span'), 'director.panel_prompt_label'), promptBox),
    h('div', {class: 'actions'}, planButton), planStatus, planBox, runBox);
  StudioI18n.bind(promptBox, 'placeholder', () => StudioI18n.t('director.panel_prompt_placeholder'), true);
  consent.onchange = () => {try {localStorage.setItem(CONSENT_KEY, consent.checked ? '1' : '0');} catch {/* see above */} renderMaterials();};
  ultra.onchange = () => {try {localStorage.setItem(ULTRA_KEY, ultra.checked ? '1' : '0');} catch {/* the choice is only forgotten after a reload */}};

  // ---------- the materials ----------
  function report(node, error) {StudioI18n.bind(node, 'textContent', () => StudioI18n.remote(error.message || String(error)), true);}
  function setDraft(value) {
    items = value.items || [];
    advice = value.advice || null;
    renderAdvice();
  }
  const kindOf = file => file.type.startsWith('image/') || IMAGE_TYPE.test(file.name) ? 'photo' : file.type.startsWith('video/') || VIDEO_TYPE.test(file.name) ? 'video'
    : file.type.startsWith('audio/') || AUDIO_TYPE.test(file.name) ? 'audio' : 'other';
  // A picture block takes pictures (and files the browser cannot name, such as HEIC), a video block takes videos, a sound block takes sound (a video file may carry the song).
  const fits = (file, media) => {
    const kind = kindOf(file);
    return media === 'photo' ? kind === 'photo' || kind === 'other' : media === 'video' ? kind === 'video' : kind === 'audio' || kind === 'video' || kind === 'other';
  };
  const byteLimit = media => ({photo: limits.max_photo_bytes || 100 * 1024 ** 2, video: limits.max_video_bytes, audio: limits.max_song_bytes || 1536 * 1024 ** 2, voice: limits.max_voice_bytes || 200 * 1024 ** 2}[media]);
  const mediaTail = {photo: 'director.panel_converting_photo', video: 'director.panel_converting_video', audio: 'director.panel_converting_song', voice: 'director.panel_converting_voice'};

  function upload(file, group) {
    return new Promise((resolve, reject) => {
      const bar = h('progress', {max: 1, value: 0}), note = h('span', {class: 'muted', textContent: file.name});
      const card = h('div', {class: 'director-item pending'}, bar, note);
      const entry = {group: group.id, node: card};
      pending.push(entry);
      slotsOf(group.id)?.append(card);
      const finish = () => {card.remove(); pending.splice(pending.indexOf(entry), 1);};
      const request = new XMLHttpRequest();
      request.open('POST', `/api/director/attach?draft=${draft}&group=${group.id}&name=${encodeURIComponent(file.name)}${group.media === 'voice' ? '&consent=1' : ''}`);
      request.upload.onprogress = event => {if (event.lengthComputable) bar.value = event.loaded / event.total;};
      request.upload.onload = () => {bar.removeAttribute('value'); say(note, mediaTail[group.media]);};
      request.onload = () => {
        finish();
        let value = {};
        try {value = JSON.parse(request.responseText);} catch {/* the message below says it */}
        if (request.status === 200) resolve(value); else reject(new Error(StudioI18n.remote(value.error) || StudioI18n.t('director.panel_upload_failed')));
      };
      request.onerror = () => {finish(); reject(new Error(StudioI18n.t('director.panel_upload_failed')));};
      request.send(file);
    });
  }
  async function addFiles(files, groupId) {
    StudioI18n.bind(materialsStatus, 'textContent', () => '', true);
    const group = groupRow(groupId);
    for (const file of files) {
      const have = items.filter(row => row.group === groupId).length;
      const refusal = have >= group.limit ? StudioI18n.t('director.panel_group_full', [group.limit])
        : !fits(file, group.media) ? StudioI18n.t('director.panel_wrong_media')
        : group.media === 'voice' && !consent.checked ? StudioI18n.t('director.panel_voice_consent_needed')
        : file.size > byteLimit(group.media) ? StudioI18n.t('director.panel_file_too_big', [Math.round(byteLimit(group.media) / 1024 ** 2)])
        : '';
      if (refusal) report(materialsStatus, refusal);
      else {try {setDraft(await upload(file, group));} catch (error) {report(materialsStatus, error);}}
      renderMaterials();
    }
  }
  // Files dropped or pasted outside any block go to the first block of their kind that has room: pictures to the heroes, a video to the reference videos, a sound to the songs.
  function placeLoose(files) {
    for (const file of files) {
      const kind = kindOf(file), media = kind === 'video' ? 'video' : kind === 'audio' ? 'audio' : 'photo';
      const group = limits.groups.find(row => row.media === media && items.filter(item => item.group === row.id).length < row.limit) || limits.groups.find(row => row.media === media);
      if (group) addFiles([file], group.id);
    }
  }

  const slotsOf = id => materialsBox.querySelector(`[data-group="${id}"] .director-slots`);
  function tile(item) {
    const remove = say(h('button', {type: 'button', class: 'secondary'}), 'director.panel_remove_button');
    remove.onclick = async () => {try {setDraft(await api('detach', {draft, number: item.number}));} catch (error) {report(materialsStatus, error);} renderMaterials();};
    const label = h('input', {type: 'text', maxLength: 60, value: item.label || '', class: 'director-label'});
    StudioI18n.bind(label, 'placeholder', () => StudioI18n.t('director.panel_label_placeholder'), true);
    StudioI18n.bind(label, 'title', () => StudioI18n.t('director.panel_label_placeholder'), true);
    label.onchange = async () => {try {setDraft(await api('label', {draft, number: item.number, text: label.value}));} catch (error) {report(materialsStatus, error);}};
    const sound = item.kind === 'audio' || item.kind === 'voice';
    const info = item.kind === 'video' ? StudioI18n.t('director.panel_video_info', [clock(item.seconds), item.width, item.height]) : sound ? clock(item.seconds)
      : StudioI18n.t('director.panel_photo_info', [item.width, item.height]);
    const name = h('strong'), where = positionOf(item);
    StudioI18n.bind(name, 'textContent', () => slotName(item.group, where) + ' · #' + item.number, true);
    return h('div', {class: 'director-item' + (sound ? ' sound' : ''), 'data-number': item.number}, h('img', {src: item.thumb, alt: ''}),
             ...(sound ? [h('audio', {controls: true, preload: 'none', src: `/api/director/media?draft=${draft}&number=${item.number}`})] : []),
             name, h('span', {class: 'director-name', textContent: item.name, title: item.name}), h('span', {class: 'muted', textContent: info}), label, remove);
  }
  function adder(group, count) {
    const input = h('input', {type: 'file', multiple: true, hidden: true, accept: ACCEPT[group.media]});
    const button = say(h('button', {type: 'button', class: 'director-adder'}), `director.group_${group.id}_add`);
    const name = h('strong'), blocked = !available || (group.media === 'voice' && !(consent.checked && speechReady));
    StudioI18n.bind(name, 'textContent', () => slotName(group.id, count + 1), true);
    button.disabled = blocked;
    button.onclick = () => input.click();
    input.onchange = () => {const files = [...input.files]; input.value = ''; addFiles(files, group.id);};
    return h('div', {class: 'director-item adder'}, name, button, input);
  }
  function groupBlock(group) {
    const own = items.filter(item => item.group === group.id);
    const slots = h('div', {class: 'director-slots'}, ...own.map(tile), ...(own.length < group.limit ? [adder(group, own.length)] : []));
    const count = h('span', {class: 'muted', textContent: `${own.length} / ${group.limit}`});
    const block = h('section', {class: 'director-group', 'data-group': group.id}, h('h4', {}, say(h('span'), `director.group_${group.id}_title`), count),
                    say(h('p', {class: 'preset-hint'}), `director.group_${group.id}_hint`), slots);
    block.addEventListener('dragover', event => {if (event.dataTransfer?.types?.includes('Files')) {event.preventDefault(); event.stopPropagation(); block.classList.add('dropping');}});
    block.addEventListener('dragleave', () => block.classList.remove('dropping'));
    block.addEventListener('drop', event => {
      block.classList.remove('dropping');
      if (event.dataTransfer?.files?.length) {event.preventDefault(); event.stopPropagation(); addFiles([...event.dataTransfer.files], group.id);}
    });
    return block;
  }
  function renderMaterials() {
    const blocks = [];
    for (const group of limits.groups) {
      if (group.media === 'voice' && !blocks.some(node => node.classList?.contains('director-consent'))) {
        const warning = say(h('p', {class: 'status', hidden: speechReady}), 'director.panel_voice_engine_missing');
        blocks.push(h('div', {class: 'director-consent'}, h('label', {class: 'director-check'}, consent, say(h('span'), 'director.panel_voice_consent')), warning));
      }
      blocks.push(groupBlock(group));
    }
    materialsBox.replaceChildren(...blocks);
    for (const entry of pending) slotsOf(entry.group)?.append(entry.node);
  }
  root.addEventListener('paste', event => {const files = [...(event.clipboardData?.files || [])]; if (files.length) {event.preventDefault(); placeLoose(files);}});
  root.addEventListener('dragover', event => {if (event.dataTransfer?.types?.includes('Files')) event.preventDefault();});
  root.addEventListener('drop', event => {if (event.dataTransfer?.files?.length) {event.preventDefault(); placeLoose([...event.dataTransfer.files]);}});

  // ---------- length and scenes ----------
  function syncFields(settle) {
    let seconds = Number(secondsInput.value);
    const valid = Number.isFinite(seconds) && seconds >= MIN_SECONDS && seconds <= MAX_SECONDS;
    if (settle) {seconds = Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, Math.round(seconds || MIN_SECONDS))); secondsInput.value = seconds;}
    const known = valid || settle;
    const [fewest, most] = sceneRange(known ? seconds : MIN_SECONDS);
    scenesInput.min = fewest; scenesInput.max = most; scenesInput.readOnly = fewest === most;
    if (fewest === most) scenesInput.value = fewest;
    else if (!scenesTouched) scenesInput.value = Math.min(most, Math.max(fewest, Math.round(seconds / SCENE_PACE)));          // the person has not chosen: a scene of about fifteen seconds
    else if (settle || Number(scenesInput.value) > most) scenesInput.value = Math.min(most, Math.max(fewest, Math.round(Number(scenesInput.value) || fewest)));
    secondsText.textContent = known ? '= ' + clock(seconds) : '';
    if (!known) {say(sceneHint, 'director.panel_seconds_range', [MIN_SECONDS, MAX_SECONDS]); renderAdvice(); return;}
    if (seconds < 60) say(sceneHint, 'director.panel_scene_hint_level_zero', [MIN_SCENES, SCENES_PER_MINUTE]);
    else say(sceneHint, 'director.panel_scene_hint_range', [clock(seconds), fewest, most, SCENES_PER_MINUTE]);
    renderAdvice();
  }
  // The running time that suits the attached materials (the server counts it, see `advise` in plan_rules.py): said under the length, with the reasons and a button that takes it.
  const adviceValues = reason => ({baseline: [clock(reason.low), clock(reason.high)], places: [reason.count, clock(reason.seconds)], voices: [reason.count, clock(reason.seconds)],
                                   songs: [reason.count, clock(reason.seconds)], song_excerpt: [clock(reason.seconds)]}[reason.code] || []);
  function renderAdvice() {
    if (!advice) {adviceBox.hidden = true; return;}
    const seconds = Number(secondsInput.value), valid = Number.isFinite(seconds) && seconds >= MIN_SECONDS;
    const inside = valid && seconds >= advice.low && seconds <= advice.high;
    const apply = say(h('button', {type: 'button', class: 'secondary', id: 'directorAdviceApply'}), 'director.panel_advice_apply', [clock(advice.ideal)]);
    apply.onclick = () => {scenesTouched = true; secondsInput.value = advice.ideal; syncFields(true); scenesInput.value = advice.scenes; syncFields(true);};      // the second pass keeps the scenes inside what the length allows
    const reasons = h('ul', {class: 'director-reasons'}, ...advice.reasons.filter(reason => ADVICE.includes(reason.code)).map(reason => say(h('li'), 'director.panel_advice_' + reason.code, adviceValues(reason))));
    adviceBox.replaceChildren(advice.low === advice.high ? say(h('strong'), 'director.panel_advice_exact', [clock(advice.low)]) : say(h('strong'), 'director.panel_advice_range', [clock(advice.low), clock(advice.high)]), ' ',
                              valid ? say(h('span', {class: inside ? 'ok' : 'warn'}), inside ? 'director.panel_advice_inside' : 'director.panel_advice_outside', [clock(seconds)]) : h('span'),
                              reasons, ...(inside && seconds === advice.ideal ? [] : [apply]));
    adviceBox.hidden = false;
  }
  secondsInput.addEventListener('input', () => syncFields(false));
  secondsInput.addEventListener('change', () => syncFields(true));
  scenesInput.addEventListener('input', () => {scenesTouched = true;});
  scenesInput.addEventListener('change', () => {scenesTouched = true; syncFields(true);});
  syncFields(true);

  // ---------- the plan ----------
  // Where a plan of many scenes has come to (it takes minutes): the server says which part Gemma is writing, the page tells the person.
  const PLANNING = {look: 'director.panel_planning_look', plan: 'director.panel_planning_plan', outline: 'director.panel_planning_outline', scenes: 'director.panel_planning_scenes'};
  async function watchPlanning() {
    try {
      const state = await api('planning');
      if (working && PLANNING[state.stage]) say(planStatus, PLANNING[state.stage], [state.first, state.last, state.total]);
    } catch {/* the next look will do */}
  }
  async function makePlan() {
    syncFields(true);
    const body = {draft, prompt: promptBox.value.trim(), seconds: Number(secondsInput.value), scenes: Number(scenesInput.value), language: language(),
                  orientation: orientation.value || undefined, quality: quality.value || undefined, ultra: ultra.checked};
    if (!body.prompt) {say(planStatus, 'director.panel_enter_idea'); return;}
    working = true; planButton.disabled = true; planBox.hidden = true;
    say(planStatus, 'director.panel_planning_status');
    const watching = setInterval(watchPlanning, 2000);
    try {plan = await api('plan', body); renderPlan(body); say(planStatus, 'director.panel_plan_ready');}
    catch (error) {report(planStatus, error);}
    finally {clearInterval(watching); working = false; planButton.disabled = false;}
  }
  planButton.onclick = makePlan;

  function songRow(cue) {
    const item = items.find(row => row.number === cue.song);
    const use = h('input', {type: 'checkbox', checked: true}), number = (value, min, max, step) => h('input', {type: 'number', value, min, max, step});
    const row = {song: cue.song, use, offset: number(cue.offset, 0, 3600, 0.1), start: number(cue.start, 0, MAX_SECONDS, 0.1), seconds: number(cue.seconds, 2, MAX_SECONDS, 0.1),
                 gain: number(cue.gain_db, -24, 0, 1), fade_in: cue.fade_in, fade_out: cue.fade_out};
    const title = h('span');
    StudioI18n.bind(title, 'textContent', () => StudioI18n.t('director.panel_plan_song_use', [item ? titleOf(item) : '#' + cue.song]), true);
    view.songs.push(row);
    return h('div', {class: 'director-scene'}, h('label', {class: 'director-check'}, use, title),
             h('div', {class: 'director-fields'}, field('director.panel_plan_song_offset', row.offset), field('director.panel_plan_song_start', row.start), field('director.panel_scene_seconds', row.seconds),
               field('director.panel_plan_music_gain', row.gain)));
  }
  function speechRow(scene, line) {
    const voices = items.filter(item => item.kind === 'voice');
    const voice = h('select');
    for (const item of voices) {const node = h('option', {value: item.number}); StudioI18n.bind(node, 'textContent', () => titleOf(item), true); voice.append(node);}
    voice.value = line.voice;
    const text = h('textarea', {rows: 2, maxLength: 300, value: line.text});
    const row = {scene, voice, text, language: line.language || ideaLanguage(promptBox.value), delay: line.delay ?? 0.3, gain_db: line.gain_db ?? 0};
    const remove = say(h('button', {type: 'button', class: 'secondary'}), 'director.panel_speech_remove');
    const node = h('div', {class: 'director-line'}, field('director.panel_speech_voice', voice), field('director.panel_speech_text', text), remove);
    remove.onclick = () => {node.remove(); view.speech.splice(view.speech.indexOf(row), 1);};
    view.speech.push(row);
    return node;
  }
  function renderPlan(request) {
    view = {request, scenes: [], songs: [], speech: []};
    const title = h('input', {type: 'text', value: plan.title, maxLength: 120});
    const style = h('input', {type: 'text', value: plan.style.phrase, maxLength: 300});
    const number = (value, min, max, step) => h('input', {type: 'number', value, min, max, step});
    const warmth = number(plan.style.warmth, -1, 1, 0.05), contrast = number(plan.style.contrast, 0.8, 1.3, 0.05), saturation = number(plan.style.saturation, 0, 1.4, 0.05);
    const format = h('select'), level = h('select');
    format.append(option('landscape', 'director.panel_landscape_choice'), option('portrait', 'director.panel_portrait_choice'));
    level.append(option('draft', 'director.panel_quality_draft'), option('standard', 'director.panel_quality_standard'), option('max', 'director.panel_quality_max'));
    format.value = plan.orientation; level.value = plan.quality;
    Object.assign(view, {title, style, warmth, contrast, saturation, format, level});
    const kids = [say(h('h3'), 'director.panel_plan_title'), h('p', {textContent: plan.summary}), say(h('p', {class: 'muted'}), plan.ultra ? 'director.panel_plan_mode_ultra' : 'director.panel_plan_mode_normal'),
                  ...(plan.notes || []).map(note => {const node = h('p', {class: 'status'}); StudioI18n.bind(node, 'textContent', () => StudioI18n.remote(note), true); return node;}),
                  h('div', {class: 'director-fields'}, field('director.panel_plan_name', title), field('director.panel_format_label', format), field('director.panel_quality_label', level)),
                  h('div', {class: 'director-fields'}, field('director.panel_plan_style', style), field('director.panel_plan_warmth', warmth), field('director.panel_plan_contrast', contrast),
                    field('director.panel_plan_saturation', saturation))];
    if (plan.music) {
      const use = h('input', {type: 'checkbox', checked: true}), caption = h('textarea', {rows: 2, value: plan.music.caption, maxLength: 1500});
      const lyrics = h('textarea', {rows: 3, value: plan.music.lyrics, maxLength: 3000, hidden: plan.music.mode !== 'song'}), gain = number(plan.music.gain_db, -30, -6, 1);
      Object.assign(view, {music: {use, caption, lyrics, gain}});
      kids.push(say(h('h3'), 'director.panel_plan_music'), h('label', {class: 'director-check'}, use, say(h('span'), 'director.panel_plan_music_use')),
                h('p', {class: 'muted', textContent: `${plan.music.engine} · ${plan.music.mode} · ${plan.music.seconds} s`}), field('director.panel_plan_music_caption', caption),
                ...(plan.music.mode === 'song' ? [field('director.panel_plan_music_lyrics', lyrics)] : []), field('director.panel_plan_music_gain', gain));
    } else if (!(plan.songs || []).length) {kids.push(say(h('p', {class: 'muted'}), 'director.panel_plan_no_music'));}
    if ((plan.songs || []).length) kids.push(say(h('h3'), 'director.panel_plan_songs'), ...plan.songs.map(songRow));
    kids.push(say(h('h3'), 'director.panel_plan_scenes'), h('p', {class: 'muted'}, '', (view.sum = h('span'))));
    const speakers = items.some(item => item.kind === 'voice') && speechReady;
    plan.scenes.forEach((scene, index) => {
      const length = number(scene.seconds, 2, 60, 0.1), prompt = h('textarea', {rows: 5, value: scene.prompt, maxLength: 1500}), transition = h('select');
      for (const name of TRANSITIONS) transition.append(option(name, 'director.transition_kind_' + name));
      transition.value = scene.transition; transition.disabled = index === plan.scenes.length - 1;
      const keyframe = scene.keyframe ? h('textarea', {rows: 2, value: scene.keyframe, maxLength: 600}) : null;
      view.scenes.push({length, prompt, transition, keyframe, references: scene.references});
      length.addEventListener('input', sumUp);
      const lines = h('div', {class: 'director-lines'}, ...(plan.speech || []).filter(line => line.scene === index + 1).map(line => speechRow(index + 1, line)));
      const addLine = say(h('button', {type: 'button', class: 'secondary'}), 'director.panel_speech_add');
      addLine.onclick = () => lines.append(speechRow(index + 1, {voice: items.find(item => item.kind === 'voice').number, text: ''}));
      kids.push(h('div', {class: 'director-scene'}, say(h('strong'), 'director.panel_scene_number', [index + 1]),
                  h('div', {class: 'director-fields'}, field('director.panel_scene_seconds', length), field('director.panel_scene_transition', transition)),
                  scene.references.length ? h('p', {class: 'muted', textContent: StudioI18n.t('director.panel_scene_pictures', [scene.references.map(n => '#' + n).join(', ')])}) : h('span'),
                  ...(keyframe ? [field('director.panel_scene_keyframe', keyframe)] : []), field('director.panel_scene_prompt', prompt),
                  ...(speakers ? [say(h('strong'), 'director.panel_scene_speech'), lines, h('div', {class: 'actions'}, addLine)] : [])));
    });
    const start = say(h('button', {id: 'directorStart', type: 'button'}), 'director.panel_start_button');
    start.onclick = startFilm;
    const minutes = plan.estimate_minutes;           // an Ultra film takes hours: past an hour and a half the time is told in hours
    kids.push(minutes >= 90 ? say(h('p', {class: 'status'}), 'director.panel_estimate_time_hours', [Math.floor(minutes / 60), minutes % 60]) : say(h('p', {class: 'status'}), 'director.panel_estimate_time', [minutes]),
              h('div', {class: 'actions'}, start));
    planBox.replaceChildren(...kids);
    planBox.hidden = false;
    sumUp();
  }
  function sumUp() {
    const total = view.scenes.reduce((sum, row) => sum + (Number(row.length.value) || 0), 0);
    say(view.sum, Math.abs(total - plan.seconds) < 0.15 ? 'director.panel_sum_exact' : 'director.panel_sum_fitted', [total.toFixed(1), plan.seconds]);
  }
  function readPlan() {
    const edited = JSON.parse(JSON.stringify(plan));
    Object.assign(edited, {title: view.title.value, orientation: view.format.value, quality: view.level.value});
    edited.style = {phrase: view.style.value, warmth: Number(view.warmth.value), contrast: Number(view.contrast.value), saturation: Number(view.saturation.value)};
    if (plan.music) {
      if (!view.music.use.checked) edited.music = null;
      else Object.assign(edited.music, {caption: view.music.caption.value, lyrics: view.music.lyrics.value, gain_db: Number(view.music.gain.value)});
    }
    edited.songs = view.songs.filter(row => row.use.checked).map(row => ({song: row.song, offset: Number(row.offset.value), start: Number(row.start.value), seconds: Number(row.seconds.value),
                                                                          gain_db: Number(row.gain.value), fade_in: row.fade_in, fade_out: row.fade_out}));
    edited.speech = view.speech.filter(row => row.text.value.trim()).map(row => ({voice: Number(row.voice.value), scene: row.scene, text: row.text.value, language: row.language, delay: row.delay, gain_db: row.gain_db}));
    edited.scenes = view.scenes.map(row => ({seconds: Number(row.length.value), prompt: row.prompt.value, references: row.references, keyframe: row.keyframe ? row.keyframe.value : null, transition: row.transition.value}));
    return edited;
  }
  async function startFilm() {
    const request = view.request, edited = readPlan();
    working = true;
    try {
      // The mode is the one the plan was written for, not the toggle as it stands now: a plan made for Ultra has a keyframe for every scene.
      run = await api('start', {draft, plan: edited, prompt: request.prompt, seconds: request.seconds, scenes: request.scenes, orientation: edited.orientation, quality: edited.quality, language: language(),
                                ultra: request.ultra === true});
      announced = ''; planBox.hidden = true; renderRun(); keepPolling();
    } catch (error) {report(planStatus, error);}
    finally {working = false;}
  }

  // ---------- the shooting ----------
  const STEP_KEYS = {speech: 'director.step_speak_voice', keyframe: 'director.step_scene_keyframe', scene: 'director.step_shoot_scene', music: 'director.step_compose_music',
                     assemble: 'director.step_edit_assemble', mix: 'director.step_mix_music'};
  const MARKS = {pending: '○', running: '◐', done: '✓', failed: '✗', skipped: '–'};
  const COMPACT_STEPS = 14, NEXT_STEPS = 4;
  function stepLabel(step) {
    if (step.kind !== 'speech') return say(h('span'), STEP_KEYS[step.kind], [step.index]);
    const item = items.find(row => row.number === step.index), own = run.materials?.[step.index], node = h('span');
    // The voice may have left the draft since the film began (the film keeps its own copy and its names): the run's own record names it then.
    StudioI18n.bind(node, 'textContent', () => StudioI18n.t(STEP_KEYS.speech, [item ? titleOf(item) : own ? own.label || own.name : '#' + step.index]), true);
    return node;
  }
  let shown = '', bar = null, etaNode = null;
  // The time left is told under the bar and moves in place (the box itself is rebuilt only when a step changes).
  function showEta() {
    if (!etaNode) return;
    const left = run && run.state === 'running' && Number.isFinite(run.eta_seconds) ? run.eta_seconds : null;
    etaNode.hidden = left === null;
    if (left !== null) say(etaNode, 'director.run_time_left', [clock(left)]);
  }
  function renderRun() {
    if (!run || !run.steps) {runBox.hidden = true; shown = ''; return;}
    // The poll repeats every two seconds. A box rebuilt each time takes the keyboard focus away from the Stop button and drops a click that begins before and ends after the rebuild,
    // so it is rebuilt only when something it shows has changed; the bar moves in place.
    const signature = JSON.stringify([run.id, run.state, run.phase, run.error, run.notes, run.resumable, run.steps.map(step => step.state), items.map(row => [row.number, row.label, row.name])]);
    if (signature === shown && bar) {bar.value = run.progress || 0; showEta(); return;}
    shown = signature;
    const running = !FINISHED.includes(run.state);
    const stop = say(h('button', {type: 'button', class: 'secondary', disabled: !running}), 'director.panel_stop_button');
    stop.dataset.allowBusy = '1';
    stop.onclick = async () => {stop.disabled = true; try {await api('cancel', {id: run.id});} catch (error) {stop.disabled = false; report(planStatus, error);}};
    const line = step => h('li', {class: step.state}, h('span', {class: 'director-mark', textContent: MARKS[step.state] || '○'}), stepLabel(step));
    // A film of fifty scenes has sixty steps: past `COMPACT_STEPS` the finished ones are counted in one line, the step in progress and the next ones are listed and the rest is counted again.
    let rows = run.steps.map(line);
    if (run.steps.length > COMPACT_STEPS) {
      const finished = run.steps.filter(step => step.state === 'done' || step.state === 'skipped').length;
      const open = run.steps.filter(step => step.state !== 'done' && step.state !== 'skipped');
      const shown = open.slice(0, NEXT_STEPS), later = open.length - shown.length;
      rows = [...(finished ? [h('li', {class: 'done'}, h('span', {class: 'director-mark', textContent: MARKS.done}), say(h('span'), 'director.run_steps_done', [finished, run.steps.length]))] : []),
              ...shown.map(line), ...(later > 0 ? [h('li', {class: 'pending'}, h('span', {class: 'director-mark', textContent: '…'}), say(h('span'), 'director.run_steps_later', [later]))] : [])];
    }
    const list = h('ol', {class: 'director-steps'}, ...rows);
    const phase = h('p', {class: 'muted'});
    StudioI18n.bind(phase, 'textContent', () => StudioI18n.remote(run.phase || ''), true);
    const outcome = h('p', {class: 'status'});
    if (run.state === 'done') say(outcome, 'director.run_film_ready');
    else if (run.state === 'error' || run.state === 'interrupted') StudioI18n.bind(outcome, 'textContent', () => StudioI18n.remote(run.error), true);
    else if (run.state === 'cancelled') say(outcome, 'director.run_was_cancelled');
    const notes = h('ul', {class: 'director-reasons'}, ...(run.notes || []).map(note => {const node = h('li'); StudioI18n.bind(node, 'textContent', () => StudioI18n.remote(note), true); return node;}));
    bar = h('progress', {max: 1, value: run.progress || 0});
    const open = say(h('button', {type: 'button', hidden: run.state !== 'done'}), 'director.run_open_film');
    open.onclick = () => window.dispatchEvent(new CustomEvent('visualforge:director-done', {detail: {id: run.result.id}}));
    // A film that failed, was stopped or was interrupted by a closed studio keeps what its finished steps made (voices, keyframes, scene clips, the track): it is continued from there.
    const again = say(h('button', {type: 'button', id: 'directorResume', hidden: !run.resumable}), 'director.panel_resume_button');
    again.onclick = async () => {
      again.disabled = true;
      try {run = await api('resume', {id: run.id}); announced = ''; shown = ''; planBox.hidden = true; renderRun(); keepPolling();}
      catch (error) {again.disabled = false; report(planStatus, error);}
    };
    const giveUp = say(h('button', {type: 'button', class: 'secondary', hidden: !run.resumable}), 'director.panel_discard_button');
    giveUp.onclick = async () => {
      giveUp.disabled = true;
      try {await api('discard', {id: run.id}); run = null; shown = ''; renderRun(); if (plan && planBox.children.length) planBox.hidden = false;}
      catch (error) {giveUp.disabled = false; report(planStatus, error);}
    };
    const hint = run.resumable ? say(h('p', {class: 'muted'}), 'director.run_resumable_hint') : h('span');
    etaNode = h('p', {class: 'muted', hidden: true});
    runBox.replaceChildren(say(h('h3'), 'director.run_section_title'), bar, etaNode, list, phase, outcome, hint, notes, h('div', {class: 'actions'}, stop, again, giveUp, open));
    showEta();
    runBox.hidden = false;
  }
  async function pollOnce() {
    try {run = await api('status' + (run?.id ? '?id=' + run.id : '')); renderRun();} catch {return;}
    if (FINISHED.includes(run.state) || run.state === 'none') {
      clearInterval(timer); timer = null;
      if (run.state === 'done' && announced !== run.id) {
        announced = run.id;
        StudioUI.notify({module: 'Video', id: run.id, error: false, message: StudioI18n.t('director.run_film_ready')});
        window.dispatchEvent(new CustomEvent('visualforge:director-done', {detail: {id: run.result.id}}));
      } else if (run.state !== 'done' && run.state !== 'none' && plan && planBox.children.length) {
        planBox.hidden = false;           // a film that stopped or failed can be shot again from the same plan, which cost minutes to write
      }
    }
  }
  function keepPolling() {if (!timer) timer = setInterval(pollOnce, 2000); pollOnce();}
  async function resume() {
    try {
      const latest = await api('status');
      if (latest.state === 'running') {run = latest; announced = ''; renderRun(); keepPolling();}
      else if (latest.resumable) {run = latest; announced = latest.id; renderRun();}           // a film that stopped (also across a restart of the studio) is offered for continuation
    } catch {/* the panel works without a film in progress */}
  }

  // ---------- showing and hiding ----------
  async function ensureDraft() {
    try {draft = localStorage.getItem(DRAFT_KEY) || '';} catch {draft = '';}
    if (!/^[0-9a-f]{32}$/.test(draft)) {
      draft = crypto.randomUUID().replaceAll('-', '');
      try {localStorage.setItem(DRAFT_KEY, draft);} catch {/* a draft id that is not remembered only loses the materials after a reload */}
    }
    try {setDraft(await api('draft?id=' + draft));} catch {items = []; advice = null;}
    renderMaterials();
  }
  // Voices need a speech engine of the Voiceover module; asked from the module itself, so that the page says so before a voice is uploaded.
  async function askSpeech() {
    try {
      const answer = await (await fetch('/api/voiceover/availability')).json();
      speechReady = (answer.engines || []).some(engine => engine.available);
    } catch {speechReady = false;}
  }
  window.addEventListener('visualforge:director-engine', event => {
    const {active, profile} = event.detail;
    root.hidden = !active;
    if (!active) return;
    limits = Object.assign(limits, profile.limits || {});
    available = !!profile.available;
    for (const control of root.querySelectorAll('button, input, select, textarea')) if (!control.hidden) control.disabled = !profile.available;
    if (profile.available) {planButton.disabled = working; askSpeech().then(ensureDraft).then(resume);}
    else {say(planStatus, 'director.panel_unavailable_reason', [profile.reason]); renderMaterials();}
  });
})();
