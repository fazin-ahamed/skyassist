import type { Question } from "@/lib/agent-log";

const FLIGHT = /\b(?:DXB[ -]?\d{1,4}|(?:[A-Z][A-Z0-9]|[0-9][A-Z])[ -]?\d{1,4}[A-Z]?)\b/gi;
const SINGLE_FLIGHT = new RegExp("^(?:" + FLIGHT.source + ")$", "i");
const URGENT = /\b(?:emergency|chest pain|can'?t breathe|not breathing)\b|طوارئ|ألم في الصدر|لا أتنفس/i;
const AVIATION_QUERIES = [
  /\b(?:flight (?:status|info(?:rmation)?)|look up flight)\b|حالة الرحلة|معلومات الرحلة/i,
  /\b(?:status|gate) (?:of|for) (?:flight )?[A-Z0-9]{2}[ -]?\d{1,4}[A-Z]?\b/i,
  /\b(?:connections (?:from|between)|connecting flights|alternate flights|find connections)\b|رحلات متصلة|رحلات مواصلة/i,
];
const AVIATION_CODES = [
  /(?:\bairport(?: (?:code|is|info(?:rmation)?(?: for)?))?|(?:رمز )?مطار)\s+([A-Z]{3,4})(?![\w-])/i,
  /(?:\b(?:airline|carrier)(?: (?:code|is))?|(?:رمز )?شركة الطيران)\s+([A-Z0-9]{2,3})(?![\w-])/i,
  /(?:\baircraft(?: (?:type|code))?|(?:نوع|رمز) الطائرة)\s+([A-Z0-9]{3,4})(?![\w-])/i,
];
const NON_CODES = new Set(["map", "maps", "gate", "link", "code", "type", "info", "the", "can", "all"]);
const AIRPORT_MAP = /^(?:map|airport map|dxb map)[.?!]*$|\b(?:express maps?|(?:official|dubai airport|dxb|terminal) map)\b|\b(?:open|show) (?:me )?(?:the )?(?:airport )?map\b|خريطة مطار دبي|خريطة المطار|الخريطة الرسمية/i;
const TRANSFER_ARRIVAL = /\b(?:landed|arrived|(?:flying|coming) in)\s+from\s+(?=\p{L})/iu;
const TRANSFER_ONWARD = /\b(?:get|go|going|goin|fly|flying|connect|connecting|connection|transfer|transferring|onward)\s+to\s+(?=\p{L})/iu;
const QUESTIONS: [Question | "route", RegExp][] = [
  ["help", /\b(?:help|assist(?:ance)?|wheelchair|mobility escort)\b|مساعدة|كرسي متحرك/i],
  ["why", /\b(?:why|status)\b|لماذا|الحالة/i],
  ["time", /\b(?:time|boarding|cutoff|late|make it)\b|وقت|صعود|متأخر/i],
  ["route", /\b(?:route|directions?|how (?:do i|to) get|way to|step[ -]free|no stairs)\b|مسار|الطريق|سلالم/i],
  ["gate", /\b(?:gate|terminal|concourse)\b|بواب|القاعة|المبنى/i],
];

function digits(text: string) {
  return text.replace(/[٠-٩۰-۹]/g, (digit) => {
    const glyph = digit.charCodeAt(0);
    return String(glyph - (glyph >= 0x06f0 ? 0x06f0 : 0x0660));
  });
}

export function normalizeFlight(text: string) {
  const value = digits(text.trim()).toUpperCase();
  if (!value || !SINGLE_FLIGHT.test(value)) return "";
  return value.startsWith("DXB") ? value.replace(/^DXB[ -]?/, "DXB-") : value.replace(/[ -]/g, "");
}

export function readTransferFlight(text: string) {
  const code = normalizeFlight(text.trim().replace(/^(?:my flight is |flight |it'?s )/i, "").replace(/[.!?]$/, ""));
  return code.startsWith("DXB-") ? "" : code;
}

export function readPassengerPrompt(raw: string) {
  const text = digits(raw);
  const codes = Array.from(text.matchAll(FLIGHT), ([code]) => code)
    .filter((code) => !/^(?:at|in|to|on|is|am|pm) /.test(code));
  const flights = [...new Set(codes.map(normalizeFlight))].filter(Boolean);
  const question = QUESTIONS.find(([, match]) => match.test(text))?.[0];
  return { flights, question, urgent: URGENT.test(text) };
}

/** A flight-data question is distinct from entering the flights of this journey. */
export function isAviationLookup(text: string) {
  const value = digits(text.trim());
  if (URGENT.test(value)) return false;
  if (AVIATION_QUERIES.some((pattern) => pattern.test(value)) || AVIATION_CODES.some((pattern) => {
    const match = pattern.exec(value);
    return !!match && !NON_CODES.has(match[1].toLowerCase());
  })) return true;

  const arrival = TRANSFER_ARRIVAL.exec(value);
  return !!arrival && TRANSFER_ONWARD.test(value.slice(arrival.index + arrival[0].length)) && readPassengerPrompt(value).flights.length < 2;
}

export function isAirportMapRequest(text: string) {
  return !URGENT.test(text) && AIRPORT_MAP.test(text.trim());
}
