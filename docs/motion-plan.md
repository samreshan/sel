# Motion plan: what Sel can take from Apple

Status: built (20 Oct 2026), all phases. DESIGN.md "Effects and animation"
is now the rule; this file keeps the reasoning. Decisions on the open
questions below: the spring is for movement only (colour and opacity keep
`--ease`), and screens move in 280 ms on the spring, which reads as about
180 ms. Play/pause has no custom control (the player is the native one), so
the symbol swap is used on the mute button. Not yet measured: the
`scripts/perf_ui.py` table (needs Playwright) and the feel on the demo
laptops.

## How Apple uses motion

Taken from Apple's Human Interface Guidelines (Motion), WWDC18 "Designing
Fluid Interfaces" and WWDC23 "Animate with springs", and how iOS/macOS behave.
Apple's marketing site (scroll-linked, cinematic) is a different job and is
left out on purpose.

1. **Motion explains, it doesn't decorate.** It shows where something came
   from, where it went, and what changed. Frequent actions get less motion,
   not more.
2. **Springs, not curves.** Described by a perceived duration and a bounce.
   The system default (`.smooth`) has **zero bounce**: fast start, long soft
   settle. Bounce is kept for gestures that carry velocity.
3. **Interruptible.** A change mid-animation starts from where the element is
   now, never from the end state. Nothing makes you wait for it.
4. **Continuity of objects.** The selected-tab thumb slides; a sheet grows from
   the thing you tapped (matched geometry, zoom transition). An object keeps
   its identity across screens.
5. **Content transitions.** Numbers roll digit by digit (`numericText`).
   Symbols change with a small effect: *replace* for a state change, *bounce*
   once for confirmation, *pulse* for something ongoing, *variable color* for
   a level.
6. **Depth.** Modals lift; the background dims and recedes. Translucent
   materials keep context visible.
7. **Reduce Motion swaps, it doesn't delete.** Movement becomes a crossfade;
   feedback stays.

## Where Sel stands

Already Apple-like: one easing token (`--ease`, an ease-out with no
overshoot), three durations (120 / 180 / 280 ms), press scale `.98`,
dropdowns that open from their trigger, glass dock, playhead moved by a timer,
smooth scroll only for jumps the user asks for.

Gaps:

- Reduced motion **removes** every transition and animation
  (`style.css:3123`), so state changes snap with no fade.
- Selection jumps: tab rails, segmented controls and the sidebar's active row
  repaint in place instead of moving.
- Screens swap with no continuity (call list → call, Today → drill).
- `<dialog>` and dropdown menus have no exit; dialogs have no entrance.
- Numbers that change while you watch (live score, counts, badges) just
  replace their text.
- Icon state changes (mic on/off, draft approved) swap instantly.
- New live transcript lines pop in and push content down.
- Some motion still uses ad-hoc values (`width 0.4s ease`, `spin 800ms`,
  `pulse 1.4s`).

## Constraints that still hold

- Vanilla JS and CSS, no library, no build step, works offline.
- No overshoot (DESIGN.md). Apple's default spring has none, so this fits.
- Never animate the review transcript in bulk (1,188 rows; see the
  performance traps in CLAUDE.md). Only rows inserted live may animate.
- Transform and opacity only. No animated `height`, `width`, `top` on hot
  paths; use `grid-template-rows` or FLIP.
- Motion must never delay data: a state is correct the frame it arrives;
  animation only carries the eye there.

## Plan

### Phase 0: foundation (small, do first)

| # | Change | Where |
|---|---|---|
| 0.1 | Spring token: `--ease-spring: linear(0, .08, .238, .406, .554, .674, .767, .835, .885, .921, .946, .963, .975, .983, .989, .992, 1)` (critically damped, no bounce). Fallback to `--ease` under `@supports not (transition-timing-function: linear(0, 1))`. | `style.css` `:root` |
| 0.2 | Replace ad-hoc values (`0.4s ease`, `800ms`, `1.4s`) with tokens; add `--motion-loop` for ongoing pulses. | `style.css` |
| 0.3 | Reduced motion keeps opacity and colour transitions at `--motion-fast` and drops transforms and loops, instead of `none !important` on everything. | `style.css:3123` |
| 0.4 | One JS flag `Sel.motion` (reads the media query live, not once) for code that animates. | `app.js:73` |
| 0.5 | `#motion` section in the design system shows each pattern below with a reduced-motion toggle (the page already has one). | `design-system.*` |

### Phase 1: continuity (biggest gain)

| # | Pattern | Sel surface | How |
|---|---|---|---|
| 1.1 | Sliding thumb | `.tabs`, `.segmented`, sidebar active row | One indicator element per rail; JS sets `--x`/`--w` from the selected item; `transform` + `width` via spring. Radios and links unchanged. |
| 1.2 | Screen continuity | Calls list → call, Today → drill, Leads → lead | Same-document View Transitions (`document.startViewTransition`) around the router's render: short crossfade, the call title as a named shared element. Feature-detected; skipped on first load, on reduced motion, and when the call is already cached and renders in one frame below 16 ms. |
| 1.3 | Modal depth | `.dialog` | `@starting-style` + `transition-behavior: allow-discrete`: scale .97 → 1 and fade in, backdrop fades; reverse on close. Browsers without it open instantly as now. |
| 1.4 | Menu exit | dropdown menus | Fade and 4 px settle on close (100 ms), interruptible by reopening. |

### Phase 2: content transitions

| # | Pattern | Sel surface | How |
|---|---|---|---|
| 2.1 | Rolling numbers | Live score (simulator, drill), scorecard %, Today counts, tab badges | `Sel.setNumber(node, value)`: tabular figures, each changed digit slides 0.4 em with fade; `aria-live` text is set at once, the visual follows. Only digits that change move. |
| 2.2 | Symbol replace | Mic on/off, play/pause, draft approve | Old icon scales to .8 and fades while the new one scales from .8 (120 ms). No bounce. |
| 2.3 | Confirmation | Draft approved, answer approved, moment endorsed | Check draws its stroke once (`stroke-dashoffset`, 280 ms) next to the existing text outcome. |
| 2.4 | Ongoing activity | AI customer "is replying…", analysing, live dot | Three-dot typing indicator in the drill room (text remains for screen readers and reduced motion); one shared pulse token for the rest. |
| 2.5 | Arriving line | Live room and drill transcript only | Inserted row grows from `grid-template-rows: 0fr` to `1fr` and fades in; the container keeps its scroll anchor. Not used in the review transcript. |

### Phase 3: lists that change

| # | Pattern | Sel surface | How |
|---|---|---|---|
| 3.1 | Leave and close the gap | Today: a draft approved out of "Needs you"; dismissing a moment; resolving an escalation | FLIP: measure, apply, animate siblings from old position; the leaving card fades and collapses. Optimistic updates already exist, so rollback restores with the reverse. |
| 3.2 | Progress that fills | Review dock steps, upload progress | Fill moves with the spring; a finished step swaps its icon (2.2). |

### Left out on purpose

Scroll-linked reveals, parallax, staggered page entrances, bouncy overshoot,
3D tilt, animated gradients. Sel is a work tool used many times a day; Apple
keeps these for marketing pages and media, not for Mail or Settings.

## Order, cost and checks

Recommended order: 0 → 1.1 → 2.1 → 1.3 → 2.2/2.3 → 2.5/2.4 → 1.2 → 3.x.
View transitions (1.2) come late because they wrap routing, the path the
performance work tuned most.

Rough size: phase 0 is half a day; 1.1, 2.1, 2.5 a day each; 1.2 one to two
days with the measurements; the rest are hours each.

Every item, before it lands:

- `prefers-reduced-motion` and the design-system toggle: the end state is the
  same, feedback is a fade.
- `python scripts/perf_ui.py`: cold load, call switch, tab switch and playback
  numbers no worse than the table in CLAUDE.md.
- `python -m unittest discover -s tests -t .` including the browser tests (no
  console errors, no flaky waits on animated elements).
- `python scripts/screens_team.py` at 1440 and 390 px: no overflow, focus ring
  visible during and after motion.
- DESIGN.md "Effects and animation" updated with the new pattern and token.

## Open questions

- Should the spring replace `--ease` everywhere, or only for movement
  (thumbs, sheets, rows) while colour and opacity keep `--ease`?
  Recommendation: movement only.
- Is a 280 ms reveal still right for the sheet and screen transition, or
  should screens be faster (180 ms) since people move between calls often?
  Recommendation: 180 ms for screens.
