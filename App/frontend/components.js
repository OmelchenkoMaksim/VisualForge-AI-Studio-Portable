'use strict';
(() => {
  // The Components tab: what is installed, and downloading missing models (the programs are installed by Setup).
  const el = id => document.getElementById(id);
  const WORKSPACES = ['image', 'video', 'music', 'text'];
  let state = null, selected = new Set(), timer = null, busyLocal = false;
  const fmt = (n, digits = 1) => Number(n).toLocaleString('ru-RU', {maximumFractionDigits: digits});
  const gb = bytes => bytes >= 1e9 ? fmt(bytes / 1e9) + ' ГБ' : fmt(bytes / 1e6, 0) + ' МБ';
  const clock = seconds => {seconds = Math.max(0, Math.round(seconds));const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');};
  const STATE_TEXT = {installed: 'установлено', partial: 'скачано частично', missing: 'не установлено'};
  const api = async (route, data) => {
    let response;
    try {response = await fetch('/api/components' + route, data === undefined ? {cache: 'no-store'} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});}
    catch {throw new Error('Нет связи со студией. Проверьте, запущено ли приложение.');}
    let value;
    try {value = await response.json();} catch {throw new Error('Некорректный ответ установщика.');}
    if (!response.ok) throw new Error(value.error || 'Установщик недоступен');
    return value;
  };
  const make = (tag, className, text) => {const node = document.createElement(tag);if (className) node.className = className;if (text !== undefined) node.textContent = text;return node;};

  function render() {
    const root = el('componentsModules');
    root.textContent = '';
    if (!state) return;
    const missingSoftware = state.missing_software || [];
    const banner = el('componentsSoftware');
    banner.hidden = !missingSoftware.length;
    banner.textContent = '';
    if (missingSoftware.length) {
      const titles = state.modules.flatMap(m => m.components).filter(c => missingSoftware.includes(c.id)).map(c => c.title);
      const fix = make('button', 'secondary', 'Установить программы');
      fix.type = 'button';fix.disabled = state.install.running;fix.onclick = start;
      banner.append(make('p', '', 'Не хватает программной части: ' + titles.join('; ') + '. Без неё соответствующие вкладки не заработают, даже если модели скачаны.'), fix);
    }
    for (const module of state.modules) {
      const models = module.components.filter(c => c.kind === 'model');
      const software = module.components.filter(c => c.kind === 'software');
      if (!models.length && !software.length) continue;
      const card = make('section', 'components-card');
      card.append(make('h3', '', module.title));
      if (module.summary) card.append(make('p', 'small', module.summary));
      for (const row of software) {
        const line = make('div', 'components-row');
        line.append(make('span', 'components-check', row.state === 'installed' ? '✓' : '·'), make('span', 'components-title', row.title), make('span', 'small', gb(row.disk_bytes) + ' · ' + STATE_TEXT[row.state] + ' · программа'));
        card.append(line);
      }
      for (const row of models) {
        const line = make('div', 'components-row');
        const box = document.createElement('input');
        box.type = 'checkbox';box.value = row.id;
        box.disabled = row.state === 'installed' || state.install.running;
        box.checked = row.state === 'installed' || selected.has(row.id);
        box.onchange = () => {box.checked ? selected.add(row.id) : selected.delete(row.id);summary();};
        const text = make('span', 'components-title', row.title);
        box.setAttribute('aria-label', 'Скачать: ' + row.title);
        const info = make('span', 'small', (row.state === 'installed' ? gb(row.download_bytes) : gb(row.remaining_bytes || row.download_bytes)) + ' · ' + STATE_TEXT[row.state]);
        line.append(box, text, info);
        if (row.enabled === false) info.textContent += ' · отключена';
        if (row.description || row.note || row.license) {
          const detail = make('span', 'components-detail small', [row.description, row.note, row.license ? 'Лицензия: ' + row.license : ''].filter(Boolean).join(' '));
          line.append(detail);
        }
        if (row.removable && row.state !== 'installed' && !row.local_only) {
          const repair = make('button', 'secondary', 'Установить / восстановить');
          repair.type = 'button';repair.disabled = state.install.running || busyLocal;
          repair.onclick = () => repairModel(row.id);
          const actions = make('div', 'components-model-actions');actions.append(repair);line.append(actions);
        }
        if (row.removable && row.state !== 'missing') {
          const actions = make('div', 'components-model-actions');
          const toggle = make('button', 'secondary', row.enabled === false ? 'Включить' : 'Отключить');
          toggle.type = 'button';toggle.setAttribute('aria-label', toggle.textContent + ': ' + row.title);
          toggle.disabled = state.install.running || busyLocal;
          toggle.onclick = async () => {
            toggle.disabled = true;
            try {
              const result = await api('/model-toggle', {id:row.id, enabled:row.enabled === false});
              el('componentsModelStatus').textContent = result.repairing ? 'Восстанавливаем зависимости модели…' : row.enabled === false ? 'Модель включена.' : 'Модель отключена; файлы сохранены.';
              document.dispatchEvent(new Event('studio:models-changed'));await refresh();
            } catch (error) {el('componentsError').textContent = error.message;toggle.disabled = false;}
          };
          const remove = make('button', 'secondary', 'Удалить модель…');
          remove.type = 'button';remove.setAttribute('aria-label', 'Удалить модель: ' + row.title);
          remove.disabled = state.install.running || busyLocal;
          remove.onclick = () => prepareRemoval(row);
          actions.append(toggle, remove);line.append(actions);
        }
        card.append(line);
      }
      if (module.id === 'text') {
        const form = make('div', 'components-text');
        const input = document.createElement('input');
        input.id = 'componentsTextTag';input.placeholder = 'другая модель Ollama, например qwen3-vl:8b';input.autocomplete = 'off';input.spellcheck = false;
        input.setAttribute('aria-label', 'Название модели в реестре Ollama');
        form.append(input, make('span', 'small', 'Любая модель из реестра ollama.com; скачивается в закрытое хранилище студии.'));
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
    el('componentRemoveWait').textContent = remaining ? `Подтверждение будет доступно через ${remaining} с.` : 'Для полного удаления введите REMOVE.';
    el('componentRemoveInput').disabled = remaining > 0;
    el('componentRemoveConfirm').disabled = remaining > 0 || el('componentRemoveInput').value !== 'REMOVE';
  }
  async function prepareRemoval(row) {
    try {
      const plan = await api('/model-remove-plan', {id:row.id});
      removal = {...plan, ready:performance.now() + plan.wait_seconds * 1000};
      el('componentRemoveName').textContent = row.title;
      el('componentRemoveSummary').textContent = `${plan.note} Файлов: ${plan.files}; объём: ${gb(plan.bytes)}. Удаление необратимо.`;
      el('componentRemoveFiles').textContent = plan.examples.join('\n');
      el('componentRemoveInput').value = '';el('componentRemoveError').textContent = '';
      updateRemoval();dialog.showModal();
      clearInterval(removalTimer);removalTimer = setInterval(updateRemoval, 200);
    } catch (error) {el('componentsError').textContent = error.message;}
  }
  el('componentRemoveInput').addEventListener('input', updateRemoval);
  el('componentRemoveCancel').onclick = () => dialog.close();
  dialog.addEventListener('close', () => {clearInterval(removalTimer);removalTimer = null;removal = null;});
  el('componentRemoveConfirm').onclick = async () => {
    if (!removal) return;
    clearInterval(removalTimer);el('componentRemoveConfirm').disabled = true;el('componentRemoveCancel').disabled = true;
    el('componentRemoveWait').textContent = 'Удаляем модель и её собственный кеш…';
    try {
      const result = await api('/model-remove', {token:removal.token, confirmation:el('componentRemoveInput').value});
      dialog.close();el('componentsModelStatus').textContent = `Модель удалена. Освобождено: ${gb(result.bytes)}. Общие зависимости сохранены.`;
      document.dispatchEvent(new Event('studio:models-changed'));await refresh();
    } catch (error) {el('componentRemoveError').textContent = error.message;}
    finally {el('componentRemoveCancel').disabled = false;}
  };

  function summary() {
    if (!state) return;
    const rows = state.modules.flatMap(m => m.components).filter(c => selected.has(c.id) && c.state !== 'installed');
    const need = rows.reduce((total, c) => total + (c.remaining_bytes || c.download_bytes), 0);
    const tag = el('componentsTextTag') ? el('componentsTextTag').value.trim() : '';
    el('componentsTotal').textContent = rows.length || tag
      ? `Выбрано: ${rows.length + (tag ? 1 : 0)}. К загрузке около ${gb(need)}${tag ? ' и модель ' + tag : ''}. Свободно на диске: ${state.free_bytes ? gb(state.free_bytes) : 'неизвестно'}.`
      : 'Отметьте модели, которые нужно скачать. Уже установленные отмечены и недоступны для выбора.';
    el('componentsStart').disabled = busyLocal || state.install.running || !(rows.length || tag);
  }

  function renderProgress() {
    const install = state ? state.install : null;
    const box = el('componentsProgress');
    const show = !!install && (install.running || install.results || install.error);
    box.hidden = !show;
    el('componentsCancel').hidden = !(install && install.running);
    if (!show) return;
    const title = el('componentsCurrent');
    const bar = el('componentsBar'), line = el('componentsFile'), log = el('componentsLog');
    if (install.running) {
      const c = install.current;
      title.textContent = c ? `[${c.index}/${c.total}] ${c.title}` : 'Подготовка…';
      const p = install.progress || {};
      if (p.name && p.total) {
        bar.max = p.total;bar.value = p.done;
        const eta = p.speed > 1 ? ' · осталось ' + clock((p.total - p.done) / p.speed) : '';
        line.textContent = `${p.name}: ${fmt(100 * p.done / p.total, 0)}% · ${gb(p.done)} из ${gb(p.total)} · ${fmt((p.speed || 0) / 1e6)} МБ/с${eta}`;
      } else {bar.removeAttribute('value');line.textContent = 'Распаковка и настройка…';}
    } else {
      bar.max = 1;bar.value = 1;
      const failed = install.results ? Object.entries(install.results).filter(([, v]) => v.startsWith('failed')) : [];
      title.textContent = install.error ? 'Остановлено' : failed.length ? 'Закончено с ошибками' : 'Готово';
      line.textContent = install.error || failed.map(([k, v]) => `${k}: ${v}`).join('\n') || 'Все выбранные компоненты установлены. Перезапуск не нужен: разделы увидят модели сразу.';
    }
    log.textContent = (install.events || []).map(e => e.type === 'component_done' ? '✓ ' + e.id : e.type === 'component_failed' ? '✗ ' + e.id + ': ' + e.error : e.type === 'download_start' ? 'Скачивание: ' + e.name : e.text || '').filter(Boolean).slice(-6).join('\n');
  }

  async function refresh() {
    try {
      const wasInstalling = state?.install.running;
      state = await api('');
      if (wasInstalling && !state.install.running) document.dispatchEvent(new Event('studio:models-changed'));
      el('componentsError').textContent = '';
      if (!state.install.running) for (const id of [...selected]) {
        const row = state.modules.flatMap(m => m.components).find(c => c.id === id);
        if (!row || row.state === 'installed') selected.delete(id);
      }
      if (!el('componentsWorkspace').dataset.rendered || !state.install.running || !el('componentsModules').children.length) {render();el('componentsWorkspace').dataset.rendered = '1';}
      renderProgress();summary();
      if (state.install.running && !timer) timer = setInterval(refresh, 1000);
      if (!state.install.running && timer) {clearInterval(timer);timer = null;render();}
    } catch (error) {el('componentsError').textContent = error.message;}
  }

  async function start() {
    busyLocal = true;el('componentsStart').disabled = true;
    try {
      const tag = el('componentsTextTag') ? el('componentsTextTag').value.trim() : '';
      await api('/install', {models: [...selected], text_models: tag ? [tag] : []});
      selected.clear();if (el('componentsTextTag')) el('componentsTextTag').value = '';
      el('componentsError').textContent = '';
    } catch (error) {el('componentsError').textContent = error.message;}
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
    el('componentRecoveryWait').textContent = `Осталось ${seconds} с. Затем установщик пропустит файл.`;
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
      if (!request || request.remaining_seconds <= 0) {if (recoveryDialog.open) recoveryDialog.close();recoveryRequest = null;return;}
      if (request.token !== recoveryRequest?.token) {
        if (document.querySelector('dialog[open]') && !recoveryDialog.open) return;
        recoveryRequest = request;recoveryDeadline = performance.now() + request.remaining_seconds * 1000;
        el('componentRecoveryName').textContent = `${request.title}: ${request.name} (${gb(request.bytes)})`;
        el('componentRecoveryUrl').value = '';el('componentRecoveryPath').value = '';el('componentRecoveryError').textContent = '';
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
    } catch (error) {el('componentRecoveryError').textContent = error.message;}
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
    } catch (error) {el('componentRecoveryError').textContent = error.message;}
    finally {el('componentRecoveryBrowse').disabled = false;}
  };
  async function repairModel(id) {
    try {await api('/model-toggle', {id, enabled:true});await refresh();await recoveryPoll();}
    catch (error) {el('componentsError').textContent = error.message;}
  }
  const componentIds = {'image:qwen-edit-2511':'image.model.qwen-edit-2511','image:qwen-image-21-uc-fp8':'image.model.qwen-image-21-uc',
    'video:freevideo-h3-int8':'video.model.h3','video:wan22-ti2v-5b':'video.model.wan-ti2v-5b','video:wan22-s2v-14b':'video.model.wan-s2v-14b','video:ltx25-distilled':'video.model.ltx25',
    'music:ace-step-15-turbo':'music.model.ace-step','music:minimax-music3-int8':'music.model.minimax-music3'};
  window.repairStudioModel = (module, id) => {const component = componentIds[module + ':' + id];if (component) repairModel(component);};
  el('musicEngine').addEventListener('change', () => {const option = el('musicEngine').selectedOptions[0];if (option?.textContent.includes('не установлена') || option?.textContent.includes('недоступна') || option?.textContent.includes('отключена')) window.repairStudioModel('music', el('musicEngine').value);});
  setInterval(recoveryPoll, 1000);setInterval(recoveryTick, 200);recoveryPoll();
  // Other tabs link here: window.showComponents()
  window.showComponents = show;
})();
