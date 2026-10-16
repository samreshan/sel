/* Sel: Leads, Prep, Team and Settings. Loaded by app.js the first time one of them is opened.
 *
 * Leads (#leads, #lead/<id>) are thin: a name, a phone number, whose lead it
 * is, notes and the lead's calls. They exist for Prep (#prep/<lead>): a brief
 * before the next call, built from the lead's earlier reports, with the
 * objections likely to come back and the best answer Sel has for each (an
 * approved team answer, else the better line), promises, gaps and the rep's
 * focus; "Rehearse this call" opens a drill on the lead's last weak moment.
 *
 * Team (#team, #team/<person>) is for managers: escalations, a skill matrix,
 * patterns across reps, a review queue (team answers the Librarian proposed),
 * and per rep their skill timelines with the real moments, transfer checks,
 * drills and a 1:1 brief. Settings (#settings) shows `sel doctor`: every model,
 * where it runs and its licence. Server text goes in with textContent (el()).
 */
(function () {
  "use strict";

  const S = window.Sel;
  const { el, icon, iconTile, pill, muted, compact, plural, api, navigate, formatStarted, quoted } = S;
  const enc = encodeURIComponent;
  const STATUS = { met: ["ok", "Met"], partial: ["warn", "Partly"], missed: ["bad", "Missed"] };
  const VERDICT = { pass: ["ok", "Passed"], fail: ["bad", "Not yet"], not_applicable: ["neutral", "Did not come up"], unsure: ["warn", "Unclear"] };
  const ESCALATION = {
    contradicted_claim: "A contradicted claim reached a customer",
    failed_drill: "Failed the same drill three times",
    change_manager_focus: "The Coach wants to move on from the focus you set",
  };

  const page = { main: null, data: new Map(), errors: new Map(), loading: new Set(), librarian: null };

  const route = () => S.coachRoute();
  const here = () => S.view() === "coach" && ["leads", "lead", "prep", "team", "settings"].includes(route().page);

  function rerender() {
    if (here() && page.main) render(page.main, route().page, route().arg);
  }

  // GET with a small cache keyed by path; `fresh` re-reads.
  async function load(path, fresh) {
    if (page.loading.has(path)) return;
    page.loading.add(path);
    try {
      page.data.set(path, await api(path, { fresh: true }));
      page.errors.delete(path);
    } catch (e) {
      page.errors.set(path, e.message);
    }
    page.loading.delete(path);
    rerender();
  }

  function got(path) {
    if (!page.data.has(path) && !page.errors.has(path)) load(path);
    return page.data.get(path);
  }

  function invalidate(prefix) {
    for (const k of [...page.data.keys()]) if (k.startsWith(prefix)) page.data.delete(k);
  }

  S.on("coach", () => {
    invalidate("/api/team");
    rerender();
  });
  S.on("drill", () => invalidate("/api/team"));
  S.on("coach", (d) => {
    if (d && d.brief) rerender();
  });
  S.on("call", () => {
    invalidate("/api/leads");
    invalidate("/api/team");
  });
  // A lead changed, or a model brief for one is ready: re-read what is on screen.
  S.on("lead", () => {
    invalidate("/api/leads");
    rerender();
  });
  // The Librarian finished, or a manager approved or rejected a team answer.
  S.on("playbook", (d) => {
    invalidate("/api/team");
    invalidate("/api/leads");
    if (d && d.proposed !== undefined && page.librarian) {
      page.librarian = d.model_error ? `The model failed, so Sel's rules picked: ${d.model_error}` : `The Librarian proposed ${plural(d.proposed, "answer", "answers")}${d.refused ? ` and Sel refused ${d.refused}` : ""}.`;
    }
    rerender();
  });
  S.on("people-changed", rerender);
  S.on("viewer", rerender);

  // ---------- shared pieces ----------

  function header(eyebrow, title, sub, ...extra) {
    return el(
      "header",
      { class: "page-head" },
      el("div", {}, eyebrow ? el("p", { class: "eyebrow", text: eyebrow }) : null, el("h1", { text: title }), sub ? el("p", { class: "lede", text: sub }) : null),
      ...compact(extra)
    );
  }

  function section(cls, title, sub, iconName, tone, ...body) {
    return el(
      "section",
      { class: `list-card page-section ${cls || ""}` },
      el("div", { class: "section-head" }, el("div", { class: "section-head-main" }, iconTile(tone, iconName), el("div", {}, el("h2", { class: "section-title", text: title }), sub ? el("p", { class: "card-sub", text: sub }) : null))),
      el("div", { class: "page-section-body" }, ...compact(body))
    );
  }

  function loading(title, text) {
    return el("div", { class: "page" }, header(null, title, null), el("section", { class: "card is-loading", "aria-busy": "true" }, muted(text || "Loading…")));
  }

  function failed(title, message, retryPath) {
    return el(
      "div",
      { class: "page" },
      header(null, title, null),
      el("section", { class: "card is-error", role: "alert" }, el("p", { class: "strong", text: `${title} could not be loaded.` }), muted(message), el("div", { class: "row" }, el("button", { type: "button", text: "Try again", on: { click: () => { page.errors.delete(retryPath); load(retryPath); } } })))
    );
  }

  // Where an answer came from, labelled as on the Coaching tab.
  const answerLabel = (a) => (a.from === "team answer" ? ["info", "Team answer"] : S.lineOrigin(a));

  function aiPill(by) {
    const b = String(by || "");
    return b.startsWith("model") ? pill("violet", "AI-written") : pill("neutral", "From Sel's rules");
  }

  // ---------- dispatch ----------

  function render(main, name, arg) {
    page.main = main;
    if (name === "settings") return renderSettings(main);
    if (name === "team") return arg ? renderRep(main, arg) : renderTeam(main);
    if (name === "prep") return renderPrep(main, arg);
    if (name === "lead") return renderLead(main, arg);
    return renderLeads(main);
  }

  // ---------- leads ----------

  function renderLeads(main) {
    const me = S.viewer();
    const path = `/api/leads${me && me.role === "rep" ? `?rep=${enc(me.id)}` : ""}`;
    const leads = got(path);
    if (page.errors.has(path)) return main.replaceChildren(failed("Leads", page.errors.get(path), path));
    if (!leads) return main.replaceChildren(loading("Leads", "Finding your leads…"));
    const name = el("input", { id: "lead-name", type: "text", required: true, maxlength: "80", autocomplete: "off" });
    const phone = el("input", { id: "lead-phone", type: "tel", maxlength: "30", autocomplete: "off" });
    const err = el("p", { class: "form-error", role: "alert", hidden: true });
    const form = el("form", { class: "person-form", novalidate: true }, S.formField("lead-name", "Name", name), S.formField("lead-phone", "Phone", phone), err, el("div", { class: "row" }, el("button", { type: "submit", class: "primary", text: "Add lead" })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!name.value.trim()) {
        err.textContent = "Enter a name.";
        err.hidden = false;
        return;
      }
      try {
        const lead = await api("/api/leads", { method: "POST", json: { name: name.value.trim(), phone: phone.value.trim(), rep_id: me && me.role === "rep" ? me.id : null } });
        invalidate("/api/leads");
        navigate(`#lead/${enc(lead.id)}`);
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      }
    });
    const rows = leads.map((l) =>
      el(
        "li",
        { class: "page-row" },
        icon("user", 18),
        el("a", { class: "page-row-link", href: `#lead/${enc(l.id)}` }, el("span", { class: "strong", text: l.name }), el("span", { class: "muted small", text: compact([l.rep_name, plural((l.call_ids || []).length, "call", "calls"), l.phone]).join(" · ") })),
        (l.call_ids || []).length ? el("a", { class: "button small", href: `#prep/${enc(l.id)}`, text: "Prep" }) : null
      )
    );
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Leads", "Leads", "Who you are calling, and their earlier calls. Prep reads those calls before the next one."),
        el(
          "div",
          { class: "bento" },
          section("bento-anchor", "Your leads", null, "user", "accent", rows.length ? el("ul", { class: "page-rows" }, ...rows) : muted("No leads yet. Add one, then choose it when you add a call.")),
          section("", "Add a lead", "A name is enough. Link calls to it from the call page.", "add", "accent", form)
        )
      )
    );
  }

  function renderLead(main, id) {
    const path = `/api/leads/${enc(id)}`;
    const lead = got(path);
    if (page.errors.has(path)) return main.replaceChildren(failed("Lead", page.errors.get(path), path));
    if (!lead) return main.replaceChildren(loading("Lead"));
    const notes = el("textarea", { id: "lead-notes", class: "input", rows: "4", maxlength: "2000", value: lead.notes || "" });
    const msg = el("p", { class: "form-status", role: "status" });
    const save = el("button", { type: "button", class: "small", text: "Save notes" });
    save.addEventListener("click", async () => {
      try {
        await api(path, { method: "PUT", json: { notes: notes.value } });
        msg.textContent = "Saved.";
        msg.className = "form-status is-ok";
      } catch (e) {
        msg.textContent = e.message;
        msg.className = "form-status is-error";
      }
    });
    const calls = (lead.calls || []).map((c) =>
      el("li", { class: "page-row" }, icon("phone", 18), el("a", { class: "page-row-link", href: `#call/${enc(c.id)}` }, el("span", { class: "strong", text: formatStarted(c.started_at) }), el("span", { class: "muted small", text: compact([c.rep_name, S.packName(c.pack_id), c.status]).join(" · ") })))
    );
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Lead", lead.name, compact([lead.phone, lead.rep_name ? `Rep: ${lead.rep_name}` : null]).join(" · "), calls.length ? el("a", { class: "button primary", href: `#prep/${enc(lead.id)}`, text: "Prep for the next call" }) : null),
        el(
          "div",
          { class: "bento" },
          section("bento-anchor", "Calls", null, "phone", "accent", calls.length ? el("ul", { class: "page-rows" }, ...calls) : muted("No calls yet. On a call's page, choose this lead under Lead.")),
          section("", "Notes", "Visible to the team; Prep reads the calls, not these notes.", "doc", "accent", S.formField("lead-notes", "Notes", notes), el("div", { class: "row" }, save), msg)
        ),
        el("p", {}, el("a", { href: "#leads", text: "All leads" }))
      )
    );
  }

  // ---------- prep ----------

  function renderPrep(main, id) {
    const path = `/api/leads/${enc(id)}/prep`;
    const brief = got(path);
    if (page.errors.has(path)) return main.replaceChildren(failed("Prep", page.errors.get(path), path));
    if (!brief) return main.replaceChildren(loading("Prep", "Reading this lead's earlier calls…"));
    const rehearse = el("button", { type: "button", class: "primary", text: "Rehearse this call" });
    const rmsg = el("p", { class: "form-error", role: "alert", hidden: true });
    rehearse.addEventListener("click", async () => {
      rehearse.disabled = true;
      try {
        const me = S.viewer();
        const d = await api(`/api/leads/${enc(id)}/rehearse`, { method: "POST", json: { by: me ? me.id : null } });
        navigate(`#practice/${enc(d.id)}`);
      } catch (e) {
        rehearse.disabled = false;
        rmsg.textContent = e.message;
        rmsg.hidden = false;
      }
    });
    const objections = brief.likely_objections.map((o) =>
      el(
        "li",
        { class: "skill" },
        el("div", { class: "skill-head" }, el("span", { class: "strong", text: o.text }), el("span", { class: "muted small", text: formatStarted(o.started_at) })),
        o.quote ? el("p", { class: "said-line role-customer" }, el("span", { class: "said-who", text: "They said" }), quoted(o.quote)) : null,
        o.answer
          ? el("div", { class: "better-line" }, el("div", { class: "better-line-head" }, el("span", { class: "label", text: "Answer" }), pill(...answerLabel(o.answer))), el("p", { class: "better-line-text", text: quoted(o.answer.text) }))
          : muted("No answer on file yet. Ask what is behind the concern.")
      )
    );
    const gaps = brief.gaps.map((g) => el("li", { class: "page-row" }, icon(g.criterion ? "target" : "question", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: g.title || g.text }), el("span", { class: "muted small", text: g.criterion ? g.suggestion || g.detail || "" : "They asked this; check it was answered." }))));
    const promises = brief.promises.map((p) => el("li", { class: "page-row" }, icon(p.owner === "rep" ? "mail" : "user", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `${p.owner === "rep" ? "You" : "They"}: ${p.action}` }), el("span", { class: "muted small", text: p.due && p.due.status === "exact" ? `Due ${p.due.date}` : p.due_text ? `${quoted(p.due_text)}: no fixed day` : "No day said" }))));
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Prep", `Before your call with ${brief.lead.name}`, `${plural(brief.calls.length, "earlier call", "earlier calls")}. Read in about a minute.`, rehearse),
        rmsg,
        el(
          "div",
          { class: "bento" },
          section(
            "bento-anchor",
            "Likely objections",
            "What they raised before, with the best answer Sel has: the team's approved answer, else the better line.",
            "alert",
            "warn",
            el("div", { class: "focus-body" }, el("p", {}, brief.summary.text, " ", aiPill(brief.summary.by)), brief.model_pending ? muted("The language model is reading these calls too; this brief updates by itself.") : null),
            objections.length ? el("ul", { class: "skills" }, ...objections) : muted("No objections on earlier calls.")
          ),
          el(
            "div",
            { class: "stack" },
            brief.focus ? section("", "Your focus", null, "target", "violet", el("p", { class: "strong", text: brief.focus.title }), brief.focus.reason ? muted(brief.focus.reason) : null) : null,
            section("", "Cover this time", null, "question", "info", gaps.length ? el("ul", { class: "page-rows" }, ...gaps) : muted("No gaps found.")),
            section("", "Promises", null, "mail", "info", promises.length ? el("ul", { class: "page-rows" }, ...promises) : muted("No promises on earlier calls."))
          )
        )
      )
    );
  }

  // ---------- team ----------

  function renderTeam(main) {
    const me = S.viewer();
    if (!me || me.role !== "manager") {
      main.replaceChildren(el("div", { class: "page" }, header("Team", "Team", "For managers. Choose a manager under Viewing as."), el("a", { class: "button", href: "#people", text: "People" })));
      return;
    }
    const path = "/api/team";
    const t = got(path);
    if (page.errors.has(path)) return main.replaceChildren(failed("Team", page.errors.get(path), path));
    if (!t) return main.replaceChildren(loading("Team", "Reading the team's calls…"));
    const nodes = [header("Team", "Team", "Where each rep stands on each skill, what repeats across the team, and what needs you.")];
    if (t.escalations.length) nodes.push(escalationList(t.escalations, me));
    nodes.push(el("div", { class: "bento" }, matrix(t), patterns(t.patterns)));
    nodes.push(reviewQueue(t.review, me));
    main.replaceChildren(el("div", { class: "page" }, ...compact(nodes)));
  }

  function escalationList(list, me) {
    const rows = list.map((e) => {
      const done = el("button", { type: "button", class: "small", text: "Mark handled" });
      done.addEventListener("click", async () => {
        done.disabled = true;
        try {
          await api(`/api/coach/${enc(e.person_id)}/escalations/${enc(e.key)}`, { method: "PUT", json: { status: "resolved", by: me.id } });
          invalidate("/api/team");
          rerender();
        } catch (ex) {
          done.disabled = false;
          done.textContent = ex.message;
        }
      });
      return el(
        "li",
        { class: "page-row" },
        icon("alert", 18),
        el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `${e.person_name}: ${ESCALATION[e.reason] || e.reason}` }), el("span", { class: "muted small", text: e.note || "" })),
        e.call_id ? el("a", { class: "button small", href: `#call/${enc(e.call_id)}`, text: "Open the call" }) : e.drill_id ? el("a", { class: "button small", href: `#practice/${enc(e.drill_id)}`, text: "Open the drill" }) : null,
        done
      );
    });
    return section("", "Needs you", "Sel's coach escalates only these; everything else it handles itself.", "alert", "warn", el("ul", { class: "page-rows" }, ...rows));
  }

  function matrix(t) {
    if (!t.reps.length) return section("bento-anchor", "Skills by rep", null, "target", "violet", muted("Add reps under People, and say whose each call is."));
    const head = el("tr", {}, el("th", { scope: "col", text: "Rep" }), ...t.criteria.map((c) => el("th", { scope: "col", text: c.title, title: S.packName(c.pack_id) })), el("th", { scope: "col", text: "Focus" }));
    const rows = t.reps.map((r) =>
      el(
        "tr",
        {},
        el("th", { scope: "row" }, el("a", { href: `#team/${enc(r.id)}`, text: r.name })),
        ...t.criteria.map((c) => {
          const s = r.skills[c.key];
          if (!s || !s.scored) return el("td", { class: "muted" }, el("span", { "aria-hidden": "true", text: "–" }), el("span", { class: "sr-only", text: "Not scored" }));
          const ratio = s.short / s.scored;
          const tone = ratio === 0 ? "ok" : ratio >= 0.5 ? "bad" : "warn";
          return el("td", {}, pill(tone, `${s.scored - s.short} of ${s.scored}`));
        }),
        el("td", { class: "matrix-focus", text: r.focus ? r.focus.title : "None" })
      )
    );
    return section("bento-anchor", "Skills by rep", "Calls where each skill was met, of the reviewed real calls it was scored on (last 10 per rep).", "target", "violet", el("div", { class: "table-scroll", tabindex: "0", role: "region", "aria-label": "Skills by rep" }, el("table", { class: "team-table matrix" }, el("thead", {}, head), el("tbody", {}, ...rows))));
  }

  function patterns(p) {
    const skills = p.skills.slice(0, 4).map((s) => el("li", { class: "page-row" }, icon("target", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: s.title }), el("span", { class: "muted small", text: `${s.reps_short} of ${plural(s.reps_scored, "rep", "reps")} fell short recently` }))));
    const objections = p.objections.slice(0, 4).map((o) =>
      el("li", { class: "page-row" }, icon("alert", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `“${o.cue}”: ${plural(o.count, "time", "times")}` }), el("span", { class: "muted small", text: o.examples.map((x) => `${x.rep_name || "a rep"}: ${quoted(x.quote)}`).join(" · ") })))
    );
    return section("", "Patterns", "Across reps, from Sel's rules over real calls.", "lines", "accent", el("h3", { class: "group-title", text: "Skills more than one rep fell short on" }), skills.length ? el("ul", { class: "page-rows" }, ...skills) : muted("Nothing shared yet."), el("h3", { class: "group-title", text: "Objections customers raise" }), objections.length ? el("ul", { class: "page-rows" }, ...objections) : muted("No objections yet."));
  }

  function reviewQueue(review, me) {
    const run = el("button", { type: "button", class: "small", text: "Ask the Librarian" });
    const msg = el("p", { class: "form-status", role: "status", text: page.librarian || "" });
    run.addEventListener("click", async () => {
      run.disabled = true;
      page.librarian = "The Librarian is reading the team's strong moments and endorsed lines…";
      msg.textContent = page.librarian;
      try {
        // Queued on the server; a `playbook` event says when each pack is done.
        for (const p of S.packs()) await api(`/api/playbook/${enc(p.id)}/answers`, { method: "POST", json: { action: "propose", by: me.id } });
      } catch (e) {
        page.librarian = e.message;
        msg.textContent = e.message;
        run.disabled = false;
      }
    });
    const rows = review.proposals.map((a) => {
      const decide = (action) => async (ev) => {
        ev.currentTarget.disabled = true;
        try {
          await api(`/api/playbook/${enc(a.pack_id)}/answers`, { method: "POST", json: { action, id: a.id, by: me.id } });
          invalidate("/api/team");
          rerender();
        } catch (e) {
          msg.textContent = e.message;
        }
      };
      return el(
        "li",
        { class: "skill" },
        el("div", { class: "skill-head" }, el("span", { class: "strong", text: a.objection }), pill("neutral", S.packName(a.pack_id)), a.by.startsWith("librarian:model") ? pill("violet", "Picked by AI") : pill("neutral", "Picked by Sel's rules")),
        el("p", { class: "better-line-text", text: quoted(a.answer) }),
        el("p", { class: "muted small", text: `${a.answer_from === "rep" ? "Said by a rep on a real call" : "A better line a rep endorsed"}. Checked against the pack.` }),
        el("div", { class: "row" }, el("button", { type: "button", class: "small primary", text: "Approve for the team", on: { click: decide("approve") } }), el("button", { type: "button", class: "small ghost", text: "Reject", on: { click: decide("reject") } }), a.source && a.source.call_id ? el("a", { class: "button small", href: `#call/${enc(a.source.call_id)}`, text: "Open the call" }) : null)
      );
    });
    return section("", "Review queue", "Answers the Librarian found in the team's calls. Approved ones feed better lines, Prep and rehearsals.", "check", "ok", el("div", { class: "row" }, run), msg, rows.length ? el("ul", { class: "skills" }, ...rows) : muted("Nothing waiting."), review.unassigned && review.unassigned.length ? muted(`${plural(review.unassigned.length, "call has", "calls have")} no rep yet: see Today.`) : null);
  }

  function renderRep(main, id) {
    const path = `/api/team/${enc(id)}`;
    const r = got(path);
    if (page.errors.has(path)) return main.replaceChildren(failed("Rep", page.errors.get(path), path));
    if (!r) return main.replaceChildren(loading("Rep", "Reading this rep's calls…"));
    const timelines = r.timelines.map((tl) =>
      el(
        "li",
        { class: "skill" },
        el("div", { class: "skill-head" }, el("span", { class: "strong", text: tl.title }), r.focus && r.focus.criterion === tl.criterion ? pill("violet", "Focus") : null),
        el(
          "ol",
          { class: "timeline" },
          ...tl.points.map((p) => {
            const st = STATUS[p.status] || ["neutral", p.status];
            return el(
              "li",
              { class: `is-${p.status}` },
              el("div", { class: "better-line-head" }, pill(st[0], st[1]), el("a", { href: `#call/${enc(p.call_id)}`, text: compact([p.customer_name, formatStarted(p.started_at)]).join(" · ") })),
              p.moment ? el("p", { class: "said-line role-rep" }, el("span", { class: "said-who", text: p.moment.kind === "strong" ? "Did well" : "Fell short" }), quoted(p.moment.quote)) : null
            );
          })
        )
      )
    );
    const transfer = (r.transfer || []).slice().reverse().map((x) => {
      const v = VERDICT[x.verdict] || ["neutral", x.verdict];
      return el("li", { class: "page-row" }, icon("phone", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: x.quote ? quoted(x.quote) : x.why }), el("span", { class: "muted small", text: compact([x.customer_name, formatStarted(x.started_at), x.why]).join(" · ") })), pill(v[0], v[1]));
    });
    const brief = r.brief;
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Team", r.person.name, r.focus ? `Focus: ${r.focus.title}` : "No focus yet", el("a", { class: "button", href: "#team", text: "Team" })),
        el(
          "div",
          { class: "bento" },
          section(
            "bento-anchor",
            "1:1 brief",
            "Talking points from this rep's calls, drills and transfer checks.",
            "user",
            "accent",
            brief.summary ? el("p", {}, brief.summary.text, " ", aiPill(brief.summary.by)) : brief.model_pending ? muted("The chat model is writing a short summary of these points…") : null,
            el("ul", { class: "page-rows" }, ...brief.points.map((p) => el("li", { class: "page-row" }, icon(p.icon || "check", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: p.title }), p.detail ? el("span", { class: "muted small", text: p.detail }) : null), p.call_id ? el("a", { class: "button small", href: `#call/${enc(p.call_id)}`, text: "Open the call", "aria-label": `Open the call: ${p.title}` }) : p.drill_id ? el("a", { class: "button small", href: `#practice/${enc(p.drill_id)}`, text: "Open the drill" }) : null)))
          ),
          section("", "On real calls since the focus", null, "phone", "info", transfer.length ? el("ul", { class: "page-rows" }, ...transfer) : muted("No real call checked against the focus yet."))
        ),
        section("", "Skills over time", "Each reviewed real call, oldest first, with the moment Sel found.", "target", "violet", timelines.length ? el("ul", { class: "skills" }, ...timelines) : muted("No reviewed calls yet."))
      )
    );
  }

  // ---------- settings ----------

  function renderSettings(main) {
    const path = "/api/doctor";
    const d = got(path);
    if (page.errors.has(path)) return main.replaceChildren(failed("Settings", page.errors.get(path), path));
    if (!d) return main.replaceChildren(loading("Settings", "Checking every model Sel uses…"));
    const model = (m, label) => {
      if (!m) return null;
      if (m.error) return el("li", { class: "page-row" }, icon("alert", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: label }), el("span", { class: "muted small", text: m.error })));
      const reach = m.health ? (m.health.ok ? pill("ok", "Answering") : pill("bad", "Not answering")) : null;
      return el(
        "li",
        { class: "page-row" },
        icon("bolt", 18),
        el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `${label}: ${m.model}` }), el("span", { class: "muted small", text: compact([m.role, `runs on ${m.runs_on}`, m.endpoint]).join(" · ") })),
        pill(String(m.licence).startsWith("unknown") ? "warn" : "info", m.licence),
        reach
      );
    };
    const sp = d.speech || {};
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Settings", "Models on this computer", "Every model Sel uses, what for, where it runs and its licence. The same check as `python -m sel doctor`."),
        el("section", { class: `card ${d.ok ? "is-success" : "is-error"}`, role: "status" }, el("p", { class: "strong", text: d.ok ? "Open models only: no proprietary model service is configured." : `Not allowed: ${d.problems.join("; ")}` })),
        section(
          "",
          "Models",
          "Change them with the SEL_LLM_*, SEL_CHAT_LLM_* and SEL_WHISPER_* settings (docs/running.md).",
          "bolt",
          "accent",
          el(
            "ul",
            { class: "page-rows" },
            ...compact([
              model(d.llm, "Analysis and coaching"),
              model(d.chat, "Conversation (Coach, AI customer)"),
              el("li", { class: "page-row" }, icon("mic", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `Speech: ${sp.uploads}` }), el("span", { class: "muted small", text: `Live: ${sp.live}; runs on ${sp.runs_on}` })), pill(sp.installed ? "ok" : "warn", sp.installed ? "Installed" : "Not installed")),
              el("li", { class: "page-row" }, icon("lock", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: "Browser speech services" }), el("span", { class: "muted small", text: d.browser_speech_services })))
            ])
          )
        ),
        el("p", { class: "muted small", text: "Sel itself is Apache-2.0. MODELS.md in the source lists every model and dependency with its licence." })
      )
    );
  }

  S.team = { render };
})();
