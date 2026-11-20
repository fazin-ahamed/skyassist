import type { AnswerSource, AskTrace, Citation } from "@/lib/types";

export type CardKey = "gate" | "map" | "ledger" | "route" | "assist" | "board" | "aircraft";

export interface WorkStep { id: string; state: "running" | "done" | "refused" }
export interface Work { steps: WorkStep[]; text: string; aviation: boolean }

export interface ChatChoice { id: string; label: string }
export interface ChatMessage {
  id: string;
  role: "agent" | "user";
  text: string;
  error?: boolean;
  /** Tappable answers to the current question. */
  choices?: ChatChoice[];
  /** The card on the canvas this message is about. The canvas brings it forward. */
  focus?: CardKey;
  source?: AnswerSource;
  citations?: Citation[];
  trace?: AskTrace;
  /** Why the agent is asking, so the same question is not printed twice. */
  key?: string;
}

let counter = 0;
export const messageId = () => `m${Date.now().toString(36)}${(counter++).toString(36)}`;

export const FOCUS_FOR_KIND: Record<string, CardKey | undefined> = {
  gate: "gate", time: "gate", route: "route", why: "ledger", reassure: "ledger", help: "assist", urgent: "assist",
};
