"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type PointerEvent } from "react";
import {
  RiAlertLine, RiArrowDownSLine, RiArrowRightUpLine, RiCheckLine, RiCloseLine, RiExternalLinkLine, RiKeyboardLine, RiMicLine,
  RiMicOffLine, RiSendPlane2Fill, RiShieldCheckLine, RiStopFill, RiVolumeMuteLine, RiVolumeUpLine,
} from "@remixicon/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Button, ButtonLink } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Spinner } from "@/components/base/spinner/spinner";
import { ScrollFade, useScrollFade } from "@/components/foundations/scroll-fade/scroll-fade";
import type { VoiceApi } from "@/hooks/use-voice";
import type { CardKey, ChatChoice, ChatMessage, Work } from "@/lib/chat";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/utils/cx";

export interface IslandConfirm { lines: string[]; onYes: () => void; onNo: () => void }
export interface IslandAction { label: string; onClick: () => void }

interface Props {
  open: boolean;
  onOpen: (open: boolean) => void;
  draft: string;
  placeholder: string;
  onDraft: (draft: string) => void;
  messages: ChatMessage[];
  thinking: boolean;
  work: Work | null;
  suggestions: string[];
  voice: VoiceApi;
  confirm: IslandConfirm | null;
  attention: boolean;
  activeQuestion?: string;
  action: IslandAction | null;
  onSend: (text: string) => void;
  onChoose: (message: ChatMessage, choice: ChatChoice) => void;
  onTrace: (message: ChatMessage) => void;
  onReveal: (card: CardKey) => void;
}

const morph = { duration: 0.28, ease: [0.22, 1, 0.36, 1] } as const;
const HOLD_MS = 450;
const VOICE_ERRORS: Record<string, string> = {
  mic_denied: "voice.error.mic_denied", mic_missing: "voice.error.mic_missing", busy: "voice.error.busy", generic: "voice.error.generic",
  unreachable: "voice.unavailable.offline", not_configured: "voice.unavailable.not_configured", key_rejected: "voice.unavailable.key_rejected",
};

function isSecureLink(url: string) {
  try { return new URL(url).protocol === "https:"; } catch { return false; }
}

function Bars({ level, speech }: { level: number; speech: boolean }) {
  const reduce = useReducedMotion();
  return (
    <div className="flex h-6 items-center gap-1" aria-hidden>
      {Array.from({ length: 14 }, (_, i) => {
        const shape = 0.35 + 0.65 * Math.abs(Math.sin(i * 0.85 + level * 6));
        const height = reduce ? 6 + level * 10 : 4 + level * 20 * shape;
        return <span key={i} className={cx("w-1 rounded-full", speech ? "bg-text-primary" : "bg-foreground-icon-tertiary")} style={{ height }} />;
      })}
    </div>
  );
}

function Confirmation({ confirm }: { confirm: IslandConfirm }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-border-button-default bg-background-primary-default p-4" role="group" aria-label={t("voice.confirm.title")}>
      <p className="text-body-medium text-text-secondary">{t("voice.confirm.title")}</p>
      {confirm.lines.map((line, i) => <p key={i} className="text-headline-medium text-text-primary" dir="auto">{line}</p>)}
      <div className="flex flex-wrap gap-2">
        <Button className="h-14 rounded-xl px-4" onClick={confirm.onYes}>{t("voice.confirm.yes")}</Button>
        <Button variant="secondary" className="h-14 rounded-xl px-4" onClick={confirm.onNo}>{t("voice.confirm.no")}</Button>
      </div>
    </div>
  );
}

function WorkProgress({ work }: { work: Work }) {
  const { t } = useI18n();
  return (
    <div className="flex max-w-full flex-col gap-2 self-start">
      {work.steps.length > 0 && (
        <ul className="flex flex-col gap-2 rounded-2xl border border-border-button-default bg-background-primary-default px-4 py-3 text-body-medium">
          {work.steps.map((step) => {
            const key = work.aviation && step.id === "search" ? "kk.flight_data" : "live.step." + (step.state === "refused" ? "refused" : step.id);
            const label = t(key);
            return (
              <li key={step.id} className="flex items-center gap-2">
                {step.state === "running" ? <Spinner size="xs" /> : step.state === "refused"
                  ? <RiAlertLine className="size-4 shrink-0 text-status-orange-text" aria-hidden />
                  : <RiCheckLine className="size-4 shrink-0 text-status-lime-text" aria-hidden />}
                <span className={step.state === "running" ? "text-text-primary" : "text-text-secondary"}>
                  {label === key ? t("kk.thinking") : label}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {work.text && <p className="kk-bubble whitespace-pre-wrap rounded-[1.25rem] bg-background-primary-default text-headline-regular" dir="auto" aria-hidden>{work.text}</p>}
    </div>
  );
}

export function Island({ open, onOpen, draft, placeholder, onDraft, messages, thinking, work, suggestions, voice, confirm, attention,
  activeQuestion, action, onSend, onChoose, onTrace, onReveal }: Props) {
  const { t } = useI18n();
  const reduce = useReducedMotion();
  const panelId = useId();
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const restore = useRef(false);
  const followTail = useRef(true);
  const pressAt = useRef(0);
  const pressed = useRef(false);
  const fade = useScrollFade();
  const [keyboardInset, setKeyboardInset] = useState(0);
  const blocked = voice.unavailable !== null;
  const mode = open ? "chat" : confirm ? "confirm" : voice.busy ? "listening" : voice.error || voice.notice ? "notice" : "rest";
  const busy = thinking || voice.busy;

  useEffect(() => {
    if (!voice.error && !voice.notice) return;
    const timer = setTimeout(voice.clearError, 7000);
    return () => clearTimeout(timer);
  }, [voice.error, voice.notice, voice.clearError]);

  useEffect(() => {
    if (open) {
      const target = window.matchMedia("(pointer: fine)").matches ? input.current : heading.current;
      target?.focus({ preventScroll: true });
    } else if (restore.current) {
      restore.current = false;
      opener.current?.focus({ preventScroll: true });
    }
  }, [open]);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!open || !viewport) return;
    const resize = () => setKeyboardInset(Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop));
    viewport.addEventListener("resize", resize);
    viewport.addEventListener("scroll", resize);
    return () => {
      viewport.removeEventListener("resize", resize);
      viewport.removeEventListener("scroll", resize);
    };
  }, [open]);

  useEffect(() => {
    if (followTail.current && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [messages.length, open, work, thinking, confirm]);

  const close = () => {
    restore.current = true;
    followTail.current = true;
    fade.reset();
    onOpen(false);
  };
  const down = (event: PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || blocked || thinking) return;
    pressed.current = true;
    pressAt.current = Date.now();
    if (!voice.busy) void voice.start();
  };
  const up = () => {
    if (!pressed.current) return;
    pressed.current = false;
    if (Date.now() - pressAt.current > HOLD_MS && voice.listening) voice.stop();
  };
  const toggleMic = () => {
    if (blocked) { onOpen(true); return; }
    if (pressed.current || Date.now() - pressAt.current < 400) return;
    if (voice.busy) voice.stop(); else void voice.start();
  };
  const send = (text: string) => {
    if (busy || !text.trim()) return;
    followTail.current = true;
    onSend(text.trim());
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy || !draft.trim()) return;
    send(draft);
    onDraft("");
  };
  const notice = voice.error ? t(VOICE_ERRORS[voice.error] ?? "voice.error.generic")
    : voice.notice === "didnt_catch" ? t("voice.didnt_catch") : voice.notice ? t("voice.nothing_heard") : "";
  const unavailable = voice.unavailable ? t("voice.unavailable." + voice.unavailable) : "";
  const lastAgent = messages.findLast((message) => message.role === "agent");
  const question = activeQuestion ? messages.findLast((message) => message.key === activeQuestion && message.choices?.length) : undefined;
  const micProps = { onPointerDown: down, onPointerUp: up, onPointerCancel: up, onClick: toggleMic };
  const micLabel = blocked ? unavailable : t("voice.mic");
  const listening = (
    <div className="flex min-w-0 flex-1 flex-col gap-1" role="status">
      <p className="truncate text-headline-medium text-text-primary" dir="auto">
        {voice.caption || t(voice.phase === "connecting" ? "voice.connecting" : voice.phase === "stopping" ? "voice.thinking" : "voice.listening")}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Bars level={voice.meter?.level ?? 0} speech={!!voice.meter?.speech} />
        <span className="text-body-2-medium text-text-secondary">{t("voice.noise." + voice.noise)}</span>
      </div>
      {voice.closer && <p className="text-body-2-medium text-status-orange-text">{t("voice.noise.closer")}</p>}
    </div>
  );

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
      style={open && keyboardInset > 0 ? { bottom: keyboardInset } : undefined}>
      <motion.div layout={!reduce} transition={morph} style={{ borderRadius: mode === "chat" ? 24 : 36 }}
        role="region" aria-label={t("kk.ask")}
        className={cx("kk-island dark pointer-events-auto max-w-full", mode === "chat" ? "w-[min(100%,46rem)]" : "w-auto")}>
        <AnimatePresence initial={false} mode="popLayout">
          {mode === "rest" && (
            <motion.div key="rest" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex max-w-full items-center gap-1.5 p-2">
              <Button variant={blocked ? "secondary" : "primary"} iconOnly leadingIcon={blocked ? RiMicOffLine : RiMicLine}
                className="size-14 shrink-0 rounded-full" aria-label={micLabel} title={blocked ? unavailable : t("voice.press_hint")} disabled={thinking} {...micProps} />
              <Button ref={opener} variant="ghost" className="h-14 min-w-0 justify-start rounded-xl bg-transparent px-2 text-start hover:bg-background-tertiary-default [&>span]:min-w-0 [&>span]:shrink"
                aria-expanded={false} aria-controls={panelId} onClick={() => { followTail.current = true; onOpen(true); }}>
                <span className="flex min-w-0 flex-col items-start gap-0.5">
                  <span className="text-headline-semibold text-text-primary">{t("kk.ask")}</span>
                  <span className="max-w-48 truncate text-body-2-medium text-text-secondary" dir="auto">
                    {thinking ? t("kk.thinking") : attention ? t("agent.asks") : lastAgent?.text ?? t("kk.ask_hint")}
                  </span>
                </span>
              </Button>
              {action ? (
                <Button variant="secondary" className="me-1 h-12 max-w-28 shrink-0 rounded-full px-2" onClick={action.onClick} disabled={thinking}>{action.label}</Button>
              ) : (
                <Button variant="secondary" iconOnly leadingIcon={RiKeyboardLine} className="me-1 size-12 shrink-0 rounded-full"
                  aria-label={t("kk.open_chat")} aria-expanded={false} aria-controls={panelId} onClick={() => { followTail.current = true; onOpen(true); }} />
              )}
            </motion.div>
          )}

          {mode === "listening" && (
            <motion.div key="listening" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex w-[min(94vw,36rem)] items-center gap-3 p-3">
              <Button iconOnly leadingIcon={RiStopFill} className="size-14 shrink-0 rounded-full" aria-label={t("voice.mic_busy")} onPointerUp={up} onClick={voice.stop} />
              {listening}
            </motion.div>
          )}

          {mode === "confirm" && confirm && (
            <motion.div key="confirm" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="w-[min(94vw,32rem)] p-3">
              <Confirmation confirm={confirm} />
            </motion.div>
          )}

          {mode === "notice" && (
            <motion.div key="notice" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex w-[min(94vw,32rem)] items-center gap-3 p-3" role="alert">
              <RiAlertLine className="ms-1 size-5 shrink-0 text-status-orange-text" aria-hidden />
              <p className="flex-1 text-headline-medium">{notice}</p>
              <Button variant="secondary" iconOnly leadingIcon={RiCloseLine} className="size-11 rounded-full" aria-label={t("agent.dismiss")} onClick={voice.clearError} />
            </motion.div>
          )}

          {mode === "chat" && (
            <motion.div key="chat" id={panelId} initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="kk-chat flex flex-col" style={keyboardInset > 0 ? { maxHeight: Math.max(180, window.innerHeight - keyboardInset - 24) } : undefined}
              onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } }}>
              <header className="flex shrink-0 items-center justify-between gap-3 border-b border-separator-border px-3 py-2 sm:px-5">
                <div className="min-w-0">
                  <h2 ref={heading} tabIndex={-1} className="text-title-3-semibold text-text-primary outline-none">{t("kk.chat.title")}</h2>
                  <p role="status" className="text-body-2-medium text-text-secondary">
                    {thinking ? t("kk.thinking") : attention ? t("agent.asks") : t("kk.chat.subtitle")}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button variant={voice.readAloud ? "primary" : "secondary"} iconOnly leadingIcon={voice.readAloud ? RiVolumeUpLine : RiVolumeMuteLine}
                    className="size-12 rounded-xl" aria-label={t("voice.read_aloud")} aria-pressed={voice.readAloud} onClick={() => voice.setReadAloud(!voice.readAloud)} />
                  <Button variant="secondary" iconOnly leadingIcon={RiArrowDownSLine} className="size-12 rounded-xl"
                    aria-label={t("kk.minimise")} aria-expanded={true} aria-controls={panelId} onClick={close} />
                </div>
              </header>

              <div className="relative min-h-0 flex-1">
                <div ref={list} tabIndex={0} aria-label={t("kk.chat.title")} className="h-full overflow-y-auto overscroll-contain px-3 py-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-border-focus-ring sm:px-5"
                  onScroll={(event) => {
                    const viewport = event.currentTarget;
                    followTail.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80;
                    fade.onScroll(event);
                  }}>
                  <div className="flex flex-col gap-3" role="log" aria-live="polite">
                    {messages.map((message) => {
                      const isUser = message.role === "user";
                      const citations = message.citations?.filter(({ url }) => isSecureLink(url)).slice(0, 3) ?? [];
                      return (
                        <div key={message.id} className={cx("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
                          <p className={cx("kk-bubble whitespace-pre-wrap rounded-[1.25rem] text-headline-regular",
                            isUser ? "rounded-ee-md bg-button-primary text-text-white" : "rounded-es-md bg-background-primary-default text-text-primary",
                            message.error && "border border-border-error-default text-status-orange-text")} dir="auto" role={message.error ? "alert" : undefined}>{message.text}</p>
                          {(citations.length > 0 || message.source || (message.focus && message === lastAgent)) && (
                            <div className="flex flex-wrap items-center gap-1">
                              {citations.map(({ title, url }) => (
                                <ButtonLink key={url} variant="ghost" trailingIcon={RiExternalLinkLine} className="h-11 max-w-full rounded-lg bg-transparent text-body-2-medium text-text-secondary [&>span]:min-w-0 [&>span]:shrink [&>span]:truncate"
                                  href={url} target="_blank" rel="noopener noreferrer">{title}</ButtonLink>
                              ))}
                              {message.source && citations.length === 0 && (
                                <Button variant="ghost" leadingIcon={RiShieldCheckLine} className="h-11 rounded-lg bg-transparent text-body-2-medium text-text-secondary" onClick={() => onTrace(message)}>
                                  {t("kk.source." + message.source)}
                                </Button>
                              )}
                              {message.focus && message === lastAgent && (
                                <Button variant="ghost" trailingIcon={RiArrowRightUpLine} className="h-11 rounded-lg bg-transparent text-body-2-medium text-text-secondary"
                                  onClick={() => onReveal(message.focus!)}>{t("kk.view_card")}</Button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {question?.choices && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {question.choices.map((choice) => (
                        <Button key={choice.id} variant="secondary" className="min-h-14 max-w-full whitespace-normal rounded-xl px-3 text-headline-medium"
                          disabled={busy} onClick={() => { followTail.current = true; onChoose(question, choice); }}>{choice.label}</Button>
                      ))}
                    </div>
                  )}
                  {work && <div className="mt-3"><WorkProgress work={work} /></div>}
                  {thinking && !work?.steps.length && <div className="mt-3 flex items-center gap-2 text-body-medium text-text-secondary" aria-hidden><Spinner size="xs" />{t("kk.thinking")}</div>}
                  {confirm && <div className="mt-3"><Confirmation confirm={confirm} /></div>}
                </div>
                <ScrollFade progress={fade.progress} surfaceClassName="from-background-secondary-default" />
              </div>

              {suggestions.length > 0 && !busy && !confirm && (
                <div className="flex shrink-0 gap-2 overflow-x-auto border-t border-separator-border px-3 pt-2 sm:px-5" aria-label={t("kk.quick_actions")}>
                  {suggestions.map((suggestion) => <Button key={suggestion} variant="secondary" className="h-12 shrink-0 rounded-xl px-3"
                    onClick={() => send(suggestion)}>{suggestion}</Button>)}
                </div>
              )}
              {notice && <p className="px-3 pt-2 text-body-medium text-status-orange-text sm:px-5" role="alert">{notice}</p>}
              <form onSubmit={submit} className="flex shrink-0 items-center gap-2 p-3 sm:px-5">
                {voice.busy ? <div className="flex min-h-14 min-w-0 flex-1 items-center rounded-xl bg-background-primary-default px-3">{listening}</div> : (
                  <Input ref={input} value={draft} onChange={onDraft} maxLength={300} enterKeyHint="send" autoComplete="off" spellCheck="false"
                    placeholder={placeholder} aria-label={placeholder} inputDir="auto"
                    className="min-w-0 flex-1" inputClassName="text-headline-regular" fieldClassName="min-h-14 rounded-xl bg-background-primary-default px-3" />
                )}
                {voice.busy ? (
                  <Button iconOnly leadingIcon={RiStopFill} className="size-14 shrink-0 rounded-xl" aria-label={t("voice.mic_busy")} onClick={voice.stop} />
                ) : draft.trim() ? (
                  <Button type="submit" iconOnly leadingIcon={RiSendPlane2Fill} className="size-14 shrink-0 rounded-xl"
                    aria-label={t("kk.send")} disabled={busy} />
                ) : (
                  <Button variant={blocked ? "secondary" : "primary"} iconOnly leadingIcon={blocked ? RiMicOffLine : RiMicLine}
                    className="size-14 shrink-0 rounded-xl" aria-label={micLabel} disabled={busy || blocked}
                    title={blocked ? unavailable : t("voice.press_hint")} {...micProps} />
                )}
              </form>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
