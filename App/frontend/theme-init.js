/* Applies the saved light/dark theme and its color scheme before the first paint (loaded synchronously in <head>), so a light-theme user never sees a dark flash. */
(() => {
  'use strict';
  let theme = 'dark';
  try { if (localStorage.getItem('vf-theme') === 'light') theme = 'light'; } catch {}
  document.documentElement.dataset.theme = theme;
  // The scheme is chosen separately for each theme (color-palettes.js owns the list); an id with no CSS block simply keeps the default look.
  try {
    const scheme = localStorage.getItem('vf-palette-' + theme);
    if (scheme && /^[a-z]{1,24}$/.test(scheme)) document.documentElement.dataset.palette = scheme;
  } catch {}
})();
