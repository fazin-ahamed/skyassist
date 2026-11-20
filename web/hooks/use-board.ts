"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { FlightBoard } from "@/lib/types";

export function useBoard(direction: "departures" | "arrivals") {
  const [board, setBoard] = useState<FlightBoard | null>(null);
  const [lastDirection, setLastDirection] = useState<typeof direction>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const next = await api.flights(direction);
        if (!stop) { setLastDirection(direction); setBoard(next); setFailed(false); }
      } catch {
        if (!stop) { setLastDirection(direction); setBoard(null); setFailed(true); }
      }
    };
    load();
    const timer = setInterval(load, 8000);
    return () => { stop = true; clearInterval(timer); };
  }, [direction, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { board: direction === lastDirection ? board : null, failed: direction === lastDirection && failed, retry };
}
