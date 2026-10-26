/* Permanent, model-specific requirements beside each module's selector. */
(() => {
  'use strict';
  const fields = {engineSelect:'image', videoEngine:'video', musicEngine:'music', textModel:'text'};
  const blocks = {};
  const item = (tag, text, className) => {const node = document.createElement(tag);node.textContent = text;if (className) node.className = className;return node;};
  const gb = value => Number.isFinite(value) ? `${value.toLocaleString('ru-RU', {maximumFractionDigits:1})} ГБ` : 'Не определено';
  const bytes = value => Number.isFinite(value) ? value === 0 ? '0 ГБ' : value < 1024 ** 3 ? `${(value / 1024 ** 2).toLocaleString('ru-RU', {maximumFractionDigits:1})} МБ` : gb(value / 1024 ** 3) : 'Нет данных';
  function render(id) {
    const select = document.getElementById(id), {details, brief, body, name} = blocks[id];
    const result = window.StudioHardware?.snapshot()?.models[`${fields[id]}:${select.value}`];
    name.textContent = select.selectedOptions[0]?.textContent.split(/ · (?:выпуск|публикация)/)[0] || 'Выберите модель';
    body.replaceChildren();
    if (!result) {brief.textContent = 'Характеристики пока недоступны';body.append(item('p', 'Выберите модель или обновите данные ПК в боковой панели.'));return;}
    const profile = result.profile, storage = profile.storage || {};
    brief.textContent = `VRAM: ${gb(profile.recommended_vram_gb)} рекоменд. · На диске: ${bytes(storage.model_bytes)}`;
    const facts = document.createElement('dl');facts.className = 'requirements-facts';
    const row = (label, value) => facts.append(item('dt', label), item('dd', value));
    row('Система', profile.cuda ? 'Windows x64 · NVIDIA CUDA · процессор x64' : 'Windows x64 · процессор x64; GPU необязательна');
    row('VRAM рекомендуется', gb(profile.recommended_vram_gb));
    row('VRAM для запуска', !profile.cuda ? 'Можно без GPU — через CPU и ОЗУ' : profile.minimum_free_vram_gb > 0 ? `Не менее ${gb(profile.minimum_free_vram_gb)} свободной VRAM — порог запуска` : 'Точный минимум не подтверждён; используйте рекомендуемый профиль');
    row('ОЗУ рекомендуется', gb(profile.recommended_ram_gb) + (profile.minimum_ram_gb ? ` · минимум ${gb(profile.minimum_ram_gb)}` : ''));
    for (const [label, value] of profile.characteristics || []) row(label, value);
    body.append(facts);
    body.append(item('h4', 'Скачивание и место на диске'));
    const disk = document.createElement('dl');disk.className = 'requirements-facts';
    const diskRow = (label, value) => disk.append(item('dt', label), item('dd', value));
    if (storage.source === 'local_ollama') {
      diskRow('Сборка на диске', bytes(storage.model_bytes));
      diskRow('Импорт / перенос', bytes(storage.model_bytes) + ' на другом ПК для одной полной копии');
      diskRow('Скачивание', 'Локальная сборка Ollama; публичный источник не задан');
      diskRow('Программа для запуска', `Ollama · около ${bytes(storage.runtime_disk_bytes)} на диске отдельно`);
    } else {
      diskRow('Скачать модель целиком', bytes(storage.model_download_bytes));
      diskRow('Модель на диске', bytes(storage.model_bytes));
      diskRow('Вся загрузка с нуля', bytes(storage.full_download_bytes) + ' · модель и программы');
      diskRow('Для новой установки', `≈ ${bytes(storage.clean_install_free_bytes)} свободно · с распаковкой и запасом`);
      diskRow('Осталось скачать здесь', bytes(storage.remaining_download_bytes));
      diskRow('Дополнительно свободно', `≈ ${bytes(storage.additional_free_bytes)} для установки здесь`);
      if (storage.shared_model_components?.length) diskRow('Общие файлы включены', storage.shared_model_components.join(', '));
    }
    body.append(disk);
    if (profile.optional_download) body.append(item('p', `Дополнительно, по необходимости: ${profile.optional_download.name} — ${bytes(profile.optional_download.bytes)} загрузки и весов. В основной объём не входит.`, 'requirements-note'));
    body.append(item('p', profile.note, 'requirements-note'));
    body.append(item('p', 'Размеры из каталога выбранной сборки. ГБ = 1024³ байт. Место для результатов, рабочего кеша и файла подкачки понадобится дополнительно. VRAM зависит от кадра и контекста; порог запуска не гарантирует завершение генерации.', 'requirements-note'));
    details.dataset.status = result.status;
  }
  for (const [id, module] of Object.entries(fields)) {
    const select = document.getElementById(id), details = document.createElement('details'), summary = document.createElement('summary');
    details.className = 'model-requirements';details.id = `${module}ModelRequirements`;
    summary.append(item('strong', 'Требования модели'));
    const brief = item('span', 'Определяем требования…', 'requirements-preview');summary.append(brief);
    const body = document.createElement('div');body.className = 'requirements-body';
    const name = item('p', '', 'requirements-name');details.append(summary, name, body);
    const anchor = id === 'musicEngine' ? select.closest('.music-grid') : select;
    anchor.insertAdjacentElement('afterend', details);
    blocks[id] = {details, brief, body, name};
    select.addEventListener('change', () => render(id));
    new MutationObserver(() => render(id)).observe(select, {childList:true});
    render(id);
  }
  document.addEventListener('studio:hardware', () => Object.keys(fields).forEach(render));
})();
