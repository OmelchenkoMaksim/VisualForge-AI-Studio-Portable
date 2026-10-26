/* Applies the saved light/dark theme before the first paint (loaded synchronously in <head>), so a light-theme user never sees a dark flash. */
(() => {
  'use strict';
  let theme = 'dark';
  try { if (localStorage.getItem('vf-theme') === 'light') theme = 'light'; } catch {}
  document.documentElement.dataset.theme = theme;
})();
