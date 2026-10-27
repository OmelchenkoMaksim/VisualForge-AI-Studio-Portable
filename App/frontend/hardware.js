/* Hardware stays local. A single compatibility card follows the user's model choice. */
(() => {
  'use strict';
  const aside = document.getElementById('hardwarePanel');
  const popup = document.getElementById('compatibilityCard');
  const selectors = {engineSelect:'image', videoEngine:'video', musicEngine:'music', textModel:'text'};
  // The card is a popover under the top-bar chip (shell.js), so its parent must not grow to the card's height.
  let data, pending, selection = '', failed = false;
  const el = id => document.getElementById(id);
  const node = (tag, text, className) => {const item = document.createElement(tag);item.textContent = text;if (className) item.className = className;return item;};
  const gb = value => Number.isFinite(value) ? `${(Math.abs(value - Math.round(value)) < .15 ? Math.round(value) : value).toLocaleString('ru-RU', {maximumFractionDigits:1})} ГБ` : 'Нет данных';
  function current() {
    for (const [id, module] of Object.entries(selectors)) {
      const select = el(id);
      if (select?.getClientRects().length && select.value) return {key:`${module}:${select.value}`, select};
    }
    return null;
  }
  function machine() {
    const hw = data.hardware, list = el('hardwareSpecs');
    list.replaceChildren();
    const row = (label, value) => {list.append(node('dt', label), node('dd', value));};
    row('Процессор', hw.cpu);
    row('Видеокарта', hw.gpus.map(g => g.name).join(' · ') || 'Не определена');
    row('Видеопамять', hw.gpus.length ? hw.gpus.map(g => gb(g.vram_gb)).join(' · ') : 'Нет данных');
    row('ОЗУ', gb(hw.ram_gb));
    el('hardwareFootnote').textContent = hw.gpus.length > 1 ? 'Память разных видеокарт не суммируется.' : 'Данные этого ПК · только локально';
    aside.setAttribute('aria-busy', 'false');
  }
  function show(open = false) {
    const chosen = current();
    if (!chosen) {el('hardwareCompatibility').hidden = true;popup.hidden = true;return;}
    el('hardwareCompatibility').hidden = false;
    const result = data?.models[chosen.key] || (data ? {
      status:'yellow', title:'Недостаточно данных о модели', reasons:['Для этой сборки ещё нет профиля требований.'],
      profile:{name:chosen.select.value, note:'Обновите данные после установки новой модели.', recommended_vram_gb:null, recommended_ram_gb:null},
      actual_vram_gb:data.hardware.gpus.find(g => g.cuda)?.vram_gb ?? null, actual_ram_gb:data.hardware.ram_gb, caveat:'Совместимость не подтверждена.'
    } : null);
    const summary = el('hardwareCompatibility');
    selection = chosen.key;
    summary.dataset.status = result?.status || 'yellow';
    summary.textContent = result ? `${{green:'✓',yellow:'!',red:'×'}[result.status]} ${result.title}` : failed ? 'Не удалось проверить · обновить данные' : 'Проверяем совместимость…';
    if (!result) {if (open && !failed) load().then(() => show(true)).catch(() => {});return;}
    if (!open && popup.hidden) return;
    popup.dataset.status = result.status;
    el('compatibilityTitle').textContent = result.title;
    el('compatibilityModel').textContent = chosen.select.selectedOptions[0]?.textContent.split(/ · (?:выпуск|публикация)/)[0] || result.profile.name;
    el('compatibilityReasons').replaceChildren(...result.reasons.map(reason => node('li', reason)));
    const body = el('compatibilityMetrics');
    body.replaceChildren();
    for (const [label, recommended, actual] of [['Видеопамять', result.profile.recommended_vram_gb, result.actual_vram_gb], ['ОЗУ', result.profile.recommended_ram_gb, result.actual_ram_gb]]) {
      const tr = document.createElement('tr');tr.append(node('th', label), node('td', gb(recommended)), node('td', gb(actual)));body.append(tr);
    }
    el('compatibilityProfile').textContent = result.profile.note;
    el('compatibilityCaveat').textContent = result.caveat;
    el('compatibilityGpu').textContent = result.gpu_name ? `Оценка для ${result.gpu_name}` : 'Ускоритель CUDA не подтверждён';
    popup.hidden = false;
  }
  async function load(refresh = false) {
    if (pending) return pending;
    el('hardwareRefresh').disabled = true;
    pending = fetch('/api/hardware' + (refresh ? '?refresh=1' : ''), {cache:'no-store'})
      .then(response => {
        if (!response.ok) throw new Error('hardware');
        return response.json();
      })
      .then(result => {
        data = result;failed = false;machine();show();document.dispatchEvent(new Event('studio:hardware'));
      })
      .catch(error => {
        failed = true;data = null;popup.hidden = true;aside.setAttribute('aria-busy','false');
        el('hardwareFootnote').textContent = 'Не удалось получить данные. Нажмите «Обновить».';
        show();document.dispatchEvent(new Event('studio:hardware'));throw error;
      })
      .finally(() => {el('hardwareRefresh').disabled = false;pending = null;});
    return pending;
  }
  el('hardwareRefresh').onclick = () => load(true).catch(() => {});
  el('hardwareCompatibility').onclick = () => {if (failed) load().then(() => show(true)).catch(() => {});else show(true);};
  el('compatibilityClose').onclick = () => {popup.hidden = true;};
  document.addEventListener('keydown', event => {if (event.key === 'Escape') popup.hidden = true;});
  document.addEventListener('change', event => {if (selectors[event.target.id]) show(true);});
  document.querySelector('.module-tabs').addEventListener('click', () => setTimeout(() => {popup.hidden = true;show();}, 0));
  // Modules fill their selectors asynchronously; refresh the current summary, never open a card by itself.
  for (const id of Object.keys(selectors)) new MutationObserver(() => {if (current()?.key !== selection) show();}).observe(el(id), {childList:true});
  window.StudioHardware = {snapshot: () => data};
  load().catch(() => {});
})();
