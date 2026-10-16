# Sel project instructions

## Design system for all UI work

For every UI design, implementation, refinement, or visual review in this project,
read and apply [sel-design](.agents/skills/sel-design/SKILL.md) before starting.
This is the default project design skill; the user may also invoke `$sel-design`.
For a planning or critique request, use the same language without making edits
unless requested. Backend-only work does not require the design skill.

Use these maintained sources:

- [DESIGN.md](DESIGN.md): design language and component rules.
- [.impeccable.md](.impeccable.md): audience, personality, and design intent.
- [style.css](sel/web/style.css): shared tokens and production primitives.
- [Design system](sel/web/design-system.html): interactive reference, served at
  `/static/design-system.html`; `#cards` includes bento and card-state examples.

Preserve the light grey canvas, rounded white cards, dark charcoal gradient
buttons, soft semantic colors, Switzer, Solar icons, and restrained interactions.
Use asymmetric bento compositions where they suit the content, not as a mandatory
layout for every screen. Keep shared rules in the sources above rather than
creating competing palettes or component systems.

## Project constraints

Sel serves vanilla HTML/CSS/JS from a standard-library Python server. There is
no npm build pipeline. Read [README.md](README.md) for the product and
[CLAUDE.md](CLAUDE.md) for architecture and operational details. Actual code wins
over stale documentation; flag disagreements. The latest user direction controls
requested design changes.

Keep call evidence, real processing state, and human approval semantics intact.
Sel does not send emails or create calendar events. UI-only work must not change
these rules. Keep demo interactions separate from real call data.

## Current state - 16 October 2026

The design system is implemented in shared CSS and documented in the interactive
reference. The project-local `sel-design` skill is the entry point for future UI
work. Verify the current implementation and affected screens for each task;
previous validation is not evidence for new changes.
