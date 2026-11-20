"use client";

import { useEffect, useRef, useState } from "react";
import { RiArrowRightUpLine, RiArrowUpLine, RiCloseLine, RiFlightTakeoffLine, RiShieldCheckLine } from "@remixicon/react";
import { motion, useReducedMotion } from "motion/react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Spinner } from "@/components/base/spinner/spinner";
import type { Question } from "@/lib/agent-log";
import { useI18n } from "@/lib/i18n";
import { useDismissOnOutsidePress } from "@/utils/use-dismiss-on-outside-press";

const QUESTIONS: Question[] = ["gate", "time", "why"];
const EASE = [0.22, 1, 0.36, 1] as const;

function DotMark() {
  return <span className="dock-dot-mark" aria-hidden="true">{Array.from({ length: 9 }, (_, index) => <i key={index} />)}</span>;
}

interface DockProps {
  open: boolean;
  busy: boolean;
  canAsk: boolean;
  error: string;
  onOpen: (open: boolean) => void;
  onSend: (text: string) => Promise<boolean>;
  onAsk: (question: Question) => void;
  onBrowse: () => void;
}

// The shared surface follows 21st.dev's morphing popover pattern with Motion
// layout projection. Panel size changes immediately and the transform does the work.
export function AgentDock({ open, busy, canAsk, error, onOpen, onSend, onAsk, onBrowse }: DockProps) {
  const { t } = useI18n();
  const reduce = useReducedMotion();
  const dock = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const restore = useRef(false);
  const [draft, setDraft] = useState("");
  const [occluded, setOccluded] = useState(0);

  useDismissOnOutsidePress(open, () => onOpen(false), [dock]);
  useEffect(() => {
    if (open) {
      const target = window.matchMedia("(pointer: fine)").matches ? input : heading;
      target.current?.focus({ preventScroll: true });
    } else if (restore.current) {
      restore.current = false;
      trigger.current?.focus({ preventScroll: true });
    }
  }, [open]);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const update = () => {
      const distance = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
      setOccluded(viewport.scale < 1.1 && distance > 80 ? distance : 0);
    };
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
    };
  }, []);

  const collapse = () => {
    restore.current = true;
    onOpen(false);
  };
  const transition = { duration: reduce ? 0 : 0.38, ease: EASE };

  return (
    <motion.div layoutRoot className="dock-position" data-keyboard={occluded && !open ? "covered" : undefined}
      style={{ insetBlockEnd: occluded && open ? occluded + 8 : undefined }}>
      <motion.section
        ref={dock}
        aria-label={t("ui.ask_skyassist")}
        layout={!reduce}
        transition={transition}
        style={{ borderRadius: open ? 20 : 40 }}
        className="agent-dock"
        data-expanded={open}
        onKeyDown={(event) => { if (open && event.key === "Escape" && !event.defaultPrevented) { event.stopPropagation(); collapse(); } }}
      >
        {open ? (
          <motion.div layout={reduce ? false : "position"} transition={transition} className="dock-composer" id="dock-composer">
            <div className="dock-heading">
              <h2 ref={heading} tabIndex={-1} className="flex items-center gap-3 text-title-3-semibold"><DotMark />{t("ui.ask_skyassist")}</h2>
              <Button variant="ghost" iconOnly leadingIcon={RiCloseLine} className="dock-action size-14" aria-label={t("workspace.collapse")} onClick={collapse} />
            </div>
            {canAsk && (
              <div className="dock-suggestions">
                {QUESTIONS.map((question) => (
                  <Button variant="ghost" key={question} className="dock-suggestion" onClick={() => onAsk(question)} disabled={busy}>
                    {t("agent.ask." + question)}
                  </Button>
                ))}
              </div>
            )}
            <form className="dock-form" onSubmit={async (event) => {
              event.preventDefault();
              if (!busy && draft.trim() && await onSend(draft.trim())) setDraft("");
            }}>
              <Input ref={input} aria-label={t("kiosk.ask_placeholder")} placeholder={t(canAsk ? "kiosk.ask_placeholder" : "workspace.dock_hint")}
                value={draft} onChange={setDraft} autoComplete="off" spellCheck="false" maxLength={160}
                enterKeyHint="send" fieldClassName="dock-input" />
              <Button type="submit" variant="primary" iconOnly leadingIcon={busy ? undefined : RiArrowUpLine} aria-label={t("workspace.send")}
                className="dock-send size-14" disabled={busy || !draft.trim()}>{busy && <Spinner size="xs" />}</Button>
            </form>
            <p className="dock-note text-body-2-medium"><RiShieldCheckLine className="size-4 shrink-0" aria-hidden />{t("workspace.dock_private")}</p>
            {error && <p role="alert" className="dock-error text-body-medium">{error}</p>}
          </motion.div>
        ) : (
          <motion.div layout={reduce ? false : "position"} transition={transition} className="dock-closed">
            <Button ref={trigger} variant="ghost" trailingIcon={RiArrowRightUpLine} className="dock-action dock-ask"
              aria-expanded={false} aria-controls="dock-composer" onClick={() => onOpen(true)}>
              <span className="flex items-center gap-3"><DotMark />{t("ui.ask_skyassist")}</span>
            </Button>
            <span className="dock-divider" aria-hidden />
            <Button variant="ghost" leadingIcon={RiFlightTakeoffLine} className="dock-action dock-flights" onClick={onBrowse}>
              {t("workspace.flights")}
            </Button>
          </motion.div>
        )}
      </motion.section>
    </motion.div>
  );
}
