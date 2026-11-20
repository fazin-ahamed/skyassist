import { hhmm, statusKey } from "@/lib/format";
import { STATUS_TONE, type Tone } from "@/components/kiosk/ui";
import type { I18nText, ViewModel } from "@/lib/types";

export type LogKind = "info" | "good" | "warn" | "risk" | "you";
/** One printed entry on the receipt: a time, a tone, and one or more lines. */
export interface LogEntry { id: string; at: string; kind: LogKind; lines: (I18nText | string)[] }

const toneKind = (t: Tone): LogKind => (t === "lime" ? "good" : t === "orange" ? "warn" : t === "rose" ? "risk" : "info");
const statusTone = (v: ViewModel): Tone => (STATUS_TONE[v.status.status] ?? STATUS_TONE["Timing unverified"]).tone;


const asking = (v: ViewModel) => v.next_action.choices.length > 0 || v.next_action.action === "choose_landmark";
const range = (v: ViewModel) => (v.transfer && v.transfer.lower_min != null && v.transfer.upper_min != null ? `${v.transfer.lower_min}-${v.transfer.upper_min}` : "");

function gateLine(v: ViewModel): I18nText[] {
  if (v.gate.state === "fresh" && v.gate.value) return [{ key: "agent.log.gate", params: { gate: v.gate.value } }];
  if (v.gate.state === "stale" || v.gate.state === "conflict") return [{ key: `reason.GATE_${v.gate.state.toUpperCase()}` }];
  return [{ key: "agent.log.gate_none" }];
}

/** What the agent printed when it first had your connection, oldest line first. */
export function snapshotEntries(v: ViewModel): LogEntry[] {
  const at = v.as_of;
  const out: LogEntry[] = [{ id: `found-${v.revision}`, at, kind: "info", lines: [{ key: "agent.log.found" }] }];
  out.push({ id: `gate-${v.revision}`, at, kind: v.gate.state === "fresh" && v.gate.value ? "good" : "warn", lines: gateLine(v) });
  if (v.cutoff.value && !v.cutoff.unavailable) {
    out.push({ id: `cutoff-${v.revision}`, at, kind: "good", lines: [{ key: "agent.log.cutoff", params: { time: hhmm(v.cutoff.value) } }] });
  } else {
    out.push({ id: `cutoff-${v.revision}`, at, kind: "warn", lines: [{ key: "flag.cutoff_unavailable" }] });
  }
  if (range(v)) out.push({ id: `transfer-${v.revision}`, at, kind: "info", lines: [{ key: "agent.log.transfer", params: { range: range(v) } }] });
  if (v.route) out.push({ id: `route-${v.revision}`, at, kind: "good", lines: [{ key: "agent.log.route", params: { n: v.route.steps.length } }] });
  out.push({ id: `status-${v.revision}`, at, kind: toneKind(statusTone(v)), lines: [{ key: statusKey(v.status.status) }] });
  return out;
}

/** What changed between two views that is worth a line on the receipt. */
export function diffEntries(prev: ViewModel, next: ViewModel): LogEntry[] {
  const at = next.as_of;
  const out: LogEntry[] = [];
  const r = next.revision;
  if (prev.gate.value && next.gate.value && prev.gate.value !== next.gate.value && next.gate.state === "fresh") {
    out.push({ id: `gate-${next.gate.value}-${r}`, at, kind: "warn", lines: [{ key: "agent.gate_changed", params: { gate: next.gate.value } }] });
  } else if (prev.gate.state !== next.gate.state) {
    out.push({ id: `gatestate-${next.gate.state}-${r}`, at, kind: next.gate.state === "fresh" ? "good" : "warn", lines: gateLine(next) });
  }
  if (prev.cutoff.value !== next.cutoff.value && next.cutoff.value) {
    out.push({ id: `cutoff-${r}`, at, kind: "warn", lines: [{ key: "agent.log.cutoff_changed", params: { time: hhmm(next.cutoff.value) } }] });
  }
  if (range(prev) !== range(next) && range(next)) {
    out.push({ id: `transfer-${r}`, at, kind: "info", lines: [{ key: "agent.log.transfer", params: { range: range(next) } }] });
  }
  if (next.route && (prev.route?.route_id !== next.route.route_id || prev.route?.revision !== next.route.revision)) {
    out.push({ id: `route-${r}`, at, kind: "info", lines: [{ key: prev.route ? "agent.log.route_new" : "agent.log.route", params: { n: next.route.steps.length } }] });
  }
  for (const b of next.banners) {
    if (!prev.banners.some((p) => p.kind === b.kind)) out.push({ id: `banner-${b.kind}-${r}`, at, kind: "warn", lines: [b.text] });
  }
  if (prev.status.status !== next.status.status) {
    out.push({ id: `status-${r}`, at, kind: toneKind(statusTone(next)), lines: [{ key: statusKey(next.status.status) }] });
  }
  if (next.assistance && prev.assistance?.state !== next.assistance.state) {
    out.push({ id: `assist-${next.assistance.state}-${r}`, at, kind: "info", lines: [next.assistance.stage] });
  }
  if (asking(next) && (!asking(prev) || prev.next_action.text.key !== next.next_action.text.key)) {
    out.push({ id: `ask-${r}`, at, kind: "info", lines: [{ key: "agent.log.asking" }, next.next_action.text] });
  }
  return out;
}

export type Question = "time" | "gate" | "why" | "help";

/** Answers composed only from fields the server already sent. The agent states facts; it computes none. */
export function answerEntries(q: Question, v: ViewModel | null, now: string): LogEntry[] {
  const you: LogEntry = { id: `you-${q}-${now}-${Math.random().toString(36).slice(2, 6)}`, at: v?.as_of ?? now, kind: "you", lines: [{ key: `agent.ask.${q}` }] };
  const reply = (lines: I18nText[], kind: LogKind = "info"): LogEntry => ({ id: `${you.id}-reply`, at: you.at, kind, lines });
  if (!v) return [reply([{ key: "agent.checking" }]), you];
  if (q === "gate") {
    const where: I18nText[] = [];
    if (v.gate.concourse) where.push({ key: "kiosk.gate.concourse", params: { concourse: v.gate.concourse } });
    if (v.gate.level) where.push({ key: "kiosk.gate.level", params: { level: v.gate.level } });
    if (v.gate.state === "fresh" && v.gate.value) return [reply([{ key: "agent.log.gate", params: { gate: v.gate.value } }, ...where], "good"), you];
    return [reply(gateLine(v), "warn"), you];
  }
  if (q === "time") {
    const tr = v.transfer;
    const lines: I18nText[] = [{ key: statusKey(v.status.status) }];
    if (tr && tr.lower_min != null && tr.upper_min != null) lines.push({ key: tr.includes_readiness ? "transfer.total_with_readiness" : "kiosk.transfer.range", params: { lower: tr.lower_min, upper: tr.upper_min } });
    if (tr?.margin_min != null) lines.push(tr.margin_min >= 0 ? { key: "transfer.margin", params: { margin: tr.margin_min } } : { key: "kiosk.transfer.short_by", params: { margin: Math.abs(tr.margin_min) } });
    return [reply(lines, toneKind(statusTone(v))), you];
  }
  if (q === "why") {
    const codes = v.status.reason_codes.filter((c) => c !== "TIGHT_MARGIN").slice(0, 3);
    return [reply(codes.length ? codes.map((c) => ({ key: `reason.${c}` })) : [{ key: statusKey(v.status.status) }]), you];
  }
  return [reply([{ key: "agent.reply.help" }], "info"), you];
}
