/* Application shell behaviour: theme switch, hardware chip, section-rail indicator and collapsible explanations.
   It only reads the module scripts' DOM state (aria-selected, hardware texts) and never takes over their handlers. */
(() => {
  'use strict';
  const root = document.documentElement;
  const el = id => document.getElementById(id);
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const brandIcon = document.querySelector('.brand-symbol img');
  if (brandIcon) {
    // CSP blocks inline handlers; a cached load failure can precede this deferred script.
    const hideBrokenIcon = () => { brandIcon.hidden = true; };
    brandIcon.addEventListener('error', hideBrokenIcon);
    if (brandIcon.complete && brandIcon.naturalWidth === 0) hideBrokenIcon();
  }

  // ---- light / dark ------------------------------------------------------------------------------------------------
  const themeButton = el('themeToggle');
  function labelTheme() {
    const light = root.dataset.theme === 'light';
    themeButton.setAttribute('aria-label', light ? 'Включить тёмную тему' : 'Включить светлую тему');
    themeButton.title = themeButton.getAttribute('aria-label');
  }
  function applyTheme(theme) {
    root.dataset.theme = theme;
    try { localStorage.setItem('vf-theme', theme); } catch {}
    labelTheme();
  }
  themeButton.addEventListener('click', () => {
    const next = root.dataset.theme === 'light' ? 'dark' : 'light';
    // The View Transitions API snapshots the page once and reveals the new theme as a growing circle from the button:
    // a single composited animation instead of transitioning colors on every element.
    if (!document.startViewTransition || reducedMotion()) { applyTheme(next); return; }
    const box = themeButton.getBoundingClientRect();
    const x = box.left + box.width / 2, y = box.top + box.height / 2;
    // The circle grows 25% past the farthest corner. With the target exactly at the corner, the decelerating curve made
    // the edge crawl across the last corner and then snap when the transition ended; now the edge leaves the screen while
    // still moving, and the remaining frames are already fully covered.
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y)) * 1.25;
    const transition = document.startViewTransition(() => applyTheme(next));
    transition.ready.then(() => {
      root.animate({clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`]},
        {duration: 560, easing: 'cubic-bezier(.45,0,.3,1)', pseudoElement: '::view-transition-new(root)'});
    }).catch(() => {});
  });
  labelTheme();

  // ---- hardware chip and popover -------------------------------------------------------------------------------------
  const hwWrap = document.querySelector('.hw-wrap'), chip = el('hardwareChip'), chipName = chip.querySelector('.hw-name');
  const specs = el('hardwareSpecs'), compat = el('hardwareCompatibility');
  function syncChip() {
    let gpu = '', vram = '';
    const terms = specs.querySelectorAll('dt');
    for (const term of terms) {
      const value = term.nextElementSibling?.textContent.trim() || '';
      if (/^Видеокарта/.test(term.textContent)) gpu = value.replace(/^NVIDIA\s+(GeForce\s+)?/i, '');
      if (/^Видеопамять/.test(term.textContent)) vram = value;
    }
    chipName.textContent = gpu ? (vram ? `${gpu} · ${vram}` : gpu) : 'Ваш компьютер';
    chip.dataset.status = compat.dataset.status || '';
    chip.title = (compat.textContent.trim() || 'Характеристики этого ПК') + ' · нажмите, чтобы открыть';
  }
  new MutationObserver(syncChip).observe(specs, {childList: true, subtree: true, characterData: true});
  new MutationObserver(syncChip).observe(compat, {attributes: true, attributeFilter: ['data-status'], childList: true, characterData: true, subtree: true});
  syncChip();
  function setHw(open) {
    hwWrap.classList.toggle('open', open);
    chip.setAttribute('aria-expanded', String(open));
  }
  chip.addEventListener('click', () => setHw(!hwWrap.classList.contains('open')));
  document.addEventListener('click', event => { if (!hwWrap.contains(event.target)) setHw(false); });
  hwWrap.addEventListener('keydown', event => {
    if (event.key === 'Escape' && hwWrap.classList.contains('open')) { event.stopPropagation(); setHw(false); chip.focus(); }
  });

  // ---- section rail: one indicator slides to the selected section --------------------------------------------------------
  const rail = document.querySelector('nav.module-tabs');
  const indicator = document.createElement('span');
  indicator.className = 'rail-ind'; indicator.setAttribute('aria-hidden', 'true');
  rail.prepend(indicator);
  function moveIndicator() {
    const selected = rail.querySelector('button[aria-selected="true"]');
    indicator.classList.toggle('on', !!selected);
    if (selected) indicator.style.transform = `translateY(${selected.offsetTop}px)`;
  }
  new MutationObserver(moveIndicator).observe(rail, {attributes: true, subtree: true, attributeFilter: ['aria-selected']});
  // Components lives in the settings menu; when it opens, no rail section is current.
  new MutationObserver(moveIndicator).observe(document.body, {attributes: true, attributeFilter: ['data-studio-module']});
  addEventListener('resize', moveIndicator);
  moveIndicator();
  requestAnimationFrame(() => rail.classList.add('ready'));

  // ---- readiness pill: long texts are clipped in the top bar, so the full sentence is a tooltip ------------------------
  const preflight = el('preflight');
  const syncTitle = () => { preflight.title = preflight.textContent.trim(); };
  new MutationObserver(syncTitle).observe(preflight, {childList: true, characterData: true, subtree: true});
  syncTitle();

  // ---- «Звуки» in the gear menu: pick and preview the completion melodies ---------------------------------------------
  const sounds = window.StudioSounds, soundsDialog = el('soundsDialog');
  if (sounds && soundsDialog) {
    const fields = {success: el('soundSuccess'), error: el('soundError')};
    for (const [kind, select] of Object.entries(fields)) {
      for (const preset of [...sounds.PRESETS[kind], {id: 'off', name: 'Без звука'}]) select.add(new Option(preset.name, preset.id));
      select.addEventListener('change', () => { sounds.save({[kind]: select.value}); sounds.play(kind, select.value, {force: true}); });
    }
    const range = el('soundVolume'), shown = el('soundVolumeValue');
    const showVolume = () => { shown.textContent = range.value === '0' ? 'выкл.' : `${range.value}%`; };
    range.addEventListener('input', () => { sounds.save({volume: Number(range.value) / 100}); showVolume(); });
    range.addEventListener('change', () => sounds.play('success', null, {force: true}));
    el('soundSuccessPlay').addEventListener('click', () => sounds.play('success', fields.success.value, {force: true}));
    el('soundErrorPlay').addEventListener('click', () => sounds.play('error', fields.error.value, {force: true}));
    el('openSounds').addEventListener('click', () => {
      const current = sounds.prefs();
      fields.success.value = current.success; fields.error.value = current.error;
      range.value = String(Math.round(current.volume * 100)); showVolume();
      soundsDialog.showModal();
    });
    for (const id of ['soundsClose', 'soundsDone']) el(id).addEventListener('click', () => soundsDialog.close());
    // A click on the dimmed backdrop (the dialog element itself, outside its content box) closes it as well.
    soundsDialog.addEventListener('click', event => { if (event.target === soundsDialog) soundsDialog.close(); });
  }

  // ---- long static explanations fold behind a small "?" so the form stays short but the help stays one click away ------
  const explanations = ['.engine-panel > .preset-hint:not([id])', '.quality-explainer', '#seedHint', '#videoQualityHelp', '#videoSeedHelp', '#videoProfileHint'];
  for (const selector of explanations) {
    for (const node of document.querySelectorAll(selector)) {
      if (node.closest('details.hint-more')) continue;
      const wrap = document.createElement('details');
      wrap.className = 'hint-more';
      const summary = document.createElement('summary');
      summary.textContent = 'Что это значит?';
      node.before(wrap);
      wrap.append(summary, node);
    }
  }
})();
