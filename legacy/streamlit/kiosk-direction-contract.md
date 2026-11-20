---
version: 1
slug: "kiosk-app-py"
primary_target: "kiosk/app.py"
related_targets: ["staff/app.py"]
---

## Scope

Mode: Operate. Surfaces: passenger kiosk (kiosk/) and coordinator dashboard (staff/), both Streamlit rendering server-owned view models. Shared component set so status labels mean the same everywhere.

## Audience and task

Stressed passenger, standing, glare, luggage, maybe a child or wheelchair. Job: learn in seconds whether the connection is makeable and what to do next. Coordinator: review pickup, gate, deadline, time remaining, then approve or decline.

## Constraints

World is pinned by SKYASSIST-PLAN.md section 6 (tokens, fonts, status colors, 56 px targets, 1440x900 landscape, portrait stack). No concept roll: a brief-pinned direction beats the roll. All data synthetic and labelled "Simulation · Fictional terminal map". Arabic wired but flagged unreviewed. No logo supplied: text wordmark.

## Direction contract

THESIS: The kiosk is a departures-hall sign that answers one question, can I make this connection and what do I do next. It refuses the dashboard of cards, ticks and charts, and refuses any confident look for an estimate or an unknown.

OWN-WORLD: Warm stone canvas #F7F6F1, white surfaces, deep teal #005F61 owning directions and primary actions, amber and red held back for time-critical states. Source Sans 3 and Noto Sans Arabic, tabular numerals, gate numerals at signage scale. Flat surfaces, no glass, one elevation level only for the sticky action bar. Controls radius 12, panels 20, never pills. Identity motif: the gate plate, a teal signage rectangle with the gate in 72 px numerals, and a metro-style route line with numbered round stops, one thick teal stroke per floor.

STORY: Passenger sees verified gate, cutoff and status before anything else, taps one dominant action, and watches the same panel update in place: a gate change visibly revokes the old route before the new one appears, a pending assistance request stays pinned with a staffed alternative until a coordinator decides.

FIRST VIEWPORT: 1440x900. Top bar: wordmark, EN / عربي, Help, End session with idle countdown. Tab row: Departures, Arrivals, Find my connection, search. Left 40 percent: scrollable flight list. Right 60 percent: gate plate with level and concourse, boarding cutoff with source and time, status chip with label and icon, "21-29 min total, including readiness" and the margin line, then Take me there (primary), Request physical assistance (secondary), Ask SkyAssist mic and Type. Route line map and numbered text steps below; aircraft section last and lazy.

FORM: Wayfinding signage grammar (airport sign systems, metro line maps), pinned by the brief, so no roll and no seed key.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved

Exact ViewModel field names (S0 delivers docs/CONTRACTS.md). Real event hardware size and orientation. Native review of Arabic strings.
