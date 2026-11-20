"use client";

import {
  RiArrowRightLine, RiFlightLandLine, RiFlightTakeoffLine, RiWheelchairLine,
} from "@remixicon/react";
import { RadioGroup } from "react-aria-components";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { RadioCard } from "@/components/base/radio/radio-card";
import { WayfindingPlot } from "@/components/kiosk/wayfinding-plot";
import { useI18n } from "@/lib/i18n";
import type { PartyKey } from "@/lib/types";

const OPTIONS: PartyKey[] = ["standard", "family", "wheelchair", "both"];
export type FlightSetup = { inbound: string; outbound: string; party: PartyKey };

export function AgentStage({ onBrowse, onMobility }: { onBrowse: () => void; onMobility: () => void }) {
  const { t } = useI18n();
  return (
    <section className="agent-stage" aria-labelledby="workspace-title">
      <WayfindingPlot />
      <div className="agent-stage-copy">
        <p className="flex items-center gap-3 text-headline-medium">
          <span className="workspace-signal" aria-hidden="true" />
          {t("workspace.guide")}
        </p>
        <h1 id="workspace-title" tabIndex={-1} className="text-workspace-hero outline-none">{t("agent.compose_title")}</h1>
        <p className="agent-stage-intro text-workspace-lead">{t("workspace.intro")}</p>
      </div>
      <div className="agent-stage-actions">
        <Button variant="ghost" className="stage-action" leadingIcon={RiFlightTakeoffLine} trailingIcon={RiArrowRightLine} onClick={onBrowse}>{t("agent.browse")}</Button>
        <Button variant="ghost" className="stage-action" leadingIcon={RiWheelchairLine} trailingIcon={RiArrowRightLine} onClick={onMobility}>{t("workspace.mobility")}</Button>
      </div>
    </section>
  );
}

export function StartPanel({ setup, busy, error, onChange, onStart, onTouch }: {
  setup: FlightSetup;
  busy: boolean;
  error: string;
  onChange: (setup: FlightSetup) => void;
  onStart: () => void;
  onTouch: () => void;
}) {
  const { t } = useI18n();
  return (
    <form className="workspace-panel workspace-setup" aria-labelledby="start-h" onFocus={onTouch}
      onSubmit={(event) => { event.preventDefault(); onStart(); }}>
      <div>
        <h2 id="start-h" className="text-workspace-title">{t("workspace.plan")}</h2>
        <p className="mt-2 text-headline-regular text-text-secondary">{t("workspace.plan_hint")}</p>
      </div>
      <div className="flex flex-col gap-4">
        <Input id="inbound-flight" label={t("kiosk.conn.inbound")} value={setup.inbound} leadingIcon={RiFlightLandLine}
          placeholder={t("workspace.example_inbound")} inputDir="ltr" autoComplete="off" spellCheck="false"
          maxLength={12} fieldClassName="workspace-input" isDisabled={busy}
          onChange={(inbound) => onChange({ ...setup, inbound })} />
        <Input label={t("kiosk.conn.outbound")} value={setup.outbound} leadingIcon={RiFlightTakeoffLine}
          placeholder={t("workspace.example_outbound")} inputDir="ltr" autoComplete="off" spellCheck="false"
          maxLength={12} fieldClassName="workspace-input" isDisabled={busy}
          onChange={(outbound) => onChange({ ...setup, outbound })} />
      </div>
      <RadioGroup value={setup.party} onChange={(party) => onChange({ ...setup, party: party as PartyKey })} isDisabled={busy}
        aria-labelledby="party-label" className="flex min-w-0 flex-col gap-3">
        <p id="party-label" className="text-headline-semibold">{t("kiosk.party.label")}</p>
        <div className="workspace-party">
          {OPTIONS.map((option) => <RadioCard key={option} value={option} title={t("kiosk.party.short." + option)} className="workspace-radio" />)}
        </div>
      </RadioGroup>
      <div className="workspace-setup-submit">
        {error && <p role="alert" className="text-body-medium text-text-error-primary">{error}</p>}
        <Button type="submit" variant="primary" trailingIcon={RiArrowRightLine} className="workspace-primary h-14 w-full text-headline-semibold"
          disabled={busy}>
          {t(busy ? "agent.checking" : "agent.start")}
        </Button>
      </div>
    </form>
  );
}
