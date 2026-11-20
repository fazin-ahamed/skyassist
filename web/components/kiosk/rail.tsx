"use client";

import { RiRadarLine } from "@remixicon/react";
import { Receipt } from "@/components/kiosk/receipt";
import type { LogEntry } from "@/lib/agent-log";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { ViewModel } from "@/lib/types";

function WatchRows({ view }: { view: ViewModel }) {
  const { t, r, source } = useI18n();
  const { gate, cutoff, route, assistance, aircraft } = view;
  const gateNote = gate.state === "fresh" ? "agent.watch.verified" : gate.state === "stale" ? "agent.watch.stale"
    : gate.state === "conflict" ? "agent.watch.conflict" : "agent.watch.unknown";
  const cutoffKnown = !cutoff.unavailable && !!cutoff.value;
  const rows = [
    { label: t("ui.gate"), value: gate.state === "fresh" ? gate.value : null, note: t(gateNote) },
    { label: t("agent.watch.cutoff"), value: cutoffKnown ? hhmm(cutoff.value) : null,
      note: cutoffKnown ? t("ui.last_verified", { time: hhmm(cutoff.observed_at), source: source(cutoff.source) }) : t("flag.cutoff_unavailable") },
    { label: t("agent.watch.route"), value: route ? t("agent.watch.steps", { n: route.steps.length }) : null,
      note: route ? "" : t("agent.watch.unknown") },
    { label: t("agent.watch.assistance"), value: assistance ? r(assistance.stage) : t("agent.watch.none"), note: "" },
  ];
  if (aircraft.position && (aircraft.freshness === "current" || aircraft.freshness === "last_reported")) {
    rows.push({
      label: t("agent.watch.aircraft"),
      value: [aircraft.position.latitude.toFixed(2), aircraft.position.longitude.toFixed(2)].join(", "),
      note: t(aircraft.freshness === "last_reported" ? "ui.aircraft_last_reported" : "kiosk.aircraft.current", { time: hhmm(aircraft.observed_at) }),
    });
  }
  return (
    <ul aria-label={t("agent.watch.title")} className="mt-4 flex flex-col">
      {rows.map((row) => (
        <li key={row.label} className="border-t border-separator-border py-3 last:pb-0">
          <p className="flex flex-wrap justify-between gap-x-3 text-body-semibold"><span>{row.label}</span><bdi className="tabular-nums">{row.value ?? "—"}</bdi></p>
          {row.note && <p className="text-body-2-regular text-text-secondary">{row.note}</p>}
        </li>
      ))}
    </ul>
  );
}

export function AgentRail({ view, asking, log, down }: { view: ViewModel | null; asking: boolean; log: LogEntry[]; down: boolean }) {
  const { t } = useI18n();
  const state = down ? "kiosk.service_down" : !view ? "agent.checking" : asking ? "agent.state.asking"
    : view.assistance?.state === "pending_approval" ? "agent.state.waiting_staff" : "agent.state.watching";
  return (
    <aside aria-label={t("workspace.guide")} className="workspace-rail">
      <section className="workspace-panel p-5 sm:p-6">
        <div className="flex items-start gap-3">
          <RiRadarLine className="mt-1 size-5 shrink-0 text-text-secondary" aria-hidden />
          <div className="flex min-w-0 flex-col gap-1">
            <h2 className="text-title-2-semibold">{t("workspace.guide")}</h2>
            <p className="text-headline-medium text-text-secondary" role="status">{t(state)}</p>
            {view && <p className="text-body-regular text-text-secondary">{t("agent.last_checked", { time: hhmm(view.as_of) })}</p>}
          </div>
        </div>
        {view && <WatchRows view={view} />}
      </section>
      <Receipt title={t("agent.log.title")} entries={log} empty={t("agent.log.empty")} workspace />
    </aside>
  );
}
