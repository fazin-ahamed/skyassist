import type {
  AskReply, BuggyPlan, DecisionResult, DemoState, DraftCheck, FlightBoard, MapLayout, MapLive, PartyKey, PositionInput, Scoreboard, StaffCaseItem, SystemStatus, Traffic, ViewModel, VoiceStatus,
} from "@/lib/types";
import { PARTY } from "@/lib/types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  constructor(public status: number, public reason?: string) {
    super(`${status} ${reason ?? ""}`.trim());
  }
}

type Options = { token?: string; body?: unknown; keepalive?: boolean; params?: Record<string, string | number | string[] | undefined> };

async function call<T>(method: string, path: string, opts: Options = {}): Promise<{ data: T; headers: Headers }> {
  const url = new URL(BASE + path, typeof window === "undefined" ? "http://localhost" : window.location.origin);
  for (const [k, v] of Object.entries(opts.params ?? {})) {
    if (v === undefined) continue;
    for (const item of Array.isArray(v) ? v : [v]) url.searchParams.append(k, String(item));
  }
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: {
        ...(opts.body ? { "Content-Type": "application/json" } : {}),
        ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
      },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
      keepalive: opts.keepalive,
    });
  } catch {
    throw new ApiError(0, "SOURCE_UNAVAILABLE");
  }
  if (!res.ok) {
    let reason: string | undefined;
    try {
      const detail = (await res.json()).detail;
      reason = typeof detail === "object" ? detail?.reason_code : undefined;
    } catch {}
    throw new ApiError(res.status, reason);
  }
  const text = await res.text();
  return { data: (text ? JSON.parse(text) : undefined) as T, headers: res.headers };
}

const sessions = new Map<string, string>();
function tokenFor(journeyId: string): string {
  const cached = sessions.get(journeyId);
  if (!cached) throw new ApiError(401, "SESSION_EXPIRED");
  return cached;
}

export const api = {
  async now(): Promise<string> {
    return (await call<{ now: string }>("GET", "/clock")).data.now;
  },

  async flights(direction: "departures" | "arrivals", query?: string): Promise<FlightBoard> {
    const now = new Date(await api.now());
    const start = new Date(now.getTime() - 6 * 3600e3).toISOString();
    const end = new Date(now.getTime() + 42 * 3600e3).toISOString();
    return (await call<FlightBoard>("GET", "/flights", {
      params: { airport: "DXB", direction, start, end, limit: 50, query: query || undefined },
    })).data;
  },

  async createJourney(opts: { party?: PartyKey; language: string; outbound?: string; inbound?: string; booking_ref?: string; needs?: Record<string, unknown> }): Promise<string> {
    const party = PARTY[opts.party ?? "standard"];
    const { data, headers } = await call<{ journey_id: string }>("POST", "/journeys", {
      body: {
        airport: "DXB", profile: party.profile, party_needs: opts.needs ?? party.needs, language: opts.language,
        outbound_leg_id: opts.outbound, inbound_leg_id: opts.inbound, booking_ref: opts.booking_ref,
      },
    });
    const token = (headers.get("Authorization") ?? "").replace(/^Bearer /, "");
    if (!token) throw new ApiError(401, "SESSION_EXPIRED");
    sessions.set(data.journey_id, token);
    return data.journey_id;
  },

  async sendInput(journeyId: string, fields: Record<string, unknown>, knownRevision?: number): Promise<void> {
    await call("POST", `/journeys/${journeyId}/inputs`, {
      token: tokenFor(journeyId),
      body: { journey_id: journeyId, client_input_id: crypto.randomUUID().replace(/-/g, ""), known_revision: knownRevision, ...fields },
    });
  },

  async poll(journeyId: string, known?: number): Promise<{ revision: number; unchanged: boolean; view?: ViewModel }> {
    return (await call<{ revision: number; unchanged: boolean; view?: ViewModel }>("GET", `/journeys/${journeyId}`, {
      token: tokenFor(journeyId), params: { known_revision: known },
    })).data;
  },

  async assist(journeyId: string, body: { revision: number; service: string; pickup: string }): Promise<void> {
    await call("POST", `/journeys/${journeyId}/assistance`, {
      token: tokenFor(journeyId),
      body: {
        journey_id: journeyId, client_input_id: crypto.randomUUID().replace(/-/g, ""), journey_revision: body.revision,
        service: body.service, pickup_node_id: body.pickup, pickup_confirmed: true, consent: true,
      },
    });
  },

  async closeJourney(journeyId: string): Promise<void> {
    const token = sessions.get(journeyId);
    sessions.delete(journeyId);
    if (token) await call("DELETE", `/journeys/${journeyId}`, { token, keepalive: true });
  },

  async scoreboard(): Promise<Scoreboard> {
    return (await call<Scoreboard>("GET", "/eval/scoreboard")).data;
  },

  /** The same answer as ask(), narrated: steps while it works, then the words as they are written. */
  async askStream(journeyId: string, question: string, language: string, onEvent: (event: string, data: Record<string, unknown>) => void): Promise<AskReply | null> {
    let res: Response;
    try {
      res = await fetch(`${BASE}/journeys/${journeyId}/ask/stream`, {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokenFor(journeyId)}` },
        body: JSON.stringify({ question, language }), cache: "no-store",
      });
    } catch { return null; }
    if (!res.ok || !res.body) return null;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let final: AskReply | null = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const event = /^event: (.+)$/m.exec(block)?.[1] ?? "message";
        const raw = /^data: (.+)$/m.exec(block)?.[1];
        if (!raw) continue;
        const data = JSON.parse(raw) as Record<string, unknown>;
        if (event === "final") final = data as unknown as AskReply;
        onEvent(event, data);
      }
    }
    return final;
  },

  /** Plain words about a trip ("landed from Kolkata, need Madrid") into airports, airlines and the flights on those routes. */
  async understandTrip(text: string, language: string): Promise<{ understood: boolean; live: boolean; lines: string[] }> {
    return (await call<{ understood: boolean; live: boolean; lines: string[] }>("POST", "/flights/understand", { body: { text, language } })).data;
  },

  async ask(journeyId: string, question: string, language: string): Promise<AskReply> {
    return (await call<AskReply>("POST", `/journeys/${journeyId}/ask`, { token: tokenFor(journeyId), body: { question, language } })).data;
  },

  async checkDraft(journeyId: string, draft: string, language: string): Promise<DraftCheck> {
    return (await call<DraftCheck>("POST", `/journeys/${journeyId}/check-draft`, { token: tokenFor(journeyId), body: { draft, language } })).data;
  },

  async mapLayout(): Promise<MapLayout> {
    return (await call<MapLayout>("GET", "/map/layout")).data;
  },

  /** Closures, queue waits, gate, route progress and position. Pass a journey for its own layer. */
  async mapLive(journeyId?: string | null): Promise<MapLive> {
    if (journeyId) return (await call<MapLive>("GET", `/journeys/${journeyId}/live`, { token: tokenFor(journeyId) })).data;
    return (await call<MapLive>("GET", "/map/live")).data;
  },

  async setPosition(journeyId: string, where: PositionInput): Promise<MapLive> {
    return (await call<MapLive>("POST", `/journeys/${journeyId}/position`, { token: tokenFor(journeyId), body: where })).data;
  },

  async systemStatus(): Promise<SystemStatus> {
    return (await call<SystemStatus>("GET", "/system/status")).data;
  },

  async traffic(): Promise<Traffic> {
    return (await call<Traffic>("GET", "/traffic/nearby")).data;
  },

  async voiceStatus(): Promise<VoiceStatus> {
    return (await call<VoiceStatus>("GET", "/voice/status")).data;
  },

  async voiceVocabulary(language: string): Promise<{ terms: string[]; flights: { code: string; aliases: string[]; direction: "arrival" | "departure" }[] }> {
    return (await call<{ terms: string[]; flights: { code: string; aliases: string[]; direction: "arrival" | "departure" }[] }>("GET", "/voice/vocabulary", { params: { language } })).data;
  },

  /** Spoken reply as MP3 bytes, or null when the server has no voice for this language. */
  async speak(text: string, language: string): Promise<Blob | null> {
    try {
      const res = await fetch(`${BASE}/voice/speak`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }), cache: "no-store",
      });
      return res.ok ? await res.blob() : null;
    } catch {
      return null;
    }
  },

  clearLegacyPassengerTokens(): void {
    try {
      const store = window.sessionStorage;
      Object.keys(store).filter((k) => /^sa:[a-f0-9]{32}$/.test(k)).forEach((k) => store.removeItem(k));
    } catch {
      // A kiosk browser can disable storage entirely.
    }
  },

  async catalog(lang: string): Promise<{ texts: Record<string, string>; review: string; mode?: "demo" | "live" }> {
    return (await call<{ texts: Record<string, string>; review: string; mode?: "demo" | "live" }>("GET", `/i18n/${lang}`)).data;
  },
};

/** Demo-only controls for rehearsing curveballs. They use the demo coordinator token, which only exists in demo mode. */
const DEMO_TOKEN = "demo-coordinator";
export const demo = {
  async applyEvent(id: string): Promise<void> {
    await call("POST", `/demo/events/${id}/apply`, { token: DEMO_TOKEN, body: {} });
  },
  async setResource(kind: "wheelchair" | "escort" | "buggy", total: number): Promise<void> {
    await call("POST", "/demo/resources", { token: DEMO_TOKEN, body: { kind, total } });
  },
  async buggyPlans(): Promise<{ plans: BuggyPlan[] }> {
    return (await call<{ plans: BuggyPlan[] }>("GET", "/demo/buggy/plans", { token: DEMO_TOKEN })).data;
  },
};

export const staff = {
  async cases(token: string, states: string[]): Promise<{ as_of: string; cases: StaffCaseItem[] }> {
    return (await call<{ as_of: string; cases: StaffCaseItem[] }>("GET", "/staff/cases", {
      token, params: { airport: "DXB", states, limit: 50 },
    })).data;
  },
  async decide(token: string, c: { id: string; request_revision: number }, decision: "approve" | "decline", why?: { reason_code: string; wait_min: number }): Promise<DecisionResult> {
    return (await call<DecisionResult>("POST", `/staff/cases/${c.id}/decisions`, {
      token, body: { event_id: crypto.randomUUID().replace(/-/g, ""), case_id: c.id, request_revision: c.request_revision, decision, ...(why ?? {}) },
    })).data;
  },
  async dispatch(token: string, c: { id: string; request_revision: number }): Promise<DecisionResult> {
    const observed_at = await api.now();
    return (await call<DecisionResult>("POST", `/staff/cases/${c.id}/dispatch`, {
      token, body: {
        event_id: crypto.randomUUID().replace(/-/g, ""), case_id: c.id, request_revision: c.request_revision,
        status: "dispatched", evidence: { source: "demo-staff", observed_at },
      },
    })).data;
  },
  async demo(token: string): Promise<DemoState> {
    return (await call<DemoState>("GET", "/demo/state", { token })).data;
  },
  async clock(token: string, body: { name?: string; advance_seconds?: number }): Promise<void> {
    await call("POST", "/demo/clock", { token, body });
  },
  async apply(token: string, eventId: string): Promise<void> {
    await call("POST", `/demo/events/${eventId}/apply`, { token });
  },
  async feed(token: string, action: "hold" | "resume", legId: string): Promise<void> {
    await call("POST", `/demo/feed/${action}`, { token, body: { leg_id: legId, field: "gate" } });
  },
  async offline(token: string, offline: boolean): Promise<void> {
    await call("POST", "/system/offline", { token, body: { offline } });
  },
  async legs(): Promise<{ id: string; label: string }[]> {
    const board = await api.flights("departures");
    return board.flights.map((f) => ({
      id: f.leg.id, label: `${f.leg.operating_carrier} ${f.leg.operating_number} (${f.leg.scheduled_operation_date})`,
    }));
  },
};
