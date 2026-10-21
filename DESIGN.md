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
design-system reference, and the Solar icon credit, live under Settings → References.

The desktop sidebar is 256 px. Preserve its real call list and device-health
section. Navigation uses a neutral active surface, restrained borders, and
compact insets. A call stays two columns down to about 1100 px, then the
review stacks under the transcript. Spoken lines stay within a comfortable
measure on wide monitors. Below 900 px the same sidebar becomes a compact
header: brand and the viewer control share one row, section links sit in a
row, and the call list scrolls sideways so the page itself starts on screen.
The call date stays on its own line and shortens with an ellipsis.

## Navigation, Today and the Playbook

- Sidebar order: brand, **Viewing as** (dropdown; no login),
  section navigation (Today · Calls · Leads · Playground · Team ·
  Settings), device health.
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

- Practice (`#practice`) is the rep's focus and drills. It is opened from
  Today and from a drill, and is not a sidebar item. Free practice with a
  person stays at `#sim`. A drill page shows the scenario as a
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

## Leads, Prep, Team and Settings

- Sidebar order continues: Today · Calls · **Leads** · Playground ·
  **Team** (only when viewing as a manager) · **Settings**. Same neutral
  `.primary-nav` links; on a phone the links wrap in as many 104 px columns
  as fit. `#lead/<id>` and `#prep/<id>` keep Leads active.
- Leads (`#leads`) is one card. **Add a lead** is the page's primary button
  and opens a `.dialog` (name, optional phone). Inside the card: a search
  field, a status filter (All, Ready to prep, No calls yet) and a Table / Card
  switch. The table lists name, status, calls, rep and when the lead changed;
  a row opens the lead, and Prep is there when the lead has calls. Card view
  is the same leads as separate cards. A lead (`#lead/<id>`) opens with a
  `.crumbs` trail (Leads / name) and a `.stat-strip`: phone, rep, status,
  updated. **Edit details** changes the name, phone and rep. **Prep for the
  next call** is the primary action once a call is linked; with no calls, the
  calls card links to Calls. The page stays a bento: calls anchor, notes
  support. Prep continues the trail (Leads / name / Prep). **Likely objections**
  is the anchor (each with "They said" and the answer as a `.better-line`, labelled "Team
  answer", "Example line" or "AI-suggested" as on the Coaching tab), with the
  focus, gaps and promises stacked beside it. One primary action: **Rehearse
  this call**.
- Calls (`#calls`) uses the same card as Leads. The toolbar shows the count
  and the Table / Card switch. The customer is the row, with Example or
  Simulated on the line under the name. Status is an icon and a word (Done in
  sage, Failed in rose), with a progress line while Sel is still working. The
  date is relative; the full time is on the timestamp.
- Team (managers): **Needs you** first (escalations only), then **Reps**
  (calls, average score over the last 10 reviewed calls, focus, open
  follow-ups, and View as), then the bento: **Skills by rep** as the anchor
  beside **Patterns**, then the review queue.
  A rep's page: the 1:1 brief as the anchor beside transfer checks, then
  **Skills over time**.
- `.table-scroll`: a wide table scrolls inside its card, never the page. It
  is focusable (`tabindex="0"`, `role="region"` with a label) so the keyboard
  can scroll it, and is `position: relative` so `.sr-only` cell text cannot
  widen the page. Bento children have `min-width: 0` for the same reason.
- `.matrix` (with `.team-table`): reps × skills. The rep column is sticky,
  headers wrap at 160 px, cells are centred pills ("2 of 4": met of scored;
  sage all met, honey some short, rose half or more short) and an unscored
  cell is a dash with "Not scored" for screen readers.
- `.timeline`: an `<ol>` of reviewed calls, oldest first, on one hairline;
  each `li.is-met | is-partial | is-missed` carries a dot in the status colour
  plus the status pill (colour never alone), the call link and the moment as
  a `.said-line`.
- Playbook gains **Team answers** at the top of the supporting column and, for
  managers, the **Review queue** below the bento (Approve for the team is the
  primary action; refused answers sit in a `details.disclosure` with reasons).
- Settings is one status card (`.is-success` or `.is-error`) and the models
  list. Examples of the matrix and timeline are under `#patterns` in the
  reference.

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
- `.dialog` is a native dialog: white surface, large radius, quiet overlay.
  Escape closes it and focus returns to the control that opened it. Use it
  for a short create step, such as Add a lead.
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
The call player's pause symbol is drawn in the same Bold style as play.
Sel's waveform brand mark remains its own identity asset.
Standalone section icon containers are 32 px. Review heading icons use a
20 px unboxed area so they stay subordinate to the card title.

Solar artwork is by 480 Design under CC BY 4.0; the package wrapper is MIT.
The full notices ship in `sel/web/solar-icons-LICENSE.txt`, and the sidebar
contains visible author/license credit. No runtime CDN or framework is needed.

## Effects and animation

Motion explains a change (where something went, what it became); it never
delays one. Every pattern puts the end state in the DOM at once and animation
only carries the eye there. Helpers live in `sel/web/motion.js`
(`window.SelMotion`, also `Sel.motion`); the plan and its reasoning are in
`docs/motion-plan.md`.

- Press: 120 ms (`--motion-fast`). Hover, focus, and state transitions: 180 ms
  (`--motion`). Reveals: 280 ms (`--motion-slow`). Movement to a new place:
  320 ms (`--motion-glide`). Ongoing activity: `--motion-loop`; spinners:
  `--motion-spin`.
- Easing: `--ease` for fades and colour; `--ease-spring` (a critically damped
  spring sampled with `linear()`, no overshoot) for anything that moves.
  Browsers without `linear()` fall back to `--ease`.
- Patterns, all in the shared stylesheet:
  - **Rails**: tabs, segmented controls, the Playground sections and the
    sidebar keep one `.rail-thumb` that slides to the selection
    (`SelMotion.glide(rail)` after rendering or changing it).
  - **Screens**: same-document view transitions on the main pane only. Deeper
    (a list → an item) slides in from the trailing edge, back up from the
    leading edge, another section cross-fades with a 6 px rise, and the same
    page with other content (one call to the next) just changes. A page that is
    not painting skips the animation after 250 ms.
  - **Dialogs** lift in (`@starting-style`) and settle back on close; a dialog
    redrawn while open carries `.is-settled` so it does not enter again.
    Menus open from their control and a copy fades out on dismiss.
  - **Numbers** that change on screen roll by digit (`SelMotion.number`): live
    and review scores, tab counts, Today's counts.
  - **Symbols**: a changed icon swaps in (`.is-swapping`, the mute button); a
    confirmation check draws once (`SelMotion.check(true)`: approved drafts and
    team answers, endorsed lines, finished review steps).
  - **Conversation**: a live line rises in from its speaker's side
    (`.is-arriving`, at most three at a time); the AI customer shows three
    dots while it owes a reply. The dots are not a `.sim-line`.
  - **Lists**: cards and rows that re-sort or leave slide or fade from where
    they stood (`SelMotion.flip`): dismiss and restore, an approved team answer
    moving out of the review queue, a handled escalation.
- No bounce, no decorative loops, no scroll-linked effects, no staggered page
  entrances.
- Reduced motion swaps movement for a short fade; it never removes feedback.
  Colour and opacity transitions stay at the press speed, transforms and loops
  go, and entrances that only fade opt back in. The design-system preview
  toggle sets `data-motion="reduce"` on `<html>`, which the helpers also read.
- Preserve existing real recording/loading feedback. A selected workflow step
  does not need a continuously pulsing effect.
- Never animate the review transcript in bulk; only rows that arrive live may
  animate. Preserve live meter transforms and transcript performance
  optimizations.
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
Before the call starts, the room is laid out around what the people in it need
to do. While someone is still missing, their tile becomes the invite: the call
code in mono and a primary "Copy invite link" (shown first on phones). Your
own tile checks the microphone ("Say something: the bars should move", then
"Your microphone is working"). Meter bars only appear when there is a
microphone to listen to. The side panel previews the pack's scorecard in the
pack's own words, with the rep's coaching focus marked if it belongs to this
pack. The transcript stays hidden until the call starts; a one-line tip takes
its place. When both people have joined, a line above "Start the call" says
they are here and whether the audio link is connected.

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
or border, so the drawing is not clipped by a frame. A team member page
(`#team/<person>`) uses that same size and multiply treatment beside the name.
A seat with no team
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

## Playground · 17 October 2026

Playground replaces the Playbook label while preserving `#playbook` links.
It sits after Leads and before Team.
Practise, Library, and My progress use the existing `aria-pressed` segmented
control. Library retains skills, approved facts, documents, team answers, and
manager review. Short document exercises save separately from call drills;
feedback labels its provenance and limits. Objections (the pack's
`[[objections]]` and those heard on the team's calls, marked "Heard on a call ·
anonymised") open a typed drill with the AI customer on the shared drill page,
which says "Playground: practice only" and offers "Back to Playground". Drafts stay in browser
storage per viewer and pack. Existing call-moment practice remains `#practice`.
Practice attempts never close a focus or count as real-call improvement.

Playground's page header pairs the title with a compact, labelled business-pack
selector. Its Practise view places conversation practice beside reading, then
objection drills ("Practise an objection", one `.page-row` per objection with the
skill it is judged on) beside call-moment practice in an asymmetric two-column layout.
At narrow widths these stack in the same reading order. Page-specific layout
classes reuse the shared card, section, button, pill, and field primitives.

## Page polish · 20 October 2026

Every page now follows the Today dashboard's language.

- Text links use the foreground colour with a quiet `--border-2` underline that
  darkens on hover. No link shows the browser's default blue.
- A page opened from another page starts with `.crumbs` (the shared
  `Sel.crumbs()`), as Leads and Prep already did: Calls / call,
  Playground / Practice / drill, Playground / Call simulator, Team / rep,
  Team / People (Today / People for a rep). Top-level pages keep a plain
  title. The sidebar highlights the section a page belongs to: New call →
  Calls, the simulator → Playground, People → Team.
- Header actions sit level with the title (`.page-head` aligns to the top).
  A page's header has at most one primary action. Team's **Manage people** is
  secondary.
- The call page puts the pack in the header's eyebrow. **Whose call** and
  **Lead** sit on one `.call-assign` row in the header's footer, and Prep is a
  small button (see "The call page" below).
- A card body that opens with text or controls gets the same top inset as one
  that opens with rows. Group titles inside a card body have 16 px above them.
- People: **The team** is the anchor and **Add a person** supports it.
  Row actions share one column, so View as and You line up.
- Playground's Practise / Library / My progress rail uses the full-size
  segmented metrics (28 px items, 14 px labels).
- Simulator lobby cards carry an icon tile beside their title.
- Settings → References lists the design system and the icon credit as rows.
  Command names are set in `code`.
- Customer promise days display as "8 Oct 2026" (`Sel.formatDay`, string
  formatting only). The computed date itself is unchanged.

## The call page · 20 October 2026

A call is the main working page, so it reads like Today and puts the answer
to "what needs me?" first. Each part maps to one question a reviewer asks:

| Question | Part | Weight |
| --- | --- | --- |
| Which call is this? | Trail and date, eyebrow, customer name, the two people | High |
| How did it go? | Scorecard, first in the summary strip | High |
| What needs me? | Header sentence and its one primary action | High |
| What can improve? | To improve, then the Coaching tab | Medium |
| Can I hear it? | Recording card: speaker lanes, play, transcript | Medium |
| Whose is it? | Whose call, Lead, Prep in the header's footer | Low |
| Where did this come from? | Transcribed and analysed by, Example data | Low, always shown |

- **Toolbar** (`.call-toolbar`): `.crumbs` (Calls / customer) on the left,
  the call's start time with a calendar icon on the right, as on Today.
- **Header** (`.call-hero`, also `.call-head`): Today's hero layout
  (`.today-hero` type and spacing, without the landscape) inside one
  featured card. The eyebrow is the pack and "Call review", with an
  `Example data`, `Simulated` or `Practice` pill. The title is the customer. The lede says who spoke with
  whom and for how long, then what needs a person first, in this order:
  contradicted claims, follow-up drafts that need input, drafts ready for
  approval, moments to improve. The one primary action opens that tab
  (Check claims, Fill in follow-ups, Review follow-ups, See where to
  improve, Open the scorecard), scrolls the review into view on narrow
  screens, and moves focus to the tab. The footer (`.call-hero-foot`, quiet
  inset surface) holds the `.call-assign` pickers, Prep (or Manage leads
  when the call has no lead), the provenance caption, and the simulator or
  Sel's-rules banner with its actions.
- **The people on the call** (`.call-cast`) stand where Today has its
  landscape: the rep and the customer as Today's portraits (`.avatar-call`
  drawings) on 136 px discs, the rep's neutral, the customer's sage, with
  name and role under each and a dashed line with the phone icon between
  them. The drawings face right, so the customer's disc is mirrored to face
  the rep (the disc turns, not the drawing, so multiply still blends onto
  it). The rep has their team portrait once **Whose call** is set; until
  then, and for the customer, the portrait is picked from the call's own
  ids (the lead's id when the call has a lead, so a lead's calls match).
  Never from a name, so a portrait need not look like the name: people
  choose their own (below). The two never share a drawing; a chosen one
  stays and an automatic one steps aside. Below about 1180 px the people stand above the
  words at 104 px, and at 88 px on a phone.
- **Summary strip** (`.stat-strip.call-stats`): Today's strip, passive.
  Scorecard (score /100; met, partly, missed), To improve (moments; questions
  and objections heard), Claims (contradicted, else unverified, else
  supported), Follow-ups (need input, else ready, else approved). Each cell
  is a number, its unit and one line of detail. A low score or a
  contradiction colours the number rose, missing input honey; the words
  always say it too. Two by two below 1100 px.
- **Recording** (`.player`): a lane per speaker (`.lane-labels`: name in the
  transcript's speaker colour, role, seconds, share; talk time lives here
  now). Each line is a block at its real start and length on a quiet rail,
  so pauses show. The lanes are the seek bar: a transparent range input lies
  over them (click or drag to play from there, arrow keys step a second),
  with a visible focus ring on the track and a charcoal playhead. Below:
  a round charcoal play/pause, the time, playback speed (1×, 1.25×, 1.5×,
  2×) and Download. "Evidence for the selected card" appears with a
  highlight. Evidence and the playing block keep their own treatments.
- **Review column**: "Review" and Keys, then the tabs, which stay at the top
  of the column while it scrolls (a canvas ring hides cards passing under
  them). Scorecard rows put the play button beside the detail. On desktop
  the column reserves room under the progress dock.
- **Review tabs** (21 October): every card plays its evidence from the
  head (`cardHead(..., play)`, the time chip beside the title), so no card
  spends a row on one button. Moments keep the order said, what fell short,
  better line, then Endorse · Edit · Practise with Add note and Dismiss as
  quiet actions. **Claims** split into "Needs a look" and "Supported by your
  pack"; each card pairs **On the call** (the quote) with **Your pack says**
  (the source), then the reason (rose inset when contradicted).
  **Follow-ups** group "Waiting for you" (needs input first, then ready),
  "Approved" and "The customer's promises"; due days read "8 Oct 2026"; a
  draft opens with what is missing before approval, and a closed Approve
  says why under it.
- The progress dock, transcript, tabs and keyboard shortcuts are unchanged.
- **Laptop-height screens** (wider than 1180 px and 960 px tall or less)
  get a compact header so the recording, the review tabs and the first
  transcript line are on screen at 1280×800 and 1366×768: the title at
  `--text-hero-compact` (32 px), the sentence on one line, 96 px portraits,
  provenance beside the pickers, and tighter strip, player and transcript
  head. The work area starts about 430 px down instead of 580. Taller
  screens keep the roomy header.
- The design-system reference shows this page under `#patterns` (Call page).

## Choosing a portrait · 21 October 2026

Portraits are never guessed from a name, so people choose. On People, a
person's portrait is a button ("Choose a portrait for …") that opens a
`.dialog` with `.portrait-picker`: a fieldset of native radios, Automatic on
its own row (the drawing the id picks, shown as it is) and the six drawings
in the next, each labelled "Drawing 1" to "Drawing 6" for screen readers.
Selected is the blue-grey border and ring; keyboard focus keeps its outline.
The choice (`portrait`, 0 to 5, or none for Automatic) follows the person
everywhere their portrait appears. A lead's **Edit details** has the same
picker as "Customer portrait"; it is the customer on that lead's calls and
the sage disc beside the lead's name (`.lead-portrait`).

### Today illustration

Today uses two generated PNG sheets: `dashboard-characters-day.png` and
`dashboard-characters-night.png`. Each has six equal cells in the same 3 by 2
order as `team-portraits.png`. Each character has a matching desk scene, with
a sun by day and moon by night. No SVG or CSS-drawn scene pieces.
The shared `portraitFor` helper selects the viewer's chosen drawing, falling
back to their stable ID, exactly as for their avatar. Switching viewers or
changing their portrait updates the scene. The artwork is decorative, not
an indication of their real activity.
Day runs from 06:00 to 18:00 in the browser's local time, refreshed each minute
and when tab visibility changes. The workspace stays light. On phones the scene
sits below the copy and actions. The scene is hidden from assistive technology.

## People header on Lead, Prep and a rep · 21 October 2026

Pages about people share the call page's header (`Sel.pageHero`,
`Sel.pageToolbar`): the `.crumbs` trail, then one featured card with the
eyebrow, the name as title, one sentence, the page's actions (one primary)
and the people on discs (`castPair` keeps the call page's portrait rules).

- **Lead**: "Asha Gurung's lead" (or "Lead"), the lead's name, how many calls
  are on file; Prep for the next call and Edit details; the rep and the
  customer facing each other. The phone, rep, status and updated strip stays.
- **Prep**: "Before your next call", the lead's name, the brief's own
  sentence with who wrote it; Rehearse this call; the same two people. Then
  a strip: earlier calls, likely objections, to cover, promises.
- **A rep**: "Team · Rep", the name, the focus and why; View as; the rep
  alone, disc only (`.call-cast.is-single`, the title already names them).
  Then a strip: reviewed calls, drills, checked since the focus, needs you.
- Loading and failure keep the page's trail instead of a bare title.
- Strips use Today's strip styles (`.hero-page .stat…`).

## Supporting line-art assets · 21 October 2026

`Sel.lineArt` serves decorative PNGs only. Recording preparation and conversation
practice each have a 3 by 2 character sheet, ordered exactly as `team-portraits.png`.
They use `portraitFor` for a selected person's choice or stable-ID fallback; with
no viewer, the first drawing is a decorative default. New call follows the rep
chosen in its form without resetting inputs; Playground follows the viewer.
The setup ensemble includes all six characters. Empty Calls, empty Leads, and
unmatched Leads searches use distinct shared object illustrations. Text and
existing actions remain the accessible explanation and next step; artwork has
empty alternative text or is hidden from assistive technology and never acts as
a status or control. These scenes do not indicate microphone access or real
activity. Portrait scenes are 160px in cards, 120px on phones (88px in the narrow
recording header); empty states are 96–112px. The setup scene is 280px, 240px on
phones. No day/night variants outside Today. Sources are locally served and work
offline. The reference includes all supporting assets under `#line-art`.

Generation direction: use the canonical six portraits, preserve faces and clothing,
charcoal line work and white garments; recording uses headset and notebook,
conversation uses a gentle gesture and empty speech bubbles. Shared transparent
PNGs depict the welcoming team, a microphone and empty tray, a blank address book,
and a magnifying glass over blank cards. Generated with the built-in image tool.

### Practice preparation and reading · 21 October 2026

`line-art-rehearsal.png` and `line-art-reading.png` complete the planned supporting
placements. Both PNG sheets use the canonical 3 by 2 character order and the same
`Sel.lineArt` / `portraitFor` mapping as recording and conversation artwork.
Ready drills with no attempts show a compact rehearsal drawing beside the existing
start button. It follows the drill's rep (viewer fallback) and disappears during
a call, judging and after an attempt. The illustration is not the AI customer;
AI persona labels and the original scenario, evidence and actions stay intact.
Reading appears beside the existing Read & practise introduction and in the
Library Skills header, following the viewer. Documents and Library controls keep
their existing behavior. Supporting art is 120px, 88px below 700px, and decorative.

Built-in image generation direction: preserve all six source faces, hair and
clothing; rehearsal uses a blank cue card and notebook, reading uses an open
blank book and an annotation notebook. White PNG sheets, charcoal line work,
restrained grey shading, equal sprite cells. No letters or painted UI controls.
