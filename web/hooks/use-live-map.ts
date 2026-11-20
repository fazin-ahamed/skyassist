"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MapLayout, MapLive, MapPosition, PositionInput } from "@/lib/types";

const POLL_MS = 1200;
/** After a local move, keep showing it this long so a slower poll cannot snap the pin back. */
const HOLD_MS = 2200;

export function nodePosition(layout: MapLayout, id: string): MapPosition | null {
  const n = layout.nodes.find((x) => x.id === id);
  return n ? { x: n.x, y: n.y, floor: n.floor, concourse: n.concourse, terminal: n.terminal, node_id: n.id, edge_id: null, observed_at: new Date().toISOString(), near_node: n.id, near_distance: 0 } : null;
}

/**
 * The map's data: the static layout once, the live layer on a short poll, and the passenger's position.
 * The server owns position. A local move shows immediately, is sent on (throttled), and the server's snapped answer replaces it.
 */
export function useLiveMap(journeyId: string | null) {
  const [layout, setLayout] = useState<MapLayout | null>(null);
  const [live, setLive] = useState<MapLive | null>(null);
  const [offline, setOffline] = useState(false);
  const [local, setLocal] = useState<{ pos: MapPosition; input: PositionInput; stamp: number } | null>(null);
  const stamp = useRef(0);
  const sending = useRef(false);
  const queued = useRef<PositionInput | null>(null);
  const journey = useRef(journeyId);
  useEffect(() => { journey.current = journeyId; });

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const next = await api.mapLayout();
        if (!stop) setLayout(next);
      } catch {
        if (!stop) timer = setTimeout(load, 2000);
      }
    };
    load();
    return () => { stop = true; clearTimeout(timer); };
  }, []);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      if (document.visibilityState !== "hidden") {
        try {
          const next = await api.mapLive(journeyId);
          if (stop) return;
          setOffline(false);
          setLive(next);
        } catch {
          if (!stop) setOffline(true);
        }
      }
      if (!stop) timer = setTimeout(run, POLL_MS);
    };
    run();
    return () => { stop = true; clearTimeout(timer); };
  }, [journeyId]);

  const flush = useCallback(async () => {
    const id = journey.current;
    if (!id || sending.current || !queued.current) return;
    sending.current = true;
    const input = queued.current;
    queued.current = null;
    try {
      const next = await api.setPosition(id, input);
      setLive(next);
      if (!queued.current && next.position) setLocal((cur) => (cur ? { ...cur, pos: next.position! } : cur));
    } catch {
      /* the next poll shows what the server holds */
    } finally {
      sending.current = false;
      if (queued.current) setTimeout(flush, 120);
    }
  }, []);

  const place = useCallback((input: PositionInput, optimistic?: MapPosition | null) => {
    if (!layout) return;
    const pos = optimistic ?? ("node_id" in input ? nodePosition(layout, input.node_id)
      : { x: input.x, y: input.y, floor: input.floor, concourse: "", terminal: "", node_id: null, edge_id: null, observed_at: new Date().toISOString() });
    if (!pos) return;
    const mine = ++stamp.current;
    setLocal({ pos, input, stamp: mine });
    queued.current = input;
    flush();
    // With a journey the server answers for position again soon; before one exists the local pin is all there is.
    if (journey.current) window.setTimeout(() => setLocal((cur) => (cur && cur.stamp === mine ? null : cur)), HOLD_MS);
  }, [layout, flush]);

  // A position chosen before the journey existed goes to the server as soon as it can.
  useEffect(() => {
    if (journeyId && local) { queued.current = local.input; flush(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [journeyId]);

  const position = local?.pos ?? live?.position ?? null;

  return { layout, live, position, place, offline };
}
