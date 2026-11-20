import json
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from skyassist.models import ReasonCode, Success, ToolError
from skyassist.tools import MAX_ARGUMENT_BYTES, MAX_ARGUMENT_DEPTH, REPAIR_RULE_VERSION
from test_evidence import NOW, conn
from test_tools import FLIGHT, FlightSpy, audit, payload, turn, world


@dataclass(frozen=True)
class Case:
    label: str
    meaning: tuple[str, ...] | ReasonCode
    raw: str
    rules: str | None = None
    choices: tuple[str, ...] = ()
    tool: str = FLIGHT

    @property
    def category(self):
        if isinstance(self.meaning, tuple):
            return "safe repair"
        return "ambiguous-must-ask" if self.choices else "unsafe-must-refuse"


SAFE = [
    Case("airport leading space", ("out",), payload(airport=" DXB"), "airport_trim"),
    Case("airport trailing space", ("out",), payload(airport="DXB "), "airport_trim"),
    Case("airport ASCII line whitespace", ("out",), payload(airport="\tDXB\r\n"), "airport_trim"),
    Case("airport lowercase", ("out",), payload(airport="dxb"), "airport_case"),
    Case("airport mixed case", ("out",), payload(airport="DxB"), "airport_case"),
    Case("airport case and whitespace", ("out",), payload(airport=" dXb "), "airport_trim+airport_case"),
    Case("airport repair preserves second leg", ("other",), payload(airport="dxb", refs=["other"]), "airport_case"),
    Case("single stringified list", ("out",), payload(refs='["out"]'), "refs_json_list"),
    Case("stringified list preserves duplicates", ("out", "out"),
         payload(refs='["out","out"]'), "refs_json_list"),
    Case("stringified list preserves order", ("other", "out", "in"),
         payload(refs='["other", "out", "in"]'), "refs_json_list"),
    Case("stringified list with whitespace", ("in",), payload(refs=' \r\n["in"] \t'), "refs_json_list"),
    Case("stringified list with JSON escaping", ("out",), payload(refs=r'["\u006fut"]'), "refs_json_list"),
    Case("synthetic alias", ("out",), payload(refs=["DXB-402"]), "flight_alias"),
    Case("alias case", ("out",), payload(refs=["dxb-402"]), "flight_alias"),
    Case("alias exterior whitespace", ("out",), payload(refs=["  DXB-402\t"]), "flight_alias"),
    Case("unique operating code", ("other",), payload(refs=["DX104"]), "flight_alias"),
    Case("canonical alongside alias", ("out", "other"), payload(refs=["DXB-402", "other"]), "flight_alias"),
    Case("alias preserves multiplicity", ("out", "out"), payload(refs=["DXB-402", "DXB-402"]), "flight_alias"),
    Case("stringified alias", ("out",), payload(refs='["DXB-402"]'), "refs_json_list+flight_alias"),
    Case("all repairs preserve instant and order", ("out", "other", "out"),
         payload(refs='["dxb-402","other","DXB-402"]', airport=" dxb ", as_of="2026-11-19T10:00:00+04:00"),
         "airport_trim+airport_case+refs_json_list+flight_alias"),
]

INVALID = ReasonCode.INVALID_ARGUMENTS
UNKNOWN = ReasonCode.FLIGHT_UNKNOWN
WRONG_AIRPORT = ReasonCode.WRONG_AIRPORT
UNSAFE = [
    Case("truncated JSON", INVALID, '{"refs":'),
    Case("duplicate keys", INVALID, '{"refs":["out"],"refs":["other"]}'),
    Case("escaped duplicate keys", INVALID, r'{"refs":["out"],"\u0072efs":["in"]}'),
    Case("duplicate nested keys", INVALID, '{"x":{"airport":"DXB","airport":"AUH"}}'),
    Case("invalid NaN value", INVALID, '{"refs":NaN}'),
    Case("oversized document", INVALID, " " * MAX_ARGUMENT_BYTES + payload()),
    Case("deep document", INVALID, "[" * (MAX_ARGUMENT_DEPTH + 1) + "]" * (MAX_ARGUMENT_DEPTH + 1)),
    Case("empty refs", INVALID, payload(refs=[])),
    Case("empty stringified refs", INVALID, payload(refs="[]"), "refs_json_list"),
    Case("scalar disguised as list", INVALID, payload(refs='"out"')),
    Case("bare flight ref cannot become a list", INVALID, payload(refs="DXB-402")),
    Case("nested stringified list", INVALID, payload(refs='[["out"]]'), "refs_json_list"),
    Case("object ref in stringified list", INVALID, payload(refs='[{"id":"out"}]'), "refs_json_list"),
    Case("numeric ref in stringified list", INVALID, payload(refs='["out", 402]'), "refs_json_list"),
    Case("boolean direct ref", INVALID, payload(refs=[True])),
    Case("null ref dropped would change cardinality", INVALID, payload(refs=["out", None])),
    Case("missing time", INVALID, json.dumps({"refs": ["out"], "airport": "DXB"})),
    Case("numeric time is not an instant", INVALID, payload(as_of=0)),
    Case("invalid date cannot be corrected", INVALID, payload(as_of="2026-02-30T06:00:00Z")),
    Case("naive time cannot assume timezone", INVALID, payload(as_of="2026-11-19T06:00:00")),
    Case("different time must stay different", INVALID, payload(as_of=(NOW + timedelta(seconds=1)).isoformat())),
    Case("ref swapped for different airport", WRONG_AIRPORT, payload(airport="auh"), "airport_case"),
    Case("airport typo", INVALID, payload(airport="DXXB")),
    Case("airport Unicode lookalike", INVALID, payload(airport="ＤＸＢ")),
    Case("airport digit substitution", INVALID, payload(airport="DX8")),
    Case("refs field renamed", INVALID, json.dumps({"legs": ["out"], "airport": "DXB", "as_of": NOW.isoformat()})),
    Case("additional cutoff field", INVALID, payload(cutoff="10:35")),
    Case("arbitrary gate cannot become a flight", UNKNOWN, payload(refs=["B14"])),
    Case("alias punctuation removal changes value", UNKNOWN, payload(refs=["DXB402"])),
    Case("unlisted flight-number substitution", UNKNOWN, payload(refs=["DXB-420"])),
    Case("unknown flight with case change", UNKNOWN, payload(refs=["out", "ouT"])),
    Case("airport stripped before scope check", WRONG_AIRPORT, payload(airport=" AUH "), "airport_trim"),
    Case("airport padded Unicode whitespace", INVALID, payload(airport="\u00a0DXB")),
    Case("protected tool cannot repair", ReasonCode.UNAUTHORIZED_TOOL, payload(), tool="call_assistance"),
    Case("unknown tool cannot repair", ReasonCode.UNSUPPORTED_INPUT, payload(), tool="get_flight_status"),
]

DATE = ReasonCode.AMBIGUOUS_DATE
FLIGHTS = ReasonCode.AMBIGUOUS_FLIGHT
DATES = ("2026-11-19", "2026-11-20")
LEGS = ("other", "out")
AMBIGUOUS = [
    Case("marketing code on two dates", DATE, payload(refs=["XY402"]), choices=DATES),
    Case("lowercase marketing code on two dates", DATE, payload(refs=["xy402"]), choices=DATES),
    Case("marketing code on two dates with whitespace", DATE, payload(refs=[" XY402 "]), choices=DATES),
    Case("other known ref cannot choose date", DATE, payload(refs=["out", "XY402"]), choices=DATES),
    Case("stringified marketing code on two dates", DATE, payload(refs='["XY402"]'), "refs_json_list", DATES),
    Case("airport correction cannot choose date", DATE, payload(refs=["XY402"], airport="dxb"), "airport_case", DATES),
    Case("another dated choice cannot choose date", DATE, payload(refs=["tomorrow", "XY402"]), choices=DATES),
    Case("operating code on two dates", DATE, payload(refs=["DX402"]), choices=DATES),
    Case("same date codeshare collision", FLIGHTS, payload(refs=["ZZ402"]), choices=LEGS),
    Case("lowercase same date collision", FLIGHTS, payload(refs=["zz402"]), choices=LEGS),
    Case("same date collision with whitespace", FLIGHTS, payload(refs=["\tZZ402"]), choices=LEGS),
    Case("standalone leg cannot disambiguate second ref", FLIGHTS, payload(refs=["other", "ZZ402"]), choices=LEGS),
    Case("stringified same date collision", FLIGHTS, payload(refs='["ZZ402"]'), "refs_json_list", LEGS),
    Case("airport correction cannot choose leg", FLIGHTS, payload(refs=["ZZ402"], airport=" DXB "), "airport_trim", LEGS),
    Case("two repair rules cannot choose leg", FLIGHTS, payload(refs='["ZZ402"]', airport="dxb"),
         "airport_case+refs_json_list", LEGS),
    Case("repetition cannot choose leg", FLIGHTS, payload(refs=["ZZ402", "ZZ402"]), choices=LEGS),
]


def test_labelled_repair_regression(conn, world, capsys):
    counts, correct = Counter(), Counter()
    failures = []
    for case in SAFE + UNSAFE + AMBIGUOUS:
        spy = FlightSpy(conn)
        result = turn(conn, flights=spy).call(case.tool, case.raw)
        record = audit(conn)[-1]
        if case.category == "safe repair":
            passed = (
                isinstance(result, Success)
                and tuple(leg.id for leg in result.value.legs) == case.meaning
                and len(spy.requests) == 1
                and spy.requests[0][0].refs == case.meaning
                and spy.requests[0][0].airport == "DXB"
                and spy.requests[0][0].as_of == NOW
                and record["outcome"] == "repaired"
            )
        else:
            passed = (
                isinstance(result, ToolError) and result.diagnostic is case.meaning
                and tuple(choice.id for choice in result.permitted_choices) == case.choices
                and not spy.requests and record["attempts"] == 0
            )
        passed = passed and record["rule_id"] == case.rules and record["repair_rule_version"] == REPAIR_RULE_VERSION
        if not passed:
            failures.append((case.label, result, record["rule_id"]))
        for rule in (case.rules or "no repair").split("+"):
            key = (case.category, rule)
            counts[key] += 1
            correct[key] += passed

    with capsys.disabled():
        print(f"\nRepair regression v{REPAIR_RULE_VERSION}, {len(SAFE + UNSAFE + AMBIGUOUS)} labelled cases")
        for (category, rule), count in sorted(counts.items()):
            matched = correct[category, rule]
            print(f"{category} / {rule}  {matched}/{count}  {matched / count:.1%}")
    assert len(SAFE + UNSAFE + AMBIGUOUS) >= 60
    assert not failures, failures
