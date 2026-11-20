"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RiAlertLine, RiChat3Line, RiExternalLinkLine, RiFlightTakeoffLine, RiMapPinLine, RiRouteLine } from "@remixicon/react";
import { Button, ButtonLink } from "@/components/base/buttons/button";
import { Island, type IslandAction } from "@/components/chat/island";
import { AircraftCard, AssistCard, BoardCard, Card, CardHead, LedgerCard, RouteCard, VerdictHero } from "@/components/canvas/cards";
import { DubaiMap } from "@/components/canvas/dubai-map";
import { BrowseModal } from "@/components/kiosk/browse";
import { AssistModal, HelpModal, IdleModal } from "@/components/kiosk/modals";
import { JudgeSheet } from "@/components/shell/judge";
import { Topbar } from "@/components/shell/topbar";
import { useHealth } from "@/hooks/use-health";
import { useIdle } from "@/hooks/use-idle";
import { useJourney } from "@/hooks/use-journey";
import { useLiveMap } from "@/hooks/use-live-map";
import { useVoice, type Utterance } from "@/hooks/use-voice";
import { useVoiceActions } from "@/hooks/use-voice-actions";
import { useWalker } from "@/hooks/use-walker";
import { api, ApiError } from "@/lib/api";

import { FOCUS_FOR_KIND, messageId, type CardKey, type ChatChoice, type ChatMessage, type Work } from "@/lib/chat";
import { rowsOf } from "@/components/kiosk/board";

/** ?booking= and ?party= from the launch URL, read once; a judge can open /?booking=BK-MINOR1 as an unaccompanied child. */
function readLaunch(): { booking?: string; party?: "standard" | "family" | "wheelchair" | "both" } {
  if (typeof window === "undefined") return {};
  const q = new URLSearchParams(window.location.search);
  const party = q.get("party");
  const parties = ["standard", "family", "wheelchair", "both"] as const;
  const booking = q.get("booking") ?? undefined;
  return {
    booking: booking || undefined,
    party: parties.find((p) => p === party),
  };
}
import { useI18n } from "@/lib/i18n";
import { isAirportMapRequest, isAviationLookup, readPassengerPrompt, readTransferFlight } from "@/lib/passenger-prompt";
import { deriveSignal } from "@/lib/signals";
import type { AskTrace, Choice, ViewModel } from "@/lib/types";
import type { KnownFlight, Place } from "@/lib/voice/intent";

const IDLE_WARN = 90;
const IDLE_END = 120;
const ASKING = ["choose_landmark", "choose_flight", "confirm_date", "confirm_pickup"];
const HERE = "__here";
const OFFICIAL_MAP = "https://dubaiairports.ae/express-maps";
const DXB_DATE = new Intl.DateTimeFormat("sv-SE", { dateStyle: "short", timeZone: "Asia/Dubai" });

export default function PassengerPage() {
  const [epoch, setEpoch] = useState(0);
  const reset = useCallback(() => { setEpoch((n) => n + 1); window.scrollTo(0, 0); }, []);
  return <Kiosk key={epoch} onReset={reset} />;
}

function Kiosk({ onReset }: { onReset: () => void }) {
  const { r, t, lang, setLang, mode } = useI18n();
  const [jid, setJid] = useState<string | null>(null);
  const [legs, setLegs] = useState<{ inbound: string; outbound: string } | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinking, setThinking] = useState(false);
  const [work, setWork] = useState<Work | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [awaitingTransferFlight, setAwaitingTransferFlight] = useState(false);
  const [focus, setFocus] = useState<CardKey | null>(null);
  const [trace, setTrace] = useState<AskTrace | null>(null);
  const [dialog, setDialog] = useState<"help" | "browse" | "judge" | null>(null);
  const [pickup, setPickup] = useState<Choice | null>(null);
  const [assistError, setAssistError] = useState("");
  const [touched, setTouched] = useState(false);

  const active = useRef<string | null>(null);
  const lookup = useRef<string | null>(null);
  const working = useRef(false);
  const ended = useRef(false);
  const prev = useRef<ViewModel | null>(null);
  const asked = useRef(new Set<string>());
  const wantAssist = useRef(false);
  const summarised = useRef<string | null>(null);

  const { view, down, expired, nudge } = useJourney(jid);
  const health = useHealth();
  const map = useLiveMap(jid);
  const [launch] = useState(readLaunch);
  const [walkSpeed, setWalkSpeed] = useState<1 | 2 | 4>(1);
  const walker = useWalker(map.layout, map.live, map.position, (input) => map.place(input), walkSpeed);
  const noticeSeq = useRef(-1);
  const arrivedSaid = useRef(false);

  const closeLookup = useCallback(() => {
    const id = lookup.current;
    lookup.current = null;
    if (id) void api.closeJourney(id).catch(() => {});
  }, []);
  const closeSessions = useCallback(() => {
    ended.current = true;
    const id = active.current;
    active.current = null;
    if (id) void api.closeJourney(id).catch(() => {});
    closeLookup();
  }, [closeLookup]);
  const end = useCallback(() => {
    closeSessions();
    onReset();
  }, [closeSessions, onReset]);
  const inSession = jid !== null || touched;
  const { idle, keep } = useIdle(inSession, IDLE_WARN, IDLE_END, end);
  useEffect(() => { if (expired) end(); }, [expired, end]);
  useEffect(() => {
    const restore = (event: PageTransitionEvent) => { if (event.persisted) end(); };
    window.addEventListener("pagehide", closeSessions);
    window.addEventListener("pageshow", restore);
    return () => {
      window.removeEventListener("pagehide", closeSessions);
      window.removeEventListener("pageshow", restore);
    };
  }, [closeSessions, end]);

  // ---------- the conversation
  const say = useRef<(text: string) => void>(() => {});
  const addAgent = useCallback((text: string, extra: Partial<ChatMessage> = {}) => {
    if (ended.current) return;
    setMessages((list) => [...list, { id: messageId(), role: "agent", text, ...extra }]);
    say.current(text);
    if (extra.focus) setFocus(extra.focus);
  }, []);
  const addUser = useCallback((text: string) => setMessages((list) => [...list, { id: messageId(), role: "user", text }]), []);

  const here = map.position?.node_id ?? ((map.position?.near_distance ?? 99) <= 8 ? map.position?.near_node ?? null : null);
  const hereNode = here ? map.layout?.nodes.find((n) => n.id === here) : undefined;
  const next = view?.next_action;
  const asking = !!next && (next.choices.length > 0 || ASKING.includes(next.action));
  const questionKey = view && next && asking ? `${next.action}|${next.choices.map((choice) => choice.id).join(",")}|${view.status.reason_codes.join(",")}` : undefined;

  // The agent asks what only the passenger knows, once per question, with answers they can tap.
  useEffect(() => {
    if (!view || !next) return;
    const before = prev.current;
    prev.current = view;
    const key = questionKey;
    if (key && !asked.current.has(key)) {
      asked.current.add(key);
      const choices: ChatChoice[] = next.choices.map((c) => ({ id: c.id, label: r(c.label) }));
      if (next.action === "choose_landmark" && hereNode) choices.unshift({ id: HERE, label: t("kk.use_here", { place: r(hereNode.landmark) }) });
      addAgent(r(next.text), { choices, key });
      setChatOpen(true);
      if (next.action === "choose_flight" && choices.length === 0) {
        void api.flights("departures").then((board) => {
          const rows = rowsOf(board, "departures").filter((row) => row.gate).slice(0, 6);
          setMessages((list) => list.map((m) => (m.key === key ? { ...m, text: `${m.text} ${t("kk.pick_schedule")}`, choices: rows.map((row) => ({ id: row.code.replace(" ", ""), label: `${row.code} · ${row.city} ${row.time}` })) } : m)));
        }).catch(() => {});
      }
      return;
    }
    if (key) return;
    const signal = deriveSignal(before, view);
    // a change in the verdict is announced by the grounded summary, so only other changes speak here
    if (signal && before && !signal.id.startsWith("status-")) addAgent([r(signal.title), signal.body && signal.sticky ? r(signal.body) : ""].filter(Boolean).join(". "), { focus: signal.action === "route" ? "route" : "gate" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // A change the system made on its own (gate moved, request refused, child escort) is said once, in the chat.
  useEffect(() => {
    const n = view?.notice;
    if (!n || n.seq === noticeSeq.current) return;
    const first = noticeSeq.current === -1;
    noticeSeq.current = n.seq;
    if (first && jid && view && view.revision < 2) return;
    addAgent(r(n.text), { focus: "assist" });
    setChatOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.notice?.seq]);

  useEffect(() => {
    if (walker.arrived && !arrivedSaid.current && view?.gate.value) { arrivedSaid.current = true; addAgent(t("walk.arrived", { gate: view.gate.value }), { focus: "gate" }); }
    if (!walker.arrived) arrivedSaid.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walker.arrived]);

  // The first time there is a verdict, the agent says it in its own grounded words.
  useEffect(() => {
    const level = view?.verdict?.level;
    // say it once the verdict means something, and again whenever it changes
    if (!jid || !view?.verdict || asking || level === "unverified") return;
    const stamp = `${jid}|${level}|${view.verdict.margin_min}`;
    if (summarised.current === stamp) return;
    summarised.current = stamp;
    api.ask(jid, t("agent.ask.time"), lang)
      .then((reply) => {
        if (ended.current || active.current !== jid || summarised.current !== stamp) return;
        setTrace(reply.trace);
        addAgent(reply.answer, { focus: "gate", source: reply.source, trace: reply.trace });
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jid, view?.verdict?.level, view?.verdict?.margin_min, asking]);

  const fail = (cause: unknown) => {
    const reason = cause instanceof ApiError && cause.reason ? t("reason." + cause.reason) : t("ui.error_generic");
    addAgent(cause instanceof ApiError && cause.status === 0 ? t("kiosk.service_down") : reason, { error: true });
  };

  const startJourney = async (fields: Record<string, unknown>, flights?: { inbound: string; outbound: string; party: NonNullable<typeof launch.party> }) => {
    if (working.current || ended.current) return;
    working.current = true;
    setThinking(true);
    closeLookup();
    try {
      const id = await api.createJourney({ party: launch.party ?? flights?.party, language: lang, booking_ref: launch.booking });
      if (ended.current) {
        void api.closeJourney(id).catch(() => {});
        return;
      }
      active.current = id;
      await api.sendInput(id, fields);
      if (ended.current) return;
      prev.current = null;
      asked.current.clear();
      summarised.current = null;
      setAwaitingTransferFlight(false);
      if (flights) setLegs({ inbound: flights.inbound, outbound: flights.outbound });
      setJid(id);
    } catch (cause) {
      const id = active.current;
      active.current = null;
      if (id) void api.closeJourney(id).catch(() => {});
      fail(cause);
    } finally {
      working.current = false;
      setThinking(false);
    }
  };

  const send = async (fields: Record<string, unknown>) => {
    if (!jid || working.current || ended.current) return;
    working.current = true;
    setThinking(true);
    try { await api.sendInput(jid, fields, view?.revision); nudge(); } catch (cause) { fail(cause); }
    finally { working.current = false; setThinking(false); }
  };

  const onAssist = () => {
    if (!jid || !view || working.current) return;
    if (view.assistance) { setFocus("assist"); return; }
    if (next?.action === "confirm_pickup" && next.choices[0]) { setPickup(next.choices[0]); return; }
    wantAssist.current = true;
    send({ intent: "request_assistance" });
  };
  useEffect(() => {
    if (view && wantAssist.current && next?.action === "confirm_pickup" && next.choices[0]) { wantAssist.current = false; setPickup(next.choices[0]); }
  }, [view, next]);

  const choose = (id: string) => {
    if (id === HERE) { if (here) void send({ confirmed_origin_id: here }); return; }
    const action = next?.action;
    if (action === "confirm_pickup") return onAssist();
    if (action === "choose_landmark") return void send({ confirmed_origin_id: id });
    if (action === "confirm_date") return void send({ intent: wantAssist.current ? "request_assistance" : "check_connection", operation_date: id });
    return void send({ intent: "check_connection", flight_aliases: [id] });
  };

  /** One question to the grounded answer path: code classifies it, the ledger answers it, a model may only phrase it. */
  const ask = useCallback(async (question: string, aviation = false): Promise<string> => {
    if (working.current || ended.current) return "";
    if (!jid && !aviation) return t("answer.nothing_yet");
    working.current = true;
    setThinking(true);
    setWork({ steps: [], text: "", aviation });
    let requestHelp = false;
    try {
      // Public flight lookups use an authenticated session without starting a connection.
      const id = jid ?? lookup.current ?? await api.createJourney({ language: lang });
      if (ended.current) {
        void api.closeJourney(id).catch(() => {});
        return "";
      }
      if (!jid) lookup.current = id;
      // the agent works in the open: steps as it reads each card, the check on its own draft, then the words
      const streamed = await api.askStream(id, question, lang, (event, data) => {
        if (ended.current) return;
        if (event === "step") {
          const step = data as unknown as { id: string; state: "running" | "done" | "refused"; target?: string };
          if (step.target && step.state === "running") setFocus(step.target as CardKey);
          setWork((w) => w && { ...w, steps: [...w.steps.filter((s) => s.id !== step.id), { id: step.id, state: step.state }] });
        } else if (event === "token") setWork((w) => w && { ...w, text: w.text + String((data as { text: string }).text) });
      });
      if (ended.current) return "";
      setWork(null);
      const reply = streamed ?? await api.ask(id, question, lang);
      if (ended.current) return "";
      setTrace(reply.trace);
      const focusKey = FOCUS_FOR_KIND[reply.kind];
      if (!jid && reply.kind === "transfer") setAwaitingTransferFlight(true);
      addAgent(reply.answer, { focus: focusKey, source: aviation ? undefined : reply.source, trace: reply.trace, citations: reply.citations });
      requestHelp = reply.kind === "help";
      return reply.answer;
    } catch (cause) {
      if (!jid && cause instanceof ApiError && [401, 403, 410].includes(cause.status)) closeLookup();
      fail(cause);
      return "";
    } finally {
      working.current = false;
      setThinking(false);
      setWork(null);
      if (requestHelp && !ended.current) onAssist();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jid, lang, view]);

  /** What the passenger said that no command matched: start with it, answer the open question with it, or ask it. */
  const sendText = async (text: string) => {
    setTouched(true);
    setChatOpen(true);
    if (isAirportMapRequest(text)) {
      addAgent(t("kk.official_map_answer"), { citations: [{ title: t("kk.official_map"), url: OFFICIAL_MAP }], focus: "map" });
      return;
    }
    if (isAviationLookup(text)) { await ask(text, true); return; }
    if (!jid) {
      const onward = awaitingTransferFlight ? readTransferFlight(text) : "";
      if (onward) { await ask(`Flight status ${onward} departing DXB`, true); return; }
      // plain words about a trip ("landed from Kolkata, need Madrid"): say what was understood and what the schedule shows
      setThinking(true);
      setWork({ steps: [{ id: "understand", state: "running" }], text: "", aviation: false });
      const trip = await api.understandTrip(text, lang).catch(() => null);
      setWork(null);
      setThinking(false);
      if (trip?.understood && trip.live && trip.lines.length) { addAgent(`${trip.lines.join(" ")} ${t("trip.pick")}`); return; }
      await startJourney({ text });
      return;
    }
    const { flights, question } = readPassengerPrompt(text);
    if (asking && (flights.length > 0 || !question)) { await send({ text }); return; }
    await ask(text);
  };

  // ---------- voice and typing share one path: both become an utterance
  const [vocab, setVocab] = useState<{ terms: string[]; flights: KnownFlight[] }>({ terms: [], flights: [] });
  useEffect(() => { api.voiceVocabulary(lang).then(setVocab).catch(() => {}); }, [lang]);
  const utter = useRef<(u: Utterance & { typed?: boolean }) => void>(() => {});
  const voice = useVoice({ lang, terms: () => vocab.terms, onUtterance: (u) => { setTouched(true); addUser(u.text); setChatOpen(true); utter.current(u); } });
  useEffect(() => { say.current = (text) => { void voice.say(text); }; }, [voice]);

  const placeNodes = (map.layout?.nodes ?? []).filter((n) => ["gate", "lift", "train", "cafe", "exit", "hall"].includes(n.kind));
  const places: Place[] = placeNodes.map((n) => {
    const full = r(n.landmark);
    return { id: n.id, names: [full, `${t(`map.kind.${n.kind}`)} ${n.concourse}`, n.kind === "gate" ? n.id.toUpperCase() : "", n.kind === "cafe" ? full.split(/[ ,]/)[0] : ""].filter(Boolean) };
  });
  const parseAsking = next?.action === "confirm_date" ? "date" : next?.action === "choose_landmark" ? "landmark"
    : next?.action === "choose_flight" ? "flight" : next?.action === "confirm_pickup" ? "pickup" : null;
  const speech = useVoiceActions({
    lang, journey: !!jid, awaitingTransferFlight, asking: parseAsking, places, flights: vocab.flights,
    choices: (next?.choices ?? []).map((c) => ({ id: c.id, label: r(c.label) })),
    voice, t,
    actions: {
      startJourney: (a) => {
        setTouched(true);
        void startJourney({ intent: "check_connection", flight_aliases: [a.inbound, a.outbound].filter(Boolean) }, a);
      },
      changeFlights: (flights) => { setLegs({ inbound: flights[0] ?? "", outbound: flights[1] ?? "" }); void send({ intent: "check_connection", flight_aliases: flights }); },
      prefill: () => setChatOpen(true),
      choose, chooseOrigin: (id) => void send({ confirmed_origin_id: id }),
      setPosition: (id) => map.place({ node_id: id }),
      sendText: (text) => void sendText(text),
      ask: (q) => ask(t(`agent.ask.${q}`)),
      assist: onAssist,
      openRoute: () => setFocus("route"),
      end, urgent: () => setDialog("help"), browse: () => setDialog("browse"),
      locate: () => setFocus("map"), setLang,
    },
  });
  useEffect(() => { utter.current = speech.onUtterance; }, [speech.onUtterance]);
  const typed = (text: string) => {
    if (ended.current) return;
    setTouched(true);
    addUser(text);
    setChatOpen(true);
    // never ignore a message in silence: if the last one is still being worked on, say so
    if (working.current) { addAgent(t("kk.busy")); return; }
    utter.current({ text, confidence: 1, typed: true });
  };

  useEffect(() => { if (voice.busy || voice.speaking) keep(); }, [voice.busy, voice.speaking, keep]);

  // The canvas follows the conversation: the card being talked about comes forward.
  useEffect(() => {
    if (!focus) return;
    const el = document.getElementById(`card-${focus}`);
    el?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    const id = setTimeout(() => setFocus(null), 5200);
    return () => clearTimeout(id);
  }, [focus]);

  const lost = !!jid && (down || !health.reachable);
  const greeting: ChatMessage = useMemo(() => ({ id: "greeting", role: "agent", text: t("kk.greeting") }), [t]);
  const suggestions = awaitingTransferFlight ? [] : !jid ? (mode === "demo" ? [t("kk.suggest.1"), t("kk.suggest.3")] : [t("kk.suggest.3")])
    : view && !asking ? [t("agent.ask.time"), t("agent.ask.gate"), t("agent.ask.why")] : [];
  const prepare = (text?: string) => {
    setTouched(true);
    if (text !== undefined) setDraft(text);
    setChatOpen(true);
  };
  const mapLink = <ButtonLink variant="secondary" trailingIcon={RiExternalLinkLine} className="h-12 shrink-0 rounded-xl px-3"
    href={OFFICIAL_MAP} target="_blank" rel="noopener noreferrer">{t("kk.official_map")}</ButtonLink>;
  const action: IslandAction | null = asking && next?.action === "choose_landmark" && hereNode
    ? { label: t("kk.use_here_short"), onClick: () => here && void send({ confirmed_origin_id: here }) }
    : view?.route && !asking ? { label: t("kk.show_route"), onClick: () => setFocus("route") } : null;

  /** Three families on the same tight connection, one 4-seat buggy: each one is a real journey with its own help request. */
  const runBuggyDemo = async () => {
    const families = [
      { booking: "BK-FAM1", needs: { party_size: 2, wheelchair: true, step_free: true }, service: "wheelchair" },
      { booking: "BK-ELD1", needs: { party_size: 1, elderly: 1, step_free: true }, service: "mobility_escort" },
      { booking: "BK-FAM2", needs: { party_size: 2, children: 1, step_free: true }, service: "mobility_escort" },
    ];
    for (const family of families) {
      const id = await api.createJourney({ language: lang, booking_ref: family.booking, needs: family.needs });
      await api.sendInput(id, { intent: "check_connection", flight_aliases: ["DXB-402", "ZX515"], operation_date: "2026-11-19", confirmed_origin_id: "c22" });
      let revision = 0;
      for (let i = 0; i < 25 && !revision; i++) {
        const polled = await api.poll(id).catch(() => null);
        if (polled?.view?.route || polled?.view?.next_action.action === "confirm_pickup") revision = polled.revision;
        else await new Promise((resolve) => setTimeout(resolve, 400));
      }
      if (revision) await api.assist(id, { revision, service: family.service, pickup: "c22" });
    }
  };

  const pickBoard = (code: string) => { setChatOpen(true); typed(jid ? t("kk.board_pick", { code }) : t("kk.board_start", { code })); };
  const onPickFlight = (code: string) => { setDialog(null); pickBoard(code); };

  return (
    <div className="kk-stage">
      <Topbar onHelp={() => setDialog("help")} onEnd={end} onJudge={() => setDialog("judge")} judgeOpen={dialog === "judge"} inSession={inSession} offline={!health.internet || !health.reachable} />
      <main className="kk-canvas" data-chat={chatOpen ? "open" : "closed"}>
        <div className="kk-wrap flex flex-col gap-4">
          {(down || !health.reachable) && <p role="alert" className="flex items-center gap-2 rounded-2xl bg-[var(--wait-soft)] px-4 py-3 font-semibold text-[var(--wait)]"><RiAlertLine className="size-5" aria-hidden />{t("kiosk.service_down")}</p>}
          {view?.banners.map((b) => <p key={b.kind} role="status" className="flex items-center gap-2 rounded-2xl bg-[var(--wait-soft)] px-4 py-3 font-semibold text-[var(--wait)]"><RiAlertLine className="size-5" aria-hidden />{r(b.text)}</p>)}

          {!jid || !view ? (
            <>
              <section className="kk-rise px-1 pt-2 pb-1">
                <h1 className="text-[2.5rem] leading-[1.05] font-bold sm:text-[3.25rem]" style={{ fontFamily: "var(--font-display)", letterSpacing: "-0.01em" }}>{t("kk.hello")}</h1>
                <p className="mt-2 max-w-xl text-headline-regular text-[var(--ink-2)]">{t("kk.hello_sub")}</p>
                {!jid && (
                  <div className="mt-4 flex flex-wrap gap-2" aria-label={t("kk.quick_actions")}>
                    <Button leadingIcon={RiChat3Line} className="h-14 rounded-xl px-4" onClick={() => prepare()}>{t("kk.ask")}</Button>
                    <Button variant="secondary" leadingIcon={RiFlightTakeoffLine} className="h-14 rounded-xl px-4"
                      onClick={() => prepare(t("kk.example_flight"))}>{t("kk.quick_flight")}</Button>
                    <Button variant="secondary" leadingIcon={RiMapPinLine} className="h-14 rounded-xl px-4"
                      onClick={() => prepare(t("kk.example_airport"))}>{t("kk.quick_airport")}</Button>
                    <Button variant="secondary" leadingIcon={RiRouteLine} className="h-14 rounded-xl px-4"
                      onClick={() => prepare(t("kk.example_connections", { when: DXB_DATE.format(Date.now() + 86_400_000) }))}>{t("kk.quick_connections")}</Button>
                  </div>
                )}
                {jid && <p className="mt-4 text-headline-medium text-text-secondary" role="status">{t("agent.checking")}</p>}
              </section>
              <div className="kk-grid">
                <Card id="map" index={1} focus={focus === "map"} className="kk-span-6" label={t("kk.card.map")}>
                  <CardHead title={t("kk.card.map")} sub={t("kk.map.sub")} aside={mapLink} />
                  <DubaiMap layout={map.layout} live={map.live} position={map.position} view={null} onPlace={(i) => map.place(i)} walk={{ playing: walker.playing, can: walker.canWalk, toggle: walker.toggle }} />
                </Card>
                <div className="kk-span-3"><BoardCard onPick={pickBoard} index={2} /></div>
                <div className="kk-span-3"><AircraftCard active index={3} /></div>
              </div>
            </>
          ) : (
            <div className="kk-grid">
              <div className="kk-span-6"><VerdictHero view={view} legs={legs} focus={focus === "gate"} onRoute={() => setFocus("map")} onAssist={onAssist}
                walk={{ playing: walker.playing, can: walker.canWalk, toggle: walker.toggle, speed: walkSpeed, setSpeed: setWalkSpeed, step: map.live?.route?.progress?.reached_steps ?? null, total: map.live?.route?.progress?.total_steps ?? null }} /></div>
              <Card id="map" index={1} focus={focus === "map"} className="kk-span-6" label={t("kk.card.map")}>
                <CardHead title={t("kk.card.map")} sub={t("kk.map.sub_route")} aside={mapLink} />
                <DubaiMap layout={map.layout} live={map.live} position={map.position} view={view} onPlace={(i) => map.place(i)} walk={{ playing: walker.playing, can: walker.canWalk, toggle: walker.toggle }} />
              </Card>
              <div className="kk-span-3"><LedgerCard view={view} focus={focus === "ledger"} /></div>
              <div className="kk-span-3"><AssistCard a={view.assistance} focus={focus === "assist"} onRequest={onAssist} requests={view.requests}
                onCancel={(caseRef) => { void api.sendInput(jid!, { cancel_case_ref: caseRef }, view.revision).then(nudge).catch(() => {}); }} /></div>
              <div className="kk-span-6"><RouteCard view={view} live={map.live} focus={focus === "route"} /></div>
              <div className="kk-span-6"><AircraftCard active index={5} /></div>
            </div>
          )}
          <p className="px-2 pt-2 pb-1 text-center text-sm text-[var(--ink-3)]">{t("system.simulated_note")}</p>
        </div>
      </main>

      <Island open={chatOpen} onOpen={(open) => { if (open) setTouched(true); setChatOpen(open); }} messages={[greeting, ...messages]} thinking={thinking} work={work} suggestions={suggestions} voice={voice}
        placeholder={awaitingTransferFlight ? t("kk.transfer_placeholder") : t("kk.placeholder")}
        draft={draft} onDraft={setDraft} activeQuestion={questionKey} confirm={speech.confirm} attention={asking} action={action} onSend={typed}
        onChoose={(m, c) => { if (!working.current && m.key === questionKey) { addUser(c.label); choose(c.id); } }}
        onReveal={(card) => { setChatOpen(false); setFocus(card); }}
        onTrace={(m) => { if (m.trace) setTrace(m.trace); setDialog("judge"); }} />

      <HelpModal open={dialog === "help"} onClose={() => setDialog(null)} />
      <BrowseModal open={dialog === "browse"} onClose={() => setDialog(null)} onPick={onPickFlight} />
      <JudgeSheet open={dialog === "judge"} onClose={() => setDialog(null)} jid={jid} view={view} trace={trace} status={health.status}
        demoMode={mode === "demo"} onSay={(text) => { setDialog(null); typed(text); }} onBuggy={runBuggyDemo} />
      <IdleModal open={inSession && idle >= IDLE_WARN && !lost} remaining={Math.max(0, IDLE_END - idle)} total={IDLE_END - IDLE_WARN} onKeep={keep} onEnd={end} />
      {pickup && (
        <AssistModal open pickupLabel={r(pickup.label)} error={assistError} onClose={() => setPickup(null)}
          onSubmit={async (service) => {
            if (!jid || !view) return false;
            try {
              await api.assist(jid, { revision: view.revision, service, pickup: pickup.id });
              setPickup(null); setAssistError(""); nudge(); setFocus("assist");
              return true;
            } catch (cause) {
              setAssistError(cause instanceof ApiError && cause.reason ? t("reason." + cause.reason) : t("ui.error_generic"));
              return false;
            }
          }} />
      )}
    </div>
  );
}
