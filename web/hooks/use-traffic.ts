"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Traffic } from "@/lib/types";

// One poller for the whole page: every component that asks for traffic shares it, and it runs once a minute.
const INTERVAL_MS = 60_000;
let current: Traffic | null = null;
const listeners = new Set<(t: Traffic) => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function poll() {
  api.traffic()
    .then((t) => {
      current = t;
      listeners.forEach((fn) => fn(t));
    })
    .catch(() => {});
}

function subscribe(fn: (t: Traffic) => void): () => void {
  listeners.add(fn);
  if (current) fn(current);
  if (!timer) {
    poll();
    timer = setInterval(poll, INTERVAL_MS);
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function useTraffic(): { traffic: Traffic | null } {
  const [traffic, setTraffic] = useState<Traffic | null>(current);
  useEffect(() => subscribe(setTraffic), []);
  return { traffic };
}
