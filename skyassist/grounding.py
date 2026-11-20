"""Grounding: the rule that decides what a reply is allowed to say.

A language model may write the wording of an answer. It may not add a fact. This module is plain Python and has no
model in it. It takes a draft sentence and the Promise Ledger for the journey and rejects the draft when it

  * contains a number, time, gate or flight code that no verified or pending ledger entry holds,
  * promises an outcome ("definitely", "you'll make it", "don't worry"),
  * says staff are coming when the ledger does not hold a verified dispatch,
  * repeats text that looks like an instruction to the assistant.

When a draft fails, the caller regenerates once with the reasons, then falls back to a template built from the ledger
itself. Nothing unverified ever reaches the screen.
"""

import re
from dataclasses import dataclass

from skyassist.i18n import render
from skyassist.models import I18nText, PromiseState, VerdictLevel, ViewModel

_BIDI = re.compile("[⁦⁧⁨⁩‎‏]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "ninety": 90, "hundred": 100,
}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER = re.compile(r"(?<![\w:])\d+(?![\w:])")
_CODE = re.compile(r"\b[A-Za-z]{1,3}-?\d{1,4}\b")

OVERPROMISE = re.compile(
    r"\b(guarantee[sd]?|definitely|certainly|for sure|no problem|don'?t worry|do not worry|nothing to worry"
    r"|you'?ll (?:definitely |certainly )?make it|will (?:definitely |certainly )?make it|plenty of time"
    r"|i promise|promise you|absolutely fine|all good|it'?ll be fine|it will be fine)\b"
    r"|بالتأكيد|لا تقلق|مضمون|أضمن|لا مشكلة|ستلحق",
    re.I,
)
STAFF_CLAIM = re.compile(
    r"\b(on (?:his|her|their|the) way|will (?:arrive|be there|meet you|come)|is coming|are coming|has been (?:approved|dispatched|sent)"
    r"|have been (?:approved|dispatched)|is approved|already approved|staff (?:is|are) (?:coming|arriving|on))\b"
    r"|في طريقه|في طريقهم|سيصل|تمت الموافقة",
    re.I,
)
OPTIMISTIC = re.compile(
    r"\b(you have (?:enough |plenty of |lots of )?time|on track|no rush|no need to hurry|you'?ll be fine|you will be fine|comfortabl[ey]|easily)\b"
    r"|لديك وقت|لا داعي للعجلة",
    re.I,
)
PESSIMISTIC = re.compile(r"\b(you will miss|you'?ll miss|too late|cannot make it|can'?t make it|no chance)\b|ستفوتك|فات الأوان", re.I)
INJECTION = re.compile(
    r"\b(ignore (?:all |any |the |your )?(?:previous|above|prior|earlier)|disregard|system prompt|you are now|as an ai"
    r"|developer mode|jailbreak|reveal your (?:prompt|instructions))\b",
    re.I,
)


@dataclass(frozen=True)
class Violation:
    kind: str       # ungrounded_number | ungrounded_code | overpromise | staff_claim | injection
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail}


def _clean(text: str) -> str:
    return _BIDI.sub("", text).translate(_ARABIC_DIGITS)


def _numbers(text: str) -> set[str]:
    text = _clean(text)
    found = set(_TIME.findall(text))
    scrubbed = _TIME.sub(" ", text)
    found.update(str(int(n)) for n in _NUMBER.findall(scrubbed))
    found.update(str(_WORDS[w.lower()]) for w in _WORD.findall(scrubbed))
    return found


def _codes(text: str) -> set[str]:
    return {re.sub(r"[- ]", "", c).upper() for c in _CODE.findall(_clean(text)) if not _TIME.fullmatch(c)}


def facts(view: ViewModel, lang: str) -> list[tuple[str, PromiseState, str]]:
    """What the screen may stand behind: (key, state, sentence) for every ledger entry, in the passenger's language."""
    return [(p.key, p.state, render(p.text, lang)) for p in view.ledger]


def allowed_tokens(view: ViewModel, lang: str, extra: tuple[str, ...] = ()) -> tuple[set[str], set[str]]:
    """Numbers and codes that appear in entries we can stand behind. An unknown entry contributes nothing.

    `extra` is other verified text: the airport clock, or the snippets of a web search that was shown with the answer.
    """
    numbers: set[str] = set()
    codes: set[str] = set()
    for text in extra:
        numbers |= _numbers(text)
        codes |= _codes(text)
    for _, state, sentence in facts(view, lang):
        if state is PromiseState.UNKNOWN:
            continue
        numbers |= _numbers(sentence)
        codes |= _codes(sentence)
    return numbers, codes


def check(draft: str, view: ViewModel, lang: str, extra: tuple[str, ...] = ()) -> list[Violation]:
    """Every reason this draft may not be shown. An empty list means it is grounded in the ledger."""
    problems: list[Violation] = []
    if INJECTION.search(draft):
        problems.append(Violation("injection", "the draft repeats an instruction to the assistant"))
    numbers, codes = allowed_tokens(view, lang, extra)
    for token in sorted(_numbers(draft) - numbers):
        problems.append(Violation("ungrounded_number", f"{token} is not in a verified or pending ledger entry"))
    for token in sorted(_codes(draft) - codes):
        problems.append(Violation("ungrounded_code", f"{token} is not in a verified or pending ledger entry"))
    if match := OVERPROMISE.search(draft):
        problems.append(Violation("overpromise", f"'{match.group(0)}' promises an outcome the system cannot guarantee"))
    level = view.verdict.level if view.verdict else VerdictLevel.UNVERIFIED
    if (match := OPTIMISTIC.search(draft)) and level is not VerdictLevel.SAFE:
        problems.append(Violation("contradicts_verdict", f"'{match.group(0)}' is more hopeful than the verdict ({level.value})"))
    if (match := PESSIMISTIC.search(draft)) and level in (VerdictLevel.SAFE, VerdictLevel.TIGHT):
        problems.append(Violation("contradicts_verdict", f"'{match.group(0)}' is bleaker than the verdict ({level.value})"))
    assistance = next((s for k, s, _ in facts(view, lang) if k == "assistance"), None)
    if (match := STAFF_CLAIM.search(draft)) and assistance is not PromiseState.VERIFIED:
        problems.append(Violation("staff_claim", f"'{match.group(0)}' claims staff action the ledger does not hold as verified"))
    return problems


# ---------- questions, and answers built from the ledger alone

KINDS = ("urgent", "help", "reassure", "why", "time", "route", "gate", "clock", "flightinfo", "other")
_URGENT = re.compile(r"\b(emergency|chest pain|can'?t breathe|not breathing|heart attack|bleeding|fire)\b|طوارئ|ألم في الصدر|لا أتنفس", re.I)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("clock", re.compile(r"\b(what time is it|current time|time now|what'?s the time|what is the time|today'?s date|what day|what date)\b|كم الساعة|ما التاريخ", re.I)),
    ("flightinfo", re.compile(r"\b(?:status of|is|about|check|flight)\s+[A-Za-z0-9]{2}\s?-?\d{1,4}\b|\b[A-Za-z]{2}\s?\d{2,4}\b.*\b(?:delayed|on time|status|depart|land|arriv)", re.I)),
    ("help", re.compile(r"\b(help|assist(?:ance)?|wheelchair|escort|buggy|stroller|pram)\b|مساعدة|كرسي متحرك|عربة", re.I)),
    ("reassure", re.compile(r"\b(fine|promise|guarantee|will i make it|tell me (?:it|that)|reassure|worry)\b|أضمن|لا تقلق|ستكون بخير", re.I)),
    ("why", re.compile(r"\b(why|status|explain|how (?:do you|did you)|sure|source|verified)\b|لماذا|الحالة|كيف عرفت", re.I)),
    ("time", re.compile(r"\b(time|boarding|cut-?off|late|make it|spare|minutes?|long)\b|وقت|صعود|متأخر|كم", re.I)),
    ("route", re.compile(r"\b(route|directions?|way|walk|step[ -]free|no stairs|lift|elevator|map|where do i go)\b|مسار|الطريق|سلالم|مصعد|خريطة", re.I)),
    ("gate", re.compile(r"\b(gate|terminal|concourse)\b|بواب|القاعة|المبنى", re.I)),
]


def classify(question: str) -> str:
    text = _clean(question)
    if _URGENT.search(text):
        return "urgent"
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return "other"


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", "؟")) else text + "."


def _line(view: ViewModel, lang: str, *keys: str) -> list[str]:
    wanted = {k: _sentence(s) for k, _, s in facts(view, lang)}
    return [wanted[k] for k in keys if k in wanted]


def _say(key: str, lang: str, **params: object) -> str:
    return render(I18nText(key=key, params=params), lang)


def template_answer(kind: str, view: ViewModel, lang: str) -> str:
    """The answer, built from the ledger with no model involved. Grounded by construction."""
    level = view.verdict.level if view.verdict else VerdictLevel.UNVERIFIED
    verdict = _say(f"answer.verdict.{level.value}", lang)
    if kind == "urgent":
        return _say("answer.urgent", lang)
    if kind == "other":
        return _say("answer.scope", lang)
    if kind == "clock":
        return _say("answer.clock_unknown", lang)
    if kind == "flightinfo":
        return _say("answer.flight_unknown", lang)
    if kind == "gate":
        return " ".join(_line(view, lang, "gate", "route")) or _say("answer.nothing_yet", lang)
    if kind == "route":
        return " ".join(_line(view, lang, "route", "transfer"))
    if kind == "help":
        held = _line(view, lang, "assistance")
        return " ".join(held) if held else _say("answer.help_offer", lang)
    if kind == "reassure":
        held = _line(view, lang, "cutoff", "margin")
        return " ".join([_say("answer.no_promise", lang), verdict, *held])
    if kind == "why":
        return " ".join([verdict, *(f"{_say(f'ledger.state.{s.value}', lang)}: {text}." for _, s, text in facts(view, lang))])
    return " ".join([verdict, *_line(view, lang, "cutoff", "transfer", "margin")])


def facts_for_prompt(view: ViewModel, lang: str) -> str:
    return "\n".join(f"- [{state.value}] {text}" for _, state, text in facts(view, lang))
