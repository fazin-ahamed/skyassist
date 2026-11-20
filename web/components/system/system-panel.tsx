"use client";

import { RiPlaneLine, RiWifiOffLine } from "@remixicon/react";
import { Tag } from "@/components/kiosk/ui";
import { useTraffic } from "@/hooks/use-traffic";
import { useI18n } from "@/lib/i18n";
import type { SystemStatus, Traffic } from "@/lib/types";
import { cx } from "@/utils/cx";

/** One quiet line under the header: this is simulated data, and what is offline right now. Always honest, never loud. */
export function StatusStrip({ simulated, internet, reachable }: { simulated: boolean; internet: boolean; reachable: boolean }) {
  const { t } = useI18n();
  if (!simulated && internet && reachable) return null;
  return (
    <div role="status" className="mx-auto flex w-full max-w-[860px] flex-wrap items-center gap-x-5 gap-y-1 px-6 pt-3 text-body-2-medium text-belt-soft">
      {simulated && <span className="flex items-center gap-2"><RiPlaneLine className="size-4" aria-hidden />{t("system.simulated")}</span>}
      {(!internet || !reachable) && (
        <span className="flex items-center gap-2 font-semibold text-status-orange-text">
          <RiWifiOffLine className="size-4" aria-hidden />{t("system.offline_chip")}
        </span>
      )}
    </div>
  );
}

const km = (n: number) => `${Math.round(n)}`;

/** A plan view of the aircraft around DXB. Dots point the way they are heading; a ring is 25, 50 and 100 km. */
function Radar({ traffic }: { traffic: Traffic }) {
  const [lat0, lon0] = traffic.centre;
  const R = 100, size = 220, half = size / 2, scale = (half - 10) / R;
  const dots = traffic.aircraft.map((a) => {
    const dx = (a.lon - lon0) * 111.32 * Math.cos((lat0 * Math.PI) / 180);
    const dy = (a.lat - lat0) * 110.57;
    return { a, x: half + dx * scale, y: half - dy * scale };
  }).filter((d) => Math.hypot(d.x - half, d.y - half) <= half - 4);
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="size-44 shrink-0" role="img" aria-label="Aircraft near DXB">
      {[25, 50, 100].map((r) => <circle key={r} cx={half} cy={half} r={r * scale} fill="none" stroke="var(--tag-rule)" strokeWidth={1} />)}
      <line x1={half} y1={10} x2={half} y2={size - 10} stroke="var(--tag-rule)" strokeWidth={1} />
      <line x1={10} y1={half} x2={size - 10} y2={half} stroke="var(--tag-rule)" strokeWidth={1} />
      <g className="kk-sweep" aria-hidden>
        <path d={`M ${half} ${half} L ${half} 10 A ${half - 10} ${half - 10} 0 0 1 ${half + (half - 10) * Math.sin(0.6)} ${half - (half - 10) * Math.cos(0.6)} Z`} fill="var(--blue)" opacity={0.16} />
      </g>
      <rect x={half - 4} y={half - 4} width={8} height={8} rx={2} fill="var(--plate)" stroke="var(--tag-ink)" strokeWidth={1.2} />
      {dots.map(({ a, x, y }) => (
        <g key={a.icao24} transform={`translate(${x} ${y}) rotate(${a.heading ?? 0})`} opacity={a.on_ground ? 0.45 : 1}>
          <path d="M0 -5 L3.4 4 L0 2 L-3.4 4 Z" fill="var(--tag-ink)" />
        </g>
      ))}
    </svg>
  );
}

export function Aircraft({ active }: { active: boolean }) {
  const { t } = useI18n();
  const { traffic: shared } = useTraffic();
  const traffic = active ? shared : null;
  if (!traffic) return <p className="text-body-medium text-ink-soft">{t("ui.loading")}</p>;
  const note = traffic.status === "live" ? t("system.aircraft.live", { seconds: traffic.age_s ?? 0 })
    : traffic.status === "cached" ? t("system.aircraft.cached", { minutes: Math.max(1, Math.round((traffic.age_s ?? 0) / 60)) })
    : traffic.status === "offline" ? t("system.aircraft.offline") : t("system.aircraft.none");
  const nearest = traffic.aircraft.filter((a) => !a.on_ground).slice(0, 4);
  return (
    <div className="flex flex-wrap items-center gap-6">
      {traffic.aircraft.length > 0 && <Radar traffic={traffic} />}
      <div className="flex min-w-48 flex-1 flex-col gap-2">
        <p className="text-body-semibold">{t("system.aircraft.count", { n: traffic.aircraft.length })}</p>
        <p className={cx("text-body-medium", traffic.status === "live" ? "text-ink-soft" : "text-status-orange-text")}>{note} · {t("system.aircraft.source")}</p>
        {(traffic.arrivals ?? []).length > 0 && (
          <div className="flex flex-col gap-1 text-body-medium">
            <p className="text-body-semibold">{t("system.aircraft.arrivals")}</p>
            {(traffic.arrivals ?? []).map((a, i) => (
              <p key={`${a.callsign ?? "x"}-${i}`} className="flex justify-between gap-4 tabular-nums" dir="ltr">
                <span className="font-semibold">{a.callsign ?? "-"}</span>
                <span className="text-ink-soft">{t("system.aircraft.landing", { wait: a.eta_min })}</span>
              </p>
            ))}
          </div>
        )}
        {(traffic.sources ?? []).length > 0 && (
          <p className="text-body-medium text-ink-soft">{t("system.aircraft.sources", { source: (traffic.sources ?? []).join(", ") })}</p>
        )}
        <ul className="flex flex-col gap-1 text-body-medium tabular-nums">
          {nearest.map((a) => (
            <li key={a.icao24} className="flex justify-between gap-4" dir="ltr">
              <span className="font-semibold">{a.callsign ?? a.icao24}</span>
              <span className="text-ink-soft">{a.altitude_m != null ? `${Math.round(a.altitude_m * 3.281 / 100) * 100} ft` : ""} · {km(a.km)} km</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-dashed border-tag-rule py-3 last:border-b-0">
      <dt className="text-body-medium text-ink-soft">{label}</dt>
      <dd className={cx("text-body-semibold", tone === "warn" && "text-status-orange-text")}>{value}</dd>
    </div>
  );
}

/** What is running where, which model understood the passenger, and what needs the internet. */
export function SystemPanel({ status, reachable, open }: { status: SystemStatus | null; reachable: boolean; open: boolean }) {
  const { t } = useI18n();
  if (!status) return <Tag className="p-6"><p className="text-body-medium text-ink-soft">{reachable ? t("ui.loading") : t("system.offline")}</p></Tag>;
  const used = status.understanding.last_used;
  const voiceValue = !status.configured.voice ? t("system.voice.off") : !status.internet ? t("system.voice.offline") : t("system.voice.ready");
  return (
    <Tag className="flex flex-col gap-6 p-6">
      <dl>
        <Row label={t("system.simulated")} value={status.simulated ? t("system.simulated_note") : "-"} />
        <Row label="Internet" value={status.internet ? t("system.online") : t("system.offline")} tone={status.internet ? "ok" : "warn"} />
        <Row label={t("system.understanding")} value={t(`system.understanding.${used}`)} />
        <Row label={t("system.voice")} value={voiceValue} tone={status.configured.voice && !status.internet ? "warn" : undefined} />
        <Row label={t("system.key.deepgram")} value={t(`system.key.${status.keys.deepgram}`)} tone={status.keys.deepgram === "ok" ? "ok" : status.keys.deepgram === "missing" ? undefined : "warn"} />
        <Row label={t("system.key.openrouter")} value={t(`system.key.${status.keys.openrouter}`)} tone={status.keys.openrouter === "ok" ? "ok" : status.keys.openrouter === "missing" ? undefined : "warn"} />
      </dl>
      {status.setup.length > 0 && <ul className="flex flex-col gap-1 text-body-medium text-ink-soft">{status.setup.map((name) => <li key={name}>{t("system.setup_hint", { name })}</li>)}</ul>}
      {!status.internet && <p className="rounded-xl bg-status-orange-background px-4 py-3 text-body-medium text-status-orange-text">{t("system.offline_note")}</p>}
      <section aria-label={t("system.aircraft")} className="flex flex-col gap-3">
        <h3 className="text-body-semibold text-ink-soft">{t("system.aircraft")}</h3>
        <Aircraft active={open} />
      </section>
    </Tag>
  );
}
