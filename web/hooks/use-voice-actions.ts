"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IslandConfirm as DockConfirm } from "@/components/chat/island";
import type { Utterance, VoiceApi } from "@/hooks/use-voice";
import { isAirportMapRequest, isAviationLookup, readTransferFlight } from "@/lib/passenger-prompt";
import { parseUtterance, type KnownFlight, type ParseContext, type PartyKey, type Place } from "@/lib/voice/intent";

export interface VoiceActions {
  startJourney(a: { inbound: string; outbound: string; party: PartyKey }): void;
  changeFlights(flights: string[]): void;
  prefill(a: { inbound?: string; outbound?: string; party?: PartyKey }): void;
  choose(id: string): void;
  chooseOrigin(id: string): void;
  setPosition(id: string): void;
  sendText(text: string): void;
  /** Asks the kiosk a question and resolves with the answer, so it can be spoken too. */
  ask(q: "time" | "gate" | "why"): Promise<string>;
  assist(): void;
  openRoute(): void;
  end(): void;
  urgent(): void;
  browse(): void;
  locate(): void;
  setLang(lang: "en" | "ar"): void;
}

interface Deps {
  lang: "en" | "ar";
  journey: boolean;
  awaitingTransferFlight: boolean;
  asking: ParseContext["asking"];
  choices: { id: string; label: string }[];
  flights: KnownFlight[];
  places: Place[];
  voice: Pick<VoiceApi, "say" | "autoReadAloud" | "setNotice">;
  t: (key: string, params?: Record<string, unknown>) => string;
  actions: VoiceActions;
}

/** Below this the words are too uncertain to act on at all; between this and the sure line, ask first. */
const TOO_UNSURE = 0.45;
const SURE = 0.75;

/**
 * What the kiosk does with something it heard. Reversible and read-only things happen straight away; anything that
 * starts work or ends a session waits for a spoken or tapped yes when the recognition was not clearly sure.
 * Consent for staff assistance is never given by voice: the request opens its form and the person taps.
 */
export function useVoiceActions(deps: Deps) {
  const ref = useRef(deps);
  useEffect(() => { ref.current = deps; });
  const [confirm, setConfirm] = useState<DockConfirm | null>(null);
  const pending = useRef<DockConfirm | null>(null);
  const lastReply = useRef("");

  const clear = useCallback(() => { pending.current = null; setConfirm(null); }, []);
  const reply = useCallback((text: string, force = false) => { lastReply.current = text; void ref.current.voice.say(text, force); }, []);
  const ask = useCallback((lines: string[], yes: () => void) => {
    const card: DockConfirm = {
      lines,
      onYes: () => { clear(); yes(); },
      onNo: () => { clear(); reply(ref.current.t("voice.reply.cancelled")); },
    };
    pending.current = card;
    setConfirm(card);
  }, [clear, reply]);

  const onUtterance = useCallback((u: Utterance & { typed?: boolean }) => {
    const d = ref.current;
    const { t, actions, voice } = d;
    if (!u.typed) voice.autoReadAloud();
    const [intent] = parseUtterance(u.text, {
      lang: d.lang, asking: d.asking, choices: d.choices, places: d.places, flights: d.flights, journey: d.journey,
    });
    // Safety words are never held back by how sure the recogniser was.
    if (intent?.kind === "command" && intent.name === "urgent") {
      clear();
      actions.urgent();
      reply(t("voice.reply.urgent"), true);
      return;
    }
    if (u.confidence >= SURE && (isAirportMapRequest(u.text) || isAviationLookup(u.text) || (!d.journey && d.awaitingTransferFlight && readTransferFlight(u.text)))) {
      clear();
      actions.sendText(u.text);
      return;
    }
    if (!intent) { if (u.typed) actions.sendText(u.text); else voice.setNotice("didnt_catch"); return; }
    if (intent.kind === "command" && (intent.name === "yes" || intent.name === "no")) {
      const card = pending.current;
      if (card) (intent.name === "yes" ? card.onYes : card.onNo)();
      return;
    }
    if (u.confidence < TOO_UNSURE) { voice.setNotice("didnt_catch"); return; }
    clear();

    switch (intent.kind) {
      case "flights": {
        const { inbound, outbound, party } = intent;
        if (!inbound && !outbound) { actions.prefill({ party }); return; }
        const go = () => {
          if (d.journey) actions.changeFlights([inbound, outbound].filter((x): x is string => !!x));
          else actions.startJourney({ inbound: inbound ?? "", outbound: outbound ?? "", party: party ?? "standard" });
          reply(t("voice.reply.checking"));
        };
        if (intent.sure && u.confidence >= SURE) { go(); return; }
        actions.prefill({ inbound, outbound, party });
        ask([inbound && t("voice.confirm.flight_in", { flight: inbound }), outbound && t("voice.confirm.flight_out", { flight: outbound })].filter((x): x is string => !!x), go);
        return;
      }
      case "choice":
        actions.choose(intent.id);
        return;
      case "place":
        if (d.asking === "landmark") actions.chooseOrigin(intent.id);
        else if (d.asking === "pickup") actions.choose(intent.id);
        else { actions.setPosition(intent.id); reply(t("voice.reply.located")); }
        return;
      case "command":
        switch (intent.name) {
          case "time": case "gate": case "why":
            if (!d.journey) { reply(t("voice.reply.no_journey")); return; }
            void actions.ask(intent.name).then((line) => reply(line));
            return;
          case "route":
            if (!d.journey) { reply(t("voice.reply.no_journey")); return; }
            actions.openRoute(); reply(t("voice.reply.route"));
            return;
          case "assist":
            if (!d.journey) { reply(t("voice.reply.no_journey")); return; }
            actions.assist(); reply(t("voice.reply.assist"));
            return;
          case "end": ask([t("voice.confirm.end")], actions.end); return;
          case "repeat": if (lastReply.current) reply(lastReply.current); return;
          case "lang_ar": actions.setLang("ar"); return;
          case "lang_en": actions.setLang("en"); return;
          case "browse": actions.browse(); return;
          case "locate": actions.locate(); reply(t("voice.reply.located")); return;
          default: return;
        }
      case "text":
        if (u.confidence >= 0.6) actions.sendText(intent.text);
        else voice.setNotice("didnt_catch");
        return;
    }
  }, [ask, clear, reply]);

  return { confirm, onUtterance, dismiss: clear };
}
