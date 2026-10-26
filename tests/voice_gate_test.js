'use strict';
// Run by tests/test_voice_gate.py with Node: the speech gate must start on speech and stop after a pause, never on noise alone.
const assert = require('assert');
const {SpeechGate, rms, resample, concat, wavBuffer} = require('../App/frontend/voice-gate.js');

const RATE = 16000;
let seed = 1;
const random = () => {seed = (seed * 1664525 + 1013904223) >>> 0;return seed / 4294967296 - 0.5;};
const noise = (seconds, amplitude) => Float32Array.from({length: Math.round(seconds * RATE)}, () => random() * 2 * amplitude);
const voice = (seconds, amplitude) => Float32Array.from({length: Math.round(seconds * RATE)}, (_, i) =>
  amplitude * Math.sin(2 * Math.PI * 180 * i / RATE) * (0.6 + 0.4 * Math.sin(2 * Math.PI * 4 * i / RATE)) + random() * 0.02);

// Feed in small chunks like an AudioWorklet does; return [{event, atMs}].
function run(signal, options) {
  const gate = new SpeechGate(RATE, options);
  const events = [];
  for (let at = 0; at < signal.length; at += 128) {
    for (const event of gate.push(signal.subarray(at, at + 128))) events.push({event, atMs: Math.round(at / RATE * 1000)});
  }
  return {events, gate};
}

// 1. a room with quiet noise, a sentence of 2 s, then silence: speech-start during the sentence, stop-silence ~1.6 s after it ends
{
  const {events} = run(concat([noise(1.0, 0.004), voice(2.0, 0.2), noise(3.0, 0.004)]));
  const names = events.map(e => e.event);
  assert.deepStrictEqual(names, ['speech-start', 'stop-silence'], JSON.stringify(events));
  assert.ok(events[0].atMs >= 1000 && events[0].atMs < 1400, 'speech starts with the sentence: ' + events[0].atMs);
  assert.ok(events[1].atMs >= 3000 + 1500 && events[1].atMs <= 3000 + 1900, 'stops about 1.6 s after the sentence: ' + events[1].atMs);
}
// 2. a short pause between words must not end the recording
{
  const {events} = run(concat([voice(1.0, 0.2), noise(0.8, 0.004), voice(1.0, 0.2), noise(3.0, 0.004)]));
  assert.deepStrictEqual(events.map(e => e.event), ['speech-start', 'stop-silence']);
  assert.ok(events[1].atMs > 2800 + 1500, 'the 0.8 s pause was tolerated: ' + events[1].atMs);
}
// 3. nobody speaks: stop-no-speech after the waiting time, nothing else
{
  const {events} = run(noise(12, 0.004));
  assert.deepStrictEqual(events.map(e => e.event), ['stop-no-speech']);
  assert.ok(events[0].atMs >= 8800 && events[0].atMs <= 9300, String(events[0].atMs));
}
// 4. a noisy room (constant hiss) is learned as the floor, speech above it is still found
{
  const {events} = run(concat([noise(1.5, 0.02), voice(2.0, 0.3), noise(3.0, 0.02)]));
  assert.deepStrictEqual(events.map(e => e.event), ['speech-start', 'stop-silence'], JSON.stringify(events));
}
// 5. very long speech stops at the limit
{
  const {events} = run(voice(20, 0.2), {maxMs: 5000});
  assert.deepStrictEqual(events.map(e => e.event), ['speech-start', 'stop-max']);
}
// 6. the level meter follows loudness
{
  const quiet = run(noise(0.5, 0.004)).gate.level, loud = run(voice(0.5, 0.3)).gate.level;
  assert.ok(loud > quiet + 0.2 && loud <= 1, quiet + ' vs ' + loud);
}
// helpers
assert.ok(Math.abs(rms(Float32Array.from([0.5, -0.5, 0.5, -0.5])) - 0.5) < 1e-6);
{
  const source = Float32Array.from({length: 48000}, (_, i) => Math.sin(2 * Math.PI * 440 * i / 48000));
  const down = resample(source, 48000, 16000);
  assert.strictEqual(down.length, 16000);
  assert.ok(Math.abs(rms(down) - rms(source)) < 0.02);
  assert.strictEqual(resample(source, 48000, 48000), source);
}
{
  const buffer = wavBuffer(Float32Array.from([0, 0.5, -0.5, 1, -1, 2]), 16000), view = new DataView(buffer);
  assert.strictEqual(String.fromCharCode(...new Uint8Array(buffer, 0, 4)), 'RIFF');
  assert.strictEqual(view.getUint32(24, true), 16000);
  assert.strictEqual(view.getUint32(40, true), 12);
  assert.strictEqual(view.getInt16(44 + 6, true), 32767);   // 1.0 and anything above is clipped to full scale
  assert.strictEqual(view.getInt16(44 + 8, true), -32768);
  assert.strictEqual(buffer.byteLength, 44 + 12);
}
console.log('voice gate ok');
