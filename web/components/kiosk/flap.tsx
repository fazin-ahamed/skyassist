"use client";

import type { CSSProperties } from "react";
import { cx } from "@/utils/cx";

/**
 * Text that settles character by character, like a departures board, whenever the value changes.
 * Keyed on the value, so a new gate flips in and an unchanged one stays still. Reduced motion shows it at once.
 */
export function FlapText({ value, className }: { value: string; className?: string }) {
  return (
    <span key={value} dir="ltr" className={cx("flap", className)} aria-label={value}>
      {[...value].map((ch, i) => (
        <span key={i} aria-hidden style={{ "--i": i } as CSSProperties}>{ch === " " ? " " : ch}</span>
      ))}
    </span>
  );
}
