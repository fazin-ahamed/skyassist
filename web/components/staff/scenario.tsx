"use client";

import { useCallback, useEffect, useState } from "react";
import { RiAlertLine, RiCheckLine, RiPauseCircleLine, RiPlayCircleLine, RiTimeLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Select, SelectItem } from "@/components/base/select/select";
import { toast } from "@/components/base/toast/toast";
import { Notice, StatusPill, Tag, Tear } from "@/components/kiosk/ui";
import { RiArrowDownSLine } from "@remixicon/react";
import { api, ApiError, staff } from "@/lib/api";
import { hhmm } from "@/lib/format";
import type { DemoState } from "@/lib/types";

/** Demo world controls. Approval and dispatch stay on the case tag; these only change the clock, the feed and the events. */
export function ScenarioControls({ token }: { token: string }) {
  const [state, setState] = useState<DemoState | null>(null);
  const [legs, setLegs] = useState<{ id: string; label: string }[]>([]);
  const [leg, setLeg] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [offline, setOffline] = useState(false);

  const load = useCallback(async () => {
    api.systemStatus().then((s) => setOffline(s.offline_forced)).catch(() => {});
    try { setState(await staff.demo(token)); setError(false); } catch (e) { setState(null); setError(!(e instanceof ApiError && e.status === 404)); }
  }, [token]);
  useEffect(() => {
    load();
    staff.legs().then((l) => { setLegs(l); setLeg((c) => c ?? l[0]?.id ?? null); }).catch(() => {});
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const run = async (fn: () => Promise<void>) => {
    try { await fn(); } catch { toast("Could not apply that control"); }
    load();
  };
  if (!state) return error ? <Notice tone="orange" icon={RiAlertLine}>Scenario controls are unreachable.</Notice> : null;
  const held = state.held ?? [];
  return (
    <Tag as="details" className="group">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 rounded-[inherit] px-6 py-5 outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2 text-title-2-semibold"><RiArrowDownSLine className="size-6 transition-transform group-open:rotate-180" aria-hidden />Scenario controls</span>
        <span className="flex items-center gap-2 text-body-medium text-ink-soft"><RiTimeLine className="size-5" aria-hidden />Clock <span className="tabular-nums">{hhmm(state.now)}</span> (Dubai)</span>
      </summary>
      <Tear />
      <p className="px-6 pt-4 text-body-regular text-ink-soft">The world, not the decision.</p>
      <div className="flex flex-wrap gap-3 px-6 pt-4 pb-5">
        <Button variant="secondary" className="h-11 rounded-lg" onClick={() => run(() => staff.clock(token, { name: "C01" }))}>Back to scenario start</Button>
        <Button variant="secondary" className="h-11 rounded-lg" onClick={() => run(() => staff.clock(token, { advance_seconds: 60 }))}>Advance 1 minute</Button>
        <Button variant="secondary" className="h-11 rounded-lg" onClick={() => run(() => staff.clock(token, { advance_seconds: 300 }))}>Advance 5 minutes</Button>
      </div>
      <Tear />
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-5">
        <span className="flex flex-col">
          <strong className="text-title-3-semibold">Offline rehearsal</strong>
          <span className="text-body-medium text-ink-soft">Turns off voice, the hosted model and live aircraft, as if the internet were gone. Gates, routes and staff requests keep working.</span>
        </span>
        <Button variant={offline ? "primary" : "secondary"} className="h-11 rounded-lg" onClick={() => run(async () => { await staff.offline(token, !offline); })}>{offline ? "Back online" : "Go offline"}</Button>
      </div>
      <Tear />
      <div className="flex flex-col gap-3 px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-title-3-semibold">Gate feed</h3>
          <span className="text-body-medium text-ink-soft">Held: {held.length ? held.map((h) => h.leg_id).join(", ") : "none"}</span>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Select aria-label="Flight" className="min-w-64" selectedKey={leg} onSelectionChange={(k) => setLeg(String(k))}>
            {legs.map((l) => <SelectItem key={l.id} id={l.id} textValue={l.label}>{l.label}</SelectItem>)}
          </Select>
          <Button variant="secondary" leadingIcon={RiPauseCircleLine} className="h-10 rounded-lg" disabled={!leg} onClick={() => leg && run(() => staff.feed(token, "hold", leg))}>Hold gate updates</Button>
          <Button variant="secondary" leadingIcon={RiPlayCircleLine} className="h-10 rounded-lg" disabled={!leg} onClick={() => leg && run(() => staff.feed(token, "resume", leg))}>Resume</Button>
        </div>
      </div>
      <Tear />
      <ul className="flex max-h-[26rem] flex-col overflow-y-auto px-6 py-2">
        {state.events.map((e) => (
          <li key={e.id} className="flex items-center justify-between gap-3 border-b border-dashed border-tag-rule py-3 last:border-b-0">
            <span className="flex flex-col"><strong className="text-body-semibold">{e.label}</strong><span className="text-body-medium text-ink-soft">{e.scenario_ids.join(", ") || e.kind}</span></span>
            {e.applied ? <StatusPill tone="neutral" icon={RiCheckLine}>Applied</StatusPill> : <Button variant="secondary" className="h-10 rounded-lg" onClick={() => run(() => staff.apply(token, e.id))}>Apply</Button>}
          </li>
        ))}
      </ul>
    </Tag>
  );
}
