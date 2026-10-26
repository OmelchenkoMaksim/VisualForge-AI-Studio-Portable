// AudioWorklet that hands the microphone samples to the page (loaded from the same origin because the page's CSP forbids blob scripts).
class VoiceCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor('vf-capture', VoiceCapture);
