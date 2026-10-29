/* Color schemes: the dark and the light theme each have their own chosen scheme (color-palettes.css, defaults in studio-theme.css).
   The choice is stored per browser profile (vf-palette-dark / vf-palette-light); theme-init.js applies it before the first paint
   and this script keeps <html data-palette> in step with the theme and drives the dialog opened from the gear menu. */
(() => {
  'use strict';
  const root = document.documentElement;
  const el = id => document.getElementById(id);
  // `indigo` is the look defined by studio-theme.css itself; the others are blocks in palettes.css.
  const SCHEMES = [
    {id: 'indigo', name: 'settings.color_scheme_indigo_name_label'},
    {id: 'ocean', name: 'settings.color_scheme_ocean_name_label'},
    {id: 'emerald', name: 'settings.color_scheme_emerald_name_label'},
    {id: 'sunset', name: 'settings.color_scheme_sunset_name_label'},
    {id: 'orchid', name: 'settings.color_scheme_orchid_name_label'},
    {id: 'graphite', name: 'settings.color_scheme_graphite_name_label'}
  ];
  const MODES = ['dark', 'light'];
  const known = id => SCHEMES.some(scheme => scheme.id === id);
  const keyOf = mode => 'vf-palette-' + mode;

  function chosen(mode) {
    let id = 'indigo';
    try { const saved = localStorage.getItem(keyOf(mode)); if (known(saved)) id = saved; } catch {}
    return id;
  }
  // The attribute is what the CSS reads; the default scheme has no block, so it is removed instead of set.
  function sync() {
    const id = chosen(root.dataset.theme === 'light' ? 'light' : 'dark');
    if (id === 'indigo') delete root.dataset.palette; else root.dataset.palette = id;
  }
  function choose(mode, id) {
    if (!MODES.includes(mode) || !known(id)) return;
    try {
      if (id === 'indigo') localStorage.removeItem(keyOf(mode)); else localStorage.setItem(keyOf(mode), id);
    } catch {}
    sync();
    render();
  }
  window.StudioPalettes = {SCHEMES, chosen, choose, sync};
  addEventListener('storage', event => { if (!event.key || event.key.startsWith('vf-palette-') || event.key === 'vf-theme') { sync(); render(); } });

  const dialog = el('palettesDialog');
  if (!dialog) { sync(); return; }

  // One radio group per theme; each card previews its scheme inside a miniature window painted with that theme's own tokens.
  function buildGroup(mode) {
    const group = el(mode === 'dark' ? 'palettesDark' : 'palettesLight');
    group.textContent = '';
    for (const scheme of SCHEMES) {
      const label = document.createElement('label');
      label.className = 'palette-card';
      const input = document.createElement('input');
      input.type = 'radio'; input.name = 'palette-' + mode; input.value = scheme.id;
      input.addEventListener('change', () => { if (input.checked) choose(mode, scheme.id); });
      const preview = document.createElement('span');
      preview.className = 'palette-preview'; preview.dataset.theme = mode; preview.setAttribute('aria-hidden', 'true');
      if (scheme.id !== 'indigo') preview.dataset.palette = scheme.id;
      for (const part of ['rail', 'card', 'bar']) { const piece = document.createElement('i'); piece.className = 'pv-' + part; preview.append(piece); }
      const name = document.createElement('span');
      name.className = 'palette-name';
      StudioI18n.bind(name, 'textContent', () => StudioI18n.t(scheme.name), true);
      label.append(input, preview, name);
      group.append(label);
    }
  }
  function render() {
    if (!dialog) return;
    for (const mode of MODES) {
      const group = el(mode === 'dark' ? 'palettesDark' : 'palettesLight');
      const current = chosen(mode);
      for (const input of group.querySelectorAll('input')) input.checked = input.value === current;
      // The group of the theme on screen is marked, so it is clear which choice is visible right now.
      group.closest('fieldset').classList.toggle('active-theme', (root.dataset.theme === 'light' ? 'light' : 'dark') === mode);
    }
  }
  for (const mode of MODES) buildGroup(mode);
  el('openPalettes').addEventListener('click', () => { render(); dialog.showModal(); });
  el('palettesReset').addEventListener('click', () => { for (const mode of MODES) choose(mode, 'indigo'); });
  for (const id of ['palettesClose', 'palettesDone']) el(id).addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  // The theme switch changes which group is on screen.
  new MutationObserver(() => { sync(); render(); }).observe(root, {attributes: true, attributeFilter: ['data-theme']});
  sync();
  render();
})();
