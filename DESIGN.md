# Sel design language

Chatbase is a visual reference for components, spacing, typography, colors,
effects, and restrained motion. Sel keeps its existing screens, navigation,
page layouts, information architecture, routes, and workflows. This is not a
Chatbase page reconstruction plan.

Reference inspected on 16 October 2026 through the authenticated browser.
Measured values below come from rendered UI/computed styles. Sel-specific
semantic states remain part of Sel, including evidence, transcript playback,
claim verdicts, approval status, recording, and local model availability.

## Typography

- Inter for body text, transcript, forms, and content.
- Geist for sidebar navigation, actions, and compact controls.
- Both fonts are served locally as variable WOFF2 files, normal weights 100–900.
- Source packages: `@fontsource-variable/geist@5.3.0` and
  `@fontsource-variable/inter@5.3.0`. OFL licenses accompany the files.
- Nepali text uses the existing system/Devanagari fallback.
- Main heading: 24 px, 32 px line height, weight 600, -0.02em tracking.
- Body and controls: 14 px, 20–21 px line height.
- Labels and active controls: weight 500; headings and emphasis: 600.
- Supporting metadata: 12–13 px. Editor text: 14 px with 1.625 line height.

## Palette

The neutral semantic colors are measured from the reference. State colors
remain meaningful to Sel; do not flatten supported, contradicted, ambiguous,
playing, and selected evidence into one indistinguishable appearance.

| Role | Token / value |
| --- | --- |
| Page and card | White surface |
| Selected/hover surface | `lab(96.1634% .0993311 -.364041)` |
| Quiet inset surface | `lab(98.2716% 0 0)` |
| Foreground / primary action | `lab(2.51107% .242703 -.886115)` |
| Secondary text | `lab(15.7235% .56015 -1.99021)` |
| Muted text | `lab(47.8878% 1.65477 -5.77283)` |
| Border | `lab(90.6853% .399232 -1.45452)` |
| Stronger border | `lab(84.9837% .601262 -2.17986)` |
| Focus | `lab(65.6464% 1.53497 -5.42429)` |
| Success | `lab(59.0978% -58.6621 41.2579)` |
| Danger | `lab(40.4273% 67.2623 53.7441)` |

Use semantic variables in component rules. Define literal color values only
in the token layer. Primary actions are near-black with white text. Selection
is neutral. Retain green, red, amber and restrained informational blue where
they explain real status. The customer-configured teal Chatbase widget is not
Sel's primary brand color.

The implemented reference theme is light, including on devices that prefer
dark mode. A matching dark reference was not inspected.

## Spacing and layout treatment

Apply a 4 px spacing rhythm to Sel's existing layout structures:

| Space | Use |
| --- | --- |
| 4 px | Segmented rail inset, compact gaps |
| 8 px | Icon/label gap, small inset |
| 12 px | Field inset, closely related controls |
| 16 px | Card inset, compact section gap, mobile gutter |
| 24 px | Major card/panel inset and section separation |
| 32 px | Main content gutter and larger section separation |

Keep existing responsive stacking, column relationships and content order.
Normalize spacing within those layouts; do not introduce Chatbase navigation,
workspace switchers, page templates, sheets, or new screen routes.

The desktop sidebar is 256 px. Preserve its real call list and device-health
section. Navigation uses a neutral active surface, restrained borders, and
compact insets. Existing mobile sidebar behavior remains intact.

## Buttons

- Default height: 40 px; compact height: 36 px.
- Radius: 8 px. Icon/label gap: 8 px.
- Default inset: 8 px vertical, 16 px horizontal.
- Font: Geist, 14 px, weight 500, 20 px line height.
- Primary: foreground fill, inverse text. Hover blends 90% primary with surface.
- Secondary: white surface, 1 px neutral border; muted surface on hover.
- Ghost: transparent background and border; neutral hover surface.
- Danger: semantic danger fill; existing destructive behavior only.
- Disabled: 50% opacity and existing disabled interaction semantics.
- Focus: visible 3 px neutral ring at 50% opacity.
- Subtle control inset: `inset 0 -1.5px 0 rgb(0 0 0 / .04)`.
- Preserve compact transcript, playback and icon-button variants.

## Inputs and forms

- 40 px minimum height, 8 px corners, 1 px neutral border.
- 12 px horizontal inset; white field surface.
- Label: 14 px, weight 500; 8 px separation from field.
- Textarea line height: 1.625; preserve resizing and existing field validation.
- Placeholders use muted foreground; focus is visible.
- Missing required details retain Sel's warning/evidence feedback.
- Keep native selects, dates, file inputs, checkboxes and existing semantics.

## Cards and surfaces

- Card radius: 10 px; small controls/insets: 8 px.
- Standard inset: 16 px; larger forms: 24 px.
- White surface with a single neutral border.
- Ordinary review cards are flat; avoid colored glows and oversized shadows.
- Existing sticky audio controls may use a small elevation shadow.
- Compact shadow: `0 1px 2px rgb(0 0 0 / .05)`.
- Raised shadow: `0 4px 12px rgb(0 0 0 / .05)`.
- Use the same surface/radius system for upload, review, drafts, scorecards,
  participants, simulator controls and after-call cards.
- Keep each existing card's content, evidence actions and state feedback.

## Tabs and segmented controls

- Muted rail, 10 px corners, 4 px inset.
- Active item is white with 8 px corners; no heavy border or shadow.
- Label: 14 px, medium weight, -0.02em tracking.
- Typical item: 28 px minimum height inside a 36 px rail.
- Existing count badges remain compact. Dense review tabs can use 6 px
  horizontal inset and a 4 px badge gap to fit the existing panel.
- Preserve tab selection, keyboard behavior and responsive wrapping.

## Icons

Keep Sel's inline SVG system and actual glyphs. Standardize outline weight to
1.5 px, preserve round caps/joins and existing viewBoxes, and inherit text color.
Filled playback icons remain filled. Keep accessible names on icon controls.

The reference uses some identifiable Lucide utilities plus other unlabelled
custom SVGs; its entire icon library was not established. Do not add an icon
package or claim exact custom glyph matching on the basis of stroke weight.

## Effects and animation

- Control background, border, text and shadow transitions: 200 ms.
- Transitions communicate hover/focus/selection; no bouncing or decorative
  page entrances.
- Preserve existing real recording/loading feedback. A selected workflow step
  does not need a continuously pulsing effect.
- Respect the existing reduced-motion override for all transitions/animations.
- Preserve live meter transforms and transcript performance optimizations.
- The simulator canvas uses a 1 px neutral dot on a 16 px repeat, on a very
  light neutral background. Normal upload/review content remains clean white.
- Evidence and current-playback styling remain distinct and visible together.

## Scope and verification

Implementation changes belong in the existing shared stylesheet and local
font assets. No product logic, routing, payloads or business rules change.
The existing package-data rule `web/*` includes the flat WOFF2/license files.

Check upload, review tabs, claims, drafts, simulator lobby, and live simulator
styling at desktop and mobile sizes. Check loaded fonts, 40 px actions, 8 px
controls, 10 px cards, overflow, focus, reduced motion, and exact evidence states.
Use the repository's Python checks and browser review; there is no npm build
pipeline in this project.

Reference measurements are the target for shared design primitives. Sel's own
component needs and responsive layouts remain authoritative for product UX.
