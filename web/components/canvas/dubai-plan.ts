/**
 * Drawn plans of Dubai International (DXB) Terminal 3, redrawn from the public Dubai Airports guides:
 * Concourse B (gates B1 to B32 on two rows, duty free blocks A to N in the middle, services along one spine),
 * Concourse C (gates on the same pattern, reached from B on foot), and the Terminal 3 overview (Concourse A by train).
 * The concourse drawings use simplified geometry for the kiosk demonstration.
 *
 * Graph node ids map route and position information onto the simulated plan.
 */

export type Concourse = "A" | "B" | "C";
export type PlanId = Concourse | "T3";

export const PLAN_W = 1000;
export const PLAN_H = 330;
export const SPINE_Y = 165;
const GATE_W = 40;
const GATE_H = 24;
const TOP_Y = 66;
const BOTTOM_Y = 240;

export interface Gate { id: string; x: number; y: number; w: number; h: number; side: "top" | "bottom"; cx: number; door: [number, number]; spineX: number }
export interface Shop { id: string; x: number; y: number; w: number; h: number }
export type ServiceKind = "toilets" | "prayer" | "medical" | "info" | "lift" | "lounge" | "train" | "food" | "travelator";
export interface Service { kind: ServiceKind; x: number; y: number }

function gateRow(prefix: string, firstNumber: number, count: number, side: "top" | "bottom", x0: number, step: number): Gate[] {
  return Array.from({ length: count }, (_, i) => {
    const x = x0 + i * step;
    const y = side === "top" ? TOP_Y : BOTTOM_Y;
    const cx = x + GATE_W / 2;
    return {
      id: `${prefix}${firstNumber + i * 2}`, x, y, w: GATE_W, h: GATE_H, side, cx,
      door: [cx, side === "top" ? y + GATE_H : y] as [number, number], spineX: cx,
    };
  });
}

function shops(letters: string[], y: number, x0: number, step: number): Shop[] {
  return letters.map((id, i) => ({ id, x: x0 + i * step, y, w: step - 14, h: 34 }));
}

export interface Plan {
  id: PlanId;
  title: string;
  hull: string;
  gates: Gate[];
  shops: Shop[];
  services: Service[];
  atrium?: { x: number; y: number; r: number };
  /** Where each graph node sits on this plan. */
  nodes: Record<string, [number, number]>;
  /** The plan a node belongs to, for choosing which tab to show. */
  ends: { west: string; east: string };
}

const B_TOP = gateRow("B", 1, 16, "top", 70, 55);
const B_BOTTOM = gateRow("B", 2, 16, "bottom", 70, 55);
const C_TOP = gateRow("C", 1, 20, "top", 48, 45);
const C_BOTTOM = gateRow("C", 2, 20, "bottom", 48, 45);
const A_TOP = gateRow("A", 1, 12, "top", 60, 75);
const A_BOTTOM = gateRow("A", 2, 12, "bottom", 60, 75);
const gateAt = (gates: Gate[], id: string): Gate | undefined => gates.find((g) => g.id === id.toUpperCase());

export const PLANS: Record<Concourse, Plan> = {
  A: {
    id: "A", title: "A",
    hull: "M 44 54 H 900 Q 982 54 982 165 Q 982 276 900 276 H 44 Q 16 276 16 165 Q 16 54 44 54 Z",
    gates: [...A_TOP, ...A_BOTTOM],
    shops: shops(["A", "B", "C", "D"], 104, 150, 120),
    services: [
      { kind: "toilets", x: 100, y: SPINE_Y }, { kind: "prayer", x: 160, y: SPINE_Y }, { kind: "info", x: 360, y: SPINE_Y },
      { kind: "medical", x: 480, y: SPINE_Y }, { kind: "lounge", x: 780, y: SPINE_Y },
    ],
    nodes: {
      train_a: [60, SPINE_Y], a3: [gateAt(A_TOP, "A3")!.cx, TOP_Y + GATE_H / 2],
    },
    ends: { west: "The train to Terminal 3", east: "Gate A24" },
  },
  B: {
    id: "B", title: "B",
    hull: "M 52 54 H 906 Q 982 54 982 165 Q 982 276 906 276 H 52 Q 16 276 16 165 Q 16 54 52 54 Z",
    gates: [...B_TOP, ...B_BOTTOM],
    shops: [...shops(["A", "B", "C", "D", "E", "F", "G"], 104, 112, 112), ...shops(["H", "I", "J", "K", "L", "M", "N"], 192, 112, 112)],
    atrium: { x: 524, y: SPINE_Y, r: 30 },
    services: [
      { kind: "toilets", x: 112, y: SPINE_Y }, { kind: "prayer", x: 168, y: SPINE_Y }, { kind: "medical", x: 236, y: SPINE_Y },
      { kind: "lift", x: 336, y: SPINE_Y }, { kind: "info", x: 424, y: SPINE_Y }, { kind: "info", x: 624, y: SPINE_Y },
      { kind: "lift", x: 712, y: SPINE_Y }, { kind: "toilets", x: 788, y: SPINE_Y }, { kind: "prayer", x: 846, y: SPINE_Y },
      { kind: "lounge", x: 910, y: SPINE_Y },
    ],
    nodes: {
      train_b: [60, SPINE_Y], b_hall: [300, SPINE_Y], b_stairs: [380, 142], b_costa: [452, 124],
      b_walk: [470, SPINE_Y],
      b14: [gateAt(B_BOTTOM, "B14")!.cx, BOTTOM_Y + GATE_H / 2], b18: [gateAt(B_BOTTOM, "B18")!.cx, BOTTOM_Y + GATE_H / 2],
    },
    ends: { west: "Concourse C and the train", east: "Gate B32" },
  },
  C: {
    id: "C", title: "C",
    hull: "M 44 54 H 900 Q 982 54 982 165 Q 982 276 900 276 H 44 Q 16 276 16 165 Q 16 54 44 54 Z",
    gates: [...C_TOP, ...C_BOTTOM],
    shops: [...shops(["A", "B", "C", "D", "E"], 104, 130, 140), ...shops(["F", "G", "H", "J", "K"], 192, 130, 140)],
    services: [
      { kind: "toilets", x: 100, y: SPINE_Y }, { kind: "prayer", x: 160, y: SPINE_Y }, { kind: "info", x: 340, y: SPINE_Y },
      { kind: "medical", x: 440, y: SPINE_Y }, { kind: "toilets", x: 640, y: SPINE_Y }, { kind: "lounge", x: 760, y: SPINE_Y },
      { kind: "train", x: 920, y: SPINE_Y },
    ],
    nodes: {
      c22: [gateAt(C_BOTTOM, "C22")!.cx, BOTTOM_Y + GATE_H / 2], c12: [gateAt(C_BOTTOM, "C12")!.cx, BOTTOM_Y + GATE_H / 2],
      lift_c: [560, SPINE_Y], c_stairs: [612, 142], c_stairs_low: [612, 142], c_costa: [700, 124],
      c_walk: [660, SPINE_Y], c_hall_w: [740, SPINE_Y], c_hall: [820, SPINE_Y], train_c: [920, SPINE_Y],
    },
    ends: { west: "Gate C1", east: "The train and Concourse B" },
  },
};

export function planOfNode(id: string): Concourse | null {
  if (id in PLANS.A.nodes) return "A";
  if (id in PLANS.B.nodes) return "B";
  if (id in PLANS.C.nodes) return "C";
  return null;
}

/** The route as a list of points on one plan. Gates are entered from the spine, so the line turns rather than cuts across the shops. */
export function routeOnPlan(plan: Plan, path: string[]): [number, number][] {
  const out: [number, number][] = [];
  for (const id of path) {
    const at = plan.nodes[id];
    if (!at) continue;
    const gate = /^[abc]\d{1,2}$/.test(id);
    if (gate) {
      const last = out[out.length - 1];
      if (!last || Math.abs(last[0] - at[0]) > 1) out.push([at[0], SPINE_Y]);
      out.push([at[0], at[1] + (at[1] > SPINE_Y ? -GATE_H / 2 : GATE_H / 2)]);
    } else out.push(at);
  }
  return out;
}

export function pathLength(points: [number, number][]): number {
  return points.reduce((sum, p, i) => (i ? sum + Math.hypot(p[0] - points[i - 1][0], p[1] - points[i - 1][1]) : 0), 0);
}
