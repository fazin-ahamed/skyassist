"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { RiFocus3Line, RiFullscreenLine, RiPauseLine, RiPlayLine, RiRouteLine } from "@remixicon/react";
import { useReducedMotion } from "motion/react";
import {
  PLAN_H, PLAN_W, PLANS, SPINE_Y, pathLength, planOfNode, routeOnPlan, type Concourse, type Plan, type PlanId, type Service, type ServiceKind,
} from "@/components/canvas/dubai-plan";
import { useI18n } from "@/lib/i18n";
import type { MapLayout, MapLive, MapPosition, PositionInput, ViewModel } from "@/lib/types";
import { cx } from "@/utils/cx";

export interface DubaiMapProps {
  layout: MapLayout | null;
  live: MapLive | null;
  position: MapPosition | null;
  view: ViewModel | null;
  onPlace: (input: PositionInput) => void;
  walk: { playing: boolean; can: boolean; toggle: () => void };
}

const GLYPH: Record<ServiceKind, string> = { toilets: "WC", prayer: "P", medical: "+", info: "i", lift: "↕", lounge: "L", train: "T", food: "F", travelator: "⇄" };
const SERVICE_KEYS: Record<ServiceKind, string> = {
  toilets: "kk.svc.toilets", prayer: "kk.svc.prayer", medical: "kk.svc.medical", info: "kk.svc.info", lift: "kk.svc.lift",
  lounge: "kk.svc.lounge", train: "kk.svc.train", food: "kk.svc.food", travelator: "kk.svc.travelator",
};
const ASPECT = PLAN_H / PLAN_W;

/** Eases the SVG viewBox to a new rectangle, so zooming to the route glides instead of jumping. */
function useViewBox(target: [number, number, number, number]): string {
  const reduce = useReducedMotion();
  const [box, setBox] = useState(target);
  const from = useRef(target);
  const key = target.join(",");
  useEffect(() => {
    if (reduce) { from.current = target; return; }
    const origin = from.current;
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 650);
      const e = 1 - Math.pow(1 - t, 3);
      const next = origin.map((v, i) => v + (target[i] - v) * e) as [number, number, number, number];
      from.current = next;
      setBox(next);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reduce]);
  return (reduce ? target : box).map((n) => n.toFixed(1)).join(" ");
}

function ServiceDot({ s, label }: { s: Service; label: string }) {
  return (
    <g transform={`translate(${s.x} ${s.y})`} aria-label={label}>
      <title>{label}</title>
      <circle r={12} fill="var(--surface)" stroke={s.kind === "medical" ? "var(--risk)" : "var(--blue)"} strokeWidth={1.6} />
      <text textAnchor="middle" dy="0.34em" fontSize={s.kind === "toilets" ? 9 : 12} fontWeight={700}
        fill={s.kind === "medical" ? "var(--risk)" : "var(--blue)"} style={{ fontFamily: "var(--font-barlow), system-ui" }}>{GLYPH[s.kind]}</text>
    </g>
  );
}

function Concourse({ plan, route, pos, dest, closures, onPlace, interactive, label }: {
  plan: Plan; route: [number, number][]; pos: [number, number] | null; dest: string | null;
  closures: [number, number][]; onPlace: (id: string) => void; interactive: boolean; label: (id: string) => string;
}) {
  const { t } = useI18n();
  const len = useMemo(() => Math.ceil(pathLength(route)) + 4, [route]);
  const poly = route.map((p) => p.join(",")).join(" ");
  const nodeIds = Object.keys(plan.nodes).filter((id) => !/^[abc]\d{1,2}$/.test(id) || interactive);
  return (
    <g>
      <path d={plan.hull} fill="var(--surface-2)" stroke="var(--hair)" strokeWidth={2} />
      <rect x={30} y={SPINE_Y - 26} width={PLAN_W - 66} height={52} rx={26} fill="var(--blue-soft)" opacity={0.75} />
      <line x1={46} y1={SPINE_Y} x2={PLAN_W - 52} y2={SPINE_Y} stroke="var(--blue)" strokeOpacity={0.22} strokeWidth={2} strokeDasharray="3 9" />
      {plan.atrium && (
        <g>
          <circle cx={plan.atrium.x} cy={plan.atrium.y} r={plan.atrium.r + 8} fill="var(--surface)" stroke="var(--hair)" strokeWidth={2} />
          <circle cx={plan.atrium.x} cy={plan.atrium.y} r={plan.atrium.r - 6} fill="var(--surface-3)" />
        </g>
      )}
      {plan.shops.map((s) => (
        <g key={s.id}>
          <rect x={s.x} y={s.y} width={s.w} height={s.h} rx={9} fill="oklch(0.93 0.035 25)" stroke="oklch(0.84 0.05 25)" strokeWidth={1.2} />
          <text x={s.x + s.w / 2} y={s.y + s.h / 2} textAnchor="middle" dy="0.35em" fontSize={14} fontWeight={600} fill="oklch(0.45 0.08 25)">{s.id}</text>
        </g>
      ))}
      {plan.gates.map((g) => {
        const isDest = dest === g.id;
        return (
          <g key={g.id}>
            <rect x={g.x} y={g.y} width={g.w} height={g.h} rx={6}
              fill={isDest ? "var(--plate)" : "var(--surface)"} stroke={isDest ? "var(--plate-ink)" : "var(--blue)"} strokeWidth={isDest ? 2.4 : 1.2} />
            <text x={g.cx} y={g.y + g.h / 2} textAnchor="middle" dy="0.36em" fontSize={isDest ? 14 : 12.5} fontWeight={isDest ? 800 : 600}
              fill={isDest ? "var(--plate-ink)" : "var(--ink-2)"}>{g.id.slice(1)}</text>
          </g>
        );
      })}
      {plan.services.map((s, i) => <ServiceDot key={i} s={s} label={t(SERVICE_KEYS[s.kind])} />)}
      <text x={60} y={SPINE_Y - 34} fontSize={13} fontWeight={600} fill="var(--ink-3)">{plan.ends.west}</text>

      {closures.map(([x, y], i) => (
        <g key={i} transform={`translate(${x} ${y})`}>
          <circle r={11} fill="var(--risk)" /><path d="M-4 -4 L4 4 M4 -4 L-4 4" stroke="#fff" strokeWidth={2.4} strokeLinecap="round" />
        </g>
      ))}

      {route.length > 1 && (
        <g fill="none" strokeLinecap="round" strokeLinejoin="round">
          <polyline key={poly} points={poly} stroke="var(--blue)" strokeOpacity={0.2} strokeWidth={9} />
          <polyline key={`d${poly}`} points={poly} stroke="var(--blue)" strokeWidth={5} className="kk-draw" style={{ "--len": len } as React.CSSProperties} />
          <polyline points={poly} stroke="#fff" strokeWidth={2} className="kk-flow" />
        </g>
      )}

      {interactive && nodeIds.map((id) => {
        const [x, y] = plan.nodes[id];
        return (
          <g key={id} transform={`translate(${x} ${y})`} role="button" tabIndex={0} aria-label={label(id)} className="cursor-pointer outline-none"
            onClick={() => onPlace(id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPlace(id); } }}>
            <circle r={16} fill="transparent" />
            {!/^[abc][0-9]{1,2}$/.test(id) &&<circle r={5} fill="var(--surface)" stroke="var(--blue)" strokeWidth={2} />}
          </g>
        );
      })}

      {pos && (
        <g transform={`translate(${pos[0]} ${pos[1]})`} style={{ transition: "transform 0.5s cubic-bezier(0.22,1,0.36,1)" }}>
          <circle r={14} fill="var(--blue)" opacity={0.28} className="kk-ping" />
          <circle r={9} fill="var(--blue)" stroke="#fff" strokeWidth={3} />
          <g transform="translate(0 -26)">
            <rect x={-20} y={-12} width={40} height={22} rx={11} fill="var(--ink)" />
            <text textAnchor="middle" dy="0.1em" y={0} fontSize={12.5} fontWeight={700} fill="var(--surface)">{t("kk.map.you")}</text>
          </g>
        </g>
      )}
    </g>
  );
}

const OVERVIEW_CONCOURSES = [["C", 165], ["B", 500], ["A", 835]] as const;

/** Terminal 3 transfer modes from https://dubaiairports.ae/information/transfers. */
function Overview({ here, gate, gateConcourse }: { here: Concourse | null; gate: string | null; gateConcourse: Concourse | null }) {
  const { t } = useI18n();
  return (
    <g>
      <path d="M 265 165 H 400" stroke="var(--ink-3)" strokeWidth={3} strokeDasharray="3 9" strokeLinecap="round" />
      <path d="M 600 165 H 735" stroke="var(--ink-3)" strokeWidth={3} strokeLinecap="round" />
      <g textAnchor="middle" fontSize={22} fontWeight={600} fill="var(--ink-2)">
        <text x={332.5} y={119}>{t("maps.walk")}</text>
        <text x={667.5} y={119}>{t("kk.map.train_a")}</text>
      </g>
      {OVERVIEW_CONCOURSES.map(([concourse, x]) => {
        const isHere = here === concourse;
        const isGate = gateConcourse === concourse;
        return (
          <g key={concourse} transform={`translate(${x} 165)`} textAnchor="middle">
            <rect x={-100} y={-35} width={200} height={70} rx={24} fill={isHere || isGate ? "var(--blue-soft)" : "var(--surface)"}
              stroke={isHere || isGate ? "var(--blue)" : "var(--hair)"} strokeWidth={2} />
            <text dy="0.35em" fontSize={26} fontWeight={700} fill="var(--ink)">{t("maps.gates", { concourse })}</text>
            {isHere && (
              <g transform="translate(0 -62)">
                <circle r={15} fill="var(--blue)" opacity={0.28} className="kk-ping" />
                <circle r={10} fill="var(--blue)" stroke="var(--surface)" strokeWidth={3} />
              </g>
            )}
            {isGate && (
              <g transform="translate(0 65)">
                <rect x={-39} y={-18} width={78} height={36} rx={9} fill="var(--plate)" stroke="var(--plate-ink)" strokeWidth={2} />
                <text dy="0.35em" fontSize={22} fontWeight={800} fill="var(--plate-ink)">{gate}</text>
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}

export function DubaiMap({ layout, live, position, view, onPlace, walk }: DubaiMapProps) {
  const { t, r } = useI18n();
  const nodes = useMemo(() => new Map((layout?.nodes ?? []).map((n) => [n.id, n])), [layout]);
  const gateValue = view?.gate.state === "fresh" || view?.gate.state === "stale" ? (view.gate.value?.toUpperCase() ?? null) : null;
  const gateNode = live?.gate?.node_id ?? null;
  const dest = gateValue ?? (gateNode ? gateNode.toUpperCase() : null);
  const here = position ? planOfNode(position.node_id ?? position.near_node ?? "") : null;
  const destPlan: Concourse | null = dest && /^[ABC]\d/.test(dest) ? (dest[0] as Concourse) : null;

  const [chosen, setChosen] = useState<PlanId | null>(null);
  const [follow, setFollow] = useState(true);
  const auto: PlanId = here ?? destPlan ?? "B";
  const planId: PlanId = chosen ?? auto;
  // Follow concourse changes while keeping an explicitly selected terminal overview open.
  const lastAuto = useRef(auto);
  useEffect(() => {
    if (lastAuto.current !== auto) {
      lastAuto.current = auto;
      setChosen((id) => id === "T3" ? id : null);
    }
  }, [auto]);

  const path = live?.route?.path;
  const plan = planId === "T3" ? null : PLANS[planId];
  const route = useMemo(() => (plan && path ? routeOnPlan(plan, path) : []), [plan, path]);

  const pos = useMemo<[number, number] | null>(() => {
    if (!plan || !position) return null;
    const id = position.node_id ?? ((position.near_distance ?? 99) <= 8 ? position.near_node : undefined);
    if (id && plan.nodes[id]) return plan.nodes[id];
    if (position.edge_id && layout) {
      const edge = layout.edges.find((e) => e.id === position.edge_id);
      const a = edge && nodes.get(edge.from), b = edge && nodes.get(edge.to);
      if (a && b && plan.nodes[a.id] && plan.nodes[b.id]) {
        const dx = b.x - a.x, dy = b.y - a.y, span = dx * dx + dy * dy || 1;
        const tt = Math.max(0, Math.min(1, ((position.x - a.x) * dx + (position.y - a.y) * dy) / span));
        const [ax, ay] = plan.nodes[a.id], [bx, by] = plan.nodes[b.id];
        return [ax + (bx - ax) * tt, ay + (by - ay) * tt];
      }
    }
    return null;
  }, [plan, position, layout, nodes]);

  const closures = useMemo<[number, number][]>(() => {
    if (!plan || !layout || !live) return [];
    return live.closures.flatMap((c) => {
      const e = layout.edges.find((x) => x.id === c.edge_id);
      const a = e && plan.nodes[e.from], b = e && plan.nodes[e.to];
      return a && b ? [[(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] as [number, number]] : [];
    });
  }, [plan, layout, live]);

  // zoom to what matters: the route and the person, with a margin
  const target = useMemo<[number, number, number, number]>(() => {
    const whole: [number, number, number, number] = [0, 0, PLAN_W, PLAN_H];
    if (!plan || !follow) return whole;
    const pts: [number, number][] = [...route];
    if (pos) pts.push(pos);
    if (route.length < 2) return whole;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const w = Math.max(460, Math.max(...xs) - Math.min(...xs) + 220);
    const h = w * ASPECT;
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
    const x = Math.max(0, Math.min(PLAN_W - w, cx - w / 2));
    const y = Math.max(0, Math.min(PLAN_H - h, SPINE_Y - h / 2 + (Math.max(...ys) > SPINE_Y + 40 ? 30 : 0)));
    return w >= PLAN_W ? whole : [x, y, w, h];
  }, [plan, follow, route, pos]);
  const viewBox = useViewBox(planId === "T3" ? [0, 0, PLAN_W, PLAN_H] : target);

  const labelOf = (id: string) => { const n = nodes.get(id); return n ? r(n.landmark) : id; };
  const tabs: { id: PlanId; label: string }[] = [
    { id: "A", label: t("kk.map.concourse", { c: "A" }) }, { id: "B", label: t("kk.map.concourse", { c: "B" }) },
    { id: "C", label: t("kk.map.concourse", { c: "C" }) }, { id: "T3", label: t("kk.map.overview") },
  ];
  const queue = live?.queues[0];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="tablist" aria-label={t("kk.card.map")} className="flex max-w-full overflow-x-auto rounded-full bg-[var(--surface-3)] p-1 [scrollbar-width:none]">
          {tabs.map((tab) => (
            <button key={tab.id} role="tab" aria-selected={planId === tab.id} onClick={() => setChosen(tab.id)}
              className={cx("kk-press min-h-10 shrink-0 rounded-full px-3 text-[0.9375rem] font-semibold outline-none sm:px-4 focus-visible:ring-2 focus-visible:ring-[var(--blue)]",
                planId === tab.id ? "bg-[var(--surface)] text-[var(--ink)] shadow-sm" : "text-[var(--ink-3)] hover:text-[var(--ink)]")}>
              {tab.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {walk.can && (
            <button type="button" onClick={walk.toggle} aria-pressed={walk.playing}
              className="kk-press flex min-h-10 items-center gap-2 rounded-full bg-[var(--blue)] px-4 text-[0.9375rem] font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-[var(--blue)] focus-visible:ring-offset-2">
              {walk.playing ? <RiPauseLine className="size-5" aria-hidden /> : <RiPlayLine className="size-5" aria-hidden />}
              {walk.playing ? t("map.walk_pause") : t("map.walk")}
            </button>
          )}
          {plan && (
            <button type="button" aria-pressed={follow} aria-label={follow ? t("kk.map.fit") : t("kk.map.follow")} title={follow ? t("kk.map.fit") : t("kk.map.follow")}
              onClick={() => setFollow((v) => !v)}
              className="kk-press flex size-10 items-center justify-center rounded-full bg-[var(--surface-3)] text-[var(--ink-2)] outline-none hover:text-[var(--ink)] focus-visible:ring-2 focus-visible:ring-[var(--blue)]">
              {follow ? <RiFullscreenLine className="size-5" aria-hidden /> : <RiFocus3Line className="size-5" aria-hidden />}
            </button>
          )}
        </div>
      </div>

      <div className="relative overflow-hidden rounded-2xl bg-[var(--surface-2)] ring-1 ring-[var(--hair)]">
        <svg viewBox={viewBox} className="block h-auto w-full touch-manipulation select-none" role="img" aria-label={plan ? t("kk.map.alt", { c: planId }) : t("maps.terminal_3")}>
          {plan ? (
            <Concourse plan={plan} route={route} pos={pos} dest={dest} closures={closures} interactive
              onPlace={(id) => onPlace({ node_id: id })} label={labelOf} />
          ) : (
            <Overview here={here} gate={dest} gateConcourse={destPlan} />
          )}
        </svg>
        {plan && queue && (
          <p className="absolute start-3 bottom-3 rounded-full bg-[var(--wait-soft)] px-3 py-1 text-sm font-semibold text-[var(--wait)]">
            {queue.upper_min != null ? t("map.queue", { lower: queue.lower_min, upper: queue.upper_min }) : t("map.queue_open", { lower: queue.lower_min })}
          </p>
        )}
      </div>

      <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm text-[var(--ink-2)]" aria-label={t("kk.map.legend")}>
        <li className="flex items-center gap-2"><span className="size-3 rounded-full bg-[var(--blue)]" />{t("map.legend.you")}</li>
        <li className="flex items-center gap-2"><span className="h-3 w-5 rounded-sm bg-[var(--plate)] ring-1 ring-[var(--plate-ink)]" />{t("kk.map.gate")}</li>
        {plan && <>
          <li className="flex items-center gap-2"><span className="h-3 w-5 rounded-sm bg-[oklch(0.93_0.035_25)] ring-1 ring-[oklch(0.84_0.05_25)]" />{t("kk.map.shops")}</li>
          <li className="flex items-center gap-2"><span className="size-3 rounded-full bg-[var(--risk)]" />{t("map.legend.closed")}</li>
          {route.length > 1 && <li className="flex items-center gap-2"><RiRouteLine className="size-4 text-[var(--blue)]" aria-hidden />{t("maps.demo_route")}</li>}
        </>}
      </ul>
    </div>
  );
}
