# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

BoardUI (React + Tailwind v4) front end in `web/`, built as a Next.js static export and served by the Python FastAPI service on one port. Python 3.14, SQLite, asyncio. The user said any stack is fine; the earlier Streamlit apps are archived under `legacy/`.

## Users

- Passengers in transit at DXB or AUH on an airport kiosk, often stressed and short on time: families with children, travelers needing a wheelchair or step-free route, people with a tight connection. Multilingual (English, Arabic). Standing at a public touchscreen, sometimes with luggage.
- Mobility coordinators (staff) who review and approve or decline assistance requests from a separate dashboard and process.
- Competition judges watching a roughly four minute live demo (inferred from the brief, not interviewed).

## Product Purpose

SkyAssist resolves a passenger's connection: current gate, boarding cutoff, a usable step-free route, a conservative transfer-time range, and assistance status. It keeps monitoring while a coordinator decides, so a gate change or lift closure revokes the old route before a new one shows. Success: ten reproducible connection scenarios pass, and an externally approved assistance request updates the live kiosk without a refresh.

## Positioning

Staff own physical dispatch and gate holds. The kiosk states only what evidence supports: verified gate, labelled source and time, estimates shown as ranges, "unknown" shown as unknown. A competitor that guesses a cutoff or hides stale data cannot truthfully copy that.

## Operating Context

Public touch kiosk, landscape 1440x900 reference with a phone-width layout, viewed at distance, glare, noise, idle timeout with end-session. The connection is shown as one handling tag (gate, boarding cutoff, time needed, actions) with the agent beside it as a printed receipt of what it checked and changed; it interrupts only when it needs an answer or something changed. The coordinator console is a rack of case tags sorted by time to cutoff, English only. The coordinator dashboard is a second browser client plus a CLI second process. Demo runs offline against a demo terminal map and a replay clock. Phone handoff via short-lived QR route page on a private LAN.

## Capabilities and Constraints

- Departures and Arrivals tabs, flight details, landmark search, route graphic with numbered steps, Take me there, Request physical assistance, push-to-talk voice, QR phone handoff, aircraft map (optional, can fail independently).
- Status vocabulary is fixed: Time available, Tight connection, Transfer unlikely before cutoff, May miss boarding, Boarding cutoff passed, Timing unverified, Boarding cutoff unavailable.
- Transfer range shown as "21-29 min total, including readiness" style, rounded outward.
- Targets WCAG 2.2 AA, 56 px minimum touch targets, keyboard path for every action, reduced-motion alternative, RTL for Arabic.
- Arabic ships wired but flagged unreviewed until a native reviewer signs off (user decision).
- Open decisions: event launcher and hardware, AirLabs entitlement, real gate coordinates, boarding-cutoff contracts, real dispatch hooks. All mocked.

## Brand Commitments

Name SkyAssist. Visual reference is the supplied terminal image; tokens, type and status colors are specified in SKYASSIST-PLAN.md section 6 and are binding. The SkyAssist logo is a custom SVG in web/public and web/components/agent/logo.tsx.

## Evidence on Hand

All data is demo data. No real testimonials, statistics, customer names, compliance claims, airport floor plans or gate coordinates exist. Do not fabricate any.

## Product Principles

1. Verified fact, estimate, and unknown look different and never blur.
2. The most urgent next action leads; maps and detail follow.
3. A stale or conflicting source removes the instruction, not just the confidence.
4. Every screen state has a human alternative: staffed help, typed input, text steps.
5. Server owns truth; the screen renders it and never computes it.

## Accessibility & Inclusion

WCAG 2.2 AA, 4.5:1 body text, visible focus, screen reader order, 200% zoom, large text, Arabic RTL with Latin flight codes kept left-to-right, announcements only for meaningful state changes.
