'use strict';
// Speech gate: decides from a stream of microphone samples when speech has started, when the speaker has fallen silent (stop and
// recognize), and when nobody is speaking at all. Pure functions and one class, no browser APIs, so Node can test it.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.VoiceGate = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  const FRAME_MS = 30;

  function rms(samples, from = 0, to = samples.length) {
    let sum = 0;
    for (let i = from; i < to; i++) sum += samples[i] * samples[i];
    return to > from ? Math.sqrt(sum / (to - from)) : 0;
  }

  function resample(samples, fromRate, toRate) {
    if (fromRate === toRate || !samples.length) return samples;
    const length = Math.max(1, Math.round(samples.length * toRate / fromRate)), out = new Float32Array(length), ratio = (samples.length - 1) / Math.max(1, length - 1);
    for (let i = 0; i < length; i++) {
      const position = i * ratio, left = Math.floor(position), right = Math.min(samples.length - 1, left + 1), fraction = position - left;
      out[i] = samples[left] * (1 - fraction) + samples[right] * fraction;
    }
    return out;
  }

  function concat(chunks) {
    let total = 0;
    for (const chunk of chunks) total += chunk.length;
    const out = new Float32Array(total);
    let offset = 0;
    for (const chunk of chunks) {out.set(chunk, offset);offset += chunk.length;}
    return out;
  }

  /** 16-bit PCM WAV (mono) as an ArrayBuffer. */
  function wavBuffer(samples, rate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
    const text = (offset, value) => {for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));};
    text(0, 'RIFF');view.setUint32(4, 36 + samples.length * 2, true);text(8, 'WAVE');text(12, 'fmt ');view.setUint32(16, 16, true);view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);view.setUint32(24, rate, true);view.setUint32(28, rate * 2, true);view.setUint16(32, 2, true);view.setUint16(34, 16, true);
    text(36, 'data');view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {const value = Math.max(-1, Math.min(1, samples[i]));view.setInt16(44 + i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);}
    return buffer;
  }

  class SpeechGate {
    /** Feed `push(chunk)` the samples as they arrive; it returns the events they caused:
     *  'speech-start', 'stop-silence' (the speaker finished), 'stop-no-speech' (nobody spoke), 'stop-max' (time limit). */
    constructor(rate, options = {}) {
      this.rate = rate;
      this.frame = Math.round(rate * FRAME_MS / 1000);
      this.options = Object.assign({minRms: 0.012, ratio: 3.5, startFrames: 4, silenceMs: 1600, noSpeechMs: 9000, maxMs: 110000}, options);
      this.pending = new Float32Array(0);
      this.noise = 0.004;
      this.run = 0;
      this.started = false;
      this.finished = false;
      this.silentMs = 0;
      this.elapsedMs = 0;
      this.level = 0;
    }

    push(chunk) {
      const events = [];
      const joined = new Float32Array(this.pending.length + chunk.length);
      joined.set(this.pending);joined.set(chunk, this.pending.length);
      let offset = 0;
      while (joined.length - offset >= this.frame) {
        this._frame(rms(joined, offset, offset + this.frame), events);
        offset += this.frame;
      }
      this.pending = joined.slice(offset);
      return events;
    }

    _frame(level, events) {
      this.elapsedMs += FRAME_MS;
      this.level = Math.min(1, level * 8);
      if (this.finished) return;
      const speech = level > Math.max(this.options.minRms, this.noise * this.options.ratio);
      if (!speech) this.noise = Math.min(0.05, this.noise * 0.95 + level * 0.05);
      if (!this.started) {
        this.run = speech ? this.run + 1 : Math.max(0, this.run - 1);
        if (this.run >= this.options.startFrames) {this.started = true;this.silentMs = 0;events.push('speech-start');}
        else if (this.elapsedMs >= this.options.noSpeechMs) {this.finished = true;events.push('stop-no-speech');}
      } else if (speech) {
        this.silentMs = 0;
      } else {
        this.silentMs += FRAME_MS;
        if (this.silentMs >= this.options.silenceMs) {this.finished = true;events.push('stop-silence');}
      }
      if (!this.finished && this.elapsedMs >= this.options.maxMs) {this.finished = true;events.push('stop-max');}
    }
  }

  return {SpeechGate, rms, resample, concat, wavBuffer, FRAME_MS};
});
