# Sel design language

## Current direction · 16 October 2026

Sel's design language is calm, precise, and considerate: a light grey canvas,
rounded white cards, soft semantic tones, and charcoal-on-charcoal gradient
buttons. The user's current reference is Apple's restraint, bento compositions,
and tactile interactions, adapted to Sel's own Switzer font and Solar icons.
This supersedes the previous Chatbase-derived white canvas, 10 px cards, and
flat primary action. Existing navigation, layouts and workflows remain intact.

The interactive reference lives at `/static/design-system.html`, reachable
from the product sidebar. It loads the same `style.css` as the product, so
its component examples are a working reference rather than a separate theme.
Its sample interactions are local to the page and never call the product API.

## Typography

- Switzer for body text, transcript, forms, navigation and controls.
- Served locally from the supplied `sel/font_switxwe/` files, with OpenType copies in
  `sel/web/`: Regular 400, Medium 500, Semibold 600, Bold 700 and Italic 400.
- The supplied license says Personal Use Only. Its notice is bundled as
  `Switzer-LICENSE.txt`; commercial release requires an appropriate license.
- Nepali text uses the existing system/Devanagari fallback.
- Main heading: 24 px, 32 px line height, weight 600, -0.02em tracking.
- Panel heading: 20 px; section heading: 16 px, both weight 600.
- Body and controls: 14 px, 20–21 px line height.
- Labels and active controls: weight 500; headings and emphasis: 600.
- Supporting metadata: 12–13 px. Editor text: 14 px with 1.625 line height.

## Palette

Color literals belong only in the `:root` token layer of `sel/web/style.css`.
All component and showcase styles consume those semantic variables.

| Role | Token | Value |
| --- | --- | --- |
| Page canvas | `--bg`, `--canvas` | `#f3f4f6` |
| Card / field | `--surface` | White |
| Quiet inset | `--surface-3` | `#f7f8fa` |
| Hover / neutral selection | `--surface-2` | `#eceef1` |
| Foreground | `--text` | `#23262b` |
| Supporting text | `--text-2` | `#484e58` |
| Metadata | `--muted` | `#646b76` |
| Hairline / strong border | `--border`, `--border-2` | `#e2e2e2`, `#b7bec8` |
| Keyboard focus / selected card | `--focus` | `#526da8` |
| Primary gradient | `--action-top`, `--action-bottom` | `#484c54` to `#25282e` |
| Success | `--ok-bg`, `--ok-ink` | Sage |
| Danger | `--bad-bg`, `--bad-ink` | Rose |
| Missing input | `--warn-bg`, `--warn-ink` | Honey |
| Information | `--info-bg`, `--info-ink` | Dusty blue |

Color explains a real state and is always accompanied by text. Keep supported,
contradicted, ambiguous, playing, and evidence states distinguishable. The light
palette remains intentional even on devices that prefer dark mode.

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
workspace switchers, page templates or sheets. Product routes (Today, Practice,
Playbook, People) come from the product plan, not from visual work. The static
design-system reference is linked from the sidebar.

The desktop sidebar is 256 px. Preserve its real call list and device-health
section. Navigation uses a neutral active surface, restrained borders, and
compact insets. A call stays two columns down to about 1100 px, then the
review stacks under the transcript. Spoken lines stay within a comfortable
measure on wide monitors. Below 900 px the same sidebar becomes a compact
header: brand and the viewer control share one row, section links sit in a
row, and the call list scrolls sideways so the page itself starts on screen.
The call date stays on its own line and shortens with an ellipsis.

## Navigation, Today and the Playbook

- Sidebar order: brand, **Viewing as** (dropdown; no login), New call,
  section navigation (Today · Calls · Practice · Playbook), device health.
- `.primary-nav` links: 40 px, 12 px corners, Solar icons. Desktop active uses
  the charcoal action gradient and inverse text; compact mobile navigation uses
  the neutral surface with a hairline border.
- `.scope-toggle` (Mine / All, or No rep / All for managers) is a compact
  segmented rail beside the Calls heading; buttons carry `aria-pressed`.
- Today, Practice, Playbook and People share `.page`. On a screen wider than
  about 1440 px the top bar and the page stay a centered 1320 px column, a
  little wider than that 1440 layout, so lines do not stretch across the
  monitor. Today: an illustrated `.today-hero`, a passive `.stat-strip` (a `<dl>`,
  four equal columns, two on a phone, no hover), then `.today-work` with coaching
  and recent calls beside people and follow-ups. Coaching skills use individual
  white cards. One column below 1000 px, in reading order. Playbook uses
  `.bento`: Skills is the anchor, and Approved facts and Documents stack in
  the supporting column. The pack name in the title is a dropdown when more
  than one pack is installed. People keeps Add a person beside the team list from
  1280 px up. Lists inside cards use `.page-rows` / `.skills`; links to calls
  are `.chip-link`. Example in the reference under `#patterns`.

## Coaching moments, better lines and the focus

- Coaching tab: scorecard, then **Where to improve** (weak moments), **What worked**
  (strong moments), then objections and questions. Each moment is a passive
  `.card.moment` (no hover lift) with `.card-tags` (status tag plus Endorsed /
  Edited), the criterion as title, and `.said`: the customer's line, then the
  rep's, each labelled with its speaker in caption type.
- `.better-line`: a `--surface-3` inset with a head row (label, provenance pill,
  check pill) and the line in 16 px medium. Provenance is always shown:
  "AI-suggested" (violet), "Example line", "Written by a person", "Team answer".
  The check pill says "Checked against your pack" (sage), "No claims to check"
  (neutral) or "Refused" (rose); sources reuse `.source` blocks. With no line,
  `.better-line.is-empty` (dashed) shows the scorecard's own words.
- Refused lines sit in a native `details.disclosure` (summary is the button,
  focus ring on the summary) as dashed `.refused-lines` items with their reasons.
  Never strike through text; the reasons explain.
- Actions in `.card-actions`: play evidence, Endorse line (`aria-pressed`, only
  for a line that passed), Edit line / Write a line (inline `.moment-form`),
  Add note, Dismiss / Restore (`.push`, dismissed cards use `.is-dismissed`).
  After an action the panel redraws and focus returns to the control used.
- Today: when a rep has a focus, the bento anchor is **This week's focus**
  (`.focus-card`): `.focus-title` (20 px), the reason, then `.focus-moment` with
  the real moment (`.said`) and the better line. Skills to work on moves below.
  The manager's team table gains a Focus column. Example under `#patterns`.

## Practice: drills, the AI customer and the debrief

- Navigation **Practice** opens `#practice` (the rep's focus and drills; free
  practice with a person stays at `#sim`). A drill page shows the scenario as a
  `.bento-anchor` card: the real moment (`.said`), what fell short, the better
  line, and `.drill-persona` (who the AI customer is, labelled "AI persona" or
  "Built by Sel's rules", with the anonymisation note).
- The practice call reuses `.transcript-card`, `.sim-lines`/`.sim-line` and
  `.sim-composer`. Customer lines carry an "AI" tag; `.drill-skill` shows the
  drill criterion's live status; `.drill-status` says when the customer is
  replying. One action at a time: Start the call, then End and get feedback.
- The debrief is a `.card.debrief` (`.is-success` on a pass): the verdict pill
  and who judged it ("Judged by AI and Sel's rules" or "by Sel's rules"), then
  `.compare`: the real answer and the practice answer stacked on the left, the
  better line as the larger card on the right (one column below 900 px).
  Verdicts: Passed (sage), Not yet (rose), Did not come up (neutral), Unclear
  (honey). Example under `#patterns`.
- Manager Today starts with **Needs you**: escalations only (a contradicted
  claim reached a customer, a drill failed three times, the Coach wants to move
  on from a manager's focus).

## Buttons

- Default height: 40 px; compact height: 36 px.
- Radius: 12 px. Icon/label gap: 8 px.
- Default inset: 8 px vertical, 16 px horizontal.
- Font: Switzer, 14 px, weight 500, 20 px line height.
- Primary: `--action-gradient`, inverse text, `--shadow-action` inset highlight.
- Hover: `--action-gradient-hover`. Press: solid bottom tone, inset shadow, 0.98 scale.
- Secondary: white surface, 1 px neutral border; muted surface on hover.
- Ghost: transparent background and border; neutral hover surface.
- Danger: semantic danger fill; existing destructive behavior only.
- Disabled: 50% opacity and existing disabled interaction semantics.
- Focus: visible 3 px blue-grey ring. Keep the focus outline separate from semantic state.
- Control depth uses `--shadow-control`; gradient actions use `--shadow-action`.
- Preserve compact transcript, playback and icon-button variants.

## Inputs and forms

- 40 px minimum height, 12 px corners, 1 px neutral border.
- 12 px horizontal inset; white field surface.
- Label: 14 px, weight 500; 8 px separation from field.
- Textarea line height: 1.625; preserve resizing and existing field validation.
- Placeholders use muted foreground; focus is visible.
- Missing required details retain Sel's warning/evidence feedback.
- Keep native dates, file inputs, checkboxes and existing semantics.
- Every single-choice menu is the shared dropdown. The closed field matches
  other inputs: 40 px (36 px when compact), 12 px corners, a white surface,
  and a quiet chevron. The open menu is a white list with a hairline, 12 px
  corners and a soft shadow. The current choice has a check; the keyboard
  row uses the neutral surface. Groups are quiet labels. Arrow keys move,
  Enter chooses, Escape closes. The select element stays in the page, so
  labels, forms and saved values keep working.
- New call (`.page.upload-page`) is a bento. The recording is the anchor.
  Pack, start time, and whose call sit in the supporting card, with one
  primary action. Names, email, time zone, and recording layout stay in a
  disclosure. The customer-email hint says Sel does not send it.
- The recording drop zone is a solid quiet inset. A chosen file uses the
  sage border and label. Dashed outlines stay on dismissed and empty content.
- While `#new` is open, the sidebar New call button is the current page and
  uses the neutral selected surface, so the form owns the primary action.

## Cards and surfaces

- Card radius: `--radius` / 16 px; featured bento cards: `--radius-large` / 20 px.
- Controls and nested surfaces: `--radius-sm` / 12 px; small tags: 8 px.
- Standard inset: 16 px; larger cards and forms: 24 px.
- White surface, neutral `--border` outline (`#e2e2e2`), no drop shadow.
- `--shadow-card` and `--shadow-card-hover` are both `none`; hover adds no elevation.
  Controls, menus and state rings keep their own tokens.
- Content cards stay still. `.card.is-interactive` alone gains hover elevation.
- A clickable card is a native button or link; never nest buttons inside it.
- Selected: blue-grey border and ring. Evidence: warm amber border and ring.
- Focus and evidence can appear together. Playback retains its own indicator.
- Loading: stable space, `aria-busy`, visible explanation; motion is supplementary.
- Empty: useful next action. Error: reason and recovery. Success: explicit outcome.
- Dismissed: dashed outline, existing inactive treatment, a visible restore action.
- Semantic variants use `.is-empty`, `.is-error`, `.is-success`, `.is-loading`.
- Never apply a pointer cursor, lift, or press treatment to a passive content card.

Bento is a composition pattern, not a requirement to put everything in cards.
Use an asymmetric anchor surface with smaller supporting surfaces, aligned gaps,
and fewer competing actions. Stack in reading order on phones. The reference
includes a keyboard-operable expandable detail and reversible selection.

## Review progress dock

Call review keeps its existing four-step status in a compact bottom dock instead
of a top header. The passive `.steps` surface uses `--glass-surface`,
`--glass-border`, `--glass-blur` and existing elevation tokens; browsers without
backdrop blur get an opaque white fallback. It stays centered in the workspace,
clears the sidebar, wraps into two columns on mobile and respects the bottom
safe area. The main pane reserves space so the final content scrolls above it.
Only call views show the dock; processing and approval status remain data-driven.
An in-flow illustrative specimen lives under `#review-progress` in the reference.

## Tabs and segmented controls

- Muted rail, 12 px corners, 4 px inset. A short choice, such as which
  side of a practice call, stays only as wide as its labels. The Playbook
  pack choice is the page title: a dropdown in the heading, not a tab rail.
- Active item is white with 12 px corners and a compact ambient shadow.
- Label: 14 px, medium weight, -0.02em tracking.
- Typical item: 28 px minimum height inside a 36 px rail.
- Existing count badges remain compact. Dense review tabs can use 6 px
  horizontal inset and a 4 px badge gap to fit the existing panel.
- Preserve tab selection, keyboard behavior and responsive wrapping.
- On narrow phones, use two equal columns so wrapping stays balanced.

## Icons

Use Solar Icons from `@solar-icons/static@2.3.2`, bundled locally in
`sel/web/solar-icons.svg`. Linear icons use 1.5 px strokes; playback uses Bold.
The existing `icon(name, size)` helper renders the shared sprite for review and
simulator controls. Shell controls reference the same symbols. Icons inherit
text color and remain decorative inside controls with accessible names.
The muted microphone is adapted from Solar's microphone with a diagonal strike.
Sel's waveform brand mark remains its own identity asset.
Standalone section icon containers are 32 px. Review heading icons use a
20 px unboxed area so they stay subordinate to the card title.

Solar artwork is by 480 Design under CC BY 4.0; the package wrapper is MIT.
The full notices ship in `sel/web/solar-icons-LICENSE.txt`, and the sidebar
contains visible author/license credit. No runtime CDN or framework is needed.

## Effects and animation

- Press: 120 ms. Hover, focus, and state transitions: 180 ms. Reveals: 280 ms.
- Easing: `--ease`, cubic-bezier(.22, 1, .36, 1). No spring overshoot.
- Transitions communicate hover/focus/selection; no bouncing or decorative
  page entrances.
- Preserve existing real recording/loading feedback. A selected workflow step
  does not need a continuously pulsing effect.
- Respect the existing reduced-motion override for all transitions/animations.
- Preserve live meter transforms and transcript performance optimizations.
- Upload, review and simulator workspaces share the quiet grey canvas.
  White cards and inset surfaces establish hierarchy without a decorative dot grid.
- Evidence and current-playback styling remain distinct and visible together.

## Scope and verification

Shared primitives belong in `sel/web/style.css`. Showcase-only layout and
interactions live in `design-system.css`, `.html`, and `.js`. No product state,
payloads or business rules change. The existing `web/*` package-data rule
includes the reference and local OpenType/license assets.

Check upload, review tabs, claims, drafts, simulator lobby, and live simulator
styling at desktop and mobile sizes. Check loaded fonts, 40 px actions, 12 px
controls, 16 px cards, overflow, focus, reduced motion, and exact evidence states.
Use the repository's Python checks and browser review; there is no npm build
pipeline in this project.

The current tokens and interactive reference define the shared primitives.
Sel's existing component semantics and responsive layouts remain authoritative.

## Product polish

Review status badges sit inside their cards, followed by the title and
metadata. Review heading icons are compact, unboxed outlines. Sidebar device
status is a flat footer divided by a single hairline. It says whether this
computer can transcribe and review, with the short model name beside each line.
The full address stays on the hover tip.
Attribution sits below.
Transcript and review workspaces use the same grey canvas, with white review
cards. Body/reading text remains 14 px. Cards use 16 px corners; controls and
insets use 12 px corners. Navigation selection remains neutral; card selection
is blue-grey and source evidence stays amber.
The simulator workflow rail uses three columns when its own panel is narrow,
and two on phones, keeping step labels readable beside the live review panel.

The Perplexity parchment/teal reference and its later card and typography
changes were reverted at the user's request. Switzer, Solar icons and the
preceding product polish remain in use.

## Extension contract

- Keep color, typography, radii, spacing, elevation and motion values in tokens.
- Add variants to shared primitives before creating a new one-off treatment.
- Document default, hover, press, focus, disabled, loading, empty and error states
  where applicable. A passive card does not need button interaction states.
- Prefer native buttons, labels, selects, radios, details and dialogs. Use ARIA
  for state and relationships; it does not replace keyboard behavior.
- For overlays, contain focus and restore it to the trigger on close. Escape exits.
- Test wide desktop, narrow phone, 200% zoom, keyboard and reduced motion.
- The reference uses illustrative content. It does not send, approve, record,
  upload, or mutate any actual call. Live model accuracy is outside visual validation.


## Illustrated Today dashboard

Today uses a quiet mountain banner, a four-part summary, and an asymmetric work
area: coaching and recent calls on the left, people and follow-ups on the right.
Every skill remains visible with its call evidence and existing practice actions.
An active focus and manager escalations keep their existing behavior. The team
scorecard table remains available below the overview. Narrow screens stack the
columns in reading order; wide tables scroll within their own card.

Use `.today-hero` for a decorative landscape and real HTML headings/actions.
Use `.person-avatar` with `.portrait-0` through `.portrait-5` for character art;
`Sel.personAvatar(person)` chooses by immutable ID. Always show a real name beside
it. Portraits are decorative fictional characters, not inferred likenesses or
unique identifiers. The same six portraits may repeat in larger teams.
The live call uses the same portraits at `.avatar-call` (128px), with no fill
or border, so the drawing is not clipped by a frame. A seat with no team
member keeps that space empty, with an initial only when someone joined without
a person id.
The welcome banner uses the 20px featured-card radius, cards 16px and controls
12px. All colors, typography, spacing, shadows and interactions reuse shared tokens.

Assets are local and work offline. Generated with the built-in image generation
tool from the user's landscape dashboard and monochrome portrait references:

- `sel/web/dashboard-landscape.png`: Wide editorial gouache landscape, soft sage
  hills and pale blue Himalayan-style mountains, a winding path, warm ivory sky;
  left third fades to white for HTML copy, landscape weighted right; no text,
  logos, UI or frame.
- `sel/web/team-portraits.png`: Six original friendly monochrome head-and-shoulder
  portraits in a seamless equal-cell 3-by-2 sprite sheet; bold hand-drawn outlines,
  white fill, varied hair and glasses, consistent scale, no labels or gutters.

The complete generation prompts are in `docs/dashboard-image-prompts.md`.
