"use client";

import { useState } from "react";
import { RiAlertLine, RiCloseLine, RiMapPinLine, RiQuestionAnswerLine, RiRouteLine, RiWheelchairLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Spinner } from "@/components/base/spinner/spinner";
import { Legs } from "@/components/kiosk/legs";
import { Label, STATUS_TONE, StatusPill, TONE_CLASS, type Tone } from "@/components/kiosk/ui";
import { hhmm, minutesBetween, statusKey } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Signal, SignalKind } from "@/lib/signals";
import type { GateView, ViewModel } from "@/lib/types";
import { cx } from "@/utils/cx";

export function Gate({ gate, size = "hero" }: { gate: GateView; size?: "hero" | "xl" }) {
  const { t } = useI18n();
  if (gate.state === "fresh" && gate.value) {
    const where = [
      gate.concourse && t("kiosk.gate.concourse", { concourse: gate.concourse }),
      gate.level && t("kiosk.gate.level", { level: gate.level }),
    ].filter(Boolean).join(" · ");
    return (
      <div className="flex flex-col gap-3">
        <Label>{t("ui.gate")}</Label>
        <p className={cx(size === "hero" ? "numeral-hero" : "numeral-xl", "text-start")}><bdi dir="ltr">{gate.value}</bdi></p>
        {where && <p className="text-body-semibold text-ink-soft">{where}</p>}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <Label>{t("ui.gate")}</Label>
      <p className={cx("flex items-center rounded-xl border-2 border-dashed border-tag-rule px-6 text-title-1-semibold", size === "hero" ? "min-h-32" : "min-h-16")}>{t("kiosk.gate.unconfirmed")}</p>
    </div>
  );
}

function Timeline({ view }: { view: ViewModel }) {
  const { t } = useI18n();
  const tr = view.transfer;
  if (!view.cutoff.value || view.cutoff.estimated || !tr || tr.lower_min == null || tr.upper_min == null) return null;
  const remaining = Math.max(0, minutesBetween(view.as_of, view.cutoff.value));
  const scale = Math.max(remaining, tr.upper_min, 1);
  const pct = (m: number) => Math.min(100, (m / scale) * 100) + "%";
  return (
    <div className="flex min-w-0 flex-col gap-3" role="img" aria-label={t(tr.includes_readiness ? "transfer.total_with_readiness" : "kiosk.transfer.range", { lower: tr.lower_min, upper: tr.upper_min })}>
      <div className="relative h-3 rounded-sm bg-background-tertiary-default">
        <div className="absolute inset-y-0 start-0 rounded-sm bg-accent-300" style={{ width: pct(tr.upper_min) }} />
        <div className="absolute inset-y-0 start-0 rounded-sm bg-accent-600" style={{ width: pct(tr.lower_min) }} />
        <div className="absolute -inset-y-1 w-0.5 rounded-full bg-text-primary" style={{ insetInlineStart: "calc(" + pct(remaining) + " - 1px)" }} />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-body-medium tabular-nums text-text-secondary">
        <span className="flex items-center gap-2"><span className="size-3 rounded-sm bg-accent-600" />{t("kiosk.timeline.best", { min: tr.lower_min })}</span>
        <span className="flex items-center gap-2"><span className="size-3 rounded-sm bg-accent-300" />{t("kiosk.timeline.worst", { min: tr.upper_min })}</span>
        <span className="flex items-center gap-2"><span className="h-4 w-0.5 rounded-full bg-text-primary" />{t("ui.boarding_closes", { time: hhmm(view.cutoff.value) })}</span>
      </div>
    </div>
  );
}

interface JourneyProps {
  view: ViewModel | null;
  legs: { inbound: string; outbound: string };
  busy: boolean;
  onRoute: () => void;
  onAssist: () => void;
}

export function JourneyTag({ view, legs, onRoute, onAssist, busy }: JourneyProps) {
  const { t, r, source } = useI18n();
  if (!view) {
    return (
      <section className="workspace-panel flex min-h-48 items-center justify-center gap-4 p-8" role="status">
        <Spinner size="sm" />
        <span className="text-title-3-medium text-text-secondary">{t("agent.checking")}</span>
      </section>
    );
  }
  const status = STATUS_TONE[view.status.status] ?? STATUS_TONE["Timing unverified"];
  const { gate, cutoff, transfer } = view;
  const gateKnown = gate.state === "fresh" && !!gate.value;
  const cutoffKnown = !cutoff.unavailable && !!cutoff.value;
  const range = transfer?.lower_min != null && transfer.upper_min != null ? transfer.lower_min + "-" + transfer.upper_min : "";
  const margin = transfer?.margin_min;
  const asking = view.next_action.choices.length > 0 || view.next_action.action === "choose_landmark";
  const where = [gate.concourse && t("kiosk.gate.concourse", { concourse: gate.concourse }), gate.level && t("kiosk.gate.level", { level: gate.level })].filter(Boolean).join(" · ");
  return (
    <section className="workspace-panel flex flex-col gap-5 p-4 sm:p-6" aria-labelledby="journey-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="journey-h" className="text-title-2-semibold">
          {legs.inbound && legs.outbound ? <Legs inbound={legs.inbound} outbound={legs.outbound} /> : <bdi dir="ltr">{legs.outbound || legs.inbound}</bdi>}
        </h2>
        <StatusPill tone={status.tone} icon={status.icon}>{t(statusKey(view.status.status))}</StatusPill>
      </div>
      <dl className="workspace-fact-grid">
        <div className="workspace-fact">
          <dt className="text-headline-medium text-text-secondary">{t("ui.gate")}</dt>
          <dd className={gateKnown ? "numeral-lg" : "text-headline-semibold"}>{gateKnown ? <bdi dir="ltr">{gate.value}</bdi> : t("kiosk.gate.unconfirmed")}</dd>
          {gateKnown && where && <dd className="text-body-regular text-text-secondary">{where}</dd>}
        </div>
        <div className="workspace-fact">
          <dt className="text-headline-medium text-text-secondary">{t("agent.watch.cutoff")}</dt>
          <dd className={cutoffKnown ? "numeral-lg" : "text-headline-semibold"}>{cutoffKnown ? <bdi dir="ltr">{hhmm(cutoff.value)}</bdi> : t("flag.cutoff_unavailable")}</dd>
          {cutoffKnown && (
            <dd className="text-body-regular text-text-secondary">
              {cutoff.estimated ? t("kiosk.cutoff.estimated") : t("ui.last_verified", { time: hhmm(cutoff.observed_at), source: source(cutoff.source) })}
            </dd>
          )}
        </div>
        <div className="workspace-fact">
          <dt className="text-headline-medium text-text-secondary">{t("agent.you_need")}</dt>
          <dd className={cx("flex flex-wrap items-baseline gap-1", !range && "text-headline-semibold")}>
            {range ? <><bdi dir="ltr" className="numeral-lg">{range}</bdi><span className="text-headline-medium">{t("kiosk.min_unit")}</span></> : "—"}
          </dd>
          {range && <dd className="text-body-regular text-text-secondary">{t(transfer?.includes_readiness ? "agent.incl_readiness" : "agent.walk_only")}</dd>}
        </div>
      </dl>
      {margin != null && <p className="text-title-3-semibold"><bdi dir="ltr">{Math.abs(margin)}</bdi> {t(margin >= 0 ? "agent.margin_spare" : "agent.margin_short")}</p>}
      {transfer?.conditional && <p className="text-body-medium text-text-secondary">{t("transfer.conditional")}</p>}
      {!asking && <p className="text-headline-medium text-text-secondary">{r(view.next_action.text)}</p>}
      <Timeline view={view} />
      <div className="flex flex-wrap gap-3 border-t border-separator-border pt-4">
        {view.route && <Button variant="primary" leadingIcon={RiRouteLine} className="workspace-primary h-14 px-4 text-headline-semibold" onClick={onRoute}>{t("ui.take_me_there")}</Button>}
        {!view.assistance && <Button variant="secondary" leadingIcon={RiWheelchairLine} className="h-14 px-4 text-headline-semibold" disabled={!gateKnown || busy} onClick={onAssist}>{t("ui.request_assistance")}</Button>}
      </div>
    </section>
  );
}

const KIND_TONE: Record<SignalKind, Tone> = { info: "accent", good: "neutral", warn: "orange", risk: "rose" };

export function SignalSlip({ signal, onRoute, onDismiss }: { signal: Signal; onRoute: () => void; onDismiss: () => void }) {
  const { t, r } = useI18n();
  return (
    <section role={signal.sticky ? "alert" : "status"} className={cx("flex flex-wrap items-center gap-3 rounded-2xl p-4", TONE_CLASS[KIND_TONE[signal.kind]])}>
      <RiAlertLine className="size-5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-title-3-semibold">{r(signal.title)}</p>
        {signal.body && <p className="text-body-regular">{r(signal.body)}</p>}
      </div>
      {signal.action === "route" && <Button variant="secondary" leadingIcon={RiRouteLine} className="h-14 px-4" onClick={onRoute}>{t("agent.show_route")}</Button>}
      <Button variant="secondary" iconOnly leadingIcon={RiCloseLine} aria-label={t("agent.dismiss")} className="size-14" onClick={onDismiss} />
    </section>
  );
}

interface QuestionProps {
  view: ViewModel;
  busy: boolean;
  onChoose: (id: string) => void;
  onWhere: (text: string) => Promise<boolean>;
  onRestart: () => void;
}

export function QuestionSlip({ view, busy, onChoose, onWhere, onRestart }: QuestionProps) {
  const { t, r } = useI18n();
  const [text, setText] = useState("");
  const { next_action: next } = view;
  return (
    <section className="workspace-panel flex flex-col gap-4 border-accent-300 p-4 sm:p-6" aria-labelledby="question-title">
      <div className="flex items-center gap-2 text-text-secondary">
        <RiQuestionAnswerLine className="size-5 shrink-0" aria-hidden />
        <h2 id="question-title" className="text-headline-medium">{t("agent.asks")}</h2>
      </div>
      <p className="text-title-2-semibold" role="status">{r(next.text)}</p>
      {next.choices.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {next.choices.map((choice) => (
            <Button key={choice.id} variant="secondary" className="h-auto min-h-14 max-w-full whitespace-normal px-4 py-2 text-start text-headline-semibold" disabled={busy} onClick={() => onChoose(choice.id)}>{r(choice.label)}</Button>
          ))}
        </div>
      ) : next.action === "choose_flight" ? (
        <Button variant="primary" className="workspace-primary h-14 self-start px-4" onClick={onRestart}>{t("workspace.recheck")}</Button>
      ) : (
        <form className="flex flex-wrap items-end gap-3" onSubmit={async (event) => {
          event.preventDefault();
          if (text.trim() && await onWhere(text.trim())) setText("");
        }}>
          <Input className="min-w-40 flex-1" label={t("kiosk.assist.pickup_label")} value={text} onChange={setText}
            autoComplete="off" spellCheck="false" maxLength={80} fieldClassName="workspace-input" placeholder={t("kiosk.landmark_placeholder")} />
          <Button type="submit" variant="primary" leadingIcon={RiMapPinLine} className="workspace-primary h-14 px-4" disabled={busy || !text.trim()}>{t("workspace.locate")}</Button>
        </form>
      )}
    </section>
  );
}
