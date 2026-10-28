/* Hardware stays local. A single compatibility card follows the user's model choice. */
(() => {
  'use strict';
  const aside = document.getElementById('hardwarePanel');
  const popup = document.getElementById('compatibilityCard');
  const selectors = {engineSelect:'image', videoEngine:'video', musicEngine:'music', textModel:'text'};
  // The card is a popover under the top-bar chip (shell.js), so its parent must not grow to the card's height.
  let data, pending, selection = '', failed = false;
  const el = id => document.getElementById(id);
  const node = (tag, text, className) => {const item = document.createElement(tag);StudioI18n.bind(item, "textContent", () => (text), true);if (className) item.className = className;return item;};
  const gb = value => Number.isFinite(value) ? StudioI18n.t("hardware.memory_size_gigabytes_template", [(Math.abs(value - Math.round(value)) < .15 ? Math.round(value) : value).toLocaleString(StudioI18n.locale, {maximumFractionDigits:1})]) : StudioI18n.t("hardware.specifications_missing_value_label_machine_no_data");
  const popupKey = 'vf-compatibility-popup';
  let preferences = readPreferences(), hideTimer = null, openRequest = 0, automatic = false, closingAnimation = null;
  function readPreferences() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(popupKey) || 'null'); } catch {}
    return {
      enabled: typeof saved?.enabled === 'boolean' ? saved.enabled : true,
      seconds: Number.isFinite(saved?.seconds) ? Math.max(1, Math.min(30, Math.round(saved.seconds))) : 3
    };
  }
  function closePopup() {
    clearTimeout(hideTimer);hideTimer = null;
    automatic = false;openRequest++;
    if (popup.hidden || closingAnimation) return;
    const animation = popup.getAnimations().find(item => item.animationName === 'vf-in');
    if (!animation) {popup.hidden = true;return;}
    // Keep the card rendered until its entrance animation has played backwards.
    closingAnimation = animation;
    animation.reverse();
    const finish = () => {
      if (closingAnimation !== animation) return;
      popup.hidden = true;closingAnimation = null;
    };
    animation.finished.then(finish, finish);
  }
  function scheduleHide() {
    clearTimeout(hideTimer);hideTimer = null;
    if (automatic && !popup.hidden) hideTimer = setTimeout(closePopup, preferences.seconds * 1000);
  }
  function requestPopup(manual = false) {
    if (!manual && !preferences.enabled) {closePopup();show();return;}
    const version = ++openRequest;
    if (!data || failed) {
      // A late hardware response must not reopen a card dismissed or disabled while loading.
      load().then(() => {if (version === openRequest) show(true, manual);}).catch(() => {});
    } else show(true, manual);
  }
  function syncPopupSettings() {
    el('compatibilityPopupEnabled').checked = preferences.enabled;
    el('compatibilityPopupSeconds').value = String(preferences.seconds);
    el('compatibilityPopupSeconds').disabled = !preferences.enabled;
    StudioI18n.bind(el('compatibilityPopupSecondsValue'), "textContent", () => (StudioI18n.t("hardware.duration_seconds_template", [preferences.seconds])), true);
  }
  function savePopupSettings() {
    try { localStorage.setItem(popupKey, JSON.stringify(preferences)); } catch {}
    syncPopupSettings();
    if (!preferences.enabled) closePopup();else scheduleHide();
  }
  const popupSettings = el('compatibilitySettingsDialog');
  el('openCompatibilitySettings').addEventListener('click', () => {closePopup();syncPopupSettings();popupSettings.showModal();});
  for (const id of ['compatibilitySettingsClose', 'compatibilitySettingsDone']) el(id).addEventListener('click', () => popupSettings.close());
  popupSettings.addEventListener('click', event => {if (event.target === popupSettings) popupSettings.close();});
  el('compatibilityPopupEnabled').addEventListener('change', event => {preferences.enabled = event.target.checked;savePopupSettings();});
  el('compatibilityPopupSeconds').addEventListener('input', event => {
    preferences.seconds = Math.max(1, Math.min(30, Math.round(Number(event.target.value) || 3)));
    savePopupSettings();
  });
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
    const row = (field, label, value) => {const term=node('dt',label);term.dataset.hardwareField=field;list.append(term,node('dd',value));};
    row('cpu', StudioI18n.t("hardware.hardware_cpu_specification_label"), hw.cpu);
    row('gpu', StudioI18n.t("hardware.hardware_gpu_specification_label"), hw.gpus.map(g => g.name).join(' · ') || StudioI18n.t("hardware.machine_not_detected"));
    row('vram', StudioI18n.t("hardware.hardware_vram_specification_label"), hw.gpus.length ? hw.gpus.map(g => gb(g.vram_gb)).join(' · ') : StudioI18n.t("hardware.specifications_missing_value_label_machine_no_data"));
    row('ram', StudioI18n.t("hardware.hardware_ram_specification_label_machine"), gb(hw.ram_gb));
    StudioI18n.bind(el('hardwareFootnote'), "textContent", () => (hw.gpus.length > 1 ? StudioI18n.t("hardware.footnote_memory_different_graphics_cards_not_combined") : StudioI18n.t("hardware.footnote_pc_s_data_local")), true);
    aside.setAttribute('aria-busy', 'false');
  }
  function show(open = false, manual = false) {
    const chosen = current();
    if (!chosen) {el('hardwareCompatibility').hidden = true;closePopup();return;}
    el('hardwareCompatibility').hidden = false;
    const result = data?.models[chosen.key] || (data ? {
      status:'yellow', title:StudioI18n.t("hardware.compatibility_missing_model_profile_title"), reasons:[StudioI18n.t("hardware.reasons_build_has_no_requirements_profile_yet")],
      profile:{name:chosen.select.value, note:StudioI18n.t("hardware.note_refresh_data_after_installing_new_model"), recommended_vram_gb:null, recommended_ram_gb:null},
      actual_vram_gb:data.hardware.gpus.find(g => g.cuda)?.vram_gb ?? null, actual_ram_gb:data.hardware.ram_gb, caveat:StudioI18n.t("hardware.caveat_compatibility_not_confirmed")
    } : null);
    const summary = el('hardwareCompatibility');
    selection = chosen.key;
    summary.dataset.status = result?.status || 'yellow';
    StudioI18n.bind(summary, "textContent", () => (result ? `${{green:'✓',yellow:'!',red:'×'}[result.status]} ${result.title}` : failed ? StudioI18n.t("hardware.summary_check_failed_refresh_data") : StudioI18n.t("hardware.summary_checking_compatibility")), true);
    if (!result) return;
    if (!open && popup.hidden) return;
    popup.dataset.status = result.status;
    StudioI18n.bind(el('compatibilityTitle'), "textContent", () => (result.title), true);
    StudioI18n.bind(el('compatibilityModel'), "textContent", () => (chosen.select.selectedOptions[0]?.textContent.split(/ · (?:выпуск|публикация)/)[0] || result.profile.name), true);
    el('compatibilityReasons').replaceChildren(...result.reasons.map(reason => node('li', reason)));
    const body = el('compatibilityMetrics');
    body.replaceChildren();
    for (const [label, recommended, actual] of [[StudioI18n.t("hardware.hardware_vram_specification_label"), result.profile.recommended_vram_gb, result.actual_vram_gb], [StudioI18n.t("hardware.hardware_ram_specification_label_machine"), result.profile.recommended_ram_gb, result.actual_ram_gb]]) {
      const tr = document.createElement('tr');tr.append(node('th', label), node('td', gb(recommended)), node('td', gb(actual)));body.append(tr);
    }
    StudioI18n.bind(el('compatibilityProfile'), "textContent", () => (StudioI18n.remote(result.profile.note)), true);
    StudioI18n.bind(el('compatibilityCaveat'), "textContent", () => (StudioI18n.remote(result.caveat)), true);
    StudioI18n.bind(el('compatibilityGpu'), "textContent", () => (result.gpu_name ? StudioI18n.t("hardware.compatibility_gpu_estimate", [result.gpu_name]) : StudioI18n.t("hardware.compatibility_gpu_cuda_accelerator_not_confirmed")), true);
    if (open && closingAnimation) {
      // Reopening mid-close reverses direction from the current frame without a jump.
      const animation = closingAnimation;closingAnimation = null;animation.reverse();
    }
    popup.hidden = false;
    if (open) {automatic = !manual;scheduleHide();}
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
        failed = true;data = null;closePopup();aside.setAttribute('aria-busy','false');
        StudioI18n.bind(el('hardwareFootnote'), "textContent", () => (StudioI18n.t("hardware.footnote_retrieve_data_click_refresh_failed")), true);
        show();document.dispatchEvent(new Event('studio:hardware'));throw error;
      })
      .finally(() => {el('hardwareRefresh').disabled = false;pending = null;});
    return pending;
  }
  el('hardwareRefresh').onclick = () => load(true).catch(() => {});
  el('hardwareCompatibility').onclick = () => requestPopup(true);
  el('compatibilityClose').onclick = closePopup;
  document.addEventListener('keydown', event => {if (event.key === 'Escape') closePopup();});
  document.addEventListener('change', event => {if (selectors[event.target.id]) requestPopup();});
  document.querySelector('.module-tabs').addEventListener('click', () => setTimeout(() => {closePopup();show();}, 0));
  new MutationObserver(() => {closePopup();show();}).observe(document.body, {attributes:true, attributeFilter:['data-studio-module']});
  // Modules fill their selectors asynchronously; refresh the current summary, never open a card by itself.
  for (const id of Object.keys(selectors)) new MutationObserver(() => {if (current()?.key !== selection) show();}).observe(el(id), {childList:true});
  window.StudioHardware = {snapshot: () => data};
  window.addEventListener('studio-languagechange', () => {if(data){machine();show();}});
  load().catch(() => {});
})();
