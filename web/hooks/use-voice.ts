"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { confidenceOf } from "@/lib/voice/intent";
import { VoiceEngine, type Meter, type NoiseClass, type Segment, type VoiceError } from "@/lib/voice/engine";
import { speak, stopSpeaking } from "@/lib/voice/speak";
import type { VoiceStatus } from "@/lib/types";

export interface Utterance { text: string; confidence: number }
export type VoicePhase = "idle" | "connecting" | "listening" | "stopping";
export type Unavailable = "not_configured" | "key_rejected" | "offline" | null;

const NO_SPEECH_MS = 9000;
const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, " ").replace(/\s+/g, " ").trim();

interface Options {
  lang: "en" | "ar";
  terms: () => string[];
  onUtterance: (u: Utterance) => void;
}

/** Push-to-talk or tap-to-talk voice input with live captions, a noise reading, and spoken replies. */
export function useVoice({ lang, terms, onUtterance }: Options) {
  const [status, setStatus] = useState<VoiceStatus | "offline" | null>(null);
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [caption, setCaption] = useState("");
  const [meter, setMeter] = useState<Meter | null>(null);
  const [noise, setNoise] = useState<NoiseClass>("quiet");
  const [filtering, setFiltering] = useState(false);
  const [error, setError] = useState<VoiceError | null>(null);
  const [notice, setNotice] = useState<"nothing_heard" | "didnt_catch" | null>(null);
  const [closer, setCloser] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [readAloud, setReadAloudState] = useState(false);
  const readAloudChosen = useRef(false);

  const engine = useRef<VoiceEngine | null>(null);
  const finals = useRef<Segment[]>([]);
  const interim = useRef("");
  const heardSpeech = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const lastSpoken = useRef("");
  const lowSnr = useRef(0);
  const cb = useRef({ lang, terms, onUtterance, status });
  useEffect(() => { cb.current = { lang, terms, onUtterance, status }; });

  useEffect(() => {
    let stop = false;
    api.voiceStatus().then((s) => { if (!stop) setStatus(s); }).catch(() => { if (!stop) setStatus("offline"); });
    return () => { stop = true; };
  }, []);

  const unavailable: Unavailable = status === null ? null : status === "offline" ? "offline" : status.stt ? null : status.reason === "key_rejected" ? "key_rejected" : status.reason === "offline" ? "offline" : "not_configured";

  const finish = useCallback((e: VoiceEngine) => {
    clearTimeout(timer.current);
    e.stop().then(() => {
      const segs = finals.current;
      const text = [...segs.map((s) => s.text), segs.length ? "" : interim.current].join(" ").replace(/\s+/g, " ").trim();
      const words = segs.flatMap((s) => s.words);
      const overall = segs.length ? segs.reduce((a, s) => a + s.confidence * Math.max(1, s.text.length), 0) / segs.reduce((a, s) => a + Math.max(1, s.text.length), 0) : 0.5;
      finals.current = []; interim.current = "";
      setCaption("");
      if (!text) { setNotice("nothing_heard"); return; }
      // The kiosk's own voice can leak back into the microphone; never treat it as the person speaking.
      if (lastSpoken.current && norm(lastSpoken.current).includes(norm(text)) && norm(text).split(" ").length > 2) return;
      cb.current.onUtterance({ text, confidence: confidenceOf(words, overall) });
    });
  }, []);

  const start = useCallback(async () => {
    if (engine.current || cb.current.status === "offline") return;
    if (cb.current.status && !cb.current.status.stt) return;
    stopSpeaking();
    setSpeaking(false);
    setError(null); setNotice(null); setCaption(""); setCloser(false);
    finals.current = []; interim.current = ""; heardSpeech.current = false; lowSnr.current = 0;
    const e = new VoiceEngine({
      onState: (s) => { setPhase(s); if (s === "idle") { engine.current = null; clearTimeout(timer.current); setMeter(null); } },
      onMeter: (m, n, f) => {
        setMeter(m); setNoise(n); setFiltering(f);
        if (m.speech) {
          heardSpeech.current = true;
          lowSnr.current = m.snr < 6 ? lowSnr.current + 1 : 0;
          setCloser(lowSnr.current > 12);
        }
      },
      onInterim: (text) => { interim.current = text; setCaption([...finals.current.map((s) => s.text), text].join(" ")); },
      onFinal: (seg) => { if (seg.text) { finals.current.push(seg); interim.current = ""; setCaption(finals.current.map((s) => s.text).join(" ")); } },
      onEndpoint: () => { if (engine.current && (finals.current.length || interim.current)) finish(engine.current); },
      onError: (reason) => { engine.current = null; clearTimeout(timer.current); setError(reason); setCaption(""); },
    });
    engine.current = e;
    await e.start({ language: cb.current.lang, terms: cb.current.terms() });
    timer.current = setTimeout(() => { if (engine.current && !heardSpeech.current) finish(engine.current); }, NO_SPEECH_MS);
  }, [finish]);

  const stop = useCallback(() => { if (engine.current) finish(engine.current); }, [finish]);

  const setReadAloud = useCallback((on: boolean) => { readAloudChosen.current = true; setReadAloudState(on); }, []);
  /** Someone who talks to the kiosk expects it to answer aloud; switch that on once, unless they have chosen already. */
  const autoReadAloud = useCallback(() => { if (!readAloudChosen.current) { readAloudChosen.current = true; setReadAloudState(true); } }, []);

  const say = useCallback(async (text: string, force = false) => {
    if (!text || (!readAloud && !force)) return;
    const c = cb.current;
    lastSpoken.current = text;
    setSpeaking(true);
    try {
      const server = c.status && c.status !== "offline" && c.status.tts && c.status.tts_languages.includes(c.lang);
      await speak(text, c.lang, !!server);
    } finally { setSpeaking(false); }
  }, [readAloud]);

  useEffect(() => () => { engine.current?.dispose(); stopSpeaking(); clearTimeout(timer.current); }, []);

  return {
    status, unavailable, phase, caption, meter, noise, filtering, error, notice, setNotice, closer, speaking, readAloud, setReadAloud, autoReadAloud,
    listening: phase === "listening", busy: phase !== "idle",
    start, stop, say, clearError: () => { setError(null); setNotice(null); },
  };
}

export type VoiceApi = ReturnType<typeof useVoice>;
