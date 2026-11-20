"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { RiLogoutBoxRLine, RiMoonLine, RiQuestionLine, RiScalesLine, RiSunLine, RiWifiOffLine } from "@remixicon/react";
import { Logo } from "@/components/agent/logo";
import { api } from "@/lib/api";
import { hhmm } from "@/lib/format";
import { useI18n, type Lang } from "@/lib/i18n";
import { cx } from "@/utils/cx";

function subscribeTheme(listener: () => void) {
  const observer = new MutationObserver(listener);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

/** The airport's clock. In a demo it is frozen at the scenario time, so the screen never disagrees with the data. */
function useAirportTime() {
  const [now, setNow] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () => api.now().then((value) => { if (!stop) setNow(value); }).catch(() => {});
    load();
    const timer = setInterval(load, 20000);
    return () => { stop = true; clearInterval(timer); };
  }, []);
  return now ? hhmm(now) : "";
}

function IconButton({ label, onClick, children, active }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} aria-pressed={active}
      className={cx("kk-press flex size-12 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]",
        active ? "bg-[var(--ink)] text-[var(--surface)]" : "bg-[var(--surface)] text-[var(--ink-2)] shadow-[var(--card-shadow)] hover:text-[var(--ink)]")}>
      {children}
    </button>
  );
}

export function Topbar({ onHelp, onEnd, onJudge, judgeOpen, inSession, offline }: {
  onHelp: () => void; onEnd: () => void; onJudge: () => void; judgeOpen: boolean; inSession: boolean; offline: boolean;
}) {
  const { t, lang, setLang } = useI18n();
  const dark = useSyncExternalStore(subscribeTheme, () => document.documentElement.classList.contains("dark"), () => false);
  const time = useAirportTime();
  return (
    <header className="kk-topbar">
      <div className="kk-wrap flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-3">
        <div className="flex min-w-0 items-center gap-4">
          <Logo size={40} className="text-[var(--ink)]" />
          <p className="hidden min-w-0 flex-col leading-tight sm:flex">
            <span className="font-semibold">{t("kk.airport")}</span>
            <span className="text-sm text-[var(--ink-3)] tabular-nums" dir="ltr">{time && `${time} · `}Terminal 3</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {offline && (
            <span className="kk-chip hidden sm:inline-flex" data-tone="wait" role="status"><RiWifiOffLine className="size-4" aria-hidden />{t("system.offline_chip")}</span>
          )}
          <div role="radiogroup" aria-label={lang === "ar" ? "اللغة" : "Language"} className="flex rounded-full bg-[var(--surface)] p-1 shadow-[var(--card-shadow)]">
            {(["en", "ar"] as Lang[]).map((code) => (
              <button key={code} type="button" role="radio" aria-checked={lang === code} onClick={() => setLang(code)} lang={code}
                className={cx("kk-press min-h-10 rounded-full px-4 font-semibold outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]",
                  lang === code ? "bg-[var(--ink)] text-[var(--surface)]" : "text-[var(--ink-2)]")}>
                {code === "en" ? "EN" : "عربي"}
              </button>
            ))}
          </div>
          <IconButton label={t(dark ? "workspace.light" : "workspace.dark")} onClick={() => document.documentElement.classList.toggle("dark", !dark)}>
            {dark ? <RiSunLine className="size-5" aria-hidden /> : <RiMoonLine className="size-5" aria-hidden />}
          </IconButton>
          <IconButton label={t("kk.judge.title")} onClick={onJudge} active={judgeOpen}><RiScalesLine className="size-5" aria-hidden /></IconButton>
          <IconButton label={t("ui.help")} onClick={onHelp}><RiQuestionLine className="size-5" aria-hidden /></IconButton>
          {inSession && <IconButton label={t("ui.end_session")} onClick={onEnd}><RiLogoutBoxRLine className="size-5" aria-hidden /></IconButton>}
        </div>
      </div>
    </header>
  );
}
