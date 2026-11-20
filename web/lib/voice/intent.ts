/**
 * Turns what the recogniser heard into something the kiosk can act on.
 *
 * Pure functions, no framework imports, so Node can test them directly. Airports are loud and speech-to-text is
 * imperfect, so this file does three jobs: it repairs spoken codes ("zed ex five one five" is ZX515), it matches
 * what was said against what the screen is asking, and it says how sure it is. The caller decides what to do
 * with an unsure answer; this file never acts.
 */

export type PartyKey = "standard" | "family" | "wheelchair" | "both";
export type CommandName =
  | "time" | "gate" | "why" | "route" | "assist" | "urgent" | "end" | "yes" | "no" | "repeat"
  | "lang_ar" | "lang_en" | "browse" | "locate";

export interface KnownFlight { code: string; aliases: string[]; direction: "arrival" | "departure" }
export interface Choice { id: string; label: string }
export interface Place { id: string; names: string[] }

export interface ParseContext {
  lang: "en" | "ar";
  /** What the screen is waiting for right now. */
  asking: "flight" | "date" | "landmark" | "pickup" | null;
  choices: Choice[];
  places: Place[];
  flights: KnownFlight[];
  journey: boolean;
}

export type Intent =
  | { kind: "flights"; inbound?: string; outbound?: string; party?: PartyKey; sure: boolean }
  | { kind: "choice"; id: string; label: string }
  | { kind: "place"; id: string; name: string }
  | { kind: "command"; name: CommandName }
  | { kind: "text"; text: string };

const ONES: Record<string, string> = {
  zero: "0", oh: "0", o: "0", nought: "0", one: "1", won: "1", two: "2", too: "2", to: "2", three: "3", tree: "3",
  four: "4", for: "4", fower: "4", five: "5", fife: "5", six: "6", seven: "7", eight: "8", ate: "8", nine: "9", niner: "9",
};
const TEENS: Record<string, string> = {
  ten: "10", eleven: "11", twelve: "12", thirteen: "13", fourteen: "14", fifteen: "15", sixteen: "16",
  seventeen: "17", eighteen: "18", nineteen: "19",
};
const TENS: Record<string, string> = { twenty: "2", thirty: "3", forty: "4", fifty: "5", sixty: "6", seventy: "7", eighty: "8", ninety: "9" };
const NATO: Record<string, string> = {
  alfa: "a", alpha: "a", bravo: "b", charlie: "c", delta: "d", echo: "e", foxtrot: "f", golf: "g", hotel: "h", india: "i",
  juliet: "j", juliett: "j", kilo: "k", lima: "l", mike: "m", november: "n", oscar: "o", papa: "p", quebec: "q", romeo: "r",
  sierra: "s", tango: "t", uniform: "u", victor: "v", whiskey: "w", xray: "x", yankee: "y", zulu: "z",
};
const LETTER_NAMES: Record<string, string> = {
  bee: "b", be: "b", see: "c", sea: "c", cee: "c", dee: "d", gee: "g", aitch: "h", eye: "i", jay: "j", kay: "k", ell: "l",
  em: "m", en: "n", pea: "p", pee: "p", cue: "q", ess: "s", tee: "t", tea: "t", vee: "v", ex: "x", zed: "z", zee: "z", ef: "f", eff: "f",
};
const ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";
const ARABIC_NUMBER_WORDS: Record<string, string> = {
  صفر: "0", واحد: "1", اثنين: "2", اثنان: "2", ثلاثة: "3", ثلاث: "3", أربعة: "4", اربعة: "4", أربع: "4", خمسة: "5", خمس: "5",
  ستة: "6", ست: "6", سبعة: "7", سبع: "7", ثمانية: "8", ثمان: "8", تسعة: "9", تسع: "9", عشرة: "10",
};

type Atom = { t: "L"; v: string; strong: boolean; multi: boolean } | { t: "D"; v: string };

/** Short words that look like carrier codes but are just English. A code never starts or ends on one. */
const STOP = new Set(["it", "is", "at", "to", "go", "in", "on", "of", "or", "my", "me", "we", "an", "as", "be", "by", "do", "he", "if",
  "no", "so", "up", "us", "am", "are", "the", "and", "for", "you", "can", "now", "get", "got", "has", "had", "was", "not", "but",
  "yes", "its", "our", "out", "off", "all", "any", "new", "one", "two", "who", "how", "why", "may", "let", "see", "say", "ask"]);

function fold(text: string): string {
  let out = "";
  for (const ch of text.normalize("NFKC")) {
    const i = ARABIC_DIGITS.indexOf(ch);
    out += i >= 0 ? String(i) : ch;
  }
  return out.toLowerCase().replace(/[-_/]/g, " ").replace(/[^\p{L}\p{N}\s']/gu, " ").replace(/\s+/g, " ").trim();
}

function atomOf(token: string, nextIsDigitish: boolean): Atom | null {
  if (/^\d+$/.test(token)) return { t: "D", v: token };
  if (ARABIC_NUMBER_WORDS[token]) return { t: "D", v: ARABIC_NUMBER_WORDS[token] };
  if (TEENS[token]) return { t: "D", v: TEENS[token] };
  if (TENS[token]) return { t: "D", v: TENS[token] + "0" };
  if (NATO[token]) return { t: "L", v: NATO[token], strong: true, multi: false };
  if (LETTER_NAMES[token]) return { t: "L", v: LETTER_NAMES[token], strong: false, multi: false };
  // "to", "for", "o", "oh", "won" read as digits only when digits are already in play.
  if (["oh", "o", "to", "too", "for", "won", "ate"].includes(token)) return nextIsDigitish ? { t: "D", v: ONES[token] } : null;
  if (ONES[token]) return { t: "D", v: ONES[token] };
  if (/^[a-z]$/.test(token)) return { t: "L", v: token, strong: false, multi: false };
  if (/^[a-z]{2,3}$/.test(token) && !STOP.has(token)) return { t: "L", v: token, strong: false, multi: true };
  const mixed = token.match(/^([a-z]{1,3})(\d{1,4})$/);
  if (mixed) return null; // already compact; handled by the code scanner
  return null;
}

/** Join "twenty" + "two" into 22, and keep single digits as they were said. */
function joinDigits(parts: string[]): string {
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    const next = parts[i + 1];
    if (p.length === 2 && p.endsWith("0") && p[0] !== "1" && next && next.length === 1 && next !== "0") {
      out.push(p[0] + next);
      i++;
    } else out.push(p);
  }
  return out.join("");
}

/**
 * Find codes in speech: runs of letters and digits that read as one token, like b14, c22 or zx515.
 * Returns the cleaned text with each run replaced by its compact form, plus the codes found.
 */
export function compactCodes(raw: string): { text: string; codes: string[] } {
  const words = fold(raw).split(" ").filter(Boolean);
  const out: string[] = [];
  const codes: string[] = [];
  let i = 0;
  while (i < words.length) {
    const compact = words[i].match(/^([a-z]{1,3})(\d{1,4})$/);
    if (compact) { out.push(words[i]); codes.push(words[i]); i++; continue; }
    // "double five" and "triple one" repeat the next digit
    const run: Atom[] = [];
    let j = i;
    while (j < words.length) {
      let w = words[j];
      let repeat = 1;
      if ((w === "double" || w === "triple") && j + 1 < words.length) { repeat = w === "double" ? 2 : 3; w = words[j + 1]; }
      const lookahead = words[j + (repeat > 1 ? 2 : 1)];
      const nextDigitish = !!lookahead && (/^\d+$/.test(lookahead) || !!ONES[lookahead] || !!TEENS[lookahead] || !!TENS[lookahead]);
      const hasDigit = run.some((a) => a.t === "D");
      const a = atomOf(w, nextDigitish || hasDigit);
      if (!a) break;
      if (a.t === "L" && hasDigit) break; // letters come first: a letter after digits starts the next code
      if (repeat > 1) { if (a.t !== "D") break; run.push({ t: "D", v: a.v.repeat(repeat) }); j += 2; continue; }
      run.push(a);
      j++;
    }
    const letters = run.filter((a) => a.t === "L");
    const digits = run.filter((a) => a.t === "D");
    if (run.length >= 2 && letters.length >= 1 && digits.length >= 1 && run[0].t === "L") {
      // letters first, then digits, so "at five" never becomes a code
      const split = run.findIndex((a) => a.t === "D");
      const head = run.slice(0, split);
      const tail = run.slice(split);
      const spelled = head.every((a) => a.t === "L" && !a.multi);
      const carrier = head.length === 1 && head[0].t === "L" && head[0].multi;
      if ((spelled || carrier) && head.every((a) => a.t === "L") && tail.every((a) => a.t === "D")) {
        const letterPart = head.map((a) => a.v).join("");
        const digitPart = joinDigits(tail.map((a) => a.v));
        if (letterPart.length >= 1 && letterPart.length <= 3 && digitPart.length >= 1 && digitPart.length <= 4) {
          const code = letterPart + digitPart;
          out.push(code);
          codes.push(code);
          i = j;
          continue;
        }
      }
    }
    out.push(words[i]);
    i++;
  }
  return { text: out.join(" "), codes };
}

const FLIGHT_SHAPE = /^[a-z]{2,3}\d{2,4}$/;
const MIXED_SHAPE = /^[a-z]\d\d{2,4}$/;
const GATE_SHAPE = /^[a-z]\d{1,2}$/;

const editDistance = (a: string, b: string): number => {
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
    dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  }
  return dp[a.length][b.length];
};

const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

function matchFlight(code: string, flights: KnownFlight[]): { flight: KnownFlight | null; exact: boolean } {
  const c = squash(code);
  for (const f of flights) if ([f.code, ...f.aliases].some((a) => squash(a) === c)) return { flight: f, exact: true };
  // One wrong digit is the usual noisy-room error. Accept it only when exactly one known flight is that close.
  const near = flights.filter((f) => [f.code, ...f.aliases].some((a) => { const s = squash(a); return s.length === c.length && editDistance(s, c) === 1 && s.slice(0, 2) === c.slice(0, 2); }));
  return near.length === 1 ? { flight: near[0], exact: false } : { flight: null, exact: false };
}

const has = (text: string, re: RegExp) => re.test(text);

export function partyFrom(text: string): PartyKey | undefined {
  const t = fold(text);
  const chair = has(t, /\b(wheel ?chair|step free|stepfree|no stairs|mobility|limited mobility|reduced mobility|can'?t (use|climb) (the )?stairs|walk(er|ing frame))\b/) || has(t, /كرسي متحرك|بدون درج|خالي من الدرج/);
  const kids = has(t, /\b(child|children|kids?|baby|babies|toddler|stroller|pram|pushchair|family|infant)\b/) || has(t, /أطفال|طفل|عائلة|رضيع/);
  if (chair && kids) return "both";
  if (chair) return "wheelchair";
  if (kids) return "family";
  return undefined;
}

const COMMANDS: { name: CommandName; en: RegExp; ar?: RegExp }[] = [
  { name: "urgent", en: /\b(emergency|urgent(ly)?|ambulance|medical|doctor|paramedic|police|fire|heart attack|chest pain|can'?t breathe|cannot breathe|collapsed|bleeding|unconscious|faint(ed|ing)?|injured|lost (my )?(child|kid|son|daughter))\b/, ar: /طوارئ|إسعاف|اسعاف|طبيب|شرطة|حريق|نزيف|فقد(ت)? (طفل|ابن|ابنت)/ },
  { name: "yes", en: /^(yes|yeah|yep|yup|correct|that'?s (right|correct)|right|confirm(ed)?|ok(ay)?|sure|go ahead|please do|do it|affirmative)\b/, ar: /^(نعم|أجل|صحيح|تمام|موافق|اكيد|أكيد)/ },
  { name: "no", en: /^(no|nope|nah|wrong|not (right|that|correct)|cancel|never ?mind|try again|incorrect|negative)\b/, ar: /^(لا|خطأ|غلط|الغ|إلغاء|مو)/ },
  { name: "repeat", en: /\b(repeat|say (that )?again|what did you say|come again|pardon)\b/, ar: /كرر|أعد|اعد/ },
  { name: "lang_ar", en: /\b(arabic|in arabic|switch to arabic)\b/, ar: /عربي|العربية/ },
  { name: "lang_en", en: /\b(english|in english|switch to english)\b/, ar: /انجليزي|إنجليزي|الانجليزية|الإنجليزية/ },
  { name: "end", en: /\b((end|finish|close|stop) (the |this |my )?(session|journey)|start (over|again)|sign out|log out|i'?m done|that'?s all)\b/, ar: /إنهاء|انهاء|انتهيت|ابدأ من جديد/ },
  { name: "assist", en: /\b((request|need|want|get|book|order|send|call|arrange|organi[sz]e)\b.{0,24}\b(wheel ?chair|assistance|escort|help getting)|wheel ?chair assistance|mobility assistance|physical assistance|assist me)\b/, ar: /(أحتاج|احتاج|اطلب|أطلب|ارسل|أرسل).{0,20}(كرسي|مساعدة)|مساعدة جسدية/ },
  { name: "time", en: /\b(do i have (enough )?time|how much time|how long (do i|have i|until|before)|am i (going to be |gonna be )?(late|on time)|will i make it|can i make it|time (left|do i have)|how (far|close) (is|are)|enough time|in time|am i (too )?late)\b/, ar: /هل (لدي|عندي) وقت|كم (الوقت|بقي)|هل سأصل|متأخر/ },
  { name: "gate", en: /\b(where('?s| is)( my| the)? gate|which gate|what('?s| is)( my| the)? gate|gate (number|info)|my gate)\b/, ar: /أين بوابتي|اين بوابتي|ما هي بوابتي|رقم البوابة|بوابتي/ },
  { name: "why", en: /\b(why (is|does|did|are)|explain( this| that| the status)?|what does (this|that) mean|what'?s (wrong|the problem))\b/, ar: /لماذا|ماذا يعني|اشرح/ },
  { name: "route", en: /\b(show( me)?( the| my)? (route|map|way|directions)|take me|guide me|navigate|directions|how do i get|walk me|which way|lead me|show me the way|get me to)\b/, ar: /أرني (الطريق|المسار|الخريطة)|وجهني|الاتجاهات|خذني|كيف أصل/ },
  { name: "browse", en: /\b(browse|(show|open|see|list)( me)?( the)? (flights|departures|arrivals|board)|departures board|arrivals board|flight (list|board))\b/, ar: /تصفح|لوحة الرحلات|المغادرة|الوصول/ },
  { name: "locate", en: /\b(where am i|my location|locate me|find me|center (the )?map|centre (the )?map|follow me|where are we|show where i am)\b/, ar: /أين أنا|اين انا|موقعي/ },
];

function commandFor(text: string, lang: "en" | "ar"): CommandName | null {
  const t = fold(text);
  for (const c of COMMANDS) {
    if (c.en.test(t) || (c.ar && c.ar.test(text.normalize("NFKC"))) || (lang === "ar" && c.ar && c.ar.test(t))) return c.name;
  }
  return null;
}

const DAYS: Record<string, string[]> = {
  mon: ["mon", "monday"], tue: ["tue", "tues", "tuesday"], wed: ["wed", "wednesday"], thu: ["thu", "thur", "thurs", "thursday"],
  fri: ["fri", "friday"], sat: ["sat", "saturday"], sun: ["sun", "sunday"],
};
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

function numberFromWords(words: string[]): string | null {
  const atoms = words.map((w) => (/^\d+$/.test(w) ? w : TEENS[w] ?? (TENS[w] ? TENS[w] + "0" : ONES[w] ?? null)));
  if (atoms.some((a) => a === null)) return null;
  return joinDigits(atoms as string[]);
}

const ORDINAL_WORDS: Record<string, number> = {
  first: 1, "1st": 1, second: 2, "2nd": 2, third: 3, "3rd": 3, fourth: 4, "4th": 4,
  الأول: 1, الاول: 1, الثاني: 2, الثالث: 3, الرابع: 4,
};

function matchChoice(text: string, choices: Choice[]): Choice | null {
  if (!choices.length) return null;
  const words = fold(text).split(" ");
  // "the second one", "option two": position beats content
  for (const w of words) if (ORDINAL_WORDS[w] && choices[ORDINAL_WORDS[w] - 1]) return choices[ORDINAL_WORDS[w] - 1];
  const at = words.findIndex((w) => w === "option" || w === "number" || w === "choice");
  if (at >= 0 && words[at + 1]) {
    const n = Number(numberFromWords([words[at + 1]]));
    if (n >= 1 && choices[n - 1]) return choices[n - 1];
  }
  const scored = choices.map((c) => {
    const tokens = new Set(fold(c.label).split(" "));
    let score = 0;
    for (const w of words) {
      if (tokens.has(w) && w.length > 1) score += /^\d/.test(w) ? 3 : 2;
      for (const [k, forms] of Object.entries(DAYS)) if (forms.includes(w) && tokens.has(k)) score += 3;
    }
    const day = numberFromWords(words.filter((w) => /^\d+$/.test(w) || TEENS[w] || TENS[w] || ONES[w]));
    if (day && tokens.has(String(Number(day)))) score += 3;
    for (const m of MONTHS) if (words.some((w) => w.startsWith(m) && w.length >= 3) && tokens.has(m)) score += 2;
    return { c, score };
  }).sort((a, b) => b.score - a.score);
  return scored[0].score >= 3 && (!scored[1] || scored[0].score > scored[1].score) ? scored[0].c : null;
}

function matchPlace(text: string, places: Place[]): { place: Place; name: string } | null {
  const { text: compact, codes } = compactCodes(text);
  const words = new Set(compact.split(" "));
  let best: { place: Place; name: string; score: number } | null = null;
  for (const p of places) {
    for (const name of p.names) {
      const n = squash(name);
      const tokens = fold(name).split(" ").filter((w) => !["gate", "the", "level", "platform"].includes(w));
      let score = 0;
      if (codes.some((c) => squash(c) === n || squash(c) === squash(p.id))) score = 10;
      else if (GATE_SHAPE.test(squash(name)) && words.has(squash(name))) score = 10;
      else if (tokens.length) {
        const significant = tokens.filter((tk) => tk.length > 2);
        const hit = significant.filter((tk) => words.has(tk)).length;
        score = significant.length > 0 && hit === significant.length ? 6 + hit : hit * 2;
      }
      if (score > 0 && (!best || score > best.score)) best = { place: p, name, score };
    }
  }
  return best && best.score >= 6 ? { place: best.place, name: best.name } : null;
}

/** Interpret one finished utterance. Order matters: safety first, then what the screen asked, then commands. */
export function parseUtterance(raw: string, ctx: ParseContext): Intent[] {
  const text = raw.trim();
  if (!text) return [];
  const intents: Intent[] = [];
  const urgent = commandFor(text, ctx.lang) === "urgent";
  if (urgent) return [{ kind: "command", name: "urgent" }];

  if (ctx.asking === "date" || ctx.asking === "flight" || ctx.asking === "pickup") {
    const hit = matchChoice(text, ctx.choices);
    if (hit) return [{ kind: "choice", id: hit.id, label: hit.label }];
  }
  if (ctx.asking === "landmark" || ctx.asking === "pickup") {
    const hit = matchPlace(text, ctx.places);
    if (hit) return [{ kind: "place", id: hit.place.id, name: hit.name }];
  }

  const { codes } = compactCodes(text);
  const flightCodes = codes.filter((c) => FLIGHT_SHAPE.test(c) || MIXED_SHAPE.test(c)).filter((c, i, all) => all.indexOf(c) === i);
  const command = commandFor(text, ctx.lang);
  if (flightCodes.length && !(command === "yes" || command === "no")) {
    const resolved = flightCodes.map((c) => ({ c, ...matchFlight(c, ctx.flights) }));
    const hear = fold(text);
    let inbound: string | undefined;
    let outbound: string | undefined;
    const inboundCue = /\b(arriv\w*|landing|landed|coming|from|inbound|incoming)\b/;
    const outboundCue = /\b(connect\w*|next|onward|depart\w*|leaving|outbound|going|catch|flying|transfer)\b/;
    resolved.forEach((r, index) => {
      const shown = r.flight ? r.flight.aliases[0] ?? r.flight.code : r.c.toUpperCase();
      const dir = r.flight?.direction;
      if (dir === "arrival" && !inbound) inbound = shown;
      else if (dir === "departure" && !outbound) outbound = shown;
      else if (!dir) {
        const before = hear.slice(Math.max(0, hear.indexOf(r.c.slice(0, 2)) - 28), Math.max(0, hear.indexOf(r.c.slice(0, 2))));
        if (inboundCue.test(before) && !inbound) inbound = shown;
        else if (outboundCue.test(before) && !outbound) outbound = shown;
        else if (index === 0 && !inbound && resolved.length > 1) inbound = shown;
        else if (!outbound) outbound = shown;
        else if (!inbound) inbound = shown;
      }
    });
    const sure = resolved.every((r) => r.flight && r.exact);
    intents.push({ kind: "flights", inbound, outbound, party: partyFrom(text), sure });
    return intents;
  }
  if (!ctx.journey) {
    const party = partyFrom(text);
    if (party) return [{ kind: "flights", party, sure: false }];
  }
  if (command) return [{ kind: "command", name: command }];
  if (ctx.places.length && ctx.journey) {
    const hit = matchPlace(text, ctx.places);
    if (hit) return [{ kind: "place", id: hit.place.id, name: hit.name }];
  }
  return [{ kind: "text", text }];
}

/** The words that carry meaning for how sure we are: codes and choices matter, filler does not. */
export function confidenceOf(words: { w: string; c: number }[], overall: number): number {
  if (!words.length) return overall;
  const key = words.filter((x) => /\d/.test(x.w) || /^[A-Z]{1,3}$/.test(x.w));
  const pool = key.length ? key : words;
  const min = Math.min(...pool.map((x) => x.c));
  return Math.min(overall || 1, (overall + min) / 2 + 0.05);
}
