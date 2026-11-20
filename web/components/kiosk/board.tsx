"use client";

import { RiAlertLine, RiInformationLine, RiRefreshLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { Card, StatusPill } from "@/components/kiosk/ui";
import { hhmm } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { FlightBoard, ObservationState } from "@/lib/types";
import { cx } from "@/utils/cx";

export interface Row { id: string; code: string; city: string; time: string; gate: string | null; gateState: ObservationState }

export function rowsOf(board: FlightBoard, direction: "departures" | "arrivals"): Row[] {
  return board.flights.map(({ leg, facts }) => {
    const dep = direction === "departures";
    const timeFact = facts.find((f) => f.field === (dep ? "departure" : "arrival"))?.observation;
    const gate = facts.find((f) => f.field === "gate");
    return {
      id: leg.id,
      code: `${leg.operating_carrier} ${leg.operating_number}`,
      city: dep ? leg.destination : leg.origin,
      time: hhmm(timeFact?.estimated ?? timeFact?.scheduled),
      gate: gate?.state === "fresh" && gate.observation?.value != null ? String(gate.observation.value) : null,
      gateState: gate?.state ?? "unknown",
    };
  });
}

interface BoardProps {
  rows: Row[] | null;
  failed: boolean;
  retry: () => void;
  selected: string | null;
  onSelect?: (id: string) => void;
}

export function Board({ rows, failed, retry, selected, onSelect }: BoardProps) {
  const { t } = useI18n();
  if (failed && !rows) {
    return (
      <Card className="flex flex-col items-start gap-4">
        <p className="flex items-center gap-2 text-headline-medium"><RiAlertLine className="size-5" aria-hidden />{t("kiosk.service_down")}</p>
        <Button variant="primary" leadingIcon={RiRefreshLine} className="h-12 rounded-2xl" onClick={retry}>{t("ui.retry")}</Button>
      </Card>
    );
  }
  if (!rows) {
    return (
      <Card className="flex flex-col gap-3 p-4">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-20 rounded-2xl" />)}
      </Card>
    );
  }
  if (!rows.length) {
    return <Card className="flex items-center gap-3 text-text-secondary"><RiInformationLine className="size-6" aria-hidden />{t("ui.empty_flights")}</Card>;
  }
  return (
    <Card className="min-w-0 p-2">
      <ul className="flex max-h-[calc(100dvh-14rem)] min-h-80 flex-col gap-1 overflow-y-auto p-1">
        {rows.map((row) => {
          const active = row.id === selected;
          const bad = row.gateState === "stale" || row.gateState === "conflict";
          const RowElement = onSelect ? "button" : "div";
          return (
            <li key={row.id}>
              <RowElement
                type={onSelect ? "button" : undefined} onClick={onSelect ? () => onSelect(row.id) : undefined} aria-pressed={onSelect ? active : undefined}
                className={cx(
                  "flex w-full flex-col gap-2 rounded-2xl px-4 py-3 text-start outline-none transition-colors duration-150",
                  "focus-visible:ring-2 focus-visible:ring-border-focus-ring",
                  active ? "bg-accent-50 ring-2 ring-accent-600" : onSelect && "hover:bg-background-secondary-hover",
                )}
              >
                <span className="flex items-center justify-between gap-4">
                  <span className="flex flex-col">
                    <span dir="ltr" className="text-title-3-semibold tabular-nums">{row.code}</span>
                    <span className="text-body-medium text-text-secondary"><span dir="ltr">{row.city}</span> · <span className="tabular-nums">{row.time}</span></span>
                  </span>
                  <span className="flex flex-col items-end">
                    <span className="text-body-2-medium text-text-secondary">{t("ui.gate")}</span>
                    <span className="text-title-2-semibold tabular-nums">{row.gate ? <bdi dir="ltr">{row.gate}</bdi> : t("kiosk.gate.tbc")}</span>
                  </span>
                </span>
                {bad && <StatusPill tone="rose" icon={RiAlertLine}>{t(`ui.freshness.${row.gateState}`)}</StatusPill>}
              </RowElement>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
