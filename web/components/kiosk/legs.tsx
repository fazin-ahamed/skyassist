"use client";

import { RiArrowRightLine } from "@remixicon/react";

/** Two flight codes read left to right: the one you arrive on, then the one you leave on. */
export function Legs({ inbound, outbound }: { inbound: string; outbound: string }) {
  return (
    <bdi dir="ltr" className="inline-flex items-center gap-2 tabular-nums">
      <span>{inbound || "?"}</span>
      <RiArrowRightLine className="size-5 shrink-0 text-ink-soft" aria-hidden />
      <span>{outbound || "?"}</span>
    </bdi>
  );
}
