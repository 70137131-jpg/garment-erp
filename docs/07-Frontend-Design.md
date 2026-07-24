# Frontend Design — "Atelier Monochrome" Operations Workspace

**Document:** 07 — Frontend Design
**Status:** v2.0 (implemented) — supersedes v1.0 "Nightshift" (teal/amber)
**Replaces:** the "industrial atelier" light theme

## v2.0 — Atelier Monochrome

Direction change on request: **pure black, white ink, zero chroma.**

- Canvas is `#000000`, flat — no gradient mesh, no glows. Depth comes from a
  4-step gray surface ladder (4% / 5% / 7% / 8%) and 1px hairlines
  (13% / 22% white). A 3.5% film-grain overlay keeps flat black from feeling
  digital.
- Display face is **Fraunces** (editorial serif, optical sizing) for page
  titles, stat numerals, and the wordmark — the fashion-house voice that fits
  a garment atelier. Body stays IBM Plex Sans; data stays IBM Plex Mono.
- **Status reads through weight, not hue:** critical = inverted (white fill,
  black text); positive = translucent white fill; warning = brighter hairline;
  neutral = quiet hairline. Primary buttons are solid white on black;
  destructive buttons invert to white on hover.
- Radii sharpened (6px cards, 4px controls, square ornaments); shadows nearly
  eliminated — structure is drawn, not cast.
- All v1 tokens remain as aliases resolving into the gray ladder, so no page
  code changed.

The sections below document v1.0 for the record; its layout system, component
inventory, and responsive rules are unchanged in v2.0.

## 0. The refined brief

> Re-skin the Garment ERP single-page application as a dark-first, modern
> enterprise control room, inspired by the Safar CRM dashboard: near-black
> slate surfaces, cards floating on a subtle gradient mesh, one saturated
> signal color (deep teal), quiet typography, big confident numerals, and a
> calm information hierarchy. It must feel like a premium internal tool a
> factory runs its night shift on — not a template.

**What was adapted from the original Safar CRM prompt and why:**

| Safar CRM assumption | This system's reality | Decision |
|---|---|---|
| Next.js App Router + RSC + Server Actions | React 18 + Vite SPA against a FastAPI session-cookie API | Keep the SPA. RSC/Server Actions don't apply to a FastAPI backend; rewriting the framework buys zero user value. |
| Tailwind + shadcn/ui components | A single semantic-class design system (`theme.css`) consumed by every page | Keep it. One stylesheet re-skins all 13 pages at once with no page-code churn — that IS our design-token layer. |
| TanStack Table, React Hook Form, Zod, Recharts | Existing `ListTools` (search/sort/pagination/CSV), controlled forms, API-side validation | Keep existing machinery; this brief is visual, not architectural. |
| Light + dark modes | The reference photo is dark; ERPs live on wall screens and night shifts | Commit fully to **dark-first single theme**. A timid dual theme dilutes the identity. |
| HSL tokens: Primary teal `185 72% 44%`, accent orange `35 92% 55%`, dark bg `222 38% 8%`, surface `222 34% 11%` | — | Adopted verbatim as the core palette. |

## 1. Aesthetic identity

**Name:** *Nightshift* — the factory at 2 a.m., machines humming, one calm
screen of truth.

- **Tone:** refined-industrial minimalism. Restraint over decoration; the
  drama comes from depth (gradient mesh, glows) and confident numerals.
- **The one memorable thing:** a near-black canvas where every number reads
  like an instrument panel — teal signals, amber warnings, nothing shouting.

### Color system (HSL)

| Token | Value | Use |
|---|---|---|
| `--bg` | `222 40% 7%` | Canvas, with a fixed gradient mesh: teal radial glow top-left, faint indigo bottom-right |
| `--card` | `222 32% 11%` | Cards, drawers, popovers |
| `--card-2` | `222 30% 14%` | Card headers, toolbars, table heads |
| `--teal` | `185 72% 44%` | Primary actions, focus, active nav, links |
| `--amber` | `35 92% 55%` | Accent/eyebrow, warnings, the second voice |
| `--ok` | `158 64% 45%` | Success (emerald) |
| `--bad` | `0 72% 58%` | Destructive |
| `--info` | `224 80% 66%` | Informational states |
| `--ink` | `210 30% 93%` | Primary text |
| `--ink-soft / --ink-faint` | `215 16% 68%` / `216 12% 52%` | Secondary / tertiary text |
| `--line / --line-strong` | `220 26% 17%` / `220 22% 26%` | Hairlines / interactive borders |

Status chips are translucent washes of their hue (`hsla(...,0.12)` fills,
`0.35` borders) so they glow against the dark instead of stamping solid pills.

### Typography

- **Display (`--display`): Sora** — geometric, slightly technical; used for
  page titles, stat numerals, drawer titles, the wordmark. Numerals are
  `tabular-nums` everywhere.
- **Body (`--sans`): IBM Plex Sans** — the workhorse; high legibility at 13–14px.
- **Data (`--mono`): IBM Plex Mono** — document numbers, table headers,
  eyebrows, keyboard-dense metadata. Uppercase + letterspacing for labels.

### Shape & depth

- Radius `8px` on cards/inputs/buttons (`0.5rem`, per the reference).
- Every surface has a 1px border — edges must read against the mesh.
- Shadows are deep and cool (`hsl(222 60% 3%)` based), used sparingly;
  elevation mostly comes from surface-step (+2–3% lightness per layer).
- Focus: 3px teal ring at 25% alpha. Non-negotiable on every interactive.

### Motion

One orchestrated moment: page content staggers in (60ms steps, 420ms rise).
Everything else is micro: 140ms color/border transitions, 1px press on
buttons, drawer slide at 220ms. `prefers-reduced-motion` collapses all of it.

## 2. Layout architecture (unchanged bones, new skin)

- `Shell` = fixed 264px sidebar + sticky translucent topbar (blurred,
  `--bg` at 82% alpha) + 1540px max content column.
- Sidebar sits on the darkest surface (`222 44% 5%`), active item gets a
  teal left rail + teal-tinted icon tile — the photo's selected-state.
- Stat cards follow the reference: label top-left, decorative tile top-right,
  Sora numeral, caption underneath.
- Mobile: sidebar becomes an overlay drawer at ≤920px; metric grids collapse
  4→2→1; tables stay in `overflow-x` wraps. Field agents use phones — every
  breakpoint in the old sheet is preserved.

## 3. What deliberately did NOT change

Component APIs (`Card`, `Stat`, `Chip`, `Drawer`, `Tabs`, `ListTools`),
routing, RBAC-driven nav visibility, session/CSRF handling, and all page
logic. The redesign is a pure design-system swap: `index.html` (fonts) +
`styles/theme.css` (rewritten) + minor `Shell` polish.
