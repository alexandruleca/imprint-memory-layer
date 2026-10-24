/* Mic capture only. Playback uses a separate AudioContext graph so browser AEC
   can distinguish the local output. No audio leaves the loopback voice API. */
class ImprintCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(Math.round(sampleRate / 50));
    this.offset = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.frame[this.offset++] = input[i];
      if (this.offset === this.frame.length) {
        this.port.postMessage(this.frame, [this.frame.buffer]);
        this.frame = new Float32Array(Math.round(sampleRate / 50));
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('imprint-capture', ImprintCapture);
