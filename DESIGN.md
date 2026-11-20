---
name: SkyAssist
description: A paper baggage tag on a dark or light conveyor belt, with the agent printing a receipt beside it.
colors:
  belt: "oklch(0.925 0.009 270)"
  belt-line: "oklch(0.885 0.011 270)"
  belt-soft: "oklch(0.4 0.02 270)"
  belt-dark: "oklch(0.15 0.02 270)"
  belt-line-dark: "oklch(0.19 0.022 270)"
  belt-soft-dark: "oklch(0.78 0.015 270)"
  tag-paper: "oklch(0.996 0.002 270)"
  tag-wash: "oklch(0.962 0.006 270)"
  tag-ink: "oklch(0.17 0.025 270)"
  tag-ink-soft: "oklch(0.42 0.02 270)"
  tag-rule: "oklch(0.835 0.01 270)"
  accent-50: "oklch(0.962 0.018 272.314)"
  accent-300: "oklch(0.785 0.115 274.713)"
  accent-500: "oklch(0.585 0.233 277.117)"
  accent-600: "oklch(0.511 0.262 276.966)"
  accent-700: "oklch(0.457 0.24 277.023)"
  accent-800: "oklch(0.398 0.195 277.366)"
  status-lime-text: "oklch(0.453 0.124 130.933)"
  status-lime-bg: "oklch(0.938 0.127 124.321)"
  status-orange-text: "oklch(0.47 0.157 37.304)"
  status-orange-bg: "oklch(0.901 0.076 70.697)"
  status-rose-text: "oklch(0.455 0.188 13.697)"
  status-rose-bg: "oklch(0.892 0.058 10.001)"
  status-blue-text: "oklch(0.424 0.199 265.638)"
  status-blue-bg: "oklch(0.882 0.059 254.128)"
typography:
  numeral-hero:
    fontFamily: "Barlow Condensed, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(5.5rem, 12.5vw, 11rem)"
    fontWeight: 700
    lineHeight: 0.84
    letterSpacing: "-0.012em"
    fontFeature: "tnum"
  numeral-xl:
    fontFamily: "Barlow Condensed, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(3.25rem, 5.4vw, 4.75rem)"
    fontWeight: 700
    lineHeight: 0.92
    letterSpacing: "-0.008em"
    fontFeature: "tnum"
  numeral-lg:
    fontFamily: "Barlow Condensed, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 700
    lineHeight: 1
    fontFeature: "tnum"
  numeral-md:
    fontFamily: "Barlow Condensed, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.625rem"
    fontWeight: 600
    lineHeight: 1.1
    fontFeature: "tnum"
  title:
    fontFamily: "Barlow, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: "2.125rem"
  subtitle:
    fontFamily: "Barlow, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: "1.625rem"
  body:
    fontFamily: "Barlow, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.25rem"
  body-strong:
    fontFamily: "Barlow, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: "1.25rem"
  note:
    fontFamily: "Barlow, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: "1.125rem"
rounded:
  control: "8px"
  notice: "12px"
  flag-tab: "12px"
  tag: "14px"
  dot: "9999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  tag:
    backgroundColor: "{colors.tag-paper}"
    textColor: "{colors.tag-ink}"
    rounded: "{rounded.tag}"
    padding: "24px"
  receipt:
    backgroundColor: "{colors.tag-paper}"
    textColor: "{colors.tag-ink}"
    rounded: "{rounded.tag}"
    padding: "12px 16px 20px"
  button-primary:
    backgroundColor: "{colors.accent-600}"
    textColor: "{colors.tag-paper}"
    rounded: "{rounded.control}"
    height: "56px"
    padding: "0 24px"
  button-secondary:
    backgroundColor: "{colors.tag-paper}"
    textColor: "{colors.tag-ink}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "0 14px"
  pill-accent:
    backgroundColor: "{colors.accent-50}"
    textColor: "{colors.accent-800}"
    rounded: "{rounded.control}"
    padding: "4px 12px 4px 8px"
  pill-neutral:
    backgroundColor: "{colors.tag-wash}"
    textColor: "{colors.tag-ink}"
    rounded: "{rounded.control}"
    padding: "4px 12px 4px 8px"
  status-pill-lime:
    backgroundColor: "{colors.status-lime-bg}"
    textColor: "{colors.status-lime-text}"
    rounded: "{rounded.control}"
  status-pill-orange:
    backgroundColor: "{colors.status-orange-bg}"
    textColor: "{colors.status-orange-text}"
    rounded: "{rounded.control}"
  status-pill-rose:
    backgroundColor: "{colors.status-rose-bg}"
    textColor: "{colors.status-rose-text}"
    rounded: "{rounded.control}"
  status-pill-blue:
    backgroundColor: "{colors.status-blue-bg}"
    textColor: "{colors.status-blue-text}"
    rounded: "{rounded.control}"
---

# Design System: SkyAssist

## Overview

**Creative North Star: "The Baggage Tag on the Belt"**

SkyAssist is one paper tag laid on a conveyor belt. The tag carries the facts a traveller needs (gate, cutoff, minutes of margin) as big condensed numerals; the belt is the flat ground behind it. The agent is not a chat window. It is a printer beside the tag: a state line, a list of what it is watching, and a receipt that prints a new line only when something changed. The coordinator console is the same world seen from the staff side: a cutoff lane, a rack of tags, and the open case as a full tag with its actions on the stub.

The ground flips with the theme (pale grey-lavender belt, near-black belt) while paper stays paper white in both, so a tag looks like the same object in a bright hall and a dim one. Colour is scarce and assigned: indigo is the one action and brand colour; four status tones mean connection status and nothing else; everything else is ink, paper and hairline. Depth is stacking, perforation and one short contact shadow, never blur.

The system is built on BoardUI semantic tokens with its accent ramp re-pointed to indigo and its font wired to Barlow. Tags opt back into the light token set through the `.paper` scope, so every stock control inside a tag stays light in dark mode.

**Key Characteristics:**
- Flat belt ground with faint 80px vertical slat lines; paper tags and receipts on top.
- Gate, time and range numerals in Barlow Condensed 700, tabular, always left to right.
- Indigo is the only action colour; lime, orange, rose and blue are connection status only.
- One short contact shadow plus a hairline rule; no blur, no glow, no gradients on surfaces.
- Perforated tear lines with notches, punch holes, a zigzag receipt foot, and a status flag as the only ornament, each carrying structure.
- Staff console is English only. Arabic mirrors the layout with numerals kept LTR and is unreviewed.
- The SkyAssist logo is a custom SVG and is not part of this system's tokens; leave it unchanged.

## Colors

A cool lavender-tinted neutral family (hue 270) with one indigo accent and four muted status tones.

### Primary
- **Indigo Action** (accent-600, oklch(0.511 0.262 276.966)): primary buttons (via the BoardUI accent gradient, 500 to 600), caret, selected and ghost controls. Accent ramp 50 to 950 is the Tailwind indigo ramp. The watching dot uses accent-500; the sent-by-you receipt lines use accent-700; the assistance row dot and the accent pill use accent-50 and accent-800.

### Neutral
- **Belt** (oklch(0.925 0.009 270) light, oklch(0.15 0.02 270) dark): the page ground. Slat lines are belt-line (oklch(0.885 0.011 270) light, oklch(0.19 0.022 270) dark). Secondary text on the belt is belt-soft (oklch(0.4 0.02 270) light, oklch(0.78 0.015 270) dark).
- **Tag Paper** (oklch(0.996 0.002 270)): tags, receipts, and the notch and punch fills match the belt, not the paper.
- **Tag Wash** (oklch(0.962 0.006 270)): the neutral pill and quiet inset areas on a tag.
- **Ink** (oklch(0.17 0.025 270)) and **Ink Soft** (oklch(0.42 0.02 270)): all text on paper. Labels use ink-soft.
- **Tag Rule** (oklch(0.835 0.01 270)): the 1px tag border, dashed row dividers, tear lines, inactive dots.

### Status tones (connection status only)
- **Lime** (text oklch(0.453 0.124 130.933) on oklch(0.938 0.127 124.321)): Time available.
- **Orange** (oklch(0.47 0.157 37.304) on oklch(0.901 0.076 70.697)): Tight connection; also the agent waiting on an answer.
- **Rose** (oklch(0.455 0.188 13.697) on oklch(0.892 0.058 10.001)): transfer unlikely, may miss boarding, cutoff passed; late minutes on the staff lane.
- **Blue** (oklch(0.424 0.199 265.638) on oklch(0.882 0.059 254.128)): timing unverified.

Case and assistance state do not use these. They use the indigo accent pill and the neutral pill.

### Named Rules
**The Paper Stays Paper Rule.** Tags and receipts are paper white in both themes. Only the belt flips. Any new surface that holds content is a `.paper` tag, so BoardUI controls inside it resolve to their light tokens.

**The Tones Are Status Rule.** Lime, orange, rose and blue mean connection status and nothing else. Never use them for decoration, categories, or case and assistance state.

**The One Action Colour Rule.** Indigo is the only brand and action colour. Do not introduce a second accent.

## Typography

**Display Font:** Barlow Condensed (600, 700; falls back to ui-sans-serif, system-ui)
**Body Font:** Barlow (400, 500, 600, 700; falls back to ui-sans-serif, system-ui)
**Arabic:** Noto Sans Arabic, applied under `lang="ar"` ahead of Barlow.

**Character:** A signage pairing. The condensed face is used only for numerals that carry facts, so a gate or a time reads from across a hall; Barlow does everything else at small, calm sizes.

### Hierarchy
- **Numeral Hero** (Barlow Condensed 700, clamp(5.5rem, 12.5vw, 11rem), line-height 0.84, tracking -0.012em, tabular): the gate on the first screen.
- **Numeral XL** (700, clamp(3.25rem, 5.4vw, 4.75rem), 0.92, -0.008em, tabular): cutoff time, minutes range, minutes left on the staff tag.
- **Numeral LG** (700, 2.5rem, 1, tabular): tag section titles, staff rack gate, margin minutes.
- **Numeral MD** (600, 1.625rem, 1.1, tabular): minutes left in a rack row.
- **Title** (Barlow 600, 1.5rem / 2.125rem): occasional strong statements.
- **Subtitle** (600, 1.125rem / 1.625rem): flag status text, agent state line, journey heading.
- **Body** (400/500/600, 0.875rem / 1.25rem): the working size for labels, rows, receipt lines.
- **Note** (500/600, 0.8125rem / 1.125rem): pill text, watching-row notes.

### Named Rules
**The Numerals Read Left to Right Rule.** Every gate, time and range numeral is Barlow Condensed, tabular, and wrapped in `dir="ltr"` (or `bdi dir="ltr"`), including in Arabic.

**The Condensed Is For Facts Rule.** Barlow Condensed appears only on numerals that carry facts. Never set prose or buttons in it.

## Layout

The belt page uses a centred container with a 1480px max width on the kiosk and 1600px on the staff console, 16px side padding rising to 24px at `sm`, and 24px gaps between tags.

Kiosk (`/`): below `lg`, a single column with the tag first; from `lg`, a sticky left agent rail 23rem wide beside a flexible tag column. The rail is `calc(100dvh - 7.5rem)` tall and sticks 6rem from the top. The header is a fixed-height strip on the belt with a hairline in belt-line.

Staff (`/staff`): a new-request slip in normal flow at the top (not an overlay), then a two-column grid from `lg` of `minmax(340px, 4fr)` and `8fr`. The left column holds the cutoff lane (minutes to boarding cutoff, left is urgent) and the urgency-sorted rack of case tags with the operations log receipt under it. The right holds the open case as a full tag. A collapsed Scenario controls drawer sits at the foot.

Rhythm: tag interior padding is 20 to 32px (px-5 to px-8), rows are 8 to 12px apart, sections inside a tag are separated by a tear, not a heading rule. Primary buttons are 56px high, controls and quick questions 40 to 48px.

RTL: logical properties throughout (`ps`, `pe`, `inset-inline-end`); the flag tab hangs from the inline end. Layout mirrors in Arabic; numerals stay LTR. Arabic is unreviewed.

## Elevation & Depth

Flat on the belt, stacked with paper. A tag has a 1px hairline in tag-rule and one short contact shadow, and nothing else. Depth beyond that is physical: notches bitten from the tear, punch holes, a zigzag receipt foot, and the receipt list fading at its bottom edge.

### Shadow Vocabulary
- **Contact shadow** (`box-shadow: 0 1px 0 oklch(0 0 0 / 0.05), 0 14px 14px -14px oklch(0 0 0 / 0.38)`): the only shadow, on `.tag`. The negative spread keeps it under the bottom edge.

### Named Rules
**The Contact Shadow Only Rule.** No shadow beyond the tag's contact shadow. No blur, glow, backdrop filters or stacked ambient layers on any surface.

## Shapes

Tags are 14px rounded rectangles with a 1px tag-rule border. Controls and pills are 8px (rounded-lg for buttons, inputs and quick questions; rounded-md for status pills). Notices and slip icon wells are 12px. The status flag is a band across the top of the tag on phones (13px top corners) and, from `sm` (40rem), a tab hanging 28px from the inline end with 12px bottom corners. The receipt has 14px top corners and a zigzag foot (18px by 9px teeth, a conic-gradient mask). Dots are full circles: the 12px watching dot, 10px row-health dots, 22px punch holes.

The tear is a 2px dashed tag-rule line with a 24px notch at each edge, clipped to its inner half and filled with belt colour so it reads as a bite out of the paper.

## Components

### Tag
Paper white, 14px, hairline, contact shadow. Content sections are separated by tears. The open case and the journey are full tags with a flag; the rack uses compact tags.

### Status flag
A band or tab in one status tone carrying an icon and the status in Subtitle weight. The status lives here once; slips add reasons rather than repeat it.

### Buttons
- **Shape:** 8px.
- **Primary:** BoardUI accent gradient (accent-500 to 600), white text, 56px high on the tag, 44 to 48px elsewhere.
- **Secondary:** paper-light BoardUI secondary, 40px for quick questions, 48px in the staff header.
- **Staff actions:** Approve (primary), Decline (secondary), Mark dispatched sit on the stub of the open case tag.

### Pills
Status pills use a tone pair and an icon, 8px, `py-1 ps-2 pe-3`. Case and assistance state use the accent pill (accent-50 on accent-800) or the neutral pill (tag-wash on ink).

### Agent rail
A sticky left column. Top to bottom: a state line (watching dot with 2.4s heartbeat, Subtitle state, last-checked time), a Watching tag listing gate, cutoff, route, assistance and aircraft with a health dot, value and a note carrying source and verification time, the Activity receipt, four quick questions (time, gate, why, help) answered only from server fields, and a typed Ask field with a submit button.

### Receipt
Narrow paper with a zigzag foot. Newest line on top. Each row: time in tabular ink-soft, one strong line and optional soft lines, a small icon coloured by kind (accent info, ink good, orange warn, rose risk, accent-700 you). Rows divide with dashed tag-rule lines. A new line prints in with a 0.55s `cubic-bezier(0.22, 1, 0.36, 1)` clip-and-slide, staggered by 70ms up to seven rows.

### Slips
The agent interrupts only through a question slip (SkyAssist needs one answer) or a change slip (gate changed, gate stale or conflicting, status worsened, assistance state changed, route changed). Changes that do not worsen anything stay quiet and print only on the receipt. Sticky slips wait for the traveller; others fall back to the quiet pill.

### Staff console parts
Cutoff lane, case rack, open-case tag with stub actions, operations log receipt, and the new-request slip. The scenario controls drawer is collapsed by default.

### Motion
Print-in (0.55s) and watch heartbeat (`watch-ping`, 2.4s, scale 0.7 to 2.4, fading out, infinite). Buttons press to 0.98 scale with colour transitions of 150ms. Both authored animations are disabled under `prefers-reduced-motion`, and `app.css` clamps all other animation and transition durations to 0.01ms.

### Browser surfaces
Selection is oklch(0.86 0.09 277) with ink text, scrollbars are thin with a translucent lavender thumb, and the caret is accent-600.

## Do's and Don'ts

### Do:
- **Do** put every content surface on a `.paper` tag or receipt, so it stays paper white in dark mode.
- **Do** set gate, time and range as Barlow Condensed numerals, tabular, in an LTR wrapper.
- **Do** carry source and verification time on every fact the agent shows.
- **Do** print only what changed on the receipt; keep unchanged facts in the Watching list.
- **Do** use indigo for action and brand, and the accent or neutral pill for case and assistance state.
- **Do** separate sections inside a tag with a tear and divide list rows with a dashed tag-rule line.
- **Do** keep the staff console in English and mirror the kiosk layout with logical properties in Arabic.

### Don't:
- **Don't** use lime, orange, rose or blue for anything except connection status.
- **Don't** add shadows beyond the contact shadow, or any blur, glow or backdrop filter.
- **Don't** flip tags to dark in dark mode; only the belt flips.
- **Don't** set prose or buttons in Barlow Condensed.
- **Don't** interrupt the traveller with anything but a question slip or a change slip.
- **Don't** answer quick questions from anything but server fields.
- **Don't** redraw or recolour the SkyAssist logo.
