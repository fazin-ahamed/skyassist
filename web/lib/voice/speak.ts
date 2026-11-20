"use client";

import { api } from "@/lib/api";

let audio: HTMLAudioElement | null = null;
let objectUrl: string | null = null;

export function stopSpeaking() {
  if (audio) { audio.pause(); audio.src = ""; audio = null; }
  if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
  try { window.speechSynthesis?.cancel(); } catch { /* not available */ }
}

function browserVoice(text: string, lang: "en" | "ar"): Promise<void> {
  return new Promise((resolve) => {
    const synth = window.speechSynthesis;
    if (!synth) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "ar" ? "ar-SA" : "en-GB";
    u.rate = 0.95;
    u.onend = () => resolve();
    u.onerror = () => resolve();
    synth.speak(u);
  });
}

/**
 * Speak a short reply. Deepgram's voice when the server has one for this language, the device's own voice otherwise
 * (Arabic, or no key). Resolves when speech has finished or was stopped.
 */
export async function speak(text: string, lang: "en" | "ar", serverVoice: boolean): Promise<void> {
  stopSpeaking();
  if (serverVoice) {
    const blob = await api.speak(text, lang);
    if (blob) {
      objectUrl = URL.createObjectURL(blob);
      const el = new Audio(objectUrl);
      audio = el;
      await new Promise<void>((resolve) => {
        el.onended = () => resolve();
        el.onerror = () => resolve();
        el.play().catch(() => resolve());
      });
      return;
    }
  }
  await browserVoice(text, lang);
}
