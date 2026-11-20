---
version: 1
slug: "web-app-page-tsx"
primary_target: "web/app/page.tsx"
related_targets: ["web/app/staff/page.tsx"]
---

## Scope

Mode: Operate. Surfaces: the passenger kiosk (web/app/page.tsx) and the coordinator console (web/app/staff/page.tsx), a BoardUI React front end (Next.js static export) served by the API. Both render server-owned view models and never compute facts.

## Audience and task

A passenger at a public touch kiosk, standing, in glare, short on time: wants gate, boarding cutoff, time needed and the next step at a glance, and wants to see that the agent is watching and what it has checked. A mobility coordinator at a desk who triages assistance requests by time left to the boarding cutoff, approves or declines, and marks dispatch.

## Constraints

The user rejected the liquid-glass capsule design and asked for a complete revamp with dashboards that look good for passengers and staff, and a more capable-looking agent. No glass, blur, aura or WebGL anywhere. BoardUI source components stay the base (Button, Input, Radio, Modal, Select, Toast, Skeleton) and semantic tokens flip for the belt ground; paper tags carry their own fixed light tokens. Status tones (lime, orange, rose, blue) mean connection status only; brand and action colour is indigo. Barlow and Barlow Condensed, Noto Sans Arabic, Remix icons. All data is demo data, no invented statistics. Arabic wired and unreviewed. Logo stays.

## Direction contract

THESIS: A connection is a baggage handling tag. The passenger's journey is one printed tag, gate in huge condensed numerals with the boarding cutoff on a tear-off stub; staff cases are tags on a rack sorted by time to cutoff. The agent is the thermal printer beside it, printing a receipt of what it checked and what changed. It refuses floating capsules, translucent surfaces, hero-metric tiles and rows of identical rounded cards.

OWN-WORLD: A flat belt ground (cool grey-blue light, near-black blue dark) carrying paper tags that stay white in both themes: 12px corners, hairline ink rules, dashed tear lines with semicircle notches at both edges, a punched hole, ink #0B0C14, indigo as the only action colour, status tones only for status. Barlow Condensed 700 for gate, time and range numerals, Barlow for text. The agent log is a narrow receipt strip with a zigzag tear edge, newest line printed on top. No shadows beyond one short contact shadow under a tag; depth is stacking, perforation and rules.

STORY: The passenger sees B14, boarding closes 14:40, 21-29 min needed, 6 spare, and one primary button, within a second. They believe it because every fact on the tag carries its source and time. They tap Take me there or Request assistance. The coordinator sees a rack of tags ordered by minutes left, a cutoff lane showing who is urgent, opens a case as a full tag and approves or dispatches from its stub.

FIRST VIEWPORT: 1440x900. 72px header (logo left; language, theme, help, end session right). Left 360px: the agent rail with a state line, a Watching list (gate, cutoff, lift or route, assistance) each with source and time, then the printed receipt log, then an Ask field. Right: the journey tag, status flag in the top right corner, flights line, gate numeral at 176px, boarding cutoff and transfer range beside it, tear line, spare-time bar, stub with Take me there (primary), Request assistance and the QR hand-off. Before a journey starts the same tag is a blank form: two flight fields, party choice, Start watching. Staff: 72px header, a rack header with state tabs and counts, the cutoff lane, the rack of tags on the left, the open case as a large tag on the right, demo controls in a drawer.

FORM: Baggage handling tag, candidate 7 of 7 on the seeded list, seed key 924a2c0c. The roll ran degraded (no challengers). The user confirmed it in the direction round.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved

Native review of Arabic strings. Whether the receipt log should persist across a page reload. Event hardware glare check for the dark theme.
