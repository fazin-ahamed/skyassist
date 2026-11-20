# Routing and timing spec (shared by S1 routing code and H3 fixtures)

Source: SKYASSIST-PLAN.md section 4 and 8. This file fixes the arithmetic both sides must agree on. Disagreement is a failing test, never a silent edit of the other side's files.

All arithmetic is integer seconds. Display rounding is S0's `TransferView.from_result` (lower floors to minutes, upper ceils, margin floors).

## Per-edge cost

- `Edge.fixed_movement` set (lift ride, train ride, crossing, screening, handoff): movement = that range, profile independent.
- Otherwise movement lower = ceil(distance_m / fast_mps), upper = ceil(distance_m / slow_mps), using the profile's `SpeedBand` in `PolicyPack`. `steps` and `slope` affect eligibility only.
- Required wait = `Edge.wait`, replaced by the fresh `QueueObservation.wait` for `Edge.queue_id` and the journey's flow when one exists. A queue observation with `wait=null`, stale, or missing for an edge that has `queue_id` makes the wait upper unbounded (`SecondsRange.upper=null`). Reason codes `QUEUE_BOUND_MISSING` or `QUEUE_BOUND_STALE`.
- Segment times are summed once per traversed edge.

## Route total

```
ready      = ReadinessEvent.ready (SecondsRange), else READINESS_UNKNOWN and Timing unverified
lower      = ready.lower + sum(movement.lower) + sum(wait.lower) + buffer
upper      = ready.upper + sum(movement.upper) + sum(wait.upper) + buffer   (null if any wait upper is null)
time_remaining = boarding_cutoff - as_of      (signed seconds)
margin     = time_remaining - upper           (signed; null when upper null)
```

`buffer` = `PolicyPack.buffer_floors[profile]`, applied once to the whole transfer, never lowered by anything but a policy change. Profile from `PartyNeeds`: wheelchair or step_free gives reduced_mobility, children > 0 gives family, both gives family_mobility, else standard.

## Eligibility of an edge (all must hold, else excluded)

Airport equals the journey airport. Not `closed`, not `restricted`. Overlay for the edge (fresh, unexpired) overrides `closed` and `accessible`. `requires_access_evidence` edges need a fresh Overlay stating open and accessible, else excluded (`LIFT_UNVERIFIED` or `ACCESS_UNVERIFIED`, a known closure gives `LIFT_CLOSED` or `FACILITY_CLOSED`). Passenger flow in `allowed_flows`. Security zone and direction preserved on every hop (a directed edge is never traversed backwards; a zone change only through an edge of mode checkpoint whose `allowed_flows` match). Step-free profiles (wheelchair, step_free) need `accessible=True` and `steps == 0`; `lift` edges count as accessible. `open_windows` must cover the whole expected traversal window (as_of plus cumulative upper time), empty list means unrestricted. No path found gives `NO_ACCESSIBLE_PATH` (step-free) or `NO_PATH`. Never route into aircraft door or apron.

## Path choice

Dijkstra over eligible edges weighted by upper-bound seconds (movement.upper + wait.upper). Tie break by lexicographic edge id sequence. An unbounded wait edge is used only if no bounded path exists; the route is still returned and flagged. Second pass with lower-bound weights (ready.lower + movement.lower + wait.lower + buffer) over the same eligible edges gives `best_lower`.

## Classification (first match wins)

1. Verified cutoff and `time_remaining < 0`: Boarding cutoff passed (`CUTOFF_PASSED`), even if no route exists.
2. Gate unknown, stale or in conflict; or cutoff missing; or cutoff estimate only; or ready unknown; or no eligible path; or `upper` null: Timing unverified with specific reason codes (`GATE_*`, `CUTOFF_MISSING`/`CUTOFF_ESTIMATED`, `READINESS_UNKNOWN`, `NO_ACCESSIBLE_PATH`, `QUEUE_BOUND_*`). Missing cutoff also sets flag Boarding cutoff unavailable. A valid route is still returned when its own evidence is valid (C05, C10). A gate stale or in conflict returns no route at all.
3. `0 <= margin <= tight_margin_seconds` (600 s, inclusive both ends): Tight connection (`TIGHT_MARGIN`).
4. `margin > 600`: Time available.
5. `margin < 0`: if `best_lower > time_remaining` Transfer unlikely before cutoff (`LOWER_BOUND_EXCEEDS_CUTOFF`), else May miss boarding (`NEGATIVE_MARGIN`).

Pending staff handoff marks the prediction conditional (`STAFF_HANDOFF_PENDING`); the route's first step is the staff handoff at the confirmed pickup.

## Golden numbers (minutes shown, tests use seconds)

| ID | Profile | Remaining | Ready | Move | Wait | Buffer | Range | Margin | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | reduced_mobility | 35 | 4-6 | 7-9 | 3-7 | 7 | 21-29 | 6 | Tight connection |
| C02 | reduced_mobility | 32 | per event | per graph | per graph | 7 | upper 16 | 16 | Time available |
| C03 | standard | 40 | 0 | 5-8 | 0 | 3 | 8-11 | 29 | Time available |
| C04 | family | 8 | 0 | move+wait 7-17 | | 5 | 12-22 | -14 | Transfer unlikely before cutoff |
| C09 | standard | 28 | per event | per graph | per graph | 3 | upper 15 | 13 | Time available |

C01 route: C22, Lift C, Train C, Train B, B14. C02 reroutes to B18. C05 no cutoff: Timing unverified plus Boarding cutoff unavailable, route valid. C06 stale then conflict: no route, then resolved gate route. C07 two "Costa" on different floors: AMBIGUOUS_LANDMARK before routing. C08 lift closed, stairs-only left: no step-free route, staffed assistance. C10 mandatory checkpoint, stale queue upper: route shown, Timing unverified, margin withheld.
