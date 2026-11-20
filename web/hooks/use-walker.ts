"use client";

import { useEffect, useRef, useState } from "react";
import type { MapLayout, MapLive, MapPosition, PositionInput } from "@/lib/types";

const BASE_TICK_MS = 520;
const SUBSTEPS = 5;
export type WalkSpeed = 1 | 2 | 4;

/**
 * Moves the passenger along the current route, a little at a time, so the map can show the walk as it happens.
 * It only ever calls `place`, the same call a dragged pin makes, so the server stays the one source of position.
 * speed: 1, 2 or 4 times (tick 520, 260, 130 ms). arrived is true when the position sits on the last route node.
 */
export function useWalker(
  layout: MapLayout | null, live: MapLive | null, position: MapPosition | null,
  place: (input: PositionInput) => void, speed: WalkSpeed = 1,
) {
  const [playing, setPlaying] = useState(false);
  const leg = useRef({ index: 0, step: 0 });
  const latest = useRef({ layout, live, position, place });
  useEffect(() => { latest.current = { layout, live, position, place }; });
  const path = live?.route?.path;
  const canWalk = !!path && path.length > 1 && !!layout;
  const arrived = !!path && path.length > 1 && !!position?.node_id && position.node_id === path[path.length - 1];

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      const { layout: lay, live: lv, position: pos, place: put } = latest.current;
      const route = lv?.route?.path;
      if (!lay || !route || route.length < 2) { setPlaying(false); return; }
      const nodes = new Map(lay.nodes.map((n) => [n.id, n]));
      const cur = leg.current;
      if (cur.step === 0) {
        // Resume from wherever the pin is: the last route node it sits on, or the start.
        const at = pos?.node_id ? route.indexOf(pos.node_id) : -1;
        if (at >= 0) cur.index = at;
      }
      const a = nodes.get(route[cur.index]);
      const b = nodes.get(route[cur.index + 1]);
      if (!a || !b) { setPlaying(false); cur.index = 0; cur.step = 0; return; }
      const sameFloor = a.floor === b.floor && !(lay.edges.find((e) => (e.from === a.id && e.to === b.id) || (e.from === b.id && e.to === a.id))?.cross_floor);
      cur.step += 1;
      if (!sameFloor || cur.step >= SUBSTEPS) {
        put({ node_id: b.id });
        cur.index += 1;
        cur.step = 0;
        if (cur.index >= route.length - 1) { setPlaying(false); cur.index = 0; }
      } else {
        const t = cur.step / SUBSTEPS;
        put({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, floor: a.floor });
      }
    }, BASE_TICK_MS / speed);
    return () => clearInterval(timer);
  }, [playing, speed]);

  return {
    playing: playing && canWalk && !arrived,
    canWalk,
    arrived,
    speed,
    toggle: () => { if (canWalk) { leg.current.step = 0; setPlaying((p) => !p); } },
    stop: () => setPlaying(false),
  };
}
