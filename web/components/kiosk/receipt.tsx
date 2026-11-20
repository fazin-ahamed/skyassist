"use client";

import { RiAlertLine, RiCheckLine, RiInformationLine, RiUser3Line } from "@remixicon/react";
import { ScrollFade, useScrollFade } from "@/components/foundations/scroll-fade/scroll-fade";
import type { LogEntry, LogKind } from "@/lib/agent-log";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/utils/cx";

const KIND_ICON = { info: RiInformationLine, good: RiCheckLine, warn: RiAlertLine, risk: RiAlertLine, you: RiUser3Line };
const KIND_TEXT: Record<LogKind, string> = {
  info: "text-accent-600", good: "text-ink", warn: "text-status-orange-text", risk: "text-status-rose-text", you: "text-accent-700",
};

function Entry({ e, workspace }: { e: LogEntry; workspace?: boolean }) {
  const { r } = useI18n();
  const Icon = KIND_ICON[e.kind];
  return (
    <li className={cx("flex gap-3 border-b border-separator-border py-3 last:border-b-0", !workspace && "border-dashed border-tag-rule", e.kind === "you" && "ps-4")}>
      <time dir="ltr" className="w-11 shrink-0 pt-0.5 text-body-medium tabular-nums text-text-secondary">{hhmm(e.at)}</time>
      <div className="min-w-0 flex-1">
        {e.lines.map((l, i) => (
          <p key={i} className={cx(i === 0 ? "text-body-semibold" : "text-body-regular text-text-secondary", !workspace && e.kind === "you" && "text-accent-700")}>{typeof l === "string" ? l : r(l)}</p>
        ))}
      </div>
      <Icon className={cx("mt-0.5 size-5 shrink-0", workspace ? "text-text-secondary" : KIND_TEXT[e.kind])} aria-hidden />
    </li>
  );
}

/** Narrow paper with a zigzag foot. The newest line is printed on top. */
export function Receipt({ title, entries, empty, className, workspace }: { title: string; entries: LogEntry[]; empty: string; className?: string; workspace?: boolean }) {
  const fade = useScrollFade();
  return (
    <section id={workspace ? "agent-activity" : undefined} tabIndex={workspace ? -1 : undefined} aria-label={title}
      className={cx("flex min-w-0 flex-col", workspace ? "workspace-panel workspace-scroll-target p-5" : "receipt paper px-4 pt-3 pb-5", className)}>
      <h2 className="px-1 pb-1 text-title-3-semibold">{title}</h2>
      {entries.length === 0 ? (
        <p className="px-1 py-3 text-body-regular text-text-secondary">{empty}</p>
      ) : (
        <div className="relative min-h-0 flex-1">
          <ol className={cx("min-h-0 overflow-y-auto px-1", workspace ? "max-h-72" : "h-full pb-6")} onScroll={fade.onScroll}
            tabIndex={0} aria-label={title} aria-live="polite" aria-relevant="additions">
            {entries.map((e) => <Entry key={e.id} e={e} workspace={workspace} />)}
          </ol>
          <ScrollFade progress={fade.progress} surfaceClassName={workspace ? "from-(--workspace-surface)" : undefined} />
        </div>
      )}
    </section>
  );
}
