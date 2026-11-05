/* Composition is an Image workspace, not a selectable diffusion checkpoint. */
(function () {
  'use strict';
  const workspace = document.getElementById('imageWorkspace');
  if (!workspace) return;
  const key = 'visualforge.composition.v1';
  const t = (name, args) => StudioI18n.t(`composition.panel_${name}_text`, args || []);
  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (name === 'class') node.className = value;
      else if (name in node) node[name] = value;
      else node.setAttribute(name, value);
    }
    node.append(...children);
    return node;
  };
  const say = (node, name, args) => {StudioI18n.bind(node, 'textContent', () => t(name, args), true); return node;};
  const api = async (route, body) => {
    const response = await fetch('/api/composition/' + route, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    let value = {};
    try {value = await response.json();} catch { /* An answer that is not JSON (a proxy page, a crash) is reported by the message below. */ }
    if (!response.ok) throw new Error(StudioI18n.remote(value.error) || t('unavailable'));
    return value;
  };
  let saved = {};
  try {saved = JSON.parse(localStorage.getItem(key) || '{}');} catch { /* A bad draft never blocks the normal Image form. */ }
  let active = false, initialized = false, working = false, pending = 0, draft = typeof saved.draft === 'string' ? saved.draft : '', items = [], profile = null, run = null, timer = null;
  const normal = say(h('button', {id:'compositionNormal', type:'button', 'aria-pressed':'true'}), 'normal');
  const creative = say(h('button', {id:'compositionMode', type:'button', 'aria-pressed':'false'}), 'name');
  const switcher = h('div', {class:'composition-switch'}, normal, creative);
  const panel = h('section', {id:'compositionPanel', class:'panel', hidden:true});
  const groups = h('div', {class:'composition-groups', id:'compositionGroups'});
  const prompt = h('textarea', {id:'compositionPrompt', rows:6, maxLength:5000, value:typeof saved.prompt === 'string' ? saved.prompt : ''});
  const variants = h('select', {id:'compositionVariants'});
  for (let count = 1; count <= 5; count++) variants.append(h('option', {value:String(count)}, String(count)));
  variants.value = String(saved.variants || 1);
  const canvas = h('select', {id:'compositionCanvas'});
  for (const choice of ['auto', 'square', 'landscape', 'portrait']) canvas.append(say(h('option', {value:choice}), choice));
  canvas.value = saved.canvas || 'auto';
  const start = say(h('button', {id:'compositionStart', type:'button'}), 'start');
  const stop = say(h('button', {id:'compositionStop', type:'button', hidden:true}), 'stop');
  const status = h('p', {id:'compositionStatus', role:'status', 'aria-live':'polite'});
  const progress = h('p', {class:'preset-hint', hidden:true});
  const steps = h('div', {class:'composition-steps'});
  const results = h('div', {id:'compositionResults', class:'composition-results'});
  const history = h('div', {class:'composition-history'});
  const label = (name, input) => h('label', {}, say(h('span'), name), input);
  panel.append(say(h('h2'), 'name'), say(h('p'), 'intro'), say(h('p', {class:'preset-hint'}), 'quality'),
    say(h('p', {class:'preset-hint'}), 'material_hint'), groups, label('prompt', prompt),
    h('div', {class:'composition-fields'}, label('variants', variants), label('canvas', canvas)),
    h('div', {class:'actions'}, start, stop), status, progress, steps, results, say(h('p', {class:'preset-hint'}), 'output_hint'),
    h('details', {}, say(h('summary'), 'history'), history));
  workspace.prepend(switcher, panel);
  StudioI18n.bind(prompt, 'placeholder', () => t('placeholder'), true);

  function remember() {
    try {localStorage.setItem(key, JSON.stringify({active, draft, prompt:prompt.value, variants:Number(variants.value), canvas:canvas.value, run:run?.id}));} catch { /* Optional persistence. */ }
  }
  function report(error) {
    const message = error.message || String(error);
    // Plan diagnostics contain internal IDs and English validation rules; retain them in run.json, not in the creative UI.
    const planFailure = StudioI18n.source('composition.panel_plan_failed_text', [StudioI18n.source('composition.panel_invalid_plan_text')]);
    StudioI18n.bind(status, 'textContent', () => message.startsWith(planFailure)
      ? t('plan_failed', [t('invalid_plan')]) : StudioI18n.remote(message), true);
  }
  function header() {
    if (active) StudioUI.setHeader('image', {model:t('name'), description:t('intro'), tone:'good', status:working ? t(run?.state === 'planning' ? 'planning' : 'running') : t('quality')});
  }
  function controls() {
    const locked = working || pending > 0;
    for (const input of panel.querySelectorAll('input,textarea,select,button')) input.disabled = locked;
    stop.disabled = !working;
    stop.hidden = !working;
    start.disabled = locked || !profile?.available;
    normal.disabled = creative.disabled = locked || !document.getElementById('cancel').hidden;
    header();
  }
  async function mutate(work) {
    pending++; controls();
    try {const value = await work(); draft = value.id; items = value.items; remember(); renderMaterials();}
    catch (error) {report(error);}
    finally {pending--; controls();}
  }
  async function upload(file, group) {
    await mutate(async () => {
      const response = await fetch('/api/composition/attach?' + new URLSearchParams({draft, group, name:file.name}), {method:'POST', body:file});
      let value = {};
      try {value = await response.json();} catch { /* see `api` */ }
      if (!response.ok) throw new Error(StudioI18n.remote(value.error) || t('unavailable'));
      return value;
    });
  }
  function renderMaterials() {
    groups.replaceChildren();
    for (const group of profile?.groups || []) {
      const rows = items.filter(item => item.group === group.id), slots = h('div', {class:'composition-slots', 'data-group':group.id});
      for (let index = 0; index < rows.length; index++) {
        const item = rows[index];
        const name = h('input', {type:'text', maxLength:120, value:item.label, 'aria-label':t('label')});
        const remove = say(h('button', {type:'button'}), 'remove');
        name.onchange = () => mutate(() => api('label', {draft, id:item.id, text:name.value}));
        remove.onclick = () => mutate(() => api('detach', {draft, id:item.id}));
        slots.append(h('div', {class:'composition-slot'}, h('img', {src:'/api/composition/material?' + new URLSearchParams({draft, id:item.id}), alt:t(group.id) + ' ' + (index + 1)}), name, remove));
      }
      // Exactly one empty slot remains while the group has capacity, including the six-hero limit.
      if (rows.length < group.limit) {
        const input = h('input', {type:'file', accept:'image/*,.heic,.heif,.avif,.tif,.tiff,.psd,.tga', 'aria-label':t('add') + ' · ' + t(group.id)});
        input.onchange = async () => {if (input.files[0]) await upload(input.files[0], group.id);};
        const empty = h('div', {class:'composition-slot'}, say(h('span'), 'add'), input);
        empty.ondragover = event => event.preventDefault();
        empty.ondrop = async event => {event.preventDefault(); if (!working && !pending && event.dataTransfer.files[0]) await upload(event.dataTransfer.files[0], group.id);};
        slots.append(empty);
      }
      groups.append(h('section', {}, say(h('h3'), group.id), slots));
    }
    controls();
  }
  function renderRun(value) {
    run = value;
    working = ['planning', 'running'].includes(value.state);
    const state = ['planning', 'running', 'done', 'cancelled', 'error', 'interrupted'].includes(value.state) ? value.state : 'running';
    if (value.state !== 'none') {
      if (value.error) report(new Error(value.error)); else say(status, state);
    }
    // A composition can be dozens of images: once the plan exists the person sees how far it is and how long the whole may take (a rough server estimate).
    const total = (value.plan?.variants || []).reduce((sum, row) => sum + row.steps.length, 0), finished = (value.steps || []).filter(row => row.state === 'done').length;
    progress.hidden = !total;
    if (total) say(progress, 'progress', [Math.min(finished + (working ? 1 : 0), total), total, value.estimate_minutes || 1]);
    steps.replaceChildren(...(value.steps || []).map(row => {
      const node = h('div');
      // Use the existing model picker's public label instead of exposing internal registry IDs in the creative workspace.
      const modelName = () => [...document.querySelectorAll('#engineSelect option')].find(option => option.value === row.engine)?.textContent.split(' · ')[0].trim() || row.engine;
      StudioI18n.bind(node, 'textContent', () => t('step', [row.variant, row.step, modelName()]) + ' · ' + StudioI18n.remote(row.state), true);
      return node;
    }));
    // Replacing completed cards on every status poll stole keyboard focus from their download links.
    // Completed variants are immutable; keep their nodes and append only newly finished choices.
    if (results.dataset.run !== String(value.id || '')) {
      results.replaceChildren();
      results.dataset.run = String(value.id || '');
    }
    const completed = value.results || [];
    while (results.childElementCount > completed.length) results.lastElementChild.remove();
    for (let index = results.childElementCount; index < completed.length; index++) {
      const url = '/api/composition/result?' + new URLSearchParams({id:value.id, index});
      // Real plan titles used the wrong UI language and even said "sitting" for a standing prompt. Keep those in provenance;
      // stable localized numbers identify the finished choices without making an unverified claim about their content.
      const preview = h('img', {src:url});
      StudioI18n.bind(preview, 'alt', () => t('variant', [index + 1]), true);
      results.append(h('figure', {class:'composition-result'}, preview, say(h('figcaption'), 'variant', [index + 1]),
        say(h('a', {href:url, download:'Composition - ' + (index + 1) + '.png'}), 'download')));
    }
    controls(); remember();
    if (timer) clearTimeout(timer);
    if (working) timer = setTimeout(poll, 2000);
  }
  async function poll() {
    try {renderRun(await api('status?id=' + encodeURIComponent(run.id))); if (!working) await loadHistory();}
    catch (error) {report(error); timer = setTimeout(poll, 4000);}
  }
  async function loadHistory() {
    const value = await api('history');
    history.replaceChildren(...value.entries.map(row => {
      const button = h('button', {type:'button'}, new Date(row.started_at * 1000).toLocaleString() + ' · ' + row.request.prompt.slice(0, 70));
      button.onclick = async () => {
        if (working) return;
        try {renderRun(await api('status?id=' + encodeURIComponent(row.id)));} catch (error) {report(error);}
      };
      return button;
    }));
  }
  async function initialize() {
    if (initialized) return;
    pending++; controls();
    try {
      profile = await api('profile');
      let value;
      try {value = await api('draft' + (saved.draft ? '?id=' + encodeURIComponent(saved.draft) : ''));}
      catch {value = await api('draft');}
      draft = value.id; items = value.items; renderMaterials();
      const current = await api('status');
      if (current.state !== 'none') renderRun(current);
      await loadHistory();
      initialized = true;
      if (!profile.available) report(new Error(profile.reason || t('unavailable')));
    } catch (error) {report(error);}
    finally {pending--; controls();}
  }
  function activate(value) {
    if (working || pending || value && !document.getElementById('cancel').hidden) return;
    active = value;
    workspace.classList.toggle('composition-mode', active);
    panel.hidden = !active;
    normal.setAttribute('aria-pressed', String(!active)); creative.setAttribute('aria-pressed', String(active));
    window.dispatchEvent(new CustomEvent('visualforge:composition-mode', {detail:{active}}));
    remember(); header();
    if (active) initialize();
  }
  normal.onclick = () => activate(false); creative.onclick = () => activate(true);
  prompt.oninput = variants.onchange = canvas.onchange = remember;
  start.onclick = async () => {
    if (working || pending) return;
    pending++; controls();
    try {renderRun(await api('start', {draft, prompt:prompt.value, variants:Number(variants.value), canvas:canvas.value,
      language:StudioI18n.language === 'en' ? 'English' : 'Russian'}));}
    catch (error) {report(error);}
    finally {pending--; controls();}
  };
  stop.onclick = async () => {stop.disabled = true; try {await api('cancel', {id:run.id});} catch (error) {report(error); stop.disabled = false;}};
  window.addEventListener('studio-languagechange', () => {say(normal, 'normal'); say(creative, 'name'); renderMaterials(); if (run) renderRun(run); header();});
  if (saved.active) activate(true);
})();
