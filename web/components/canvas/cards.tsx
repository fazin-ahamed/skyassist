"use client";

import { type CSSProperties, type ReactNode } from "react";
import {
  RiCheckLine, RiCustomerService2Line, RiFlightTakeoffLine, RiHandHeartLine, RiQuestionLine, RiRouteLine, RiTimeLine,
} from "@remixicon/react";
import { rowsOf } from "@/components/kiosk/board";
import { FlapText } from "@/components/kiosk/flap";
import { MODE_ICON } from "@/components/kiosk/ui";
import { Aircraft } from "@/components/system/system-panel";
import { useBoard } from "@/hooks/use-board";
import { useTween } from "@/hooks/use-tween";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { AssistanceView, MapLive, Pledge, RequestView, ViewModel } from "@/lib/types";
import { cx } from "@/utils/cx";

export type CardKey = "gate" | "map" | "ledger" | "route" | "assist" | "board" | "aircraft";

/** A bento card. `focus` is set while the agent is talking about it. */
export function Card({ id, index = 0, focus, className, children, label }: {
  id?: CardKey; index?: number; focus?: boolean; className?: string; children: ReactNode; label?: string;
}) {
  return (
    <section id={id ? `card-${id}` : undefined} aria-label={label} data-focus={focus ? "true" : undefined}
      className={cx("kk-card kk-card-pad kk-rise scroll-mt-24", className)} style={{ "--i": index } as CSSProperties}>
      {children}
    </section>
  );
}

export function CardHead({ title, sub, aside }: { title: string; sub?: string; aside?: ReactNode }) {
  return (
    <header className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="kk-title">{title}</h2>
        {sub && <p className="kk-sub mt-0.5">{sub}</p>}
      </div>
      {aside}
    </header>
  );
}

const LEVEL_TONE = { safe: "ok", tight: "wait", at_risk: "risk", impossible: "risk", unverified: "neutral" } as const;

/** The gate on its yellow plate, the verdict in one word, and the margin against boarding close drawn as a bar. */
export interface WalkControl { playing: boolean; can: boolean; toggle: () => void; speed: 1 | 2 | 4; setSpeed: (s: 1 | 2 | 4) => void; step: number | null; total: number | null }

export function VerdictHero({ view, legs, focus, onRoute, onAssist, walk }: {
  view: ViewModel; legs: { inbound: string; outbound: string } | null; focus?: boolean; onRoute: () => void; onAssist: () => void; walk?: WalkControl;
}) {
  const { t, r } = useI18n();
  const level = view.verdict?.level ?? "unverified";
  const tone = LEVEL_TONE[level];
  const gate = view.gate.state === "fresh" && view.gate.value ? view.gate.value : null;
  const margin = view.verdict?.margin_min ?? null;
  const shown = useTween(Math.abs(margin ?? 0));
  const tr = view.transfer;
  const total = tr?.upper_min != null ? tr.upper_min + Math.max(margin ?? 0, 0) : 0;
  const lowPct = total ? ((tr?.lower_min ?? 0) / total) * 100 : 0;
  const highPct = total ? ((tr?.upper_min ?? 0) / total) * 100 : 0;
  const place = [view.gate.terminal && t("kk.gate.terminal", { terminal: view.gate.terminal }), view.gate.concourse && t("kk.map.concourse", { c: view.gate.concourse })].filter(Boolean).join(", ");
  return (
    <Card id="gate" index={0} focus={focus} className="overflow-hidden" label={t("kk.card.gate")}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div>
          <p className="kk-sub">{t("kk.card.gate")}{legs?.outbound ? ` · ${legs.outbound}` : ""}</p>
          <div className="mt-2 flex items-center gap-4">
            <span className="kk-plate text-[4.75rem] sm:text-[6rem]" aria-label={gate ?? t("kk.gate.unknown")}>
              {gate ? <FlapText value={gate} /> : <span aria-hidden>--</span>}
            </span>
          </div>
          <p className="kk-sub mt-2" dir="auto">{gate ? place : t("kk.gate.unknown")}</p>
        </div>
        <div className="flex min-w-44 flex-1 flex-col items-start gap-2 sm:items-end sm:text-end">
          <span className="kk-chip kk-flag" data-tone={tone}>{r(view.verdict?.text ?? { key: "verdict.unverified" })}</span>
          {margin !== null && (
            <p className="flex items-baseline gap-2">
              <span className="text-[3.25rem] leading-none font-bold tabular-nums" style={{ fontFamily: "var(--font-display)", color: `var(--${tone === "neutral" ? "ink" : tone})` }}>{shown}</span>
              <span className="text-lg font-semibold text-[var(--ink-2)]">{margin >= 0 ? t("kk.margin.spare") : t("kk.margin.short")}</span>
            </p>
          )}
          {view.cutoff.value && !view.cutoff.unavailable && (
            <p className="kk-sub">{t("kk.closes", { time: hhmm(view.cutoff.value) })}{view.cutoff.estimated ? ` · ${t("kk.estimate")}` : ""}</p>
          )}
        </div>
      </div>

      {tr?.lower_min != null && tr.upper_min != null && total > 0 && (
        <div className="mt-6" role="img" aria-label={t("kk.bar.alt", { lower: tr.lower_min, upper: tr.upper_min, margin: margin ?? 0 })}>
          <div className="relative h-4 overflow-hidden rounded-full bg-[var(--surface-3)]">
            <div className="absolute inset-y-0 start-0 rounded-full bg-[var(--blue)] transition-[width] duration-700" style={{ width: `${lowPct}%` }} />
            <div className="absolute inset-y-0 bg-[repeating-linear-gradient(135deg,var(--blue)_0_5px,color-mix(in_oklab,var(--blue)_55%,transparent)_5px_10px)] transition-all duration-700"
              style={{ insetInlineStart: `${lowPct}%`, width: `${Math.max(0, highPct - lowPct)}%` }} />
            {(margin ?? 0) > 0 && (
              <div className="absolute inset-y-0 end-0 bg-[var(--ok-soft)]" style={{ width: `${100 - highPct}%` }} />
            )}
          </div>
          <div className="mt-2 flex justify-between gap-2 text-sm font-semibold text-[var(--ink-2)] tabular-nums">
            <span>{t("kk.bar.now")}</span>
            <span>{t("kk.bar.walk", { lower: tr.lower_min, upper: tr.upper_min })}</span>
            <span>{t("kk.bar.close")}</span>
          </div>
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-[var(--hair)] pt-4">
        <p className="min-w-0 flex-1 text-[1.0625rem] font-semibold" dir="auto">{r(view.next_action.text)}</p>
        {walk?.can && (
          <span className="flex items-center gap-2">
            <button type="button" onClick={walk.toggle} aria-pressed={walk.playing} className="kk-press flex min-h-12 items-center gap-2 rounded-full bg-[var(--blue)] px-5 font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)] focus-visible:ring-offset-2">
              {walk.playing ? t("walk.pause") : t("walk.start")}
            </button>
            <span role="radiogroup" aria-label={t("walk.speed")} className="flex rounded-full bg-[var(--surface-3)] p-1">
              {([1, 2, 4] as const).map((s) => <button key={s} type="button" role="radio" aria-checked={walk.speed === s} onClick={() => walk.setSpeed(s)} className={cx("kk-press min-h-10 rounded-full px-3 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]", walk.speed === s ? "bg-[var(--surface)] shadow-sm" : "text-[var(--ink-3)]")}>{s}x</button>)}
            </span>
            {walk.step != null && walk.total ? <span className="kk-sub tabular-nums">{t("walk.step", { n: Math.min(walk.step + 1, walk.total), total: walk.total })}</span> : null}
          </span>
        )}
        {view.route && (
          <button type="button" onClick={onRoute} className="kk-press flex min-h-12 items-center gap-2 rounded-full bg-[var(--ink)] px-5 font-semibold text-[var(--surface)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)] focus-visible:ring-offset-2">
            <RiRouteLine className="size-5" aria-hidden />{t("kk.show_route")}
          </button>
        )}
        {!view.assistance && (
          <button type="button" onClick={onAssist} className="kk-press flex min-h-12 items-center gap-2 rounded-full bg-[var(--surface-3)] px-5 font-semibold outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]">
            <RiHandHeartLine className="size-5" aria-hidden />{t("kk.ask_staff")}
          </button>
        )}
      </div>
    </Card>
  );
}

const STATE_TONE = { verified: "ok", pending: "wait", unknown: "neutral" } as const;
const STATE_ICON = { verified: RiCheckLine, pending: RiTimeLine, unknown: RiQuestionLine } as const;

/** Every statement the kiosk will make, with how far it stands behind it. Nothing here comes from a model. */
export function LedgerCard({ view, focus }: { view: ViewModel; focus?: boolean }) {
  const { t, r } = useI18n();
  const count = (s: Pledge["state"]) => view.ledger.filter((p) => p.state === s).length;
  return (
    <Card id="ledger" index={2} focus={focus} className="flex flex-col" label={t("kk.card.ledger")}>
      <CardHead title={t("kk.card.ledger")} sub={t("kk.card.ledger_sub")} />
      <div className="mb-3 flex flex-wrap gap-2">
        {(["verified", "pending", "unknown"] as const).map((s) => (
          <span key={s} className="kk-chip" data-tone={STATE_TONE[s]}>{count(s)} {t(`ledger.state.${s}`).toLowerCase()}</span>
        ))}
      </div>
      <ul className="flex flex-col">
        {view.ledger.map((p, i) => {
          const Icon = STATE_ICON[p.state];
          return (
            <li key={`${p.key}-${p.state}`} className="kk-rise flex items-start gap-3 border-t border-dashed border-[var(--hair)] py-3 first:border-t-0" style={{ "--i": i + 3 } as CSSProperties}>
              <span className="kk-pop mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full" data-tone={STATE_TONE[p.state]}
                style={{ "--i": i, background: `var(--${p.state === "verified" ? "ok" : p.state === "pending" ? "wait" : "surface"}-soft, var(--surface-3))`, color: `var(--${p.state === "verified" ? "ok" : p.state === "pending" ? "wait" : "ink-3"})` } as CSSProperties}>
                <Icon className="size-4" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[1.0625rem] leading-snug font-semibold" dir="auto">{r(p.text)}</p>
                <p className="mt-0.5 flex items-center gap-2 text-sm text-[var(--ink-3)]">
                  <span className="kk-chip" data-tone={STATE_TONE[p.state]}>{t(`ledger.state.${p.state}`)}</span>
                  {p.observed_at && <span className="tabular-nums">{t("kk.checked", { time: hhmm(p.observed_at) })}</span>}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** The way there, one numbered stop at a time. Stops already reached are struck back. */
export function RouteCard({ view, live, focus }: { view: ViewModel; live: MapLive | null; focus?: boolean }) {
  const { t, r } = useI18n();
  const route = view.route;
  if (!route) return null;
  const reached = live?.route?.progress?.reached_steps ?? 0;
  return (
    <Card id="route" index={3} focus={focus} label={t("kk.card.route")}>
      <CardHead title={t("kk.card.route")} sub={t("kk.route.steps", { n: route.steps.length })} />
      <ol className="relative flex flex-col gap-1">
        {route.steps.map((step, i) => {
          const Icon = MODE_ICON[step.accessible_mode];
          const done = i < reached;
          const current = i === reached;
          return (
            <li key={step.number} className={cx("kk-rise relative flex gap-3 rounded-2xl p-2.5 transition-colors", current && "bg-[var(--blue-soft)]")} style={{ "--i": i + 3 } as CSSProperties}>
              <span className={cx("flex size-9 shrink-0 items-center justify-center rounded-full text-base font-bold tabular-nums",
                done ? "bg-[var(--ok-soft)] text-[var(--ok)]" : current ? "bg-[var(--blue)] text-white" : "bg-[var(--surface-3)] text-[var(--ink-2)]")}>
                {done ? <RiCheckLine className="size-5" aria-hidden /> : step.number}
              </span>
              <div className={cx("min-w-0 flex-1", done && "opacity-60")}>
                <p className="flex flex-wrap items-center gap-x-2 text-[1.0625rem] leading-snug font-semibold" dir="auto">
                  <Icon className="size-5 shrink-0 text-[var(--blue)]" aria-hidden />{r(step.landmark)}
                  <span className="kk-chip">{t("kiosk.floor", { floor: step.floor })}</span>
                </p>
                <p className="kk-sub mt-0.5" dir="auto">{r(step.text)}</p>
              </div>
            </li>
          );
        })}
      </ol>
      {view.qr.token && (
        <div className="mt-4 flex items-center gap-4 rounded-2xl bg-[var(--surface-2)] p-3 ring-1 ring-[var(--hair)]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/route/${view.qr.token}/qr.svg`} alt={t("kk.route.qr")} className="size-24 shrink-0 rounded-lg bg-white p-1.5" />
          <p className="kk-sub">{t("kk.route.qr_hint")}</p>
        </div>
      )}
    </Card>
  );
}

const STAGES = ["requested", "approval", "dispatch", "met"] as const;
const DONE_COUNT: Partial<Record<AssistanceView["state"], number>> = {
  pending_approval: 1, approved: 2, dispatch_pending_verification: 2, dispatched: 3, completed: 4,
};

/** Staff help as a four-step tracker. The coordinator's approval lands as a stamp. */
const CLOSED = ["cancelled", "declined", "expired", "completed"];

function RequestList({ requests, onCancel }: { requests: RequestView[]; onCancel?: (caseRef: string) => void }) {
  const { t, r } = useI18n();
  return (
    <ul className="mt-4 flex flex-col gap-2 border-t border-dashed border-[var(--hair)] pt-3" aria-label={t("kk.requests")}>
      {requests.map((q) => (
        <li key={q.case_ref} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.9375rem]">
          <strong>{t(`assistance.service.${q.service}`)}</strong>
          <span className="kk-chip" data-tone={q.state === "declined" || q.state === "cancelled" || q.state === "expired" ? "risk" : q.state === "pending_approval" ? "wait" : "ok"}>{r(q.stage)}</span>
          {q.destination && <span className="kk-chip" data-tone="info">{t("kk.heading_to", { gate: q.destination })}</span>}
          {q.retargeted_from && <s className="kk-sub">{t("kk.moved_from", { old: q.retargeted_from })}</s>}
          {q.group_role && <span className="kk-chip">{t(q.group_role === "rides" ? "kk.request.rides" : "kk.request.waits")}</span>}
          {onCancel && !CLOSED.includes(q.state) && <button type="button" onClick={() => onCancel(q.case_ref)} className="kk-press ms-auto min-h-9 rounded-full bg-[var(--surface-3)] px-3 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)]">{t("kk.cancel_request")}</button>}
        </li>
      ))}
    </ul>
  );
}

export function AssistCard({ a, focus, onRequest, requests, onCancel }: { a: AssistanceView | null | undefined; focus?: boolean; onRequest: () => void; requests?: RequestView[]; onCancel?: (caseRef: string) => void }) {
  const { t, r } = useI18n();
  if (!a) {
    return (
      <Card id="assist" index={4} focus={focus} label={t("kk.card.assist")}>
        <CardHead title={t("kk.card.assist")} sub={t("kk.assist.none")} />
        <button type="button" onClick={onRequest} className="kk-press flex min-h-12 w-full items-center justify-center gap-2 rounded-full bg-[var(--ink)] px-5 font-semibold text-[var(--surface)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)] focus-visible:ring-offset-2">
          <RiCustomerService2Line className="size-5" aria-hidden />{t("kk.ask_staff")}
        </button>
      </Card>
    );
  }
  const ended = a.state === "declined" || a.state === "expired" || a.state === "cancelled";
  const complete = ended ? 0 : (DONE_COUNT[a.state] ?? 1);
  return (
    <Card id="assist" index={4} focus={focus} className="overflow-hidden" label={t("kk.card.assist")}>
      <CardHead title={t("kk.card.assist")} sub={r(a.stage)} />
      {complete >= 2 && (
        <span className="kk-stamp pointer-events-none absolute end-6 top-5 rounded-lg border-[3px] border-[var(--ok)] px-3 py-1 text-xl font-bold text-[var(--ok)] uppercase" style={{ fontFamily: "var(--font-display)", letterSpacing: "0.08em" }}>
          {t("kk.assist.approved")}
        </span>
      )}
      <ol className="flex items-start">
        {STAGES.map((stage, i) => {
          const done = !ended && i < complete;
          const now = !ended && i === complete;
          return (
            <li key={stage} className="relative flex flex-1 flex-col items-center gap-2 text-center">
              {i > 0 && <span aria-hidden className={cx("absolute start-[-50%] top-4 h-0.5 w-full", !ended && i <= complete ? "bg-[var(--ok)]" : "bg-[var(--surface-3)]")} />}
              <span className={cx("relative z-10 flex size-8 items-center justify-center rounded-full ring-4 ring-[var(--surface)]",
                done ? "bg-[var(--ok)] text-white" : now ? "bg-[var(--wait-soft)] text-[var(--wait)]" : "bg-[var(--surface-3)] text-[var(--ink-3)]")}>
                {done ? <RiCheckLine className="size-5" aria-hidden /> : now ? <RiTimeLine className="size-5" aria-hidden /> : i + 1}
              </span>
              <span className={cx("text-sm leading-tight font-semibold", now || done ? "text-[var(--ink)]" : "text-[var(--ink-3)]")}>{t(`kk.assist.stage.${stage}`)}</span>
            </li>
          );
        })}
      </ol>
      {ended && <p className="mt-4 rounded-xl bg-[var(--risk-soft)] p-3 font-semibold text-[var(--risk)]" role="alert">{r(a.staffed_alternative ?? a.stage)}</p>}
      {a.pickup_landmark && <p className="kk-sub mt-4" dir="auto">{t("kk.assist.pickup", { place: r(a.pickup_landmark) })}</p>}
      {requests && requests.length > 0 && <RequestList requests={requests} onCancel={onCancel} />}
    </Card>
  );
}

/** The next departures on a split-flap board. Tapping one hands its code to the conversation. */
export function BoardCard({ onPick, index = 1 }: { onPick: (code: string) => void; index?: number }) {
  const { t } = useI18n();
  const { board } = useBoard("departures");
  const rows = board ? rowsOf(board, "departures").filter((row) => row.gate).slice(0, 5) : [];
  return (
    <Card id="board" index={index} className="flex flex-col" label={t("kk.card.board")}>
      <CardHead title={t("kk.card.board")} sub={t("kk.board.sub")}
        aside={<span className="kk-chip" data-tone="ok"><span className="size-2 rounded-full bg-[var(--ok)]" />{t("kiosk.live")}</span>} />
      <ul className="flex flex-col">
        {rows.length === 0 && <li className="kk-sub py-6 text-center">{t("ui.loading")}</li>}
        {rows.map((row) => (
          <li key={row.id} className="border-t border-dashed border-[var(--hair)] first:border-t-0">
            <button type="button" onClick={() => onPick(row.code.replace(" ", ""))}
              className="kk-press flex min-h-16 w-full items-center gap-3 rounded-xl px-1 text-start outline-none hover:bg-[var(--surface-2)] focus-visible:ring-2 focus-visible:ring-[var(--blue)]">
              <RiFlightTakeoffLine className="size-5 shrink-0 text-[var(--blue)]" aria-hidden />
              <span dir="ltr" className="w-[5.5rem] shrink-0 text-xl font-bold tabular-nums" style={{ fontFamily: "var(--font-display)" }}>{row.code}</span>
              <span className="min-w-0 flex-1 truncate font-semibold text-[var(--ink-2)]"><bdi dir="ltr">{row.city}</bdi> <span className="tabular-nums">{row.time}</span></span>
              <span className="kk-plate px-2.5 py-1 text-2xl"><FlapText value={row.gate ?? ""} /></span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function AircraftCard({ active, index = 5 }: { active: boolean; index?: number }) {
  const { t } = useI18n();
  return (
    <Card id="aircraft" index={index} label={t("system.aircraft")}>
      <CardHead title={t("system.aircraft")} sub={t("kk.aircraft.sub")} />
      <Aircraft active={active} />
    </Card>
  );
}
