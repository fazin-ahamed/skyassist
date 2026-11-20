"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SystemStatus } from "@/lib/types";

/**
 * What the kiosk can reach. `reachable` is the local SkyAssist service; `internet` is the outside world.
 * They fail separately: with no internet the kiosk still decides everything locally, with no service it cannot.
 */
export function useHealth() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [reachable, setReachable] = useState(true);

  const load = useCallback(async () => {
    try {
      setStatus(await api.systemStatus());
      setReachable(true);
    } catch {
      setReachable(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 8000);
    const refresh = () => void load();
    window.addEventListener("online", refresh);
    window.addEventListener("offline", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener("online", refresh);
      window.removeEventListener("offline", refresh);
    };
  }, [load]);

  return { status, reachable, internet: status ? status.internet : true, simulated: status?.simulated ?? false };
}
