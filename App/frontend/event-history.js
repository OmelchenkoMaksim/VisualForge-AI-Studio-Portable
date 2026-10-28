/* Unified local event history; click analytics is intentionally not collected here. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const dialog = el('eventHistoryDialog'), entries = el('eventHistoryEntries'), status = el('eventHistoryStatus');
  const labels = {running:StudioI18n.t("events.history_entry_completion_unrecorded_status"), done:StudioI18n.t("events.history_entry_completed_status"), error:StudioI18n.t("events.history_entry_failed_status"), cancelled:StudioI18n.t("events.history_entry_cancelled_status"), interrupted:StudioI18n.t("events.history_entry_incomplete_status")};
  let sequence = 0, controller = null, cursor = null, rows = [], types = new Map();

  async function request(url, data=undefined, keepalive=false) {
    let response;
    try { response = await fetch(url, data === undefined ? {cache:'no-store', signal:controller?.signal} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data), keepalive
    }); } catch (error) {if(error.name==='AbortError')throw error;throw new Error(StudioI18n.t("events.history_request_studio_connection_failed"));}
    let value;
    try {value=await response.json();}catch {throw new Error(StudioI18n.t("events.history_request_invalid_response_error"));}
    if (!response.ok) throw new Error(StudioI18n.remote(value?.error) || StudioI18n.t("events.history_request_service_unavailable_error"));
    if (data === undefined && (!value || !Array.isArray(value.entries) || !Array.isArray(value.types))) throw new Error(StudioI18n.t("events.history_request_invalid_entries_error"));
    return value;
  }

  function render(row) {
    const record = row.generation, card = document.createElement('article'); card.className='event-history-card';
    const title = document.createElement('p'); title.className='event-history-title';
    const seconds = () => record.duration_sec === null ? (row.state === 'running' ? StudioI18n.t("events.history_entry_duration_pending_label") : StudioI18n.t("events.history_entry_duration_unknown_label")) : record.duration_sec.toLocaleString(StudioI18n.locale)+StudioI18n.t("events.history_entry_duration_seconds_suffix");
    StudioI18n.bind(title, "textContent", () => (new Date(record.started_at).toLocaleString(StudioI18n.locale)+' · '+StudioI18n.remote(types.get(record.type) || record.type)+' · '+StudioI18n.remote(labels[row.state] || row.state)+' · '+seconds()), false);
    const id = document.createElement('code'); StudioI18n.bind(id, "textContent", () => (record.id), true);
    const kind = document.createElement('p'); kind.className='muted';
    StudioI18n.bind(kind, "textContent", () => ((row.technical ? StudioI18n.t("events.history_entry_technical_operation_label") : StudioI18n.t("events.history_entry_generation_operation_label"))+' · '+record.type), true);
    const copy = document.createElement('button'); copy.type='button'; copy.className='secondary slim'; StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("events.history_entry_copy_identity_button_label")), true);
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(record,null,2));StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("events.history_entry_identity_copy_success")), true);}catch{StudioI18n.bind(copy, "textContent", () => (StudioI18n.t("events.history_entry_identity_copy_failed")), true);}};
    card.append(title, id, kind, copy);
    if (['ImagePromptExpansion','ImageUpscale'].includes(record.type)) {const timing=document.createElement('p');timing.className='muted';StudioI18n.bind(timing, "textContent", () => (StudioI18n.t("events.history_entry_shared_comfy_graph_duration_hint")), true);card.append(timing);}
    if (row.parent_id) {const parent=document.createElement('p');parent.className='muted';StudioI18n.bind(parent, "textContent", () => (StudioI18n.t("events.history_entry_parent_operation_prefix")+row.parent_id), true);card.append(parent);}
    return card;
  }

  async function load(more=false) {
    const ticket=++sequence;
    controller?.abort();controller=new AbortController();
    if (!more) {cursor=null;rows=[];entries.replaceChildren();el('eventHistoryMore').hidden=true;}
    el('eventHistoryMore').disabled=true;StudioI18n.bind(status, "textContent", () => (StudioI18n.t("events.history_entries_loading_status")), true);
    const query=new URLSearchParams({scope:el('eventHistoryScope').value,type:el('eventHistoryType').value,state:el('eventHistoryState').value});
    if(more && cursor)query.set('before',cursor);
    try {
      const value=await request('/api/event-history?'+query);
      if(ticket!==sequence || !dialog.open)return;
      if(!types.size) {
        types=new Map(value.types.map(row=>[row.id,row.label]));
        for(const row of value.types) {const option=document.createElement('option');option.value=row.id;StudioI18n.bind(option, "textContent", () => (row.label), true);el('eventHistoryType').append(option);}
      }
      rows.push(...value.entries);cursor=value.next;
      for(const row of value.entries)entries.append(render(row));
      StudioI18n.bind(status, "textContent", () => (StudioI18n.remote(value.warning) || (rows.length ? StudioI18n.t("events.history_loaded_entries_count_prefix")+rows.length+StudioI18n.t("events.history_newest_first_order_suffix") : StudioI18n.t("events.history_entries_empty_state_hint"))), true);
      el('eventHistoryMore').hidden=!cursor;el('eventHistoryMore').disabled=false;
    } catch(error) {
      if(ticket!==sequence || error.name==='AbortError')return;
      StudioI18n.bind(status, "textContent", () => (StudioI18n.remote(error.message) || StudioI18n.t("events.history_entries_loading_failed_error")), true);
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
