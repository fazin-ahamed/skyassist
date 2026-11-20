# Demo script: C01, about five minutes

Audience script for the synthetic DXB kiosk. Run it after the operator checklist passes. Setup is not timed. Every expected text below comes from the real-browser click-through in `web/e2e/run.mjs`, which passes all 15 steps at desktop, portrait tablet (768 by 1024) and 320 pixel widths. Target result: gate **B14**, **Tight**, **6 min to spare**, walking **21 to 29 min**, boarding closes **10:35**.

The idea to say out loud: a language model understands the passenger and may word an answer. Code holds every fact, does every sum, decides every approval and checks every sentence before it reaches the screen. The kiosk can show you both sides.

## Setup (not timed)

1. Start clean: `./run_demo.sh down`, then `./run_demo.sh reset`, then `./run_demo.sh up` (Windows: `.\run_demo.ps1 reset`, then `.\run_demo.ps1`). Expected last lines: `SkyAssist demo is up` and two URLs.
2. Open the coordinator console at http://127.0.0.1:8000/staff/#token=demo-coordinator. Click **Scenario controls**, then **Apply** on these four C01 rows in order: **Close lift**, **Reopen lift**, **Readiness: staff handoff pending**, **Change gate to B14**.
   Why: without these events the live gate is not the C01 one (ZX 515 starts at B12, which has no step-free route in the demo map, and the kiosk correctly says so).
3. Open the passenger kiosk at http://127.0.0.1:8000/ in a second window. Use a portrait tablet window (768 by 1024) for the intended look.
4. Optional: put `DEEPGRAM_API_KEY` and `OPENROUTER_API_KEY` in `.env`, then run `python -m skyassist.doctor`. Without them everything below still works; the microphone shows that voice is not set up and answers are written from the ledger.

## Step 1: say it in your own words (about 60 s)

1. The first screen asks **Where are you connecting?** over the Dubai Terminal 3 plan, a live departures board and the aircraft radar. Tap the dark pill at the bottom.
   Expected: it morphs into a chat with the greeting **Hi, I'm SkyAssist.**
2. Type: `We land on DXB-402 and fly on ZX 515, my mum uses a wheelchair`, then send.
   Expected: the gate plate appears at the top with **--**, and the agent asks **Confirm the date of your flight.** with two buttons. It asked only what it could not know.
3. Tap **Service on Thu 19 Nov**. Expected: the plate flips to a gate and the agent asks **Choose the place where you are now.** with **I am at C22 parked aircraft pickup point**.
4. Tap that button. Expected: **B14** flips in on the yellow plate, **Tight**, **6 min to spare**, a bar from **Now** through **Walk 21 to 29 min** to **Boarding closes**, and the agent says **It is tight, so start moving now. Boarding closes 10:35. Getting there takes 21 to 29 min. 6 min to spare in the worst case.** under the line **Written from verified facts**.

## Step 2: the canvas follows the conversation (about 60 s)

1. In the chat tap **Why this status?**
   Expected: the **What I can stand behind** card slides to the front with a ring around it: five entries, each **Verified** with the time it was checked.
   Say: every sentence the kiosk will say is one of verified, pending or unknown. Nothing else gets on screen.
2. Tap **Where is my gate?** The gate card comes forward. Tap **Show my route**: the Dubai plan zooms to the route, which starts in Concourse C and crosses to Concourse B. Tap **Walk the route** to watch the position move; the plan switches concourse by itself.
3. Switch the plan tabs to **Concourse B** and **Terminal 3** to show how the concourses join.

## Step 3: ask it to lie (about 60 s)

1. Tap the scales icon (**How it decides**). Expected: **0** unverified promises shown, **0** invented gates or times shown, **100%** dispatches approved by a person, **32/32** red-team cases, and the slowest response under load.
2. Left pane: what the language model did (what understood the message, and any draft it wrote with **Accepted** or **Refused**). Right pane: what the code did (the ledger and the margin sum: close 10:35 minus worst walk 29 min is margin 6 min, so tight).
3. Under **Try to make it lie** tap **Promise**, then **Invented gate**, **Staff on the way**, **Injected order**, **Invented number**.
   Expected: each is **Refused** with the reason, for example **'Don't worry' promises an outcome the system cannot guarantee**.
   Say: the same check runs on every model draft in production. A refused draft is regenerated once, then replaced by wording written from the ledger.

## Step 4: a person decides (about 60 s)

1. Close the judge view. Tap **Ask for staff help**. Expected: the request form for **C22 parked aircraft pickup point**. Tick both boxes (a person must consent, never the kiosk) and **Submit request**.
   Expected: the **Staff help** card shows **Requested** done and **Coordinator approves** waiting; the ledger shows **Pending**; the instruction becomes **Wait at your pickup point until staff confirm the handoff.**
2. In the coordinator console the case appears as an alert. Click **Approve**.
   Expected on the kiosk, within two seconds and without a tap: an **Approved** stamp lands on the Staff help card, the third step starts waiting, and the ledger line reads **Approved. Waiting for confirmation that a staff member is on the way** as **Pending**.
3. In the console click **Mark dispatched**. Expected on the kiosk: **A staff member is on the way**, now **Verified**.
   Say: nothing is dispatched without a coordinator decision. A coordinator who never answers lets the request expire after five minutes and the kiosk says to go to the staffed help point.

## Step 5: change the world (about 45 s)

1. Live gate change (optional, needs a fresh database): in setup step 2 apply only the first three events. Run Step 1 as written.
   Expected: after the location answer the kiosk says to go to the staffed help point, because gate B12 has no step-free route in the demo map. Now apply **Change gate to B14** in the console. Within two seconds the plate flips to **B14**, the route draws, the agent says **Your gate changed to B14** and then gives the new verdict in grounded words.
2. Click **Go offline** under **Offline rehearsal** in the console. Expected: the top bar shows the offline chip, the microphone says voice is unavailable, and the gate, the route, the staff request and every answer keep working.
3. Tap **عربي**. Expected: the whole screen mirrors, the plate and flight codes stay left to right, and the answers are in Arabic. The grounding check reads Arabic digits too.

## If something goes wrong

- The kiosk says **Go to the nearest staffed help point** straight after the location answer: the C01 gate event was not applied (gate B12). Apply the four events in setup step 2.
- The page shows message keys instead of words for a second: the copy file is still loading; wait.
- `python -m skyassist.evals` prints the same scoreboard in the terminal and exits non-zero if any case fails.
