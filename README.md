# SkyAssist demo

SkyAssist is a passenger connection agent for airport kiosks. It checks a passenger's flights, gate, boarding cutoff and step-free route, and stages a mobility assistance request that a coordinator approves in the dashboard. The build runs offline on synthetic DXB data: Python 3.14, SQLite and FastAPI, with a BoardUI React front end in `web/`.

## What runs

One process: the API service on 127.0.0.1:8000. The API serves the built front end from `web/out`.

| URL | What it is |
| --- | --- |
| http://127.0.0.1:8000/ | Passenger kiosk |
| http://127.0.0.1:8000/staff/#token=demo-coordinator | Coordinator dashboard (demo token) |
| http://127.0.0.1:8000/clock | API clock, frozen at 2026-11-19T06:00:00Z |

Cases and journeys live in the API database, `data/skyassist-demo.sqlite3`. The kiosk holds its passenger token in memory and closes the journey and route link when the session ends. The coordinator dashboard keeps its token in the tab's sessionStorage and clears `#token` from the address bar.

## Quick start

You need `uv` on PATH and a local Python 3.14 (uv never downloads Python). Node 22 and npm are needed only when `web/out` is missing.

    ./run_demo.sh up              # sync .venv, seed the demo DB and build web/out if missing, start the API
    ./run_demo.sh status          # API process, port and DB state; exit 1 unless it runs
    ./run_demo.sh down            # stop the API (or Ctrl-C in the `up` terminal)
    ./run_demo.sh reset           # rebuild the demo DB; refuses while the API runs
    ./run_demo.sh up --dry-run    # print the commands and URLs; start nothing

`up` binds to 127.0.0.1 only, sets `SKYASSIST_MODE=demo` and holds the demo clock at C01 with `SKYASSIST_CLOCK_SPEED=0`. Set `SKYASSIST_CLOCK_SPEED=1` only for live time. The log is `run/logs/api.log` and the PID is in `run/api.pid`.

If `SKYASSIST_COORDINATOR_TOKEN` is set, the launcher never prints the token. Sign in with that value.

On Windows, without `uv` or Python 3.14, use the PowerShell launcher instead. It needs Python 3.12 or newer:

    .\run_demo.ps1            # venv, dependencies, database and front end if missing, then start the API
    .\run_demo.ps1 build      # rebuild the front end after a change under web/
    .\run_demo.ps1 reset      # rebuild the demo database (stop the API first)

Windows stops pip at 260 characters, so if the project sits in a deeply nested folder, set `SKYASSIST_VENV` to a short path first.

## Front end build

`up` builds `web/out` once when it is missing. It never rebuilds an existing build. After a change under `web/`, rebuild by hand, then restart the API:

    npm --prefix web ci
    npm --prefix web run build

The first install needs the npm registry and Google Fonts. Behind a proxy, try `NODE_USE_ENV_PROXY=1`.

### Hot-reload preview

With the demo API listening on port 8000, start the Next.js development server in a second terminal.

    npm --prefix web run dev -- --hostname 127.0.0.1 --port 3000

Open http://127.0.0.1:3000/. Next.js forwards API requests to port 8000. Set `SKYASSIST_DEV_API` if the service listens elsewhere.

On Windows with Python 3.12 or newer, these PowerShell commands start the demo API for the preview. The preview database lives outside the repository so database writes do not trigger hot reload.

    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e .
    $env:SKYASSIST_MODE = "demo"
    $env:SKYASSIST_CLOCK_SPEED = "0"
    $env:SKYASSIST_DB_PATH = Join-Path $env:TEMP "skyassist-local-preview.sqlite3"
    if (-not (Test-Path -LiteralPath $env:SKYASSIST_DB_PATH)) { .\.venv\Scripts\python.exe -m skyassist.seed build }
    .\.venv\Scripts\python.exe -m uvicorn skyassist.service_read:app_from_env --factory --host 127.0.0.1 --port 8000 --no-access-log

## Voice

The microphone button floats at the bottom of the kiosk. Tap it and speak, or hold it and let go when done. It understands flight numbers ("zed ex five one five"), gates, the date and location questions, and commands such as "where is my gate", "do I have time" and "show me the route". It can read answers aloud, and it always answers an emergency phrase with the staffed help point.

Speech to text and the spoken replies use Deepgram. Put a key in `.env` (copy `.env.example`) or in the environment:

    DEEPGRAM_API_KEY=your-key

The key stays on the server. The browser talks to `/voice/stream`, which opens the Deepgram connection, so nothing in the page can read it. Without a key the button says voice is not set up and everything else works by touch.

How it copes with a loud hall:

- Push to talk, so only what you mean to say is sent.
- The browser measures the room's noise floor. In a quiet room it sends clean, lightly processed audio. In a busy or loud one it also turns on the browser's noise suppression and closes a soft gate between words. The choice is remembered for the next press.
- Deepgram's own guidance is that heavy denoising removes cues the recogniser needs, so the page does not stack denoisers. Recognition uses Nova-3 with the airport's flights and gates boosted as key terms, and Deepgram's speech detection decides when you stopped talking.
- It tells you when your voice is faint against the room and asks you to move closer.
- A recognition that is not clearly sure shows "Is this right?" before it starts a check or ends a session. Consent for staff assistance is never given by voice. The form opens and you tap.

Optional settings are listed in `.env.example`. Arabic recognition is on; Arabic spoken replies use the device's own voice because Deepgram's Aura voices do not cover Arabic.

## More data sources

Each is optional and turned on by a key in `.env`. `python -m skyassist.doctor` checks them.

- `OPENROUTER_API_KEY` with `OPENROUTER_MODEL` (default `nvidia/nemotron-3.5-lightning:free`) and `OPENROUTER_FALLBACK_MODEL` (default `recursive/flint-v1.0:free`): understands free text and may word answers. Code checks every answer.
- `EXA_API_KEY`: web search for questions that are not about the passenger's own journey (a pharmacy, the weather). The answer says it came from the web and links its sources.
- OpenSky and adsb.lol: live aircraft near DXB with arrival estimates, refreshed at most once a minute in total.

### OAG in the passenger workspace

| OAG product | Server-side key | Try in Ask SkyAssist |
| --- | --- | --- |
| Master Data | `OAG_MASTER_DATA_API_KEY` | `Airport DXB`, `Airline EK`, `Aircraft A320` |
| Flight Info v2 | `OAG_FLIGHT_INFO_API_KEY` | `Flight status EK202 from JFK to DXB on YYYY-MM-DD` |
| Connections | `OAG_CONNECTIONS_API_KEY` | `Connections from DXB to LHR tomorrow` |

Subscribe to the products in the [OAG API catalogue](https://developers.oag.com/apis). SkyAssist falls back to `OAG_API_KEY` when a product-specific key is blank. Run `python -m skyassist.doctor` to verify each subscription. Lookups send public flight numbers, airport or equipment codes, and dates. They show OAG as the source and work independently of the journey being tracked. Connections are scheduled options whose availability and transfer arrangements must be confirmed with the airline. In live mode, Flight Info can also provide timestamped evidence to the journey agent.

## The Dubai plans and the live map

The canvas draws Dubai International Terminal 3 the way the Dubai Airports guides do: Concourse B (gates B1 to B32 on two rows, duty free blocks A to N in the middle, services along one spine), Concourse C on the same pattern, and a Terminal 3 overview that shows how the concourses join (walk between B and C, train to A, train to the D gates in Terminal 1). The plans are redrawn and simplified in `web/components/canvas/dubai-plan.ts`; the routing graph's node ids are mapped onto them, so the live route, the gate plate and the passenger's position land in the right place and the plan switches concourse as the passenger crosses.

The server owns every fact on it: the layout (`/map/layout`), the live layer (`/journeys/{id}/live`) and the passenger position (`/journeys/{id}/position`). Position is source agnostic. Anything that can say where the passenger is can post a node or a point, and the server snaps it to a walkable corridor on the right level. Tapping a waypoint or **Walk the route** feeds the same endpoint. Before a journey exists, the pin sits at the kiosk (`SKYASSIST_KIOSK_NODE`, default `c22`).

## How the kiosk decides

The brief rewards one separation above the rest: what a language model does, and what code does.

| Code (Python, no model) | Language model (optional) |
| --- | --- |
| Holds the evidence and its age, routes, does the transfer sum, sets the verdict (safe, tight, at risk, impossible, unverified) against boarding close, builds the Promise Ledger, runs the approval state machine, decides what is shown | Understands a free-text message into slots, and may word an answer from the ledger's own sentences |

- **Promise Ledger** (`skyassist/ledger.py`). Every statement the kiosk will make is verified (a fresh source, or a number computed from one), pending (true only if a person acts) or unknown. The kiosk shows it as a card. An unknown entry carries no values, so a stale gate is never printed as a fact.
- **Grounding check** (`skyassist/grounding.py`). A draft is refused if it has a number, time, gate or flight code that no verified or pending entry holds (spelled-out numbers and Arabic digits included), promises an outcome, is more hopeful or bleaker than the verdict, claims staff are coming without a verified dispatch, or repeats an instruction. A refused draft is regenerated once with the reasons, then replaced by wording built from the ledger. With no model key the template is used.
- **Fail closed.** If any fact under the verdict is not verified the verdict is **unverified**, never a guess. An emergency phrase always points to a staffed help point and never goes near a model.
- **Humans approve dispatch.** A request is a case; only a coordinator's decision moves it on. The approval window expires after five minutes and the kiosk then says to go to the help point. Decisions and dispatch are idempotent and durable (the outbox).
- **Judge view** (scales icon). Shows what the model did and what the code did side by side, the live scoreboard, and a box where you can paste any sentence a model might write and watch the check refuse it.
- **Scoreboard** (`GET /eval/scoreboard`, `python -m skyassist.evals`). 32 labelled cases (promises, invented gates and times, spelled-out numbers, staff claims, injected instructions, stale telemetry, verdict contradictions, and good drafts that must pass), whether every dispatch in the database has a coordinator decision, and the event-loop lag while many answers are written at once. Targets: 0 unverified promises shown, 0 invented facts shown, 100 percent of dispatches approved by a person.

The API for the chat is `POST /journeys/{id}/ask` (returns the answer, its source and the trace) and `POST /journeys/{id}/check-draft`.

## Tests

    UV_CACHE_DIR=$TMPDIR/uv-cache uv run pytest tests/test_launcher.py -q
    uv run pytest -q --ignore=tests/test_web_e2e.py --ignore=tests/test_launcher.py   # stop the running API first
    node --test web/lib/voice/intent.test.mjs                                          # spoken-code and command parser

`tests/test_live_map.py` covers the layout, snapping to corridors, route progress and who may move a pin. `tests/test_voice.py` runs the whole voice relay against a local stand-in for Deepgram, so it needs no key and no network.

This checks the launcher syntax, the dry-run output and URLs, the unknown-subcommand exit, the no-dash rule and the absence of the retired UI ports. It is offline and starts no server.

The browser click-through of both UIs starts its own service on port 8021:

    UV_CACHE_DIR=$TMPDIR/uv-cache node web/e2e/run.mjs

It prints one PASS or FAIL line per step. `tests/test_web_e2e.py` wraps it.

## The C01 demo

C01 is a family that needs a step-free route. They arrive on DXB-402 and connect on ZX 515. ZX 515 runs on two dates, so the kiosk asks for the date; the demo uses 2026-11-19. The boarding cutoff is 10:35. The click path is in [docs/DEMO-SCRIPT.md](docs/DEMO-SCRIPT.md).

## Where things are

- Front end: `web/` (Next.js static export into `web/out`; source in `web/app` and `web/components`).
- Demo database: `data/skyassist-demo.sqlite3`, generated. `reset` rebuilds it.
- Fixtures: `data/`. Contracts: `docs/CONTRACTS.md` and `docs/ROUTING-SPEC.md`.
- Retired first UI: `legacy/`. The launcher does not run it.
- Operator checks: [docs/OPERATOR-CHECKLIST.md](docs/OPERATOR-CHECKLIST.md).

## Known limits

- The Dubai plans are redrawn and simplified from the public guides. They keep gate order and how the concourses join, not every shop. Route and gate data are synthetic; the screen says so.
- The grounding check is rule based. It is strict on numbers, codes, promises and staff claims. It does not judge tone or whether a reply is helpful. Extend `skyassist/grounding.py` and the cases in `skyassist/evals.py` when you find a new way a draft can mislead.
- Voice has been run end to end only against a local stand-in for Deepgram, with a synthetic audio source. It has not been run with a real Deepgram key or a real microphone in a loud room. Test it there before an event, and tune the thresholds in `web/public/voice/capture-worklet.js` and `web/lib/voice/engine.ts` if needed.
- The map position comes from tapping the plan and the walk control in this build. Nothing here reads a positioning sensor.
- Ollama is not installed on the build machine. The template extractor is the offline path. The free-text "Ask SkyAssist" input was not exercised.
- Arabic strings (`data/i18n/ar`) are not reviewed.
- Journey routing uses synthetic flights and an indoor graph for the demo. The Terminal map overview uses [Dubai Airports transfer guidance](https://dubaiairports.ae/information/transfers) and links to its [official interactive map](https://dubaiairports.ae/express-maps?areafilter=connectingflights).
- The AirLabs adapter returns unavailable unless `AIRLABS_API_KEY` is set.
- Demo tokens are fixed defaults in code. The bind is 127.0.0.1, so a phone cannot reach the route QR link.
- Re-run `web/e2e/run.mjs` after any `web/` change. It passes at 1440 by 900, 768 by 1024 and 320 pixel widths.
