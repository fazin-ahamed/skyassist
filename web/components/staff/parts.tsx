"use client";

import { RiAlertLine, RiCheckLine, RiInformationLine, RiTimeLine, RiWheelchairLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Gate } from "@/components/kiosk/journey";
import { Label, StatusPill, Tag, Tear, type Tone } from "@/components/kiosk/ui";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { CaseState, StaffCaseItem } from "@/lib/types";
import { cx } from "@/utils/cx";

export const OPEN: CaseState[] = ["pending_approval", "approved", "dispatch_pending_verification", "dispatched"];
const CASE_DONE: CaseState[] = ["completed", "declined", "cancelled", "expired"];
/** Case state is not connection status, so it never borrows a status colour. */
export const caseTone = (s: CaseState): Tone => (CASE_DONE.includes(s) ? "neutral" : "accent");

/** Minutes left as the coordinator reads it: whole minutes, or the server's own passed-cutoff wording. */
export function useLeft() {
  const { t } = useI18n();
  return (item: StaffCaseItem): { text: string; late: boolean; known: boolean } => {
    const s = item.time_remaining;
    if (s == null) return { text: t("flag.cutoff_unavailable"), late: false, known: false };
    return s < 0 ? { text: t("status.boarding_cutoff_passed"), late: true, known: true } : { text: `${Math.floor(s / 60)} min`, late: false, known: true };
  };
}

/** Urgency order: decisions first, then the least time left; unknown cutoffs last. */
export function byUrgency(a: StaffCaseItem, b: StaffCaseItem): number {
  const p = (i: StaffCaseItem) => (i.case.state === "pending_approval" ? 0 : 1);
  if (p(a) !== p(b)) return p(a) - p(b);
  return (a.time_remaining ?? Infinity) - (b.time_remaining ?? Infinity);
}

/** Every open case on one axis of minutes to its boarding cutoff. Left is urgent. */
export function CutoffLane({ items, selected, onSelect }: { items: StaffCaseItem[]; selected: string | null; onSelect: (id: string) => void }) {
  const { t } = useI18n();
  const left = useLeft();
  const known = items.filter((i) => i.time_remaining != null);
  const unknown = items.length - known.length;
  const maxMin = Math.min(180, Math.max(60, Math.ceil(Math.max(0, ...known.map((i) => (i.time_remaining as number) / 60)) / 30) * 30));
  const ticks = Array.from({ length: maxMin / 15 + 1 }, (_, i) => i * 15);
  return (
    <Tag className="px-6 pt-5 pb-4" aria-labelledby="lane-h">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="lane-h" className="text-title-3-semibold">Minutes to boarding cutoff</h2>
        <span className="text-body-medium text-ink-soft">{unknown > 0 ? `${unknown} without a verified cutoff` : "Left is most urgent"}</span>
      </div>
      <div className="relative mt-2 h-28">
        <div aria-hidden className="absolute inset-x-0 bottom-7 h-1 rounded-full bg-tag-wash" />
        {ticks.map((m) => (
          <span key={m} aria-hidden className="absolute bottom-0 -translate-x-1/2 text-body-2-medium tabular-nums text-ink-soft" style={{ left: `${(m / maxMin) * 100}%` }}>
            <span className="absolute -top-3.5 left-1/2 h-2 w-px bg-tag-rule" />{m}
          </span>
        ))}
        {known.length === 0 && <p className="absolute inset-x-0 top-6 text-center text-body-regular text-ink-soft">{items.length ? "No open case has a verified cutoff." : "Nothing is waiting. New requests appear here the moment they arrive."}</p>}
        {known.map((i, n) => {
          const l = left(i);
          const pct = Math.min(100, Math.max(0, ((i.time_remaining as number) / 60 / maxMin) * 100));
          const active = i.case.id === selected;
          const lift = 28 + (n % 2) * 34;
          return (
            <button key={i.case.id} type="button" onClick={() => onSelect(i.case.id)} aria-pressed={active} aria-label={`${i.gate.value ?? ""} ${l.text}`}
              className="absolute bottom-7 flex -translate-x-1/2 flex-col items-center outline-none" style={{ left: `${pct}%` }}>
              <span className={cx("mb-0 flex items-center gap-1.5 rounded-md px-2 py-1 text-body-semibold tabular-nums ring-2", l.late ? "bg-status-rose-background text-status-rose-text" : i.case.state === "pending_approval" ? "bg-accent-600 text-text-white" : "bg-tag-wash text-ink", active ? "ring-ink" : "ring-transparent focus-visible:ring-border-focus-ring")}
                style={{ marginBottom: lift - 28 }}>
                <RiWheelchairLine className="size-4" aria-hidden /><span dir="ltr">{i.gate.value ?? "?"}</span>
              </span>
              <span aria-hidden className="w-0.5 bg-ink" style={{ height: lift - 20 }} />
              <span aria-hidden className="size-2.5 rounded-full bg-ink" />
            </button>
          );
        })}
      </div>
    </Tag>
  );
}

/** One case on the rack: gate large, service and pickup, minutes left, and where it stands. */
export function RackRow({ item, active, onSelect }: { item: StaffCaseItem; active: boolean; onSelect: () => void }) {
  const { t, r } = useI18n();
  const left = useLeft()(item);
  return (
    <li>
      <button type="button" onClick={onSelect} aria-pressed={active}
        className={cx("paper tag flex w-full items-center gap-4 px-4 py-3 text-start outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring", active && "ring-2 ring-accent-600")}>
        <span dir="ltr" className="numeral-lg w-20 shrink-0">{item.gate.value ?? "?"}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="truncate text-body-semibold">{t(`assistance.service.${item.case.service}`)}</span>
          <span className="truncate text-body-medium text-ink-soft">{item.case.pickup ? r(item.case.pickup.landmark) : t("arrivals.unknown")}</span>
          <StatusPill tone={caseTone(item.case.state)} icon={RiTimeLine}>{t(`assistance.stage.${item.case.state}`)}</StatusPill>
        </span>
        <span className={cx("shrink-0 text-end numeral-md", left.late && "text-status-rose-text")}>{left.known && !left.late ? left.text : <span className="text-body-semibold">{left.text}</span>}</span>
      </button>
    </li>
  );
}

const field = "flex items-baseline justify-between gap-6 border-b border-dashed border-tag-rule py-3 last:border-b-0";

/** The open case as a full tag: facts at the top, the decision on the stub. */
export function CaseTag({ item, onAct }: { item: StaffCaseItem; onAct: (k: "approve" | "decline" | "dispatch" | "none_free") => void }) {
  const { t, r, source } = useI18n();
  const left = useLeft()(item);
  const c = item.case;
  const tone = caseTone(c.state);
  const pending = c.state === "pending_approval";
  return (
    <Tag aria-labelledby="case-h">
      <div className="flex flex-wrap items-center gap-4 px-8 pt-7">
        <span className="punch shrink-0" aria-hidden />
        <h2 id="case-h" className="text-title-2-semibold">{t("ui.staff.case")} <span dir="ltr" className="tabular-nums">{c.id.slice(0, 8)}</span></h2>
        <StatusPill tone={tone} icon={RiTimeLine}>{t(`assistance.stage.${c.state}`)}</StatusPill>
      </div>
      {(!c.pickup_confirmed || !c.pickup) && (
        <p className="mx-8 mt-5 flex items-start gap-2 rounded-lg bg-status-orange-background p-3 text-body-semibold text-status-orange-text"><RiAlertLine className="mt-0.5 size-5 shrink-0" aria-hidden />{t("reason.PICKUP_UNCONFIRMED")}</p>
      )}
      <div className="grid gap-x-12 gap-y-6 px-8 pt-6 pb-8 md:grid-cols-[auto_1fr_1fr]">
        <Gate gate={item.gate} size="xl" />
        <div className="flex flex-col gap-2">
          <Label>{t("ui.boarding_closes", { time: "" }).trim()}</Label>
          {item.cutoff.value && !item.cutoff.unavailable ? (
            <>
              <p dir="ltr" className="numeral-xl">{hhmm(item.cutoff.value)}</p>
              <p className="text-body-medium text-ink-soft">{t("ui.last_verified", { time: hhmm(item.cutoff.observed_at), source: source(item.cutoff.source) })}</p>
            </>
          ) : <p className="text-title-3-semibold text-status-blue-text">{t("flag.cutoff_unavailable")}</p>}
        </div>
        <div className="flex flex-col gap-2">
          <Label>{t("ui.staff.time_remaining")}</Label>
          <p className={cx("numeral-xl", left.late && "text-status-rose-text")}>{left.known && !left.late ? left.text : <span className="text-title-3-semibold">{left.text}</span>}</p>
        </div>
      </div>
      <Tear />
      <dl className="px-8 py-4 text-body-medium">
        <div className={field}><dt className="font-semibold">{t("ui.staff.service")}</dt><dd className="text-ink-soft">{t(`assistance.service.${c.service}`)}</dd></div>
        <div className={field}><dt className="font-semibold">{t("ui.staff.pickup")}</dt><dd className="text-ink-soft">{c.pickup ? r(c.pickup.landmark) : t("arrivals.unknown")}</dd></div>
        <div className={field}><dt className="font-semibold">Approval window ends</dt><dd className="tabular-nums text-ink-soft">{c.approval_expires_at ? hhmm(c.approval_expires_at) : t("arrivals.unknown")}</dd></div>
        <div className={field}><dt className="font-semibold">Request revision</dt><dd className="tabular-nums text-ink-soft">{c.request_revision}</dd></div>
        {c.reason_codes.length > 0 && (
          <div className={cx(field, "flex-col items-start gap-2")}>
            <dt className="font-semibold">{t("ui.staff.priority_reason")}</dt>
            <dd><ul className="flex flex-col gap-1.5">{c.reason_codes.map((code) => <li key={code} className="flex gap-2 text-ink-soft"><RiInformationLine className="mt-0.5 size-5 shrink-0" aria-hidden />{t(`reason.${code}`)}</li>)}</ul></dd>
          </div>
        )}
      </dl>
      <Tear />
      <div className="flex flex-wrap gap-3 px-8 pt-7 pb-8">
        <Button variant="primary" leadingIcon={RiCheckLine} className="h-14 rounded-lg px-8 text-headline-semibold" disabled={!pending} onClick={() => onAct("approve")}>{t("ui.staff.approve")}</Button>
        <Button variant="secondary" className="h-14 rounded-lg px-8 text-headline-semibold" disabled={!pending} onClick={() => onAct("decline")}>{t("ui.staff.decline")}</Button>
        <Button variant="secondary" className="h-14 rounded-lg px-6 text-headline-semibold" disabled={!pending} onClick={() => onAct("none_free")}>None free (20 min)</Button>
        {c.state === "approved" && <Button variant="secondary" className="h-14 rounded-lg px-8 text-headline-semibold" onClick={() => onAct("dispatch")}>Mark dispatched</Button>}
      </div>
    </Tag>
  );
}
