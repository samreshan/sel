---
name: sel-design
description: Design, implement, refine, or review any Sel product UI using its established design system. Use for screens, components, cards, bento compositions, forms, visual states, responsive layouts, and interactions in this project. Keep backend-only work outside this skill.
---

# Sel design

Make every Sel interface feel like part of one product: calm, precise, considerate.
Use Apple-inspired restraint and tactile surfaces with Sel's own Switzer typography,
Solar icons, and waveform identity. Match the maintained system before inventing.
Apply this to UI plans and critiques as well as implementation; a design-only request
is not permission to change code or business behavior.

## Read the maintained sources

Resolve project paths from the repository root, three directories above this skill.

1. Read root `AGENTS.md`, `.impeccable.md`, and the relevant sections of `DESIGN.md`.
2. Inspect `sel/web/style.css`: its `:root` variables and the components being reused.
3. Inspect two relevant siblings in `sel/web/app.js` or `sel/web/sim.js` before editing UI.
4. Use `sel/web/design-system.html` as the visual reference, especially `#cards` for
   bento compositions and card states. Its CSS and JS are showcase-only examples.
   Open `/static/design-system.html` on the running Sel server when available.
5. Confirm symbol IDs in `sel/web/solar-icons.svg`; reuse `icon(name, size)` in the app.

The shared stylesheet is the implementation source of truth. Flag documentation
that disagrees with it, and respect the user's latest direction. Do not copy a second
token palette into a screen or import showcase CSS/JS into product workflows.

## Compose in Sel's language

- Use `--bg` / `--canvas` for the light grey workspace and `--surface` for white cards.
  Use `--surface-3` for quiet insets; keep borders and elevation subtle.
- Keep Switzer via `--font` / `--font-ui`, existing type scales, and local font files.
  Use the bundled Solar sprite; do not substitute system fonts, Lucide, or emojis.
- Reuse `--radius` for cards, `--radius-large` for featured bento cards,
  `--radius-sm` for controls, and `--radius-xs` for small tags. Read current values
  from the tokens instead of hardcoding them.
- Use the `--space-*` rhythm, `--shadow-*` elevation, and semantic color variables.
  New raw color, font, shadow, and motion definitions belong in the token layer.
- Reuse `.primary` for charcoal gradient actions, secondary buttons for supporting
  actions, `.ghost` for quiet actions, and `.danger` only for destructive intent.
  Give a decision area one clear primary action.
- Use asymmetric bento compositions when they help group related information:
  one anchor card and smaller supporting cards, aligned gaps, deliberate whitespace.
  Avoid three equal cards and repeated icon-heading-description tiles. Do not
  force transcripts, dense evidence, or every page into a bento grid.
- Preserve existing content order and navigation unless the requested work changes
  them. New UI must be reachable using the project's actual navigation convention.
  Stack bento cards in meaningful reading order on small screens.

## Design the behavior with the surface

Reuse shared primitives such as `.card`, `.card-head`, `.card-actions`, `.pill`,
`.tabs`, `.segmented`, `.field`, `.scorecard`, and `.dropzone` where appropriate.

- Passive content cards stay still. Use `.card.is-interactive` only for a genuinely
  actionable button or link. Never nest interactive controls inside a button card.
- Keep default, hover, pressed, keyboard focus, and disabled states for controls.
  For content, handle loading, empty, error, success, selection, and dismissal when
  applicable; a passive card does not need a fake click interaction.
- Selection uses `.is-selected` or the existing `aria-pressed` pattern; evidence
  uses `.is-focused`. These are different meanings. Preserve transcript playback
  indicators when evidence and playback occur together.
- Use `.is-loading` with `aria-busy` and descriptive text; `.is-empty` with a next
  step; `.is-error` with recovery; `.is-success` with a clear outcome; and the
  existing `.is-dismissed` treatment with a restore action. Connect visual states
  to real product state rather than merely attaching a decorative class.
- Pair sage, honey, rose, and blue semantic tones with labels or icons. Never
  imply that an unverified claim is supported or an approved draft was sent.
- Use the existing `--motion-fast`, `--motion`, `--motion-slow`, and `--ease` tokens.
  Prefer settled hover/press feedback and purposeful disclosure over bounce,
  decorative loops, or scroll effects. Honor reduced motion and avoid layout jumps.
- Use native labels and controls, visible focus, keyboard-operable disclosures,
  accessible names for icon-only actions, and focus restoration for overlays.

## Implement within the existing product

Sel is a Python-served, offline vanilla HTML/CSS/JS app. Do not add React, Tailwind,
a UI framework, an icon CDN, npm, or a build pipeline merely to design a screen.
Reuse its DOM helpers and existing state/events; `sim.js` shares helpers through
`window.Sel`. Put untrusted text into `textContent`, not generated HTML.

Before editing, name the files and the component pattern being reused. Keep the
change proportional to the request. Visual work does not change API contracts,
claim evidence, approval rules, recording, or simulator business behavior.

Shared styling belongs in `sel/web/style.css`. When adding a reusable primitive
or changing a system rule, update its showcase example and `DESIGN.md` in the same
scope. Keep illustrative showcase interactions separate from real call data.

## Verify the affected work

- Inspect the affected product screen alongside the design-system reference.
  Check desktop and narrow mobile, overflow, 200% zoom, keyboard focus, and reduced
  motion. Exercise the states and interactions changed by the task.
- Check Switzer and Solar assets load. Check readable text contrast, status labels,
  and selected/evidence/focus combinations; selection must survive hover.
- Run `node --check` on changed JavaScript. For implementation changes, run
  `python -m unittest discover -s tests -t .` and relevant existing browser checks.
  Sel has no npm lint or build step; do not introduce one for verification.
- Run `git diff --check`; inspect the diff for raw styling outside tokens, wrong
  icons/fonts, scope drift, and accidental business-logic changes.
- For documentation-only changes, validate references and the edited files rather
  than rerunning unrelated app tests. Report actual checks, skips, and gaps without
  treating fixture/browser results as live model or real-call validation.
