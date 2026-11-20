"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RiAlertLine, RiCheckLine, RiInformationLine, RiLoginBoxLine, RiLogoutBoxLine, RiMoonLine, RiSunLine } from "@remixicon/react";
import { Logo } from "@/components/agent/logo";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { toast } from "@/components/base/toast/toast";
import { Receipt } from "@/components/kiosk/receipt";
import { Notice, Tag } from "@/components/kiosk/ui";
import { CaseTag, CutoffLane, OPEN, RackRow, byUrgency, caseTone, useLeft } from "@/components/staff/parts";
import { ScenarioControls } from "@/components/staff/scenario";
import type { LogEntry } from "@/lib/agent-log";
import { ApiError, staff } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { CaseState, StaffCaseItem } from "@/lib/types";

const ALL_STATES: CaseState[] = ["draft", "pending_approval", "approved", "dispatch_pending_verification", "dispatched", "completed", "declined", "cancelled", "expired"];
const KEY = "sa:staff-token";

function useToken() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const m = /token=([^&]+)/.exec(window.location.hash);
    if (m) {
      sessionStorage.setItem(KEY, decodeURIComponent(m[1]));
      history.replaceState(null, "", window.location.pathname);
    }
    setToken(sessionStorage.getItem(KEY));
    setReady(true);
  }, []);
  return {
    token, ready,
    save: (v: string) => { sessionStorage.setItem(KEY, v); setToken(v); },
    clear: () => { sessionStorage.removeItem(KEY); setToken(null); },
  };
}

export default function StaffPage() {
  const { token, ready, save, clear } = useToken();
  const { t, r } = useI18n();
  const [filter, setFilter] = useState<"open" | "pending" | "all">("open");
  const [items, setItems] = useState<StaffCaseItem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [denied, setDenied] = useState(false);
  const [draft, setDraft] = useState("");
  const [announced, setAnnounced] = useState<string | null>(null);
  const [light, setLight] = useState(true);
  const [asOf, setAsOf] = useState("");
  const [log, setLog] = useState<LogEntry[]>([]);
  const before = useRef<Map<string, StaffCaseItem> | null>(null);
  const seen = useRef<Set<string> | null>(null);
  const left = useLeft();

  useEffect(() => setLight(!document.documentElement.classList.contains("dark")), []);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const page = await staff.cases(token, ALL_STATES);
      setItems(page.cases);
      setAsOf(page.as_of);
      setFailed(false);
      setDenied(false);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) setDenied(true);
      else setFailed(true);
    }
  }, [token]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [load]);

  const lists = useMemo(() => {
    const all = [...(items ?? [])].sort(byUrgency);
    const open = all.filter((i) => OPEN.includes(i.case.state));
    return { all, open, pending: open.filter((i) => i.case.state === "pending_approval") };
  }, [items]);
  const shown = lists[filter];

  useEffect(() => {
    if (items && !shown.some((i) => i.case.id === selected)) setSelected(shown[0]?.case.id ?? null);
  }, [items, shown, selected]);

  // The console stays quiet until a request needs a decision; then it speaks up on its own.
  useEffect(() => {
    if (!items) return;
    const ids = new Set(items.map((i) => i.case.id));
    if (seen.current) {
      const added = items.find((i) => i.case.state === "pending_approval" && !seen.current!.has(i.case.id));
      if (added) { setAnnounced(added.case.id); setSelected(added.case.id); }
    } else {
      const waiting = items.find((i) => i.case.state === "pending_approval");
      if (waiting) setAnnounced(waiting.case.id);
    }
    seen.current = ids;
  }, [items]);

  // Everything the console sees change is printed to the operations log, newest on top.
  useEffect(() => {
    if (!items) return;
    const prevMap = before.current;
    before.current = new Map(items.map((i) => [i.case.id, i]));
    const label = (i: StaffCaseItem) => `${t(`assistance.service.${i.case.service}`)}, ${t("ui.gate")} ${i.gate.value ?? "?"}`;
    const add: LogEntry[] = [];
    for (const i of items) {
      const was = prevMap?.get(i.case.id);
      if (!was) add.push({ id: `${i.case.id}-new`, at: asOf, kind: "info", lines: [prevMap ? "New request" : t(`assistance.stage.${i.case.state}`), label(i)] });
      else if (was.case.state !== i.case.state) add.push({ id: `${i.case.id}-${i.case.state}`, at: asOf, kind: caseTone(i.case.state) === "neutral" ? "good" : "info", lines: [t(`assistance.stage.${i.case.state}`), label(i)] });
      else if (was.time_remaining != null && was.time_remaining >= 0 && i.time_remaining != null && i.time_remaining < 0) add.push({ id: `${i.case.id}-late`, at: asOf, kind: "risk", lines: [t("status.boarding_cutoff_passed"), label(i)] });
    }
    if (add.length) setLog((l) => [...add.reverse(), ...l].slice(0, 60));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  useEffect(() => {
    if (!announced) return;
    const timer = setTimeout(() => setAnnounced(null), 25000);
    return () => clearTimeout(timer);
  }, [announced]);

  if (!ready) return null;
  if (!token || denied) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 p-6">
        <Logo size={48} className="text-text-primary" />
        <Tag className="flex flex-col gap-5 p-8">
          <h1 className="numeral-lg">Coordinator sign-in</h1>
          {denied && <Notice tone="rose" icon={RiAlertLine}>{t("reason.WRONG_ROLE")}</Notice>}
          <Input label="Coordinator token" type="password" value={draft} onChange={setDraft} />
          <Button variant="primary" leadingIcon={RiLoginBoxLine} className="h-14 rounded-lg text-headline-semibold" disabled={!draft} onClick={() => save(draft)}>Sign in</Button>
        </Tag>
      </div>
    );
  }

  const item = items?.find((i) => i.case.id === selected) ?? null;
  const fresh = items?.find((i) => i.case.state === "pending_approval" && i.case.id === announced) ?? null;
  const act = async (kind: "approve" | "decline" | "dispatch" | "none_free", target: StaffCaseItem | null = item) => {
    if (!target) return;
    try {
      const res = kind === "dispatch" ? await staff.dispatch(token, target.case) : await staff.decide(token, target.case, kind === "none_free" ? "decline" : kind, kind === "none_free" ? { reason_code: "RESOURCE_UNAVAILABLE", wait_min: 20 } : undefined);
      const named = kind === "approve" ? "Approved" : kind === "decline" || kind === "none_free" ? "Declined" : "Marked as dispatched";
      toast(res.outcome === "rejected" ? `${t("ui.staff.decision_sent")}: ${res.outcome}` : res.outcome === "replayed" ? `${named} (already recorded)` : named, { description: res.reason_code ? t(`reason.${res.reason_code}`) : undefined });
    } catch {
      toast(t("ui.error_generic"));
    }
    load();
  };

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b border-belt-line bg-belt">
        <div className="mx-auto flex h-[4.5rem] w-full max-w-[1600px] items-center justify-between gap-3 px-4 sm:px-6">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <Logo size={44} className="text-text-primary" />
            <span className="text-headline-regular text-belt-soft">Coordinator, DXB</span>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" iconOnly className="size-12 rounded-lg" leadingIcon={light ? RiMoonLine : RiSunLine} aria-label={light ? "Dark theme" : "Light theme"}
              onClick={() => { document.documentElement.classList.toggle("dark", light); setLight(!light); }} />
            <Button variant="secondary" leadingIcon={RiLogoutBoxLine} className="h-12 rounded-lg px-4 text-body-semibold" onClick={clear}>Sign out</Button>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-6 px-4 pt-6 pb-16 sm:px-6">
        {fresh && (
          <Tag role="alert" className="print-in flex w-full flex-wrap items-center gap-3 p-4 sm:gap-4">
            <span className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-status-orange-background text-status-orange-text"><RiAlertLine className="size-6" aria-hidden /></span>
            <div className="flex min-w-0 basis-[calc(100%-4rem)] flex-1 flex-col sm:basis-0">
              <strong className="text-title-3-semibold">{t(`assistance.service.${fresh.case.service}`)} · {fresh.case.pickup ? r(fresh.case.pickup.landmark) : t("arrivals.unknown")}</strong>
              <span className="text-body-medium text-ink-soft tabular-nums">{t("ui.staff.time_remaining")}: {left(fresh).text}</span>
            </div>
            <Button variant="primary" leadingIcon={RiCheckLine} className="h-12 rounded-lg px-5 text-body-semibold" onClick={() => { act("approve", fresh); setAnnounced(null); }}>{t("ui.staff.approve")}</Button>
            <Button variant="secondary" className="h-12 rounded-lg px-5 text-body-semibold" onClick={() => { act("decline", fresh); setAnnounced(null); }}>{t("ui.staff.decline")}</Button>
          </Tag>
        )}
        <CutoffLane items={lists.open} selected={selected} onSelect={(id) => { setFilter("open"); setSelected(id); }} />
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(340px,4fr)_8fr]">
          <section aria-labelledby="rack-h" className="flex min-w-0 flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="rack-h" className="text-title-2-semibold">{t("ui.staff.queue")}</h2>
              <SegmentedControl aria-label="Queue filter" selectedKeys={[filter]} onSelectionChange={(k) => { const v = [...k][0]; if (v) setFilter(String(v) as typeof filter); }}>
                <SegmentedControlItem id="open">Open {lists.open.length}</SegmentedControlItem>
                <SegmentedControlItem id="pending">To decide {lists.pending.length}</SegmentedControlItem>
                <SegmentedControlItem id="all">All {lists.all.length}</SegmentedControlItem>
              </SegmentedControl>
            </div>
            {failed && <Notice tone="orange" icon={RiAlertLine}>{t("ui.offline_notice")}</Notice>}
            {!items && !failed && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
            {items && shown.length === 0 && <p className="flex items-center gap-2 px-1 text-body-regular text-belt-soft"><RiInformationLine className="size-5" aria-hidden />{t("ui.staff.empty_queue")}</p>}
            <ul className="flex flex-col gap-3">
              {shown.map((i) => <RackRow key={i.case.id} item={i} active={i.case.id === selected} onSelect={() => setSelected(i.case.id)} />)}
            </ul>
            <Receipt title="Operations log" entries={log} empty="Requests and decisions print here as they happen." className="mt-3 max-h-[26rem]" />
          </section>
          <div className="flex min-w-0 flex-col gap-6">
            {item ? <CaseTag item={item} onAct={(k) => act(k)} /> : items ? <Tag className="p-8 text-ink-soft">{t("kiosk.select_flight")}</Tag> : null}
            <ScenarioControls token={token} />
          </div>
        </div>
      </main>
    </div>
  );
}
