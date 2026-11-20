"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export function useIdle(enabled: boolean, warnAfter: number, endAfter: number, onEnd: () => void) {
  const [idle, setIdle] = useState(0);
  const last = useRef(0);
  const keep = useCallback(() => { last.current = Date.now(); setIdle(0); }, []);

  useEffect(() => {
    if (!enabled) return;
    last.current = Date.now();
    const seconds = () => Math.floor((Date.now() - last.current) / 1000);
    const check = () => {
      const elapsed = seconds();
      if (elapsed >= endAfter) {
        onEnd();
        return;
      }
      setIdle(elapsed);
    };
    const activity = () => {
      if (seconds() < warnAfter) keep();
      else check();
    };
    const events = ["pointerdown", "keydown", "wheel"] as const;
    events.forEach((event) => window.addEventListener(event, activity, { passive: true }));
    document.addEventListener("visibilitychange", check);
    const timer = window.setInterval(check, 1000);
    return () => {
      events.forEach((event) => window.removeEventListener(event, activity));
      document.removeEventListener("visibilitychange", check);
      window.clearInterval(timer);
    };
  }, [enabled, warnAfter, endAfter, keep, onEnd]);

  return { idle: enabled ? idle : 0, keep };
}
