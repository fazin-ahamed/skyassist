"use client";

import { Fragment } from "react";
import { useI18n } from "@/lib/i18n";
import type { RouteStep, RouteView } from "@/lib/types";
import { Card, MODE_ICON } from "@/components/kiosk/ui";
import { cx } from "@/utils/cx";

function runs(route: RouteView): { floor: string; steps: RouteStep[] }[] {
  const out: { floor: string; steps: RouteStep[] }[] = [];
  for (const s of route.steps) {
    const last = out[out.length - 1];
    if (last && last.floor === s.floor) last.steps.push(s);
    else out.push({ floor: s.floor, steps: [s] });
  }
  return out;
}

/** One line across every step; floors are labelled bands, so a level change reads at a glance. */
function RouteLine({ route }: { route: RouteView }) {
  const { t } = useI18n();
  const last = route.steps[route.steps.length - 1]?.number;
  return (
    <div className="flex items-end" role="img" aria-label={t("kiosk.route_title")}>
      {runs(route).map((run, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-2" style={{ flexGrow: run.steps.length, flexBasis: 0 }}>
          <span className={cx("px-1 text-body-2-semibold text-text-secondary", i > 0 && "border-s border-separator-border ps-3")}>
            {t("kiosk.floor", { floor: run.floor })}
          </span>
          <ol className="flex items-center">
            {run.steps.map((s, j) => (
              <Fragment key={s.number}>
                {(j > 0 || i > 0) && <li aria-hidden className="h-1.5 min-w-3 flex-1 rounded-full bg-accent-600" />}
                <li className={cx(
                  "flex size-10 shrink-0 items-center justify-center text-body-semibold tabular-nums",
                  s.number === last
                    ? "rounded-xl bg-accent-600 text-text-white"
                    : s.number === 1
                      ? "rounded-full bg-accent-600 text-text-white"
                      : "rounded-full border-4 border-accent-600 bg-background-primary-default text-text-primary",
                )}>
                  {s.number}
                </li>
              </Fragment>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}

export function RouteCard({ route }: { route: RouteView }) {
  const { t, r } = useI18n();
  return (
    <Card className="flex scroll-mt-4 flex-col gap-6" id="route">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-title-2-semibold">{t("kiosk.route_title")}</h2>
      </div>
      <RouteLine route={route} />
      <ol className="flex flex-col gap-5" aria-label={t("ui.route_text_alternative")}>
        {route.steps.map((s, i) => {
          const Icon = MODE_ICON[s.accessible_mode];
          const change = i > 0 && route.steps[i - 1].floor !== s.floor;
          return (
            <Fragment key={s.number}>
              {change && (
                <li aria-hidden className="flex items-center gap-3 text-body-2-semibold text-text-secondary">
                  <span className="h-px flex-1 bg-separator-border" />
                  {t("kiosk.floor", { floor: s.floor })}
                  <span className="h-px flex-1 bg-separator-border" />
                </li>
              )}
              <li className="flex gap-4">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-600 text-title-3-semibold tabular-nums text-text-white">
                  {s.number}
                </span>
                <div className="flex min-w-0 flex-col gap-0.5">
                  <strong className="text-headline-semibold">{r(s.landmark)}</strong>
                  <span className="text-body-regular text-text-secondary">{r(s.text)}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-body-2-medium text-text-secondary">
                    <Icon className="size-4" aria-hidden />
                    {t(`mode.${s.accessible_mode}`)}
                  </span>
                </div>
              </li>
            </Fragment>
          );
        })}
      </ol>
    </Card>
  );
}
