/* Encoder-limited image references and FLUX layout; the shared request lifecycle stays in app.js. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const rows = el('fluxLayoutRows'), add = el('fluxLayoutAdd'), extras = el('fluxExtraImages');
  let currentMode = 'single', locked = false;
  let maxExtras = 8;

  function refreshRows() {
    [...rows.children].forEach((row, index) => {
      StudioI18n.bind(row.querySelector('legend'), 'textContent', () => StudioI18n.t('image.flux_layout_object_label', [index + 1]), false);
      row.querySelector('button').disabled = locked || rows.children.length === 1;
      for (const input of row.querySelectorAll('input')) input.disabled = locked || currentMode !== 'layout';
    });
    add.disabled = locked || rows.children.length >= 8;
  }

  function addRow() {
    if (rows.children.length >= 8) return;
    const row = document.createElement('fieldset'); row.className = 'flux-layout-row';
    row.append(document.createElement('legend'));
    const fields = {desc:'image.flux_layout_description_label', top:'image.flux_layout_top_label', left:'image.flux_layout_left_label', bottom:'image.flux_layout_bottom_label', right:'image.flux_layout_right_label'};
    for (const [field, key] of Object.entries(fields)) {
      const label = document.createElement('label'), title = document.createElement('span'), input = document.createElement('input');
      StudioI18n.bind(title, 'textContent', () => StudioI18n.t(key), false);
      input.dataset.field = field; input.required = true;
      if (field === 'desc') {input.type = 'text'; input.maxLength = 600; label.className = 'flux-box-description';}
      else {input.type = 'number'; input.min = '0'; input.max = '100'; input.step = '1'; input.value = ['top','left'].includes(field) ? '10' : '90';}
      label.append(title, input); row.append(label);
    }
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary slim';
    StudioI18n.bind(remove, 'textContent', () => StudioI18n.t('image.flux_layout_remove_label'), false);
    remove.addEventListener('click', () => {row.remove(); refreshRows();}); row.append(remove);
    rows.append(row); refreshRows();
  }
  add.addEventListener('click', addRow);
  addRow();
  extras.addEventListener('change', () => {
    el('fluxExtraNames').replaceChildren();
    for (const file of extras.files) {
      const item = document.createElement('li'); item.textContent = file.name; el('fluxExtraNames').append(item);
    }
  });
  window.FluxImageControls = {
    render(mode, busy, info) {
      currentMode = mode; locked = busy;
      maxExtras = Math.max(0, (info?.max_reference_images || 10) - 2);
      const three = maxExtras === 1;
      extras.multiple = !three;
      StudioI18n.bind(el('extraImagesLabel'), 'textContent', () => StudioI18n.t(three ? 'image.qwen_third_image_label' : 'image.flux_extra_images_label'), false);
      StudioI18n.bind(el('extraImagesHint'), 'textContent', () => StudioI18n.t(three ? 'image.qwen_third_image_hint' : info?.id === 'qwen-image-21-uc-fp8' ? 'image.qwen21_extra_images_hint' : 'image.flux_extra_images_hint'), false);
      el('fluxLayoutGroup').hidden = mode !== 'layout';
      el('fluxExtraGroup').hidden = mode !== 'multi';
      extras.required = mode === 'multi'; extras.disabled = busy || mode !== 'multi';
      refreshRows();
    },
    read(source, reference, isVariant, prior) {
      const files = currentMode === 'multi' ? (isVariant ? prior.extraFiles : Array.from(extras.files)) : [];
      if (currentMode === 'multi' && (!files?.length || files.length > maxExtras ||
          [source, reference, ...files].reduce((sum, file) => sum + (file?.size || 0), 0) > 100 * 1024 * 1024)) {
        throw new Error(StudioI18n.t('image.extra_images_count_error', [maxExtras]));
      }
      const boxes = currentMode === 'layout' && !isVariant ? [...rows.children].map((row, index) => ({
        id:'obj_' + (index + 1), desc:row.querySelector('[data-field="desc"]').value.trim(),
        bbox:['top','left','bottom','right'].map(field => Number(row.querySelector(`[data-field="${field}"]`).value) * 10)
      })) : undefined;
      return {files, boxes};
    }
  };
})();
