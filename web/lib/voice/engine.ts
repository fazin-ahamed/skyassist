"use client";

/**
 * Microphone to Deepgram, through the kiosk's own server.
 *
 * Noise handling here is adaptive on purpose. The audio worklet measures the room's noise floor; this engine uses
 * that to decide how hard to work:
 *   quiet room  -> echo cancellation only, light gate. Send the cleanest, least-processed audio.
 *   busy / loud -> also ask the browser for its noise suppression and close the soft gate further.
 * The decision is remembered for the next press, so a kiosk in a loud hall starts in the right mode.
 */

export type VoiceState = "idle" | "connecting" | "listening" | "stopping";
export type NoiseClass = "quiet" | "busy" | "loud";
export type VoiceError = "mic_denied" | "mic_missing" | "not_configured" | "key_rejected" | "busy" | "unreachable" | "generic";

export interface Meter { level: number; floorDb: number; snr: number; speech: boolean; clipped: boolean }
export interface Segment { text: string; confidence: number; words: { w: string; c: number }[] }
export interface Handlers {
  onState(state: VoiceState): void;
  onMeter(meter: Meter, noise: NoiseClass, filtering: boolean): void;
  onInterim(text: string): void;
  onFinal(segment: Segment): void;
  /** The recogniser decided the person stopped talking. */
  onEndpoint(): void;
  onError(reason: VoiceError): void;
}

const QUIET_DB = -54;
const LOUD_DB = -44;
const NOISY_AT = -46; // floor above this means suppression is worth its cost
const STORE = "sa:voice-noise";

interface Calibration { noisy: boolean; floorDb: number }
let calibration: Calibration | null = null;
try { const raw = sessionStorage.getItem(STORE); if (raw) calibration = JSON.parse(raw); } catch { /* storage may be blocked */ }

const wsUrl = () => {
  const url = new URL("/voice/stream", process.env.NEXT_PUBLIC_API_BASE || window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
};

export const classify = (floorDb: number): NoiseClass => (floorDb < QUIET_DB ? "quiet" : floorDb < LOUD_DB ? "busy" : "loud");
const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];

export class VoiceEngine {
  private ws?: WebSocket;
  private ctx?: AudioContext;
  private node?: AudioWorkletNode;
  private stream?: MediaStream;
  private track?: MediaStreamTrack;
  private ready = false;
  private done = false;
  private pending: ArrayBuffer[] = [];
  private floors: number[] = [];
  private flipVotes = 0;
  private suppressing = false;
  private tick = 0;

  constructor(private h: Handlers) {}

  async start(opts: { language: "en" | "ar"; terms: string[] }): Promise<void> {
    this.done = false;
    this.h.onState("connecting");
    this.suppressing = !!calibration?.noisy;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: this.suppressing, autoGainControl: false, sampleRate: 16000 },
      });
    } catch (e) {
      const name = (e as DOMException)?.name;
      return this.fail(name === "NotAllowedError" || name === "SecurityError" ? "mic_denied" : name === "NotFoundError" || name === "OverconstrainedError" ? "mic_missing" : "generic");
    }
    this.track = this.stream.getAudioTracks()[0];
    try {
      await this.buildGraph(16000);
    } catch {
      try { await this.buildGraph(undefined); } catch { return this.fail("generic"); }
    }
    this.open(opts);
  }

  private async buildGraph(rate: number | undefined): Promise<void> {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx?.close().catch(() => {});
    this.ctx = rate ? new Ctx({ sampleRate: rate, latencyHint: "interactive" }) : new Ctx({ latencyHint: "interactive" });
    await this.ctx.audioWorklet.addModule("/voice/capture-worklet.js");
    const source = this.ctx.createMediaStreamSource(this.stream!);
    this.node = new AudioWorkletNode(this.ctx, "skyassist-capture", {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      processorOptions: { targetRate: 16000, gateDepth: this.suppressing ? 0.8 : 0.4 },
    });
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    source.connect(this.node);
    this.node.connect(mute);
    mute.connect(this.ctx.destination);
    this.node.port.onmessage = (e) => this.frame(e.data);
  }

  private open(opts: { language: "en" | "ar"; terms: string[] }) {
    const ws = new WebSocket(wsUrl());
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "start", language: opts.language, sample_rate: 16000, terms: opts.terms }));
    ws.onerror = () => { if (!this.ready) this.fail("unreachable"); };
    ws.onclose = () => { if (!this.done) { if (!this.ready) this.fail("unreachable"); else this.dispose(); } };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") return;
      let m: { type: string; reason?: string; text?: string; final?: boolean; speech_final?: boolean; confidence?: number; words?: { w: string; c: number }[] };
      try { m = JSON.parse(e.data); } catch { return; }
      switch (m.type) {
        case "ready":
          this.ready = true;
          for (const chunk of this.pending) ws.send(chunk);
          this.pending = [];
          this.h.onState("listening");
          break;
        case "transcript":
          if (m.final) this.h.onFinal({ text: m.text ?? "", confidence: m.confidence ?? 0, words: m.words ?? [] });
          else if (m.text) this.h.onInterim(m.text);
          if (m.speech_final) this.h.onEndpoint();
          break;
        case "utterance_end":
        case "limit":
          this.h.onEndpoint();
          break;
        case "unavailable":
          this.fail(m.reason === "busy" ? "busy" : m.reason === "offline" ? "unreachable" : "not_configured");
          break;
        case "error":
          this.fail(m.reason === "key_rejected" ? "key_rejected" : m.reason === "unreachable" ? "unreachable" : "generic");
          break;
      }
    };
  }

  private frame(d: { pcm: ArrayBuffer; rms: number; floorDb: number; snr: number; speech: boolean; clipped: number }) {
    if (this.done) return;
    if (this.ready && this.ws?.readyState === WebSocket.OPEN) this.ws.send(d.pcm);
    else { this.pending.push(d.pcm); if (this.pending.length > 50) this.pending.shift(); }

    this.floors.push(d.floorDb);
    if (this.floors.length > 170) this.floors.shift();
    // First look after about 1.2 s of audio, then every 5 s: two readings in a row must agree before the mode flips.
    this.tick++;
    if (this.tick === 40 || (this.tick > 40 && this.tick % 170 === 0)) this.review();

    if (this.tick % 2 === 0) {
      const dB = 20 * Math.log10(Math.max(d.rms, 1e-5));
      const level = Math.max(0, Math.min(1, (dB + 60) / 50));
      this.h.onMeter({ level, floorDb: d.floorDb, snr: d.snr, speech: d.speech, clipped: d.clipped > 8 }, classify(median(this.floors)), this.suppressing);
    }
  }

  private review() {
    const floor = median(this.floors);
    const noisy = floor > NOISY_AT;
    if (noisy === this.suppressing) { this.flipVotes = 0; } else if (++this.flipVotes >= (calibration ? 2 : 1)) {
      this.flipVotes = 0;
      this.suppressing = noisy;
      this.track?.applyConstraints({ noiseSuppression: noisy }).catch(() => {});
      this.node?.port.postMessage({ gateDepth: noisy ? 0.8 : 0.4 });
    }
    calibration = { noisy: this.suppressing, floorDb: floor };
    try { sessionStorage.setItem(STORE, JSON.stringify(calibration)); } catch { /* optional */ }
  }

  /** Ask for the last words, wait briefly for them, then close everything. */
  async stop(): Promise<void> {
    if (this.done) return;
    this.h.onState("stopping");
    const ws = this.ws;
    if (ws?.readyState === WebSocket.OPEN && this.ready) {
      ws.send(JSON.stringify({ type: "finalize" }));
      ws.send(JSON.stringify({ type: "stop" }));
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 1800);
        const prev = ws.onclose;
        ws.onclose = (ev) => { clearTimeout(timer); prev?.call(ws, ev); resolve(); };
      });
    }
    this.dispose();
  }

  private fail(reason: VoiceError) {
    if (this.done) return;
    this.dispose();
    this.h.onError(reason);
  }

  dispose() {
    if (this.done) return;
    this.done = true;
    this.ready = false;
    this.pending = [];
    this.node?.port.close();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) { this.ws.onclose = null; this.ws.close(); }
    this.ws = undefined; this.node = undefined; this.ctx = undefined; this.stream = undefined; this.track = undefined;
    this.floors = []; this.tick = 0; this.flipVotes = 0;
    this.h.onState("idle");
  }
}
