"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { I18nText } from "@/lib/types";
import canvasEn from "../../data/i18n/en/canvas.json";
import coreEn from "../../data/i18n/en/core.json";
import flowEn from "../../data/i18n/en/flow.json";
import kioskEn from "../../data/i18n/en/kiosk.json";
import landmarksEn from "../../data/i18n/en/landmarks.json";
import liveEn from "../../data/i18n/en/live.json";
import trafficEn from "../../data/i18n/en/traffic.json";
import voiceEn from "../../data/i18n/en/voice_map.json";
import walkEn from "../../data/i18n/en/walk.json";
import canvasAr from "../../data/i18n/ar/canvas.json";
import coreAr from "../../data/i18n/ar/core.json";
import flowAr from "../../data/i18n/ar/flow.json";
import kioskAr from "../../data/i18n/ar/kiosk.json";
import landmarksAr from "../../data/i18n/ar/landmarks.json";
import liveAr from "../../data/i18n/ar/live.json";
import trafficAr from "../../data/i18n/ar/traffic.json";
import voiceAr from "../../data/i18n/ar/voice_map.json";
import walkAr from "../../data/i18n/ar/walk.json";

const LRI = "⁦";
const PDI = "⁩";
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
function formatParam(name: string, value: unknown, lang: string): string {
  if (name === "date" && typeof value === "string" && ISO_DATE.test(value)) {
    return new Intl.DateTimeFormat(lang === "ar" ? "ar-AE" : "en-GB", { weekday: "short", day: "numeric", month: "short", numberingSystem: "latn", timeZone: "UTC" })
      .format(new Date(`${value}T00:00:00Z`));
  }
  return String(value ?? "");
}
const ISOLATED = new Set(["gate", "flight", "code", "time", "margin", "lower", "upper", "seconds", "source", "range"]);

export type Lang = "en" | "ar";
type Catalog = Awaited<ReturnType<typeof api.catalog>>;
const BUNDLED: Record<Lang, Catalog> = {
  en: { texts: { ...canvasEn, ...coreEn, ...flowEn, ...kioskEn, ...landmarksEn, ...liveEn, ...trafficEn, ...voiceEn, ...walkEn }, review: "unreviewed" },
  ar: { texts: { ...canvasAr, ...coreAr, ...flowAr, ...kioskAr, ...landmarksAr, ...liveAr, ...trafficAr, ...voiceAr, ...walkAr }, review: kioskAr._review },
};

interface I18n {
  lang: Lang;
  setLang: (lang: Lang) => void;
  ready: boolean;
  review: string;
  mode?: "demo" | "live";
  t: (key: string, params?: Record<string, unknown>) => string;
  r: (text?: I18nText | null) => string;
  source: (slug?: string | null) => string;
}

const Ctx = createContext<I18n | null>(null);

export function I18nStore({ children }: { children: (value: I18n) => React.ReactNode }) {
  const [lang, setLang] = useState<Lang>("en");
  const [catalogs, setCatalogs] = useState<Partial<Record<Lang, Catalog>>>({});

  useEffect(() => {
    if (catalogs[lang]) return;
    api.catalog(lang).then((c) => setCatalogs((prev) => ({ ...prev, [lang]: c }))).catch(() => {});
  }, [lang, catalogs]);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  }, [lang]);

  const active = catalogs[lang] ?? BUNDLED[lang];
  const t = useCallback(
    (key: string, params: Record<string, unknown> = {}) => {
      const template = active.texts[key] ?? BUNDLED[lang].texts[key];
      if (template === undefined) return key;
      return template.replace(/\{(\w+)\}/g, (_, name: string) => {
        const value = formatParam(name, params[name], lang);
        return lang === "ar" && ISOLATED.has(name) ? `${LRI}${value}${PDI}` : value;
      });
    },
    [active, lang],
  );
  const value = useMemo<I18n>(() => ({
    lang, setLang, ready: true, review: active.review, mode: active.mode,
    t,
    r: (text) => (text ? t(text.key, text.params ?? {}) : ""),
    source: (slug) => {
      if (!slug) return "";
      const key = `kiosk.source.${slug.replace(/-/g, "_")}`;
      return active?.texts[key] ? t(key) : slug.replace(/-/g, " ");
    },
  }), [lang, active, t]);
  return <Ctx.Provider value={value}>{children(value)}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(Ctx);
  if (!value) throw new Error("useI18n outside provider");
  return value;
}
