# SkyAssist build plan (work breakdown)

Source of truth: SKYASSIST-PLAN.md. This file only assigns owners and order.

Routing rule from the user: small, self-contained work to Haiku 5.5 (xhigh, max for correctness-critical data). Backend and logic-heavy work to GPT 6.2 Sol (high). UI and design: Claude (me). Shared directory, no git: every agent owns disjoint files, contracts first.

Env: `UV_CACHE_DIR=$TMPDIR/uv-cache uv run pytest`. Python 3.14, Streamlit 1.67, FastAPI, pydantic v2, segno. Ollama not installed on this machine (phase 0 gap; template fallback must carry the demo).

## Layout and ownership

| Path | Owner |
| --- | --- |
| skyassist/{models,clock,config,db}.py, data/schema, docs/CONTRACTS.md | S0 Sol |
| skyassist/util/{maps_url,qr}.py, kiosk/static/fonts, data/airports.* | H2 Haiku |
| skyassist/{routing,timing}.py | S1 Sol |
| skyassist/{evidence,tools}.py | S2 Sol |
| skyassist/{assist,service,staff_client}.py | S3 Sol |
| data/{graph,flights,events,landmarks,policy}.json, data/scenarios, skyassist/seed.py | H3 Haiku (max) |
| data/i18n, skyassist/i18n.py, amenities seed, synthetic aircraft trace | H4 Haiku |
| skyassist/{agent,model_boundary}.py | S4 Sol |
| tests/scenarios, fault and degradation suites, metrics report | S5 Sol |
| skyassist/adapters/*, run_demo.sh, README operator checklist | H5 Haiku |
| kiosk/, staff/, DESIGN.md, PRODUCT.md | Claude |

## Waves

| Wave | Work | Gate to start | Exit check |
| --- | --- | --- | --- |
| 1 (running) | S0 contracts, DB, clock, config, view model. H2 utils, fonts, airports. Claude: PRODUCT.md, design world, kiosk shell against static view-model JSON. | none | S0 tests green, schemas exported. H2 tests green, fonts vendored. |
| 2 | S1 routing and timing math. S2 evidence merge, tool validate and repair. S3 case state machine, outbox worker, FastAPI, second-process callback client. H3 fixtures C01-C10 plus seed loader and validator. H4 i18n EN/AR, amenities, aircraft trace. | S0 done | C01 and C02 hand-verified numbers pass. Stale gate suppresses directions. Second process approves; restart keeps state; replay yields one dispatch. |
| 3 | S4 agent loop, model boundary (Ollama/Qwen3 structured output, template fallback). S5 scenario runner at the passenger entry point, fault, degradation, safety suites, metrics. H5 adapters (AirLabs, aircraft, arrivals hook) with contract tests, launcher, operator checklist. Claude: wire kiosk and staff views to the live service, QR route page. | S1, S2, S3, H3 done | 10/10 scenarios pass offline. Safety invariants zero. |
| 4 | Claude: a11y, RTL, 200% zoom, impeccable detect, antislop Delivery Gate. Code review. Two cold-start rehearsals, one disconnected. | Wave 3 | Demo script runs in about four minutes. |

## Interface seams (why waves are ordered this way)

- Everything imports S0 models. Nothing else starts until they exist.
- UI is a dumb renderer of the S0 ViewModel; every visible string is an i18n key plus params (H4 catalog). Claude can build all UI states from static view-model JSON before the service exists.
- H3 fixtures must satisfy S1 golden numbers: C01 21-29 min and 6 min margin, C02 16 min bound and 16 min margin, C03 8-11 min and 29 margin, C04 12-22 min, C09 13 min margin. Disagreement is a test failure, reconciled by the integrator, not by editing the other side's files.

## Decisions logged

- antislop mode: DURING (user).
- Arabic: EN + AR wired, AR flagged unreviewed (user).
- Build path comp-first/code-first not asked: no image generation tool available, code-first.
- No `git init` done; no rollback besides file copies. Say the word if you want one.

## Status (end of this session)

| Area | State |
| --- | --- |
| Waves 1 to 3 | Done. 380 tests pass. reports/release_gates.md: 26 of 26 gates pass offline. |
| Core scenarios C01 to C10 | 10 of 10 through the passenger entry point. Live kiosk path also yields C01 21-29 min, 6 min margin, Tight connection. |
| Kiosk and coordinator UI | Built, finish review scored 6 of 8 fixes resolved and 2 partial (see DESIGN.md Layout). |
| Launcher and docs | run_demo.sh, README.md, docs/OPERATOR-CHECKLIST.md, docs/DEMO-SCRIPT.md. Rerun the demo script once after the final UI changes. |
| Not done | Stretch features (amenity suggestions, stroller alerts, aircraft map), native Arabic review, Ollama/Qwen3 install, real AirLabs account check, physical QR scan, screen reader and 200% zoom walkthrough on the event device, two cold-start rehearsals (one disconnected). |
