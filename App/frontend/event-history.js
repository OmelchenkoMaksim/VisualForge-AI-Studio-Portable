/* Unified local event history; click analytics is intentionally not collected here. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const dialog = el('eventHistoryDialog'), entries = el('eventHistoryEntries'), status = el('eventHistoryStatus');
  const labels = {running:'Без отметки о завершении', done:'Завершено', error:'Ошибка', cancelled:'Отменено', interrupted:'Не завершено'};
  let sequence = 0, controller = null, cursor = null, rows = [], types = new Map();

  async function request(url, data=undefined, keepalive=false) {
    let response;
    try { response = await fetch(url, data === undefined ? {cache:'no-store', signal:controller?.signal} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data), keepalive
    }); } catch (error) {if(error.name==='AbortError')throw error;throw new Error('Нет связи со студией. Проверьте, запущено ли приложение.');}
    let value;
    try {value=await response.json();}catch {throw new Error('Некорректный ответ истории событий.');}
    if (!response.ok) throw new Error(value?.error || 'История событий недоступна.');
    if (data === undefined && (!value || !Array.isArray(value.entries) || !Array.isArray(value.types))) throw new Error('Некорректные данные истории событий.');
    return value;
  }

  function render(row) {
    const record = row.generation, card = document.createElement('article'); card.className='event-history-card';
    const title = document.createElement('p'); title.className='event-history-title';
    const seconds = record.duration_sec === null ? (row.state === 'running' ? 'итоговое время ещё не записано' : 'время неизвестно') : record.duration_sec.toLocaleString('ru-RU')+' с';
    title.textContent = new Date(record.started_at).toLocaleString('ru-RU')+' · '+(types.get(record.type) || record.type)+' · '+(labels[row.state] || row.state)+' · '+seconds;
    const id = document.createElement('code'); id.textContent=record.id;
    const kind = document.createElement('p'); kind.className='muted';
    kind.textContent=(row.technical ? 'Техническая операция' : 'Генерация / обработка результата')+' · '+record.type;
    const copy = document.createElement('button'); copy.type='button'; copy.className='secondary slim'; copy.textContent='Скопировать объект';
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(record,null,2));copy.textContent='Скопировано';}catch{copy.textContent='Не удалось скопировать';}};
    card.append(title, id, kind, copy);
    if (['ImagePromptExpansion','ImageUpscale'].includes(record.type)) {const timing=document.createElement('p');timing.className='muted';timing.textContent='Время включает ожидание и выполнение общего графа ComfyUI; это не профиль отдельной нейросети.';card.append(timing);}
    if (row.parent_id) {const parent=document.createElement('p');parent.className='muted';parent.textContent='Родительская операция: '+row.parent_id;card.append(parent);}
    return card;
  }

  async function load(more=false) {
    const ticket=++sequence;
    controller?.abort();controller=new AbortController();
    if (!more) {cursor=null;rows=[];entries.replaceChildren();el('eventHistoryMore').hidden=true;}
    el('eventHistoryMore').disabled=true;status.textContent='Загрузка событий…';
    const query=new URLSearchParams({scope:el('eventHistoryScope').value,type:el('eventHistoryType').value,state:el('eventHistoryState').value});
    if(more && cursor)query.set('before',cursor);
    try {
      const value=await request('/api/event-history?'+query);
      if(ticket!==sequence || !dialog.open)return;
      if(!types.size) {
        types=new Map(value.types.map(row=>[row.id,row.label]));
        for(const row of value.types) {const option=document.createElement('option');option.value=row.id;option.textContent=row.label;el('eventHistoryType').append(option);}
      }
      rows.push(...value.entries);cursor=value.next;
      for(const row of value.entries)entries.append(render(row));
      status.textContent=value.warning || (rows.length ? 'Показано событий: '+rows.length+'. Сначала новые.' : 'Событий пока нет. Они начнут сохраняться после обновления студии.');
      el('eventHistoryMore').hidden=!cursor;el('eventHistoryMore').disabled=false;
    } catch(error) {
      if(ticket!==sequence || error.name==='AbortError')return;
      status.textContent=error.message || 'Не удалось загрузить историю событий.';
      el('eventHistoryMore').disabled=false;
    }
  }

  el('openEventHistory').addEventListener('click',()=>{if(!dialog.open)dialog.showModal();void load();});
  el('eventHistoryClose').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{++sequence;controller?.abort();el('studioSettingsToggle').focus();});
  el('eventHistoryRefresh').addEventListener('click',()=>load());
  el('eventHistoryMore').addEventListener('click',()=>load(true));
  for(const id of ['eventHistoryScope','eventHistoryType','eventHistoryState'])el(id).addEventListener('change',()=>load());

  const microphones=new Set();
  async function microphoneStart() {
    try {const value=await request('/api/event-history/session',{action:'start'});microphones.add(value.generation.id);return value.generation;}
    catch {return null;} // Diagnostics must never prevent the browser's microphone permission flow.
  }
  async function microphoneFinish(pending, state='done') {
    try {
      const record=await pending;
      if(record && microphones.delete(record.id))await request('/api/event-history/session',{action:'finish',id:record.id,state},true);
    } catch {}
  }
  window.addEventListener('pagehide',()=>{for(const id of microphones)void microphoneFinish({id},'interrupted');});
  window.StudioEvents={microphoneStart,microphoneFinish};
})();
