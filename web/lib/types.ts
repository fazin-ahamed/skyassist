export type Id = string;
export interface I18nText { key: string; params?: Record<string, string | number | boolean | null> }
export interface SourceStamp { source: string; observed_at: string }
export type ObservationState = "fresh" | "stale" | "conflict" | "unknown";

export interface GateView {
  value?: string | null; terminal?: string | null; concourse?: string | null; level?: string | null;
  state: ObservationState;
}
export interface CutoffView { value?: string | null; source?: string | null; observed_at?: string | null; unavailable: boolean; estimated: boolean }
export interface StatusView { status: string; reason_codes: string[]; provenance: SourceStamp }
export interface TransferView {
  lower_min?: number | null; upper_min?: number | null; includes_readiness: boolean;
  margin_min?: number | null; conditional: boolean;
}
export type AccessibleMode = "walk" | "step_free" | "stairs" | "lift" | "transit" | "checkpoint" | "staff_handoff";
export interface MapPoint { x: number; y: number }
export interface RouteStep {
  number: number; floor: string; landmark: I18nText; accessible_mode: AccessibleMode; text: I18nText;
  map_position?: (MapPoint & { terminal: string; concourse: string }) | null;
}
export interface FloorShape { terminal: string; concourse: string; floor: string; points: MapPoint[] }
export interface RouteView {
  route_id: string; revision: number; steps: RouteStep[]; floor_shapes: FloorShape[]; floor_outlines?: FloorShape[];
}
export type CaseState =
  | "draft" | "pending_approval" | "approved" | "dispatch_pending_verification" | "dispatched"
  | "completed" | "declined" | "cancelled" | "expired";
export interface AssistanceView {
  state: CaseState; stage: I18nText; pickup_landmark?: I18nText | null; case_ref?: string | null;
  staffed_alternative?: I18nText | null; approval_window?: { start: string; end: string } | null;
  service?: string | null; destination?: string | null; retargeted_from?: string | null;
}
export interface RequestView {
  case_ref: string; service: string; state: CaseState; stage: I18nText; destination?: string | null;
  retargeted_from?: string | null; group_role?: "rides" | "waits" | null; seats?: number | null;
}
export interface Notice { kind: string; text: I18nText; seq: number }
export type PassengerAction =
  | "choose_flight" | "confirm_date" | "choose_landmark" | "confirm_pickup" | "wait_for_handoff" | "follow_route"
  | "seek_staff" | "refresh_route" | "check_gate" | "seek_emergency_help" | "wait_for_update";
export interface Choice { id: string; label: I18nText }
export interface NextActionView { action: PassengerAction; text: I18nText; choices: Choice[] }
export interface BannerView { kind: "route_changed" | "route_expired" | "stale_data"; text: I18nText }
export interface QRView { token?: string | null; suppressed_reason?: string | null }
export interface ArrivalEstimate {
  applicability: "applicable" | "not_applicable" | "unknown"; state: ObservationState;
  wait?: { lower: number; upper: number | null } | null;
}
export interface ArrivalEstimates { baggage?: ArrivalEstimate | null; immigration?: ArrivalEstimate | null; security?: ArrivalEstimate | null }
export interface AircraftView {
  position?: { latitude: number; longitude: number } | null; source?: string | null; observed_at?: string | null;
  freshness: "current" | "last_reported" | "hidden" | "unavailable";
}
export type PledgeState = "verified" | "pending" | "unknown";
export interface Pledge { key: string; state: PledgeState; text: I18nText; source?: string | null; observed_at?: string | null }
export type VerdictLevel = "safe" | "tight" | "at_risk" | "impossible" | "unverified";
export interface Verdict { level: VerdictLevel; margin_min?: number | null; text: I18nText }
export interface ViewModel {
  revision: number; as_of: string; language: string;
  gate: GateView; cutoff: CutoffView; status: StatusView; transfer?: TransferView | null; route?: RouteView | null;
  assistance?: AssistanceView | null; next_action: NextActionView; banners: BannerView[]; qr: QRView;
  arrivals: ArrivalEstimates; aircraft: AircraftView;
  requests?: RequestView[]; notice?: Notice | null;
  verdict?: Verdict | null; ledger: Pledge[];
}

export interface FlightLeg {
  id: string; operating_carrier: string; operating_number: string; scheduled_operation_date: string;
  origin: string; destination: string;
}
export interface Observation { value?: unknown; scheduled?: string | null; estimated?: string | null; actual?: string | null }
export interface FieldEvidence { leg_id: string; field: string; state: ObservationState; observation?: Observation | null }
export interface FlightBoardItem { leg: FlightLeg; facts: FieldEvidence[] }
export interface FlightBoard { as_of: string; flights: FlightBoardItem[]; next_cursor?: string | null }

export interface StaffCase {
  id: string; journey_id: string; airport: string; service: string; state: CaseState; request_revision: number;
  journey_revision: number; created_at: string; pickup?: { node_id: string; landmark: I18nText } | null;
  pickup_confirmed: boolean; consent: boolean; approval_expires_at?: string | null; reason_codes: string[];
}
export interface StaffCaseItem { case: StaffCase; gate: GateView; cutoff: CutoffView; time_remaining?: number | null }
export interface DecisionResult { event_id: string; case_id: string; request_revision: number; state: CaseState; outcome: "applied" | "replayed" | "rejected"; reason_code?: string | null }
export interface DemoEvent { id: string; label: string; kind: string; scenario_ids: string[]; applied: boolean }
export interface DemoState { now: string; clock_starts: Record<string, string>; events: DemoEvent[]; held?: { leg_id: string; field: string }[] }

export type PartyKey = "standard" | "family" | "wheelchair" | "both";
export const PARTY: Record<PartyKey, { profile: string; needs: { party_size: number; children: number; step_free: boolean; wheelchair: boolean } }> = {
  standard: { profile: "standard", needs: { party_size: 1, children: 0, step_free: false, wheelchair: false } },
  family: { profile: "family", needs: { party_size: 3, children: 1, step_free: false, wheelchair: false } },
  wheelchair: { profile: "reduced_mobility", needs: { party_size: 2, children: 0, step_free: true, wheelchair: true } },
  both: { profile: "family_mobility", needs: { party_size: 3, children: 1, step_free: true, wheelchair: true } },
};

export type MapNodeKind = "gate" | "lift" | "stairs" | "train" | "cafe" | "exit" | "hall" | "junction" | "point";
export interface MapNode {
  id: string; kind: MapNodeKind; terminal: string; concourse: string; floor: string; x: number; y: number;
  landmark: I18nText; pickup: boolean;
}
export interface MapEdge {
  id: string; from: string; to: string; mode: AccessibleMode; lift: boolean; steps: number; accessible: boolean;
  queue_id?: string | null; cross_floor: boolean;
}
export interface MapShape { terminal: string; concourse: string; floor: string; points: [number, number][] }
export interface MapLayout {
  airport: string; version: string; floors: string[]; kiosk_node: string | null;
  nodes: MapNode[]; edges: MapEdge[]; shapes: MapShape[];
}
export interface MapPosition {
  x: number; y: number; floor: string; concourse: string; terminal: string;
  node_id: string | null; edge_id: string | null; observed_at: string; near_node?: string; near_distance?: number;
}
export interface RouteProgress {
  total_steps: number; progress: number | null; on_route: boolean | null; reached_steps: number; next_step: number | null;
}
export interface MapLive {
  as_of: string;
  closures: { edge_id: string; kind: "lift" | "passage"; observed_at: string | null }[];
  queues: { edge_id: string; queue_id: string; lower_min: number; upper_min: number | null; observed_at: string }[];
  kiosk_node: string | null;
  position: MapPosition | null;
  gate: { node_id: string; label: string; state: ObservationState } | null;
  route: { route_id: string; revision: number; step_nodes: (string | null)[]; path: string[]; progress: RouteProgress | null } | null;
  assistance: { state: CaseState; pickup_node: string | null } | null;
}
export type PositionInput = { node_id: string } | { x: number; y: number; floor: string };

export interface VoiceStatus {
  provider: string; configured: boolean; stt: boolean; tts: boolean; reason: string | null;
  model: string; languages: string[]; tts_languages: string[]; max_seconds: number;
}

export interface SystemStatus {
  mode: string; simulated: boolean; internet: boolean; offline_forced: boolean; core: string;
  understanding: { last_used: "template" | "ollama" | "openrouter"; last_used_at: number | null; providers: { name: string; cooling_down: boolean }[]; template: boolean };
  configured: { voice: boolean; openrouter: boolean };
  keys: { deepgram: string; openrouter: string };
  setup: string[];
  unavailable_online_features: string[];
}
export interface TrafficAircraft {
  icao24: string; callsign: string | null; lat: number; lon: number; altitude_m: number | null; on_ground: boolean;
  speed_ms: number | null; heading: number | null; km: number; eta_min?: number | null;
}
export interface TrafficArrival { callsign: string | null; eta_min: number; altitude_m: number | null }
export interface Traffic {
  status: "live" | "cached" | "offline" | "unavailable"; age_s: number | null; aircraft: TrafficAircraft[];
  source: string; centre: [number, number]; airborne?: number; arrivals?: TrafficArrival[]; sources?: string[];
}

export type AnswerSource = "model" | "template" | "fallback" | "web";
export interface Citation { title: string; url: string }
export interface TraceAttempt { by: "model" | "template" | "web"; text: string; ok: boolean; violations: { kind: string; detail: string }[] }
export interface AskTrace {
  question: string; kind: string; understood_by: string;
  facts: { key: string; state: PledgeState; text: string; source?: string | null; observed_at?: string | null }[];
  verdict: Verdict | null; attempts: TraceAttempt[]; final: { source: AnswerSource; text: string };
}
export interface AskReply { answer: string; kind: string; source: AnswerSource; refs: string[]; trace: AskTrace; citations?: Citation[] }
export interface DraftCheck { accepted: boolean; violations: { kind: string; detail: string }[] }

export interface BuggyPlan {
  id: string; case_id: string; state: CaseState; destination: string; seats: number; seats_used: number;
  riders: { case_id: string; people: number; basis: string; run: number }[];
  leftover: { case_id: string; people: number; basis: string }[];
}

export interface Scoreboard {
  total: number; passed: number; unverified_promises_shown: number; invented_facts_shown: number;
  dispatch_human_gated_pct: number; event_loop_lag_ms_p99: number;
  cases: { name: string; category: string; ok: boolean }[];
}
