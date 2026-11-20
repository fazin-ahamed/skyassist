# Operator checklist: pre-event and rehearsal

Run from the project root. Each line is a check; record the result beside it. Anything marked [FILL IN] is for the event team.

## 1. Machine and launcher (plan phase 0)

- [ ] Launcher plan prints one process and two URLs: `./run_demo.sh up --dry-run` (API on 8000, which serves the kiosk at `/` and the coordinator dashboard at `/staff/`).
- [ ] uv on PATH: `uv --version` -> [FILL IN]
- [ ] Python 3.14 found locally, no download: `uv python find --system 3.14` -> [FILL IN path]
- [ ] Front end built: `web/out` exists. If not, Node 22 and npm are present and the first `up` builds it (needs the npm registry and Google Fonts once).
- [ ] Browser and OS for the kiosk and dashboard: [FILL IN browser and version] / [FILL IN OS]. Check the condensed gate numerals render (Barlow Condensed is bundled, no network needed).
- [ ] Kiosk and dashboard open in two windows on this hardware: [FILL IN who approved]

## 2. Model (plan phase 0)

- [ ] Ollama and Qwen3 present: `command -v ollama` prints a path and `ollama list` shows qwen3.
- [ ] OR template fallback confirmed. Ollama is not on PATH on the build machine. The structured kiosk flows run without it. Tick this line if Ollama is absent on the event machine.

## 3. Network disabled run (plan phase 0)

- [ ] Disconnect the network (or block outbound) before starting.
- [ ] `./run_demo.sh up` reaches "SkyAssist demo is up" within 60 s.
- [ ] `./run_demo.sh status` shows the API `running` and the port open.
- [ ] `curl -s http://127.0.0.1:8000/clock` returns `{"now":"2026-11-19T06:00:00Z"}` (frozen demo clock).
- [ ] The kiosk page loads with the network off: fonts and styles are self-contained in `web/out`.

## 4. Database read and write (plan phase 0)

- [ ] `./run_demo.sh reset` prints `reset: demo DB rebuilt`.
- [ ] A request made on the kiosk (start watching, request assistance) appears in the dashboard queue.
- [ ] After `./run_demo.sh down` and `./run_demo.sh up`, the same case is still listed in the dashboard.

## 5. Second process approval (plan phase 0 and 6)

- [ ] Approve in the dashboard: the alert at the bottom or the Approve button on the case. A toast reads `Approved`.
- [ ] Approve from the CLI, second process: `.venv/bin/python -m skyassist.staff_client list` gives the case id, then `.venv/bin/python -m skyassist.staff_client approve --case <case id>` returns `"state":"approved"`.
- [ ] The kiosk prints the decision on the agent receipt and shows a slip by itself within a few seconds.
- [ ] Dispatch from the dashboard (**Mark dispatched**) or from the CLI: `.venv/bin/python -m skyassist.staff_client dispatch --case <case id> --event-id <new id>` returns `"state":"dispatched"`.

## 6. Interface checks

- [ ] Arabic: tap the language control. The page mirrors right to left and flight and gate codes stay left to right.
- [ ] Help: the help dialog opens and closes.
- [ ] Theme: light and dark both readable (the belt flips, tags stay paper white, status colours).
- [ ] Reduced motion: with the OS setting on, the agent heartbeat dot stops pulsing and receipt lines appear without the print animation.
- [ ] Idle: leave the kiosk alone for 90 s after typing a flight or starting; a countdown dialog (**Session ends in ... seconds**) appears, and at 120 s the page resets.
- [ ] Keyboard: Tab reaches every control, Escape closes dialogs.
- [ ] Browser click-through passes: `UV_CACHE_DIR=$TMPDIR/uv-cache node web/e2e/run.mjs` prints only PASS lines.

## 7. Reset between rehearsals (plan phase 6)

- [ ] Stop: `./run_demo.sh down` (or Ctrl-C in the `up` terminal).
- [ ] Rebuild: `./run_demo.sh reset` prints `reset: demo DB rebuilt`.
- [ ] Start: `./run_demo.sh up`.
- [ ] Clear the old run logs only if you want a clean record. `up` appends; it does not truncate.

## 8. Two cold-start rehearsals (plan phase 6)

- [ ] Rehearsal A, network on: reset, up, run [docs/DEMO-SCRIPT.md](DEMO-SCRIPT.md), down. Time taken: [FILL IN] min.
- [ ] Rehearsal B, network disconnected: reset, up, run the script, down. Time taken: [FILL IN] min.
- [ ] Both rehearsals end with the dispatch and the replay showing one dispatch in the audit table.

## 9. If the service dies during the demo

- [ ] `up` exits 1 when the API exits. The message names the log.
- [ ] Read the log: `run/logs/api.log`.
- [ ] Run `./run_demo.sh down`, then `./run_demo.sh up`. Cases and journeys are in SQLite and survive; reload the kiosk page.
- [ ] Do not re-run `reset` during the event. It deletes cases.
- [ ] If the API still fails after the restart: stop the demo and call the owner below. No offline fallback for live approval is verified on this build.

## 10. Contacts

- Launcher and API owner: [FILL IN name, phone]
- Front end owner: [FILL IN name, phone]
- Fixtures and data owner: [FILL IN name, phone]
- Event technical desk: [FILL IN name, phone]
- Airport operations contact for the demo room: [FILL IN name, phone]
