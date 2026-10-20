# Business packs

A business pack is everything Sel needs to know about one business. The engine reads the pack and never contains business-specific code. To adopt Sel, write a pack; you don't fork the engine.

Check a pack with:

```bash
python -m sel validate-pack path/to/pack
```

## Layout

```
my-pack/
  pack.toml
  facts.toml          optional if docs/ has content
  docs/               .md or .txt, any depth
  templates/          optional; built-in defaults are used if missing
```

## `pack.toml`

```toml
[pack]
id = "demo-solar"                 # required, stable identifier
name = "Ghamghar Solar"           # required, used in drafts as {business_name}
description = "Residential rooftop solar installer."
languages = ["ne", "en"]
week_starts_on = "sunday"         # used to resolve "next week" / "arko hapta"; default "monday"

[templates]                       # optional
email = "templates/followup-email.md"
calendar = "templates/followup-calendar.md"

[[scorecard]]                     # at least one
id = "clear-next-step"
title = "Next step with a date"
description = "Every promised follow-up is agreed with a specific day."
weight = 2
```

Scorecard descriptions are written for both the analysis model and the rep. Make each one concrete enough that two reviewers would agree on whether a call meets it. The description is also what the call simulator shows the rep as the next best action.

A criterion can name what code measures for it, for the live score and the report's scorecard:

```toml
[[scorecard]]
id = "qualify"
title = "Qualify the outlet"
description = "Find out the number of outlets and billing counters before recommending a plan."
signal = "discovery"   # discovery | objections | claims | next_step | talk_ratio
```

Without `signal`, Sel infers it from the title, then the description ("Discovery before price" → discovery, "Book a demo with a time" → next_step). A criterion that matches none is left to the coaching review.

### Objections (optional)

The concerns your customers raise, for the Playground. Each becomes a short typed practice call with the AI customer, who opens with these words, followed by a debrief:

```toml
[[objections]]
id = "competitor-cheaper"          # lower-case letters, digits and hyphens; unique
topic = "Price"                    # a short label
says = "Another company quoted me much less for the same size. Why are you so expensive?"
context = "A homeowner with two quotes who wants to compare like for like."   # optional: shapes the AI customer
criterion = "objection-handling"   # optional: the skill the drill is judged on
hint = "Ask what the other quote includes before defending the price."        # optional: shown after the drill
```

Without `criterion`, a drill is judged on the scorecard's objection-handling criterion (`signal = "objections"`), else its discovery criterion, else the first one. Write `says` the way a customer would say it, in the language your customers use; these are your words, not a customer's, so nothing in them is anonymised. The Playground also lists the objections customers raised on your team's reviewed calls, anonymised. Practice never changes a rep's focus, skills or score.

## `facts.toml`

These are short approved statements that a claim check can cite as `fact:<id>`:

```toml
[[fact]]
id = "inverter-warranty"
statement = "Inverters carry a 5-year manufacturer warranty, extendable to 10 years with the paid Care Plan."
source_doc = "docs/warranty.md"   # optional; must exist if given
```

Facts and documents work together. Facts are short, unambiguous statements that are easy to cite. Documents give fuller context and answer questions that no fact covers.

## `docs/`

These are the product sheets, price lists, warranty terms and FAQs a claim is checked against. A claim check cites them as `docs/<path>`, with a quote copied word for word. Write them the way you'd want them quoted. If something is deliberately not promised (for example "approval timing is outside our control"), say that explicitly, because the checker can only work from what is written.

## Templates

A template is a set of header lines, then a blank line, then the body:

```
Subject: Your quotation from {business_name}

Namaste {customer_name},

On our call I promised to {action}.

{rep_name}
```

Email templates use `Subject:` and calendar templates use `Title:`. Available placeholders:

| Placeholder | Value |
|---|---|
| `{business_name}` | `pack.name` |
| `{rep_name}`, `{customer_name}` | speaker names entered for the call |
| `{action}` | the commitment, e.g. "send the detailed quotation" |
| `{quote}` | the exact words from the call |
| `{due_phrase}` | the due phrase as spoken, e.g. "bholi beluka samma" |
| `{due_date}` | resolved date, only when it is exact |
| `{call_date}` | date of the call |

If a placeholder has no value, it is shown as `[customer name]` and listed in the draft's `missing` fields, and the draft becomes `needs_input`. Sel never makes up a value to fill the gap.

## Examples

- [`packs/demo-solar`](../packs/demo-solar): a rooftop-solar installer with mostly Nepali calls
- [`packs/demo-pos`](../packs/demo-pos): restaurant point-of-sale software with mostly English calls

Both are fictional.
