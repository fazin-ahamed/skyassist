"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ViewModel } from "@/lib/types";

interface Snapshot {
  id: string;
  view: ViewModel | null;
  status: "ready" | "down" | "expired";
}

export function useJourney(journeyId: string | null) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const revision = useRef<number | undefined>(undefined);
  const nudge = useCallback(() => { revision.current = undefined; }, []);

  useEffect(() => {
    if (!journeyId) return;
    revision.current = undefined;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    let misses = 0;
    const tick = async () => {
      try {
        const res = await api.poll(journeyId, revision.current);
        if (stop) return;
        misses = 0;
        revision.current = res.revision;
        setSnapshot((prev) => ({
          id: journeyId,
          view: res.revision > 0 ? res.view ?? (prev?.id === journeyId ? prev.view : null) : null,
          status: "ready",
        }));
      } catch (error) {
        if (stop) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          setSnapshot({ id: journeyId, view: null, status: "expired" });
          return;
        }
        // one dropped poll is a blip; two in a row is a lost connection
        if (++misses < 2) { timer = setTimeout(tick, 600); return; }
        revision.current = undefined;
        setSnapshot((prev) => ({ id: journeyId, view: prev?.id === journeyId ? prev.view : null, status: "down" }));
      }
      if (!stop) timer = setTimeout(tick, 700);
    };

    void tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [journeyId]);

  const current = snapshot?.id === journeyId ? snapshot : null;
  return {
    view: current?.view ?? null,
    down: current?.status === "down",
    expired: current?.status === "expired",
    nudge,
  };
}
