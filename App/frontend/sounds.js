/* Completion melodies, synthesized locally with Web Audio: no sound files to ship, license or download.
   Each preset is a short score (at most 7 s including the reverb tail) played by a few small instruments.
   The choice per outcome and the volume are a per-browser preference kept in localStorage. */
(() => {
  'use strict';
  const STORE = 'vf-sounds';
  const LIMIT = 7;  // seconds: no melody, tail included, may run longer than this
  let ctx = null, master = null, wet = null;

  // ---- notes ---------------------------------------------------------------------------------------------------------
  const NAMES = {C: -9, D: -7, E: -5, F: -4, G: -2, A: 0, B: 2};
  function hz(note) {  // 'C5', 'F#4', 'Bb3' -> frequency (A4 = 440 Hz)
    const m = /^([A-G])([#b]?)(\d)$/.exec(note);
    const semis = NAMES[m[1]] + (m[2] === '#' ? 1 : m[2] === 'b' ? -1 : 0) + (Number(m[3]) - 4) * 12;
    return 440 * 2 ** (semis / 12);
  }

  // ---- audio graph: master volume -> gentle compressor -> speakers, plus a small generated room reverb -------------------
  function ensure() {
    if (ctx) return ctx;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return null;
    ctx = new Audio();
    build();
    return ctx;
  }
  function build() {
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14; comp.ratio.value = 3; comp.attack.value = .005; comp.release.value = .2;
    master = ctx.createGain(); master.gain.value = volume();
    master.connect(comp); comp.connect(ctx.destination);
    // A decaying-noise impulse response gives a soft room without loading an impulse file.
    const seconds = 1.6, rate = ctx.sampleRate, impulse = ctx.createBuffer(2, Math.floor(seconds * rate), rate);
    for (let channel = 0; channel < 2; channel++) {
      const data = impulse.getChannelData(channel);
      for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length) ** 3;
    }
    const reverb = ctx.createConvolver(); reverb.buffer = impulse;
    wet = ctx.createGain(); wet.gain.value = .22;
    wet.connect(reverb); reverb.connect(master);
  }
  function unlock() {
    try { const audio = ensure(); if (audio?.state === 'suspended') audio.resume().catch(() => {}); } catch {}
  }
  document.addEventListener('pointerdown', unlock, {passive: true});
  document.addEventListener('keydown', unlock, {passive: true});

  // Every voice disconnects itself when it ends, so nothing accumulates in the graph between notifications.
  function out(node, dry = 1) {
    const send = ctx.createGain(); send.gain.value = dry;
    node.connect(send); send.connect(master); node.connect(wet);
    return send;
  }
  function envelope(gain, t, peak, attack, decay) {
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(peak, t + attack);
    gain.gain.exponentialRampToValueAtTime(.0001, t + attack + decay);
  }
  function finish(nodes, end) {
    nodes[0].onended = () => nodes.forEach(n => { try { n.disconnect(); } catch {} });
    nodes.filter(n => n.stop).forEach(n => n.stop(end));
  }

  // ---- instruments ---------------------------------------------------------------------------------------------------
  const instruments = {
    // Plain sine with a short tail: the original studio chime.
    chime(t, f, len, v = 1) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.value = f; envelope(g, t, .22 * v, .012, Math.max(.25, len));
      o.connect(g); const s = out(g); o.start(t); finish([o, g, s], t + len + .4);
    },
    // FM bell: a modulator at 3.5x the pitch whose depth fades gives the metallic strike.
    bell(t, f, len, v = 1) {
      const c = ctx.createOscillator(), m = ctx.createOscillator(), mg = ctx.createGain(), g = ctx.createGain();
      c.frequency.value = f; m.frequency.value = f * 3.5;
      mg.gain.setValueAtTime(f * 2.2, t); mg.gain.exponentialRampToValueAtTime(1, t + len * .8);
      envelope(g, t, .2 * v, .004, len);
      m.connect(mg); mg.connect(c.frequency); c.connect(g); const s = out(g, .9);
      c.start(t); m.start(t); finish([c, m, mg, g, s], t + len + .1);
    },
    // Marimba-like mallet: fundamental plus a quickly fading fourth partial.
    mallet(t, f, len, v = 1) {
      const a = ctx.createOscillator(), b = ctx.createOscillator(), ga = ctx.createGain(), gb = ctx.createGain();
      a.frequency.value = f; b.frequency.value = f * 4;
      envelope(ga, t, .16 * v, .003, Math.min(len, .9)); envelope(gb, t, .06 * v, .002, .08);
      a.connect(ga); b.connect(gb); const sa = out(ga), sb = out(gb);
      a.start(t); b.start(t); finish([a, b, ga, gb, sa, sb], t + Math.min(len, .9) + .1);
    },
    // Harp pluck: a triangle wave through a closing low-pass filter.
    pluck(t, f, len, v = 1) {
      const o = ctx.createOscillator(), lp = ctx.createBiquadFilter(), g = ctx.createGain();
      o.type = 'triangle'; o.frequency.value = f;
      lp.type = 'lowpass'; lp.frequency.setValueAtTime(f * 8, t); lp.frequency.exponentialRampToValueAtTime(f * 1.5, t + len);
      envelope(g, t, .11 * v, .003, len);
      o.connect(lp); lp.connect(g); const s = out(g); o.start(t); finish([o, lp, g, s], t + len + .1);
    },
    // Soft brass for fanfares: a filtered sawtooth with a slower attack.
    brass(t, f, len, v = 1) {
      const o = ctx.createOscillator(), lp = ctx.createBiquadFilter(), g = ctx.createGain();
      o.type = 'sawtooth'; o.frequency.value = f;
      lp.type = 'lowpass'; lp.frequency.setValueAtTime(f * 1.2, t); lp.frequency.linearRampToValueAtTime(f * 4, t + .08); lp.frequency.linearRampToValueAtTime(f * 2.2, t + len);
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.1 * v, t + .05);
      g.gain.setValueAtTime(.088 * v, t + Math.max(.06, len - .12)); g.gain.exponentialRampToValueAtTime(.0001, t + len + .25);
      o.connect(lp); lp.connect(g); const s = out(g); o.start(t); finish([o, lp, g, s], t + len + .3);
    },
    // Warm pad: two slightly detuned saws, slow swell, dark filter.
    pad(t, f, len, v = 1) {
      const a = ctx.createOscillator(), b = ctx.createOscillator(), lp = ctx.createBiquadFilter(), g = ctx.createGain();
      a.type = b.type = 'sawtooth'; a.frequency.value = f; b.frequency.value = f; b.detune.value = 9;
      lp.type = 'lowpass'; lp.frequency.value = Math.min(2400, f * 3);
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.035 * v, t + len * .35);
      g.gain.setValueAtTime(.035 * v, t + len * .6); g.gain.exponentialRampToValueAtTime(.0001, t + len);
      a.connect(lp); b.connect(lp); lp.connect(g); const s = out(g);
      a.start(t); b.start(t); finish([a, b, lp, g, s], t + len + .05);
    }
  };

  // ---- presets: [instrument, note, start (s), length (s), velocity] ------------------------------------------------------
  const seq = (instrument, notes, step, len, v = 1, offset = 0) => notes.map((n, i) => [instrument, n, offset + i * step, len, v]);
  const PRESETS = {
    success: [
      {id: 'classic', name: StudioI18n.t("settings.sounds_presets_name_classic_three_notes"), score: seq('chime', ['C5', 'E5', 'G5'], .15, .32)},
      {id: 'bell', name: StudioI18n.t("settings.sounds_presets_name_bell"), score: [...seq('bell', ['C6', 'E6', 'G6'], .16, 1.4), ['bell', 'C7', .52, 2.2, .8]]},
      {id: 'marimba', name: StudioI18n.t("settings.sounds_presets_name_marimba"), score: [...seq('mallet', ['G5', 'C6', 'E6', 'D6', 'G6'], .13, .7), ['mallet', 'C7', .7, .9, .9]]},
      {id: 'harp', name: StudioI18n.t("settings.sounds_presets_name_harp"), score: [...seq('pluck', ['C4', 'E4', 'G4', 'C5', 'E5', 'G5', 'C6', 'E6'], .07, 1.6, .9), ['pluck', 'G6', .6, 2, .7]]},
      {id: 'fanfare', name: StudioI18n.t("settings.sounds_presets_name_fanfare"), score: [
        ['brass', 'G4', 0, .14], ['brass', 'G4', .18, .14], ['brass', 'G4', .36, .14], ['brass', 'C5', .54, .9],
        ['brass', 'E4', .54, .9, .6], ['brass', 'G5', 1.5, 1.2], ['brass', 'C5', 1.5, 1.2, .6], ['brass', 'E5', 1.5, 1.2, .6]]},
      {id: 'chord', name: StudioI18n.t("settings.sounds_presets_name_soft_chord"), score: [...['C3', 'G3', 'E4', 'B4', 'D5'].map(n => ['pad', n, 0, 4.2]), ...seq('bell', ['E6', 'G6', 'D7'], .5, 1.8, .45, .3)]},
      {id: 'tune', name: StudioI18n.t("settings.sounds_presets_name_victory_melody"), score: [
        ...seq('mallet', ['E5', 'G5', 'C6', 'B5', 'G5', 'A5'], .22, .6), ['mallet', 'F5', 1.32, .6], ['mallet', 'G5', 1.54, .6],
        ['mallet', 'C6', 1.76, .9], ['mallet', 'E6', 2.2, .9], ['bell', 'C7', 2.64, 2.4, .7],
        ...['C3', 'G3', 'E4'].map(n => ['pad', n, 0, 2.4, .8]), ...['F3', 'C4', 'A4'].map(n => ['pad', n, 1.3, 1.6, .6]), ...['C3', 'G3', 'E4', 'C5'].map(n => ['pad', n, 2.2, 3.2, .9])]}
    ],
    error: [
      {id: 'classic', name: StudioI18n.t("settings.sounds_presets_name_classic_three_descending_notes"), score: seq('chime', ['G4', 'D4', 'G3'], .15, .32)},
      {id: 'twotone', name: StudioI18n.t("settings.sounds_presets_name_two_tones"), score: [['mallet', 'A4', 0, .7], ['mallet', 'E4', .32, .9]]},
      {id: 'gong', name: StudioI18n.t("settings.sounds_presets_name_low_gong"), score: [['bell', 'C3', 0, 3.5, 1.2], ['bell', 'G3', .02, 2.6, .5]]},
      {id: 'minor', name: StudioI18n.t("settings.sounds_presets_name_gentle_warning"), score: [...['A2', 'E3', 'C4'].map(n => ['pad', n, 0, 2.6]), ...seq('pluck', ['E5', 'C5', 'A4'], .2, 1.2, 1.7, .1)]}
    ]
  };

  // ---- preferences ---------------------------------------------------------------------------------------------------
  const DEFAULTS = {success: 'classic', error: 'classic', volume: .7};
  function prefs() {
    try { return {...DEFAULTS, ...JSON.parse(localStorage.getItem(STORE) || '{}')}; } catch { return {...DEFAULTS}; }
  }
  function save(change) {
    const next = {...prefs(), ...change};
    try { localStorage.setItem(STORE, JSON.stringify(next)); } catch {}
    if (master) master.gain.value = next.volume;
    return next;
  }
  function volume() { const v = Number(prefs().volume); return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : DEFAULTS.volume; }

  // ---- playback ------------------------------------------------------------------------------------------------------
  function schedule(preset) {
    const start = ctx.currentTime + .03;
    for (const [instrument, note, at, len, v = 1] of preset.score) {
      if (at + len > LIMIT) continue;  // a score that would overrun the limit is cut at note level, never left ringing
      instruments[instrument](start + at, hz(note), len, v);
    }
  }
  const find = (kind, id) => PRESETS[kind]?.find(p => p.id === id) || PRESETS[kind]?.[0];
  function play(kind, id, {force = false} = {}) {
    // Notifications play only when the browser already allows audio (after any click); a preview click is itself that permission.
    if (force) unlock();
    if (!ctx || (ctx.state !== 'running' && !force)) return false;
    const choice = id || prefs()[kind];
    if (choice === 'off' || volume() === 0) return false;
    const preset = find(kind, choice);
    if (!preset) return false;
    schedule(preset);
    return true;
  }
  // Silent self-check: renders a preset into memory (OfflineAudioContext) and reports its audible length and peak,
  // so melodies can be verified without playing them through the user's speakers.
  async function render(kind, id) {
    const saved = [ctx, master, wet];
    let offline;
    // Scheduling is synchronous, so the live graph is restored before the await and a real notification is never routed here.
    try {
      offline = ctx = new OfflineAudioContext(2, Math.ceil((LIMIT + 1) * 44100), 44100);
      build(); master.gain.value = 1; schedule(find(kind, id));
    } finally { [ctx, master, wet] = saved; }
    const data = (await offline.startRendering()).getChannelData(0);
    let peak = 0, last = 0;
    for (let i = 0; i < data.length; i++) { const a = Math.abs(data[i]); if (a > peak) peak = a; if (a > .001) last = i; }
    return {seconds: +(last / 44100).toFixed(2), peak: +peak.toFixed(3)};
  }

  window.StudioSounds = {PRESETS, prefs, save, play, unlock, render};
})();
