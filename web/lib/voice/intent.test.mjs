import test from "node:test";
import assert from "node:assert/strict";
import { compactCodes, parseUtterance, partyFrom, confidenceOf } from "./intent.ts";

const flights = [
  { code: "DX402", aliases: ["DXB-402", "DX402"], direction: "arrival" },
  { code: "ZX515", aliases: ["ZX515"], direction: "departure" },
  { code: "EK303", aliases: ["EK303"], direction: "departure" },
];
const base = { lang: "en", asking: null, choices: [], places: [], flights, journey: false };
const places = [
  { id: "c22", names: ["Gate C22", "C22"] },
  { id: "lift_c", names: ["Lift C lobby", "Lift C"] },
  { id: "c_costa", names: ["Costa cafe, level 1", "Costa"] },
  { id: "b14", names: ["Gate B14", "B14"] },
];

test("spoken codes are repaired", () => {
  assert.deepEqual(compactCodes("zed ex five one five").codes, ["zx515"]);
  assert.deepEqual(compactCodes("D X B four oh two").codes, ["dxb402"]);
  assert.deepEqual(compactCodes("gate see twenty two").codes, ["c22"]);
  assert.deepEqual(compactCodes("it is b fourteen please").codes, ["b14"]);
  assert.deepEqual(compactCodes("my flight is ZX 515").codes, ["zx515"]);
  assert.deepEqual(compactCodes("DXB-402").codes, ["dxb402"]);
  assert.deepEqual(compactCodes("flight ek five oh three").codes, ["ek503"]);
  assert.deepEqual(compactCodes("double five").codes, []);
});

test("ordinary sentences produce no codes", () => {
  assert.deepEqual(compactCodes("I need two wheelchairs").codes, []);
  assert.deepEqual(compactCodes("go to five").codes, []);
  assert.deepEqual(compactCodes("where is my gate").codes, []);
});

test("two flights are told apart by the board, not by order", () => {
  const [i] = parseUtterance("my flights are ZX 515 and DXB 402", base);
  assert.equal(i.kind, "flights");
  assert.equal(i.inbound, "DXB-402");
  assert.equal(i.outbound, "ZX515");
  assert.equal(i.sure, true);
});

test("spoken letters and numbers become flights", () => {
  const [i] = parseUtterance("I'm arriving on D X B four oh two and connecting to zed ex five one five", base);
  assert.equal(i.inbound, "DXB-402");
  assert.equal(i.outbound, "ZX515");
});

test("one wrong digit is repaired only when it is unambiguous, and is not sure", () => {
  const [i] = parseUtterance("flight ZX 516", base);
  assert.equal(i.kind, "flights");
  assert.equal(i.outbound, "ZX515");
  assert.equal(i.sure, false);
});

test("an unknown flight is passed on as heard and marked unsure", () => {
  const [i] = parseUtterance("QR 1234", base);
  assert.equal(i.kind, "flights");
  assert.equal(i.outbound, "QR1234");
  assert.equal(i.sure, false);
});

test("party is read from plain speech", () => {
  assert.equal(partyFrom("we have two small children"), "family");
  assert.equal(partyFrom("my mother uses a wheelchair"), "wheelchair");
  assert.equal(partyFrom("I need a step free route with my baby"), "both");
  assert.equal(partyFrom("just me"), undefined);
  const [i] = parseUtterance("DXB 402 to ZX 515, I have a wheelchair and kids", base);
  assert.equal(i.party, "both");
});

test("no journey yet: a bare need still starts the flight form", () => {
  const [i] = parseUtterance("I need wheelchair assistance", base);
  assert.equal(i.kind, "flights");
  assert.equal(i.party, "wheelchair");
});

test("date question is answered by day, number, or position", () => {
  const ctx = { ...base, asking: "date", journey: true, choices: [
    { id: "2026-11-19", label: "Service on Thu 19 Nov" }, { id: "2026-11-20", label: "Service on Fri 20 Nov" }] };
  assert.equal(parseUtterance("Thursday", ctx)[0].id, "2026-11-19");
  assert.equal(parseUtterance("the second one", ctx)[0].id, "2026-11-20");
  assert.equal(parseUtterance("Friday please", ctx)[0].id, "2026-11-20");
  assert.equal(parseUtterance("nineteen", ctx)[0].id, "2026-11-19");
  assert.equal(parseUtterance("option two", ctx)[0].id, "2026-11-20");
});

test("where-am-I is answered by gate code or landmark", () => {
  const ctx = { ...base, asking: "landmark", journey: true, places };
  assert.equal(parseUtterance("I'm at gate C twenty two", ctx)[0].id, "c22");
  assert.equal(parseUtterance("near the Costa", ctx)[0].id, "c_costa");
  assert.equal(parseUtterance("by the lift", ctx)[0].id, "lift_c");
});

test("commands", () => {
  const ctx = { ...base, journey: true };
  const name = (t) => { const [i] = parseUtterance(t, ctx); return i.kind === "command" ? i.name : i.kind; };
  assert.equal(name("where is my gate"), "gate");
  assert.equal(name("do I have enough time"), "time");
  assert.equal(name("will I make it?"), "time");
  assert.equal(name("show me the route"), "route");
  assert.equal(name("take me there"), "route");
  assert.equal(name("I need a wheelchair"), "assist");
  assert.equal(name("request assistance please"), "assist");
  assert.equal(name("why is it tight"), "why");
  assert.equal(name("yes please"), "yes");
  assert.equal(name("no that's wrong"), "no");
  assert.equal(name("end session"), "end");
  assert.equal(name("start over"), "end");
  assert.equal(name("switch to arabic"), "lang_ar");
  assert.equal(name("show me the departures"), "browse");
  assert.equal(name("where am I"), "locate");
});

test("safety words win over everything, even with codes in the sentence", () => {
  const [i] = parseUtterance("my husband collapsed near gate B 14, I need an ambulance", { ...base, journey: true });
  assert.deepEqual(i, { kind: "command", name: "urgent" });
});

test("unrecognised speech goes to the agent as text", () => {
  const [i] = parseUtterance("what is the weather in Dubai", { ...base, journey: true });
  assert.deepEqual(i, { kind: "text", text: "what is the weather in Dubai" });
});

test("arabic digits and commands", () => {
  assert.deepEqual(compactCodes("بوابة B١٤").codes, ["b14"]);
  const [i] = parseUtterance("أين بوابتي", { ...base, lang: "ar", journey: true });
  assert.deepEqual(i, { kind: "command", name: "gate" });
  const [y] = parseUtterance("نعم", { ...base, lang: "ar", journey: true });
  assert.deepEqual(y, { kind: "command", name: "yes" });
});

test("confidence leans on the words that matter", () => {
  const sure = confidenceOf([{ w: "my", c: 0.4 }, { w: "ZX515", c: 0.99 }], 0.97);
  const shaky = confidenceOf([{ w: "my", c: 0.99 }, { w: "ZX515", c: 0.41 }], 0.97);
  assert.ok(shaky < 0.75 && sure > shaky);
});
