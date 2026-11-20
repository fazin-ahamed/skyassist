import type { I18nText, ViewModel } from "@/lib/types";
import type { Tone } from "@/components/kiosk/ui";
import { STATUS_TONE } from "@/components/kiosk/ui";

export type SignalKind = "info" | "good" | "warn" | "risk";
export interface Signal {
  id: string;
  kind: SignalKind;
  title: I18nText;
  body?: I18nText;
  /** Stays on screen until the passenger acts; otherwise it fades back to the quiet pill. */
  sticky: boolean;
  action?: "route" | "details";
}

const RANK: Record<Tone, number> = { lime: 0, blue: 1, orange: 2, rose: 3, accent: 0, neutral: 0 };
const tone = (v: ViewModel): Tone => (STATUS_TONE[v.status.status] ?? STATUS_TONE["Timing unverified"]).tone;

const ASSIST_STUCK = ["declined", "cancelled", "expired"];

/** Decide whether this update is worth interrupting the passenger for. Null means stay quiet. */
export function deriveSignal(prev: ViewModel | null, next: ViewModel): Signal | null {
  if (!prev) {
    const level = tone(next);
    if (level === "orange" || level === "rose") {
      const reason = next.status.reason_codes.find((c) => c !== "STAFF_HANDOFF_PENDING");
      // The status itself is already on the tag's flag; the slip only adds the reason.
      if (!reason) return null;
      return { id: `first-${next.revision}`, kind: level === "rose" ? "risk" : "warn", title: { key: `reason.${reason}` }, sticky: true };
    }
    return { id: `first-${next.revision}`, kind: "info", title: { key: "agent.watching" }, sticky: false };
  }
  if (next.assistance && prev.assistance?.state !== next.assistance.state) {
    return { id: `assist-${next.assistance.state}`, kind: "info", title: next.assistance.stage, body: next.assistance.staffed_alternative ?? undefined, sticky: ASSIST_STUCK.includes(next.assistance.state) };
  }
  if (prev.gate.value && next.gate.value && prev.gate.value !== next.gate.value && next.gate.state === "fresh") {
    return { id: `gate-${next.gate.value}`, kind: "warn", title: { key: "agent.gate_changed", params: { gate: next.gate.value } }, sticky: true, action: "route" };
  }
  if (prev.gate.state === "fresh" && (next.gate.state === "stale" || next.gate.state === "conflict")) {
    return { id: `gate-${next.gate.state}`, kind: "warn", title: { key: `reason.GATE_${next.gate.state.toUpperCase()}` }, sticky: true };
  }
  if (RANK[tone(next)] > RANK[tone(prev)]) {
    const reason = next.status.reason_codes.find((c) => c !== "STAFF_HANDOFF_PENDING");
    // The flag on the tag already names the status; the slip says why, and only names the status when it has no reason.
    return {
      id: `status-${next.status.status}`, kind: tone(next) === "rose" ? "risk" : "warn",
      title: reason ? { key: `reason.${reason}` } : { key: `status.${next.status.status.toLowerCase().replace(/ /g, "_")}` },
      sticky: true,
    };
  }
  const banner = next.banners.find((b) => b.kind === "route_changed" && !prev.banners.some((p) => p.kind === b.kind));
  if (banner) return { id: `route-${next.route?.revision}`, kind: "info", title: banner.text, sticky: false, action: "route" };
  return null;
}
