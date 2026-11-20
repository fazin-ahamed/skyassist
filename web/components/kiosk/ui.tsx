"use client";

import type { ComponentType, ElementType, ReactNode } from "react";
import {
  RiAlertLine, RiCheckLine, RiInformationLine, RiShieldCheckLine, RiStairsLine, RiTimeLine, RiTrainLine,
  RiUser2Line, RiWalkLine, RiWheelchairLine,
} from "@remixicon/react";
import { cx } from "@/utils/cx";
import type { AccessibleMode } from "@/lib/types";

export type Tone = "lime" | "orange" | "rose" | "blue" | "accent" | "neutral";
export type Icon = ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;

/** Status colours. They mean connection status and nothing else. */
export const TONE_CLASS: Record<Tone, string> = {
  lime: "bg-status-lime-background text-status-lime-text",
  orange: "bg-status-orange-background text-status-orange-text",
  rose: "bg-status-rose-background text-status-rose-text",
  blue: "bg-status-blue-background text-status-blue-text",
  accent: "bg-accent-50 text-accent-800",
  neutral: "bg-tag-wash text-ink",
};

export const STATUS_TONE: Record<string, { tone: Tone; icon: Icon }> = {
  "Time available": { tone: "lime", icon: RiCheckLine },
  "Tight connection": { tone: "orange", icon: RiTimeLine },
  "Transfer unlikely before cutoff": { tone: "rose", icon: RiAlertLine },
  "May miss boarding": { tone: "rose", icon: RiAlertLine },
  "Boarding cutoff passed": { tone: "rose", icon: RiAlertLine },
  "Timing unverified": { tone: "blue", icon: RiInformationLine },
};

export const MODE_ICON: Record<AccessibleMode, Icon> = {
  walk: RiWalkLine, step_free: RiWheelchairLine, stairs: RiStairsLine, lift: RiStairsLine,
  transit: RiTrainLine, checkpoint: RiShieldCheckLine, staff_handoff: RiUser2Line,
};

/** A paper tag. Stays white in dark mode; its own tokens keep BoardUI controls light inside it. */
export function Tag({ as: As = "section", className, children, ...rest }: { as?: ElementType; className?: string; children: ReactNode } & Record<string, unknown>) {
  return <As className={cx("paper tag", className)} {...rest}>{children}</As>;
}

/** Perforation with a notch at each edge. Place it as a direct child of a Tag, between padded sections. */
export function Tear({ className }: { className?: string }) {
  return <div role="presentation" aria-hidden className={cx("tear", className)} />;
}

/** The label above a numeral or field on a tag. */
export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("text-body-semibold text-ink-soft", className)}>{children}</p>;
}

/** Plain content block on a tag, used where a full Tag would nest tags. */
export function Card({ className, children, id }: { className?: string; children: ReactNode; id?: string }) {
  return <div id={id} className={cx("paper tag p-6", className)}>{children}</div>;
}

export function StatusPill({ tone, icon: Icon, children }: { tone: Tone; icon: Icon; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-md py-1 ps-2 pe-3 text-body-2-semibold", TONE_CLASS[tone])}>
      <Icon className="size-4 shrink-0" aria-hidden />
      {children}
    </span>
  );
}

/** A full-width note in a status tone. Used for offline and access problems. */
export function Notice({ tone, icon: Icon, children }: { tone: Tone; icon: Icon; children: ReactNode }) {
  return (
    <div role="status" className={cx("flex items-start gap-3 rounded-xl p-4 text-body-semibold", TONE_CLASS[tone])}>
      <Icon className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
