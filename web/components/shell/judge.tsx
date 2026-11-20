"use client";

import { useEffect, useRef, useState } from "react";
import { RiBracesLine, RiCheckLine, RiCloseLine, RiCpuLine, RiSparklingLine } from "@remixicon/react";
import { AnimatePresence, motion } from "motion/react";
import { api, demo } from "@/lib/api";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { AskTrace, BuggyPlan, DraftCheck, Scoreboard, SystemStatus, ViewModel } from "@/lib/types";
import { cx } from "@/utils/cx";

const ATTACKS = ["kk.judge.attack.promise", "kk.judge.attack.gate", "kk.judge.attack.staff", "kk.judge.attack.inject", "kk.judge.attack.number"] as const;

function Pane({ icon: Icon, title, sub, tone, children }: { icon: typeof RiCpuLine; title: string; sub: string; tone: "model" | "code"; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-2xl bg-[var(--surface-2)] p-4 ring-1 ring-[var(--hair)]">
      <header className="flex items-center gap-3">
        <span className={cx("flex size-10 items-center justify-center rounded-xl", tone === "model" ? "bg-[var(--wait-soft)] text-[var(--wait)]" : "bg-[var(--blue-soft)] text-[var(--blue)]")}><Icon className="size-5" aria-hidden /></span>
        <div><h3 className="font-semibold">{title}</h3><p className="text-sm text-[var(--ink-3)]">{sub}</p></div>
      </header>
      {children}
    </section>
  );
}

function Score({ board }: { board: Scoreboard | null }) {
  const { t } = useI18n();
  if (!board) return <p className="kk-sub">{t("ui.loading")}</p>;
  const rows: [string, string, boolean][] = [
    [t("kk.judge.score.promises"), String(board.unverified_promises_shown), board.unverified_promises_shown === 0],
    [t("kk.judge.score.invented"), String(board.invented_facts_shown), board.invented_facts_shown === 0],
    [t("kk.judge.score.gated"), `${board.dispatch_human_gated_pct}%`, board.dispatch_human_gated_pct === 100],
    [t("kk.judge.score.cases"), `${board.passed}/${board.total}`, board.passed === board.total],
    [t("kk.judge.score.lag"), `${board.event_loop_lag_ms_p99} ms`, board.event_loop_lag_ms_p99 < 250],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      {rows.map(([label, value, ok]) => (
        <div key={label} className="rounded-2xl bg-[var(--surface)] p-3 ring-1 ring-[var(--hair)]">
          <p className="text-3xl font-bold tabular-nums" style={{ fontFamily: "var(--font-display)", color: ok ? "var(--ok)" : "var(--risk)" }}>{value}</p>
          <p className="mt-1 text-sm leading-tight font-semibold text-[var(--ink-2)]">{label}</p>
        </div>
      ))}
    </div>
  );
}

/** One screen for the question the brief asks above all: what did the model do, and what did the code do. */
/** One button per curveball, so each can be rehearsed on demand. Demo mode only. */
function Curveballs({ onSay, onBuggy }: { onSay: (text: string) => void; onBuggy: () => Promise<void> }) {
  const { t } = useI18n();
  const [plans, setPlans] = useState<BuggyPlan[]>([]);
  const [busy, setBusy] = useState("");
  useEffect(() => {
    let stop = false;
    const load = () => demo.buggyPlans().then((x) => { if (!stop) setPlans(x.plans); }).catch(() => {});
    load();
    const timer = setInterval(load, 3000);
    return () => { stop = true; clearInterval(timer); };
  }, []);
  const run = (key: string, fn: () => Promise<void> | void) => async () => { setBusy(key); try { await fn(); } catch { /* the panel is a rehearsal aid */ } finally { setBusy(""); } };
  const btn = "kk-press min-h-11 rounded-full bg-[var(--surface)] px-4 text-sm font-semibold ring-1 ring-[var(--hair)] outline-none hover:bg-[var(--surface-3)] focus-visible:ring-2 focus-visible:ring-[var(--blue)] disabled:opacity-50";
  return (
    <section className="mt-5 rounded-2xl bg-[var(--surface-2)] p-4 ring-1 ring-[var(--hair)]">
      <h3 className="font-semibold">{t("cb.title")}</h3>
      <p className="kk-sub mt-1">{t("cb.sub")}</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div><p className="text-sm font-semibold text-[var(--ink-2)]">{t("cb.hs1")}</p>
          <button type="button" className={btn} disabled={busy === "a3"} onClick={run("a3", () => demo.applyEvent("ev-c01-gate-a3"))}>{t("cb.gate_a3")}</button></div>
        <div><p className="text-sm font-semibold text-[var(--ink-2)]">{t("cb.hs2")}</p>
          <span className="flex flex-wrap gap-2">
            <button type="button" className={btn} onClick={run("w0", () => demo.setResource("wheelchair", 0))}>{t("cb.no_wheelchair")}</button>
            <button type="button" className={btn} onClick={run("w2", () => demo.setResource("wheelchair", 2))}>{t("cb.wheelchair_back")}</button>
          </span></div>
        <div><p className="text-sm font-semibold text-[var(--ink-2)]">{t("cb.hs3")}</p>
          <span className="flex flex-wrap gap-2">
            <a className={btn + " inline-flex items-center"} href="/?booking=BK-MINOR1">{t("cb.minor")}</a>
            <a className={btn + " inline-flex items-center"} href="/?booking=BK-ADULT1">{t("cb.adult")}</a>
          </span></div>
        <div><p className="text-sm font-semibold text-[var(--ink-2)]">{t("cb.hs4")}</p>
          <span className="flex flex-wrap gap-2">
            <button type="button" className={btn} onClick={() => onSay(t("cb.say_staff"))}>{t("cb.staff")}</button>
            <button type="button" className={btn} onClick={() => onSay(t("cb.say_cancel"))}>{t("cb.cancel_other")}</button>
          </span></div>
        <div className="sm:col-span-2"><p className="text-sm font-semibold text-[var(--ink-2)]">{t("cb.hs5")}</p>
          <button type="button" className={btn} disabled={busy === "buggy"} onClick={run("buggy", onBuggy)}>{busy === "buggy" ? t("cb.working") : t("cb.buggy")}</button></div>
      </div>
      {plans.map((p) => (
        <div key={p.id} className="mt-3 rounded-xl bg-[var(--surface)] p-3 ring-1 ring-[var(--hair)]">
          <p className="font-semibold">{t("cb.plan", { gate: p.destination, used: p.seats_used, seats: p.seats })} <span className="kk-chip ms-2" data-tone={p.state === "pending_approval" ? "wait" : "ok"}>{p.state}</span></p>
          <ul className="mt-1 text-sm text-[var(--ink-2)]">
            {p.riders.map((x) => <li key={x.case_id}>{t("cb.rides", { n: x.people })} · {x.basis}</li>)}
            {p.leftover.map((x) => <li key={x.case_id} className="text-[var(--wait)]">{t("cb.waits", { n: x.people })} · {x.basis}</li>)}
          </ul>
        </div>
      ))}
    </section>
  );
}

export function JudgeSheet({ open, onClose, jid, view, trace, status, onSay, onBuggy, demoMode }: {
  open: boolean; onClose: () => void; jid: string | null; view: ViewModel | null; trace: AskTrace | null; status: SystemStatus | null;
  onSay: (text: string) => void; onBuggy: () => Promise<void>; demoMode: boolean;
}) {
  const { t, r, lang } = useI18n();
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<DraftCheck | null>(null);
  const [board, setBoard] = useState<Scoreboard | null>(null);
  const [busy, setBusy] = useState(false);

  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; });
  useEffect(() => {
    if (!open) return;
    let stop = false;
    api.scoreboard().then((b) => { if (!stop) setBoard(b); }).catch(() => {});
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close.current(); };
    window.addEventListener("keydown", onKey);
    return () => { stop = true; window.removeEventListener("keydown", onKey); };
  }, [open]);

  const run = async (text: string) => {
    if (!jid || !text.trim()) return;
    setBusy(true);
    try { setResult(await api.checkDraft(jid, text, lang)); } catch { setResult(null); } finally { setBusy(false); }
  };
  const drafts = trace?.attempts.filter((a) => a.by === "model") ?? [];
  const tr = view?.transfer;
  const used = status?.understanding.last_used ?? "template";

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-sm sm:items-center sm:p-6" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={onClose}>
          <motion.div role="dialog" aria-modal="true" aria-label={t("kk.judge.title")} onClick={(e) => e.stopPropagation()}
            className="kk-card max-h-[92dvh] w-full max-w-4xl overflow-y-auto rounded-b-none p-5 sm:rounded-3xl sm:p-7"
            initial={{ y: 60, opacity: 0, scale: 0.98 }} animate={{ y: 0, opacity: 1, scale: 1 }} exit={{ y: 40, opacity: 0 }} transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}>
            <header className="mb-5 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-bold" style={{ fontFamily: "var(--font-display)" }}>{t("kk.judge.title")}</h2>
                <p className="kk-sub mt-1 max-w-xl">{t("kk.judge.sub")}</p>
              </div>
              <button type="button" aria-label={t("agent.dismiss")} onClick={onClose} className="kk-press flex size-12 shrink-0 items-center justify-center rounded-full bg-[var(--surface-3)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]"><RiCloseLine className="size-6" aria-hidden /></button>
            </header>

            <Score board={board} />

            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <Pane icon={RiSparklingLine} tone="model" title={t("kk.judge.model")} sub={t("kk.judge.model_sub")}>
                <p className="text-sm font-semibold text-[var(--ink-2)]">{t("kk.judge.understood")}: <span className="kk-chip ms-1" data-tone="info">{t(`system.understanding.${used}`)}</span></p>
                {drafts.length === 0 ? <p className="kk-sub">{t("kk.judge.no_draft")}</p> : drafts.map((a, i) => (
                  <div key={i} className="rounded-xl bg-[var(--surface)] p-3 ring-1 ring-[var(--hair)]">
                    <p className="text-[0.9375rem]" dir="auto">{a.text}</p>
                    <p className="mt-2 flex flex-wrap items-center gap-2">
                      <span className="kk-chip" data-tone={a.ok ? "ok" : "risk"}>{a.ok ? t("kk.judge.accepted") : t("kk.judge.refused")}</span>
                      {a.violations.map((v, k) => <span key={k} className="text-sm text-[var(--risk)]">{v.detail}</span>)}
                    </p>
                  </div>
                ))}
                {trace && <p className="text-sm text-[var(--ink-3)]">{t("kk.judge.final")}: <strong>{t(`kk.judge.source.${trace.final.source}`)}</strong></p>}
              </Pane>

              <Pane icon={RiCpuLine} tone="code" title={t("kk.judge.code")} sub={t("kk.judge.code_sub")}>
                {view ? (
                  <>
                    <ul className="flex flex-col gap-1.5">
                      {view.ledger.map((p) => (
                        <li key={p.key} className="flex items-start gap-2 text-[0.9375rem]">
                          <span className="kk-chip mt-0.5 shrink-0" data-tone={p.state === "verified" ? "ok" : p.state === "pending" ? "wait" : "neutral"}>{t(`ledger.state.${p.state}`)}</span>
                          <span dir="auto">{r(p.text)}</span>
                        </li>
                      ))}
                    </ul>
                    {tr?.upper_min != null && view.cutoff.value && (
                      <p className="rounded-xl bg-[var(--surface)] p-3 font-mono text-sm leading-relaxed ring-1 ring-[var(--hair)]" dir="ltr">
                        close {hhmm(view.cutoff.value)} − worst walk {tr.upper_min} min{"\n"}
                        = margin {view.verdict?.margin_min ?? "n/a"} min → {view.verdict?.level ?? "unverified"}
                      </p>
                    )}
                  </>
                ) : <p className="kk-sub">{t("kk.judge.no_journey")}</p>}
              </Pane>
            </div>

            {demoMode && <Curveballs onSay={onSay} onBuggy={onBuggy} />}

            <section className="mt-5 rounded-2xl bg-[var(--surface-2)] p-4 ring-1 ring-[var(--hair)]">
              <h3 className="flex items-center gap-2 font-semibold"><RiBracesLine className="size-5 text-[var(--risk)]" aria-hidden />{t("kk.judge.lie")}</h3>
              <p className="kk-sub mt-1">{t("kk.judge.lie_sub")}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {ATTACKS.map((key) => <button key={key} type="button" onClick={() => { setDraft(t(key)); void run(t(key)); }} disabled={!jid} className="kk-press min-h-10 rounded-full bg-[var(--surface)] px-4 text-sm font-semibold ring-1 ring-[var(--hair)] outline-none hover:bg-[var(--surface-3)] focus-visible:ring-2 focus-visible:ring-[var(--blue)] disabled:opacity-50">{t(`${key}.label`)}</button>)}
              </div>
              <form className="mt-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void run(draft); }}>
                <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={600} dir="auto" placeholder={t("kk.judge.lie_placeholder")} aria-label={t("kk.judge.lie")}
                  className="min-h-12 min-w-0 flex-1 rounded-full bg-[var(--surface)] px-5 ring-1 ring-[var(--hair)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]" />
                <button type="submit" disabled={!jid || busy} className="kk-press min-h-12 rounded-full bg-[var(--ink)] px-6 font-semibold text-[var(--surface)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)] disabled:opacity-50">{t("kk.judge.check")}</button>
              </form>
              {!jid && <p className="kk-sub mt-2">{t("kk.judge.no_journey")}</p>}
              {result && (
                <div className="mt-3 rounded-xl bg-[var(--surface)] p-3 ring-1 ring-[var(--hair)]" role="status">
                  <p className="flex items-center gap-2 font-semibold" style={{ color: result.accepted ? "var(--ok)" : "var(--risk)" }}>
                    {result.accepted ? <RiCheckLine className="size-5" aria-hidden /> : <RiCloseLine className="size-5" aria-hidden />}
                    {result.accepted ? t("kk.judge.accepted") : t("kk.judge.refused")}
                  </p>
                  <ul className="mt-1 list-disc ps-5 text-sm text-[var(--ink-2)]">{result.violations.map((v, i) => <li key={i}><strong>{v.kind}</strong>: {v.detail}</li>)}</ul>
                </div>
              )}
            </section>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
