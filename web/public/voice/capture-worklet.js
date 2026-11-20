/**
 * Microphone capture for the kiosk, running on the audio thread.
 *
 * What it does, in order, to each block of microphone audio:
 *   1. High-pass at 90 Hz: removes air-conditioning rumble, trolley wheels and handling thumps.
 *   2. Resample to 16 kHz mono, the rate the recogniser is trained for.
 *   3. Measure each 30 ms frame, track the room's noise floor (falls fast, rises slowly), and decide speech or not.
 *   4. Level control: bring speech to a steady loudness with a gentle, capped gain. Gain only moves while someone is speaking.
 *   5. Soft gate: between words, turn the room down (never off) so crowd noise does not get transcribed as words.
 *   6. Send 16-bit PCM frames to the page with the measurements the screen uses (level, noise floor, signal to noise).
 *
 * It deliberately does no spectral denoising. Recogniser vendors report that aggressive denoising removes cues the
 * model needs and lowers accuracy; the page decides separately whether to ask the browser for its own suppression,
 * based on the noise floor measured here.
 */
class SkyAssistCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.target = o.targetRate || 16000;
    this.ratio = sampleRate / this.target;
    this.frameSize = Math.round(this.target * 0.03); // 30 ms
    this.gateDepth = o.gateDepth == null ? 0.55 : o.gateDepth; // 0 = off, 1 = strongest
    this.agc = o.agc !== false;

    // high-pass biquad (Butterworth, 90 Hz) at the input rate
    this.hp = this.biquad("highpass", 90, 0.7071, sampleRate);
    // anti-alias low-pass before decimation
    this.lp = this.ratio > 1.01 ? this.biquad("lowpass", this.target * 0.45, 0.7071, sampleRate) : null;

    this.pos = 0; // fractional read position into the current block
    this.last = 0;
    this.frame = new Float32Array(this.frameSize);
    this.filled = 0;

    this.floor = 0.004;      // noise floor (rms), starts modestly low and adapts within a second
    this.speechRms = 0.1;    // smoothed speech loudness
    this.gain = 1.5;
    this.hang = 0;           // frames of speech hangover left
    this.gate = 1;           // current soft-gate gain, ramps between 1 and the closed value
    this.frames = 0;

    this.port.onmessage = (e) => {
      const m = e.data || {};
      if (typeof m.gateDepth === "number") this.gateDepth = Math.max(0, Math.min(1, m.gateDepth));
      if (typeof m.agc === "boolean") this.agc = m.agc;
    };
  }

  biquad(type, freq, q, fs) {
    const w0 = (2 * Math.PI * freq) / fs;
    const alpha = Math.sin(w0) / (2 * q);
    const cos = Math.cos(w0);
    let b0, b1, b2;
    if (type === "highpass") { b0 = (1 + cos) / 2; b1 = -(1 + cos); b2 = (1 + cos) / 2; }
    else { b0 = (1 - cos) / 2; b1 = 1 - cos; b2 = (1 - cos) / 2; }
    const a0 = 1 + alpha, a1 = -2 * cos, a2 = 1 - alpha;
    return { b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
  }

  run(f, x) {
    const y = f.b0 * x + f.b1 * f.x1 + f.b2 * f.x2 - f.a1 * f.y1 - f.a2 * f.y2;
    f.x2 = f.x1; f.x1 = x; f.y2 = f.y1; f.y1 = y;
    return y;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      let x = this.run(this.hp, input[i]);
      if (this.lp) x = this.run(this.lp, x);
      // linear-interpolated decimation; pos counts input samples to the next output sample
      this.pos += 1;
      while (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const t = this.ratio > 1 ? 1 - this.pos / this.ratio : 1;
        const sample = this.last + (x - this.last) * Math.min(1, Math.max(0, t));
        this.frame[this.filled++] = sample;
        if (this.filled === this.frameSize) { this.emit(); this.filled = 0; }
      }
      this.last = x;
    }
    return true;
  }

  emit() {
    const f = this.frame, n = this.frameSize;
    let sum = 0, peak = 0;
    for (let i = 0; i < n; i++) { const a = f[i]; sum += a * a; const m = Math.abs(a); if (m > peak) peak = m; }
    const rms = Math.sqrt(sum / n);
    this.frames++;

    // Noise floor: follows quiet quickly, rises slowly, and is frozen while someone is speaking.
    const ratio = rms / Math.max(this.floor, 1e-5);
    const loud = ratio > 3.2 && rms > 0.006;
    if (rms < this.floor) this.floor = this.floor * 0.85 + rms * 0.15;
    else if (!loud && this.hang === 0) this.floor += (rms - this.floor) * 0.004;
    this.floor = Math.max(this.floor, 5e-5);

    if (loud) this.hang = 9; // about 270 ms of hangover keeps word endings
    else if (this.hang > 0) this.hang--;
    const speech = this.hang > 0;
    const snr = 20 * Math.log10(Math.max(rms, 1e-5) / this.floor);

    // Level control: only while speaking, slowly, and never more than +18 dB or less than -6 dB.
    if (speech && loud) this.speechRms += (rms - this.speechRms) * 0.12;
    if (this.agc && speech) {
      const want = Math.min(8, Math.max(0.5, 0.12 / Math.max(this.speechRms, 1e-3)));
      this.gain += (want - this.gain) * 0.06;
    }

    // Soft gate: ramp toward the open or closed gain across the frame to avoid clicks.
    const closed = 1 - 0.7 * this.gateDepth;
    const goal = speech ? 1 : closed;
    const out = new Int16Array(n);
    let g = this.gate;
    const step = (goal - g) / n;
    let clipped = 0;
    for (let i = 0; i < n; i++) {
      g += step;
      let v = f[i] * this.gain * g;
      if (v > 0.98) { v = 0.98; clipped++; } else if (v < -0.98) { v = -0.98; clipped++; }
      out[i] = v < 0 ? v * 32768 : v * 32767;
    }
    this.gate = goal;

    const floorDb = 20 * Math.log10(this.floor);
    this.port.postMessage({
      pcm: out.buffer, rms, peak, floorDb, snr, speech, clipped, gain: this.gain, frames: this.frames,
    }, [out.buffer]);
  }
}

registerProcessor("skyassist-capture", SkyAssistCapture);
