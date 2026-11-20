"use client";

import { useState } from "react";
import { RiCheckLine, RiInformationLine, RiQrCodeLine, RiUser2Line, RiWheelchairLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { RouteMap } from "@/components/kiosk/route-map";
import { MODE_ICON, StatusPill, type Tone } from "@/components/kiosk/ui";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { AssistanceView, CaseState, RouteView, ViewModel } from "@/lib/types";
import { cx } from "@/utils/cx";

export function RouteTag({ route, qr }: { route: RouteView; qr: ViewModel["qr"] }) {
  const { t, r, mode } = useI18n();
  return (
    <section id="route" tabIndex={-1} aria-labelledby="route-h" className="workspace-panel workspace-scroll-target flex flex-col gap-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="route-h" className="text-title-1-semibold">{t("kiosk.route_title")}</h2>
        <span className="text-body-medium text-text-secondary">{t(mode === "demo" ? "maps.demo_route" : "maps.route_schematic")}</span>
      </div>
      <RouteMap route={route} />
      <div className="grid gap-5">
        <ol className="workspace-steps" aria-label={t("ui.route_text_alternative")}>
          {route.steps.map((s, i) => {
            const Icon = MODE_ICON[s.accessible_mode];
            const previous = route.steps[i - 1];
            const newFloor = !previous || previous.floor !== s.floor || previous.map_position?.concourse !== s.map_position?.concourse;
            return (
              <li key={s.number} className="flex gap-4 pb-5 last:pb-0">
                <span className="route-step-number relative z-10 grid size-10 shrink-0 place-items-center rounded-full text-headline-semibold tabular-nums">{s.number}</span>
                <div className="flex min-w-0 flex-col gap-1 pt-1">
                  {newFloor && <span className="text-body-2-medium text-text-secondary">{t("kiosk.floor", { floor: s.floor })}</span>}
                  <strong className="text-headline-semibold">{r(s.landmark)}</strong>
                  <span className="text-body-regular text-text-secondary">{r(s.text)}</span>
                  <span className="flex items-center gap-2 text-body-2-medium text-text-secondary"><Icon className="size-4 shrink-0" aria-hidden />{t("mode." + s.accessible_mode)}</span>
                </div>
              </li>
            );
          })}
        </ol>
        <Qr qr={qr} />
      </div>
    </section>
  );
}

function Qr({ qr }: { qr: ViewModel["qr"] }) {
  const { t } = useI18n();
  const [shared, setShared] = useState<string | null>(null);
  if (!qr.token) {
    return qr.suppressed_reason ? (
      <p className="flex max-w-56 items-start gap-2 text-body-medium text-ink-soft"><RiQrCodeLine className="mt-0.5 size-5 shrink-0" aria-hidden />{t(`reason.${qr.suppressed_reason}`)}</p>
    ) : null;
  }
  const shown = shared === qr.token;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-separator-border p-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-headline-semibold">{t("ui.scan_to_continue")}</p>
        <p className="text-body-medium text-text-secondary">{t("workspace.qr_hint")}</p>
      </div>
      <Button variant="secondary" leadingIcon={RiQrCodeLine} className="h-14 px-3" aria-expanded={shown} aria-controls="phone-qr"
        onClick={() => setShared(shown ? null : qr.token ?? null)}>
        {t(shown ? "workspace.qr_close" : "workspace.qr_open")}
      </Button>
      <div id="phone-qr" hidden={!shown} className="w-full">
        {shown && (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img src={"/route/" + qr.token + "/qr.svg"} referrerPolicy="no-referrer" alt={t("ui.scan_to_continue")}
            className="size-44 rounded-lg bg-tag" />
        )}
      </div>
    </div>
  );
}

const STAGES: { state: CaseState; key: string }[] = [
  { state: "pending_approval", key: "kiosk.stage.requested" },
  { state: "approved", key: "kiosk.stage.approved" },
  { state: "dispatched", key: "kiosk.stage.dispatched" },
  { state: "completed", key: "kiosk.stage.complete" },
];
const STAGE_INDEX: Partial<Record<CaseState, number>> = {
  draft: -1, pending_approval: 0, approved: 1, dispatch_pending_verification: 1, dispatched: 2, completed: 3,
};
const ASSIST_DONE: CaseState[] = ["completed", "declined", "cancelled", "expired"];

/** Assistance as a four-stop tracker. Staff own each step; this only shows where the request is. */
export function AssistTag({ a }: { a: AssistanceView }) {
  const { t, r } = useI18n();
  const tone: Tone = ASSIST_DONE.includes(a.state) ? "neutral" : "accent";
  const idx = STAGE_INDEX[a.state];
  return (
    <section id="assistance-details" tabIndex={-1} aria-labelledby="assist-h" aria-live="polite" className="workspace-panel workspace-scroll-target flex flex-col gap-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="assist-h" className="flex items-center gap-3 text-title-1-semibold"><RiWheelchairLine className="size-5 shrink-0" aria-hidden />{t("ui.request_assistance")}</h2>
        <StatusPill tone={tone} icon={a.state === "completed" ? RiCheckLine : RiInformationLine}>{r(a.stage)}</StatusPill>
      </div>
      <div className="flex flex-col gap-5">
        {idx !== undefined && (
          <ol className="grid grid-cols-2 gap-y-3 sm:grid-cols-4">
            {STAGES.map((s, i) => {
              const done = i < idx;
              const now = i === idx;
              return (
                <li key={s.state} aria-current={now ? "step" : undefined} className="relative flex flex-col items-start gap-2">
                  {i > 0 && <span aria-hidden className={cx("absolute start-0 top-4 hidden h-px w-full -translate-x-full sm:block rtl:translate-x-full", i <= idx ? "bg-accent-600" : "bg-separator-border")} />}
                  <span className={cx(
                    "z-10 flex size-9 items-center justify-center rounded-full border-2",
                    done ? "border-accent-600 bg-accent-600 text-text-white" : now ? "border-accent-600 bg-background-primary-default text-text-primary" : "border-separator-border bg-background-primary-default text-text-secondary",
                  )}>
                    {done ? <RiCheckLine className="size-5" aria-hidden /> : <span className="size-2.5 rounded-full bg-current" />}
                  </span>
                  <span className={cx("text-body-semibold", !done && !now && "text-text-secondary")}>{t(s.key)}</span>
                </li>
              );
            })}
          </ol>
        )}
        <dl className="flex flex-col gap-1 text-body-medium">
          {a.pickup_landmark && <div className="flex gap-2"><dt className="font-semibold">{t("ui.staff.pickup")}</dt><dd>{r(a.pickup_landmark)}</dd></div>}
          {a.case_ref && <div className="flex gap-2"><dt className="font-semibold">{t("kiosk.case_ref")}</dt><dd dir="ltr" className="tabular-nums">{a.case_ref.slice(0, 8)}</dd></div>}
          {a.approval_window && a.state === "pending_approval" && <div>{t("kiosk.approval_window", { time: hhmm(a.approval_window.end) })}</div>}
        </dl>
      </div>
      {a.staffed_alternative && (
        <p className="flex items-center gap-3 border-t border-separator-border pt-4 text-body-semibold"><RiUser2Line className="size-5 shrink-0" aria-hidden />{r(a.staffed_alternative)}</p>
      )}
    </section>
  );
}

/** Why the status reads as it does: the reason codes the server attached, in plain words. */
export function WhyTag({ view }: { view: ViewModel }) {
  const { t } = useI18n();
  const codes = view.status.reason_codes.filter((c) => c !== "TIGHT_MARGIN");
  if (!codes.length) return null;
  return (
    <section className="workspace-panel p-4 sm:p-6" aria-labelledby="why-h">
      <h2 id="why-h" className="text-title-3-semibold">{t("kiosk.why")}</h2>
      <ul className="mt-3 flex flex-col gap-2">
        {codes.map((code) => (
          <li key={code} className="flex items-start gap-2 text-body-regular text-text-secondary">
            <RiInformationLine className="mt-0.5 size-5 shrink-0" aria-hidden />{t(`reason.${code}`)}
          </li>
        ))}
      </ul>
    </section>
  );
}
