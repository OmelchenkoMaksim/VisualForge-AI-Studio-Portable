/* Permanent, model-specific requirements beside each module's selector. */
(() => {
  'use strict';
  const fields = {engineSelect:'image', videoEngine:'video', musicEngine:'music', textModel:'text'};
  const blocks = {};
  const item = (tag, text, className) => {const node = document.createElement(tag);StudioI18n.bind(node, "textContent", () => (text), true);if (className) node.className = className;return node;};
  const gb = value => Number.isFinite(value) ? StudioI18n.t("hardware.memory_size_gigabytes_template", [value.toLocaleString(StudioI18n.locale, {maximumFractionDigits:1})]) : StudioI18n.t("hardware.model_requirements_gb_not_detected");
  const bytes = value => Number.isFinite(value) ? value === 0 ? StudioI18n.t("hardware.storage_size_zero_gigabytes_label") : value < 1024 ** 3 ? StudioI18n.t("hardware.memory_size_megabytes_template", [(value / 1024 ** 2).toLocaleString(StudioI18n.locale, {maximumFractionDigits:1})]) : gb(value / 1024 ** 3) : StudioI18n.t("hardware.specifications_missing_value_label_machine_no_data");
  function render(id) {
    const select = document.getElementById(id), {details, brief, body, name} = blocks[id];
    const result = window.StudioHardware?.snapshot()?.models[`${fields[id]}:${select.value}`];
    StudioI18n.bind(name, "textContent", () => (select.selectedOptions[0]?.textContent.split(/ · (?:выпуск|публикация)/)[0] || StudioI18n.t("hardware.name_select_model")), true);
    body.replaceChildren();
    if (!result) {StudioI18n.bind(brief, "textContent", () => (StudioI18n.t("hardware.brief_specifications_currently_unavailable")), true);body.append(item('p', StudioI18n.t("hardware.requirements_select_model_or_refresh_hint")));return;}
    const profile = result.profile, storage = profile.storage || {};
    StudioI18n.bind(brief, "textContent", () => (StudioI18n.t("hardware.brief_vram_recommended_disk", [gb(profile.recommended_vram_gb), bytes(storage.model_bytes)])), true);
    const facts = document.createElement('dl');facts.className = 'requirements-facts';
    const row = (label, value) => facts.append(item('dt', label), item('dd', value));
    row(StudioI18n.t("hardware.requirements_system_row_label"), profile.cuda ? StudioI18n.t("hardware.requirements_cuda_windows_system_profile") : StudioI18n.t("hardware.requirements_cpu_windows_system_profile"));
    row(StudioI18n.t("hardware.requirements_recommended_vram_row_label"), gb(profile.recommended_vram_gb));
    row(StudioI18n.t("hardware.requirements_startup_vram_threshold_row_label"), !profile.cuda ? StudioI18n.t("hardware.requirements_cpu_execution_available_hint") : profile.minimum_free_vram_gb > 0 ? StudioI18n.t("hardware.requirements_minimum_free_vram_template", [gb(profile.minimum_free_vram_gb)]) : StudioI18n.t("hardware.requirements_unknown_minimum_memory_hint"));
    row(StudioI18n.t("hardware.requirements_recommended_ram_row_label"), gb(profile.recommended_ram_gb) + (profile.minimum_ram_gb ? StudioI18n.t("hardware.requirements_minimum_memory_value_suffix", [gb(profile.minimum_ram_gb)]) : ''));
    for (const [label, value] of profile.characteristics || []) row(label, value);
    body.append(facts);
    body.append(item('h4', StudioI18n.t("hardware.requirements_storage_download_section_title")));
    const disk = document.createElement('dl');disk.className = 'requirements-facts';
    const diskRow = (label, value) => disk.append(item('dt', label), item('dd', value));
    if (storage.source === 'local_ollama') {
      diskRow(StudioI18n.t("hardware.requirements_installed_build_size_label"), bytes(storage.model_bytes));
      diskRow(StudioI18n.t("hardware.requirements_import_transfer_row_label"), bytes(storage.model_bytes) + StudioI18n.t("hardware.requirements_import_full_copy_space_suffix"));
      diskRow(StudioI18n.t("hardware.requirements_download_size_row_label"), StudioI18n.t("hardware.requirements_local_ollama_source_unknown_hint"));
      diskRow(StudioI18n.t("hardware.requirements_runtime_software_row_label"), StudioI18n.t("hardware.requirements_ollama_runtime_disk_size_template", [bytes(storage.runtime_disk_bytes)]));
    } else {
      diskRow(StudioI18n.t("hardware.requirements_full_model_download_row_label"), bytes(storage.model_download_bytes));
      diskRow(StudioI18n.t("hardware.requirements_model_disk_size_row_label"), bytes(storage.model_bytes));
      diskRow(StudioI18n.t("hardware.requirements_fresh_download_total_row_label"), bytes(storage.full_download_bytes) + StudioI18n.t("hardware.requirements_model_and_software_total_suffix"));
      diskRow(StudioI18n.t("hardware.requirements_fresh_installation_space_row_label"), StudioI18n.t("hardware.requirements_fresh_installation_space_template", [bytes(storage.clean_install_free_bytes)]));
      diskRow(StudioI18n.t("hardware.requirements_remaining_download_row_label"), bytes(storage.remaining_download_bytes));
      diskRow(StudioI18n.t("hardware.requirements_additional_free_space_row_label"), StudioI18n.t("hardware.requirements_current_installation_space_template", [bytes(storage.additional_free_bytes)]));
      if (storage.shared_model_components?.length) diskRow(StudioI18n.t("hardware.requirements_shared_files_included_row_label"), storage.shared_model_components.join(', '));
    }
    body.append(disk);
    if (profile.optional_download) body.append(item('p', StudioI18n.t("hardware.requirements_optional_model_download_hint_template", [profile.optional_download.name, bytes(profile.optional_download.bytes)]), 'requirements-note'));
    body.append(item('p', StudioI18n.remote(profile.note), 'requirements-note'));
    body.append(item('p', StudioI18n.t("hardware.requirements_size_estimates_and_memory_caveat"), 'requirements-note'));
    details.dataset.status = result.status;
  }
  for (const [id, module] of Object.entries(fields)) {
    const select = document.getElementById(id), details = document.createElement('details'), summary = document.createElement('summary');
    details.className = 'model-requirements';details.id = `${module}ModelRequirements`;
    summary.append(item('strong', StudioI18n.t("hardware.model_requirements_panel_title")));
    const brief = item('span', StudioI18n.t("hardware.model_requirements_brief_checking"), 'requirements-preview');summary.append(brief);
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
  window.addEventListener('studio-languagechange', () => Object.keys(fields).forEach(render));
})();
