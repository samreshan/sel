/* Sel call simulator: microphone → 16 kHz 16-bit mono PCM chunks for live transcription.
 *
 * Runs on the audio thread. Each output sample averages the input samples it
 * covers (a box filter, enough to keep speech intelligible to Whisper when
 * going from 44.1 or 48 kHz down to 16 kHz). Every `chunk` samples (0.5 s by
 * default) it posts {pcm: ArrayBuffer, time: AudioContext time of the first
 * sample}; "flush" posts whatever is buffered.
 */
class SelPcm extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.ratio = sampleRate / 16000;
    this.chunk = opts.chunk || 8000;
    this.buf = new Int16Array(this.chunk);
    this.n = 0;
    this.pos = 0;
    this.acc = 0;
    this.cnt = 0;
    this.start = 0;
    this.port.onmessage = (e) => {
      if (e.data === "flush") this.flush();
    };
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.cnt += 1;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.cnt));
        this.acc = 0;
        this.cnt = 0;
        if (this.n === 0) this.start = currentTime + i / sampleRate;
        this.buf[this.n++] = v < 0 ? v * 32768 : v * 32767;
        if (this.n === this.chunk) this.flush();
      }
    }
    return true;
  }

  flush() {
    if (!this.n) return;
    const out = this.buf.slice(0, this.n);
    this.port.postMessage({ pcm: out.buffer, time: this.start }, [out.buffer]);
    this.n = 0;
  }
}

registerProcessor("sel-pcm", SelPcm);
