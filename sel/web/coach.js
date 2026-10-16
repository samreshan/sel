/* Sel: Today, Playbook and People. Loaded by app.js the first time one of them is opened.
 *
 * Today answers "what needs my attention?" for whoever this browser is
 * viewing as: their follow-ups, the scorecard skills they fell short on
 * across recent calls, and calls still being analysed. A manager sees the
 * team instead. Everything comes from GET /api/today, which reads the calls'
 * own files; nothing here is guessed.
 *
 * Playbook shows a business pack as reps use it: the skills the scorecard
 * checks, the approved facts and the documents claims are checked against.
 * People is the team list behind "Viewing as" (no login; see docs/decisions.md).
 */
(function () {
  "use strict";

  const S = window.Sel;
  const { el, icon, iconTile, pill, muted, compact, plural, api, navigate, formatStarted, stageText, DRAFT_STATUS, SCORE_STATUS } = S;
  const enc = encodeURIComponent;
  const ROLE = { rep: "Rep", manager: "Manager" };

  const page = {
    today: null, // last /api/today response
    todayFor: undefined, // viewer id it was loaded for
    todayErr: null,
    loading: false,
    refetch: 0, // debounce timer for call events
    packs: new Map(), // pack id -> detail
    docs: new Map(), // "pack|ref" -> text or {error}
    openDocs: new Set(),
    main: null,
    shown: null, // the page last rendered
  };

  // ---------- dispatch ----------

  function render(main, name, arg) {
    const arrived = page.shown !== name || S.view() !== "coach" || page.main !== main;
    page.main = main;
    page.shown = name;
    if (name === "playbook") return renderPlaybook(main, arg);
    if (name === "people") return renderPeople(main);
    renderToday(main);
    // Opening Today shows what it had at once, then re-checks: changes made in this tab (a rep
    // assigned, a draft approved) are not echoed back on the event stream.
    if (arrived && page.today && !page.loading) loadToday(true);
  }

  function rerender() {
    const r = S.coachRoute();
    if (S.view() === "coach" && page.main) render(page.main, r.page, r.arg);
  }

  // Calls change while Today is open: refetch once things settle.
  S.on("call", () => {
    if (S.view() !== "coach" || S.coachRoute().page !== "today") {
      page.todayFor = undefined;
      return;
    }
    clearTimeout(page.refetch);
    page.refetch = setTimeout(() => loadToday(true), 400);
  });
  S.on("call-changed", () => {
    page.todayFor = undefined;
  });
  // The Coach picked a focus (or someone changed one) in another tab or on the server.
  S.on("coach", () => {
    if (S.view() === "coach" && S.coachRoute().page === "today") loadToday(true);
    else page.todayFor = undefined;
  });
  S.on("people-changed", rerender);
  // Leaving a page means the next visit counts as arriving (see render).
  window.addEventListener("hashchange", () => {
    const h = location.hash.replace(/^#/, "") || "today";
    if (h.split("/")[0] !== page.shown) page.shown = null;
  });
  S.on("viewer", () => {
    page.todayFor = undefined;
  });

  function header(eyebrow, title, sub, ...extra) {
    return el(
      "header",
      { class: "page-head" },
      el("div", {}, eyebrow ? el("p", { class: "eyebrow", text: eyebrow }) : null, el("h1", { text: title }), sub ? el("p", { class: "lede", text: sub }) : null),
      ...compact(extra)
    );
  }

  function section(title, sub, iconName, tone, ...body) {
    return sectionOf("list-card page-section", title, sub, iconName, tone, ...body);
  }

  function sectionOf(cls, title, sub, iconName, tone, ...body) {
    return el(
      "section",
      { class: cls },
      el("div", { class: "section-head" }, el("div", { class: "section-head-main" }, iconTile(tone, iconName), el("div", {}, el("h2", { class: "section-title", text: title }), sub ? el("p", { class: "card-sub", text: sub }) : null))),
      el("div", { class: "page-section-body" }, ...compact(body))
    );
  }

  function callLabel(c) {
    return c.customer_name || "Untitled call";
  }

  function callLink(c, extra) {
    return el(
      "a",
      { class: "page-row-link", href: `#call/${enc(c.id)}` },
      el("span", { class: "strong", text: callLabel(c) }),
      el("span", { class: "muted small", text: compact([c.rep_name, S.packName(c.pack_id), formatStarted(c.started_at), extra]).join(" · ") })
    );
  }

  function todayDate() {
    return new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  }

  // ---------- Today ----------

  async function loadToday(force = false) {
    const me = S.viewer();
    const id = me ? me.id : null;
    if (!force && page.todayFor === id && page.today) return;
    page.loading = true;
    try {
      const data = await api(`/api/today${id ? `?person=${enc(id)}` : ""}`, { fresh: true });
      if ((S.viewer() ? S.viewer().id : null) !== id) return; // switched while loading
      page.today = data;
      page.todayFor = id;
      page.todayErr = null;
    } catch (e) {
      page.todayErr = e.message;
    } finally {
      page.loading = false;
    }
    if (S.view() === "coach" && S.coachRoute().page === "today") renderToday(page.main);
  }

  function renderToday(main) {
    const people = S.people();
    const me = S.viewer();
    if (!people.length) return main.replaceChildren(onboarding());
    if (!me) return main.replaceChildren(chooseViewer(people));
    const id = me.id;
    if (page.todayFor !== id || !page.today) {
      const head = header(todayDate(), "Today", `${me.name} · ${ROLE[me.role]}`);
      if (page.todayErr && !page.loading) {
        // A first load that failed: say why and offer to try again, rather than loading forever.
        const retry = el("button", { type: "button", text: "Try again", on: { click: () => { page.todayErr = null; renderToday(main); } } });
        main.replaceChildren(el("div", { class: "page" }, head, el("section", { class: "card is-error", role: "alert" }, el("p", { class: "strong", text: "Today could not be loaded." }), el("p", { class: "muted small", text: page.todayErr }), el("div", { class: "row" }, retry))));
        return;
      }
      main.replaceChildren(
        el("div", { class: "page" }, head, el("section", { class: "card is-loading", "aria-busy": "true" }, el("p", { class: "muted", text: `Gathering ${me.role === "manager" ? "the team's" : "your"} calls and follow-ups…` })))
      );
      if (!page.loading) loadToday();
      return;
    }
    const t = page.today;
    const manager = t.scope === "team";
    const nodes = [
      header(
        todayDate(),
        manager ? "Your team today" : `Today, ${me.name.split(" ")[0]}`,
        manager ? "Follow-ups waiting across the team, where reps fall short, and calls that need a rep." : "What needs you: follow-ups to finish, and the skills your recent calls fell short on.",
        el("button", { type: "button", class: "primary", text: "New call", on: { click: () => navigate("#new") } })
      ),
      page.todayErr ? el("p", { class: "form-error", role: "alert", text: page.todayErr }) : null,
      counts(t),
      t.focus
        ? el("div", { class: "bento" }, focusCard(t.focus), followUps(t, manager))
        : el("div", { class: "bento" }, skills(t, manager), followUps(t, manager)),
      t.focus && t.skills.length ? skills(t, manager, false) : null,
      manager ? team(t) : null,
      manager && t.unassigned.length ? unassigned(t) : null,
      working(t),
      recent(t),
    ];
    main.replaceChildren(el("div", { class: "page" }, ...compact(nodes)));
  }

  // A passive summary: numbers, not buttons.
  function counts(t) {
    const stat = (label, n, warn) => el("div", { class: `stat${warn ? " is-warn" : ""}` }, el("dt", { text: label }), el("dd", { text: String(n) }));
    return el(
      "dl",
      { class: "stat-strip", "aria-label": "Summary" },
      stat("Follow-ups needing input", t.counts.needs_input, t.counts.needs_input > 0),
      stat("Ready for approval", t.counts.ready_for_approval),
      stat("Being analysed", t.in_progress.length),
      stat(t.scope === "team" ? "Team calls" : "Your calls", t.counts.calls)
    );
  }

  function followUps(t, manager) {
    if (!t.follow_ups.length) {
      return section("Follow-ups", null, "mail", "info", muted(t.counts.calls ? "Nothing waiting. Every draft from your calls is approved." : "Follow-up drafts from your calls will show here."));
    }
    const rows = t.follow_ups.map((f) => {
      const st = DRAFT_STATUS[f.status] || { cls: "neutral", label: f.status };
      const what = f.subject || (f.kind === "calendar" ? "Calendar invite" : "Email");
      const need = f.status === "needs_input" && f.missing.length ? `Needs ${f.missing.join(", ")}` : null;
      return el(
        "li",
        { class: "page-row" },
        icon(f.kind === "calendar" ? "calendar" : "mail", 18),
        el("div", { class: "page-row-main" }, el("span", { class: "strong", text: what }), el("span", { class: "muted small", text: compact([callLabel(f.call), manager ? f.call.rep_name : null, need]).join(" · ") })),
        pill(st.cls, st.label),
        el("a", { class: "button small", href: `#call/${enc(f.call.id)}`, text: f.status === "needs_input" ? "Fill in" : "Review" })
      );
    });
    return section("Follow-ups", "Drafts wait for a person. Sel never sends them.", "mail", "info", el("ul", { class: "page-rows" }, ...rows));
  }

  // ---------- this week's focus ----------

  function shortDate(iso) {
    const d = new Date(iso || "");
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }

  function focusCard(f) {
    const who = f.set_by_name || "Sel's coach";
    const sub = compact([`Set by ${who}`, shortDate(f.since) ? `since ${shortDate(f.since)}` : null, S.packName(f.pack_id)]).join(" · ");
    const m = f.latest;
    const line = m && m.better_line;
    const body = [
      el("div", { class: "focus-body" }, el("p", { class: "focus-title", text: f.title }), f.reason ? el("p", { class: "muted", text: f.reason }) : null),
    ];
    if (m) {
      body.push(
        el(
          "div",
          { class: "focus-moment" },
          el("p", { class: "muted small", text: compact([`On your call with ${m.customer_name || "a customer"}`, formatStarted(m.started_at)]).join(" · ") }),
          el(
            "div",
            { class: "said" },
            m.customer_said ? el("p", { class: "said-line role-customer" }, el("span", { class: "said-who", text: "Customer" }), S.quoted(m.customer_said.quote)) : null,
            el("p", { class: "said-line role-rep" }, el("span", { class: "said-who", text: "You" }), S.quoted(m.quote))
          ),
          line && line.check === "ok"
            ? el(
                "div",
                { class: "better-line" },
                el("div", { class: "better-line-head" }, el("span", { class: "label", text: "Try instead" }), pill(String(line.by || "").startsWith("person") ? "neutral" : "violet", String(line.by || "").startsWith("person") ? "Written by a person" : String(line.by || "").startsWith("fixture") ? "Example line" : "AI-suggested"), line.facts && line.facts.length ? pill("ok", "Checked against your pack") : null),
                el("p", { class: "better-line-text", text: S.quoted(line.text) })
              )
            : null,
          el("div", { class: "row" }, el("a", { class: "button small", href: `#call/${enc(m.call_id)}`, text: "Open the call" }))
        )
      );
    } else body.push(muted("The moments behind this focus will show here after your next reviewed call."));
    return sectionOf("list-card page-section bento-anchor focus-card", "This week's focus", sub, "target", "violet", ...body);
  }

  async function setFocus(skill, btn) {
    const me = S.viewer();
    if (!me) return;
    btn.disabled = true;
    try {
      await api(`/api/coach/${enc(me.id)}/focus`, { method: "PUT", json: { pack_id: skill.pack_id, criterion: skill.criterion_id, by: me.id } });
      loadToday(true);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = e.message;
    }
  }

  function skills(t, manager, anchor = true) {
    if (!t.skills.length) {
      return sectionOf(
        `list-card page-section${anchor ? " bento-anchor" : ""} is-empty`,
        "Skills to work on",
        null,
        "target",
        "violet",
        muted(t.recent.length ? "Every scorecard skill was met on the recent calls." : "Once calls are reviewed, the scorecard skills they fell short on show here.")
      );
    }
    const manyPacks = new Set(t.skills.map((s) => s.pack_id)).size > 1;
    const rows = t.skills.map((s) => {
      const ratio = s.short / s.scored;
      return el(
        "li",
        { class: "skill" },
        el(
          "div",
          { class: "skill-head" },
          el("span", { class: "strong", text: s.title }),
          pill(ratio >= 0.5 ? "bad" : "warn", `Short on ${s.short} of ${plural(s.scored, "call", "calls")}`),
          manyPacks ? el("span", { class: "muted small", text: S.packName(s.pack_id) }) : null
        ),
        s.suggestion ? el("p", { class: "muted small", text: s.suggestion }) : null,
        el(
          "div",
          { class: "skill-calls" },
          ...s.calls.slice(0, 4).map((c) => {
            const st = SCORE_STATUS[c.status] || { label: c.status };
            return el("a", { class: "chip-link", href: `#call/${enc(c.id)}`, title: c.detail || "" }, `${callLabel(c)}${manager && c.rep_name ? ` (${c.rep_name})` : ""} · ${st.label}`);
          })
        ),
        el(
          "div",
          { class: "row" },
          el("button", { type: "button", class: "small ghost", text: "Practise this", on: { click: () => navigate("#sim") } }),
          !manager && !(t.focus && t.focus.pack_id === s.pack_id && t.focus.criterion === s.criterion_id)
            ? el("button", { type: "button", class: "small ghost", text: t.focus ? "Make this my focus" : "Focus on this", on: { click: (e) => setFocus(s, e.currentTarget) } })
            : null
        )
      );
    });
    return sectionOf(
      `list-card page-section${anchor ? " bento-anchor" : ""}`,
      "Skills to work on",
      manager ? "Scorecard skills the team fell short on in recent calls, from the pack's scorecard." : "Scorecard skills your recent calls fell short on. Practise one, then watch it on your next calls.",
      "target",
      "violet",
      el("ul", { class: "skills" }, ...rows)
    );
  }

  function team(t) {
    if (!t.team.length) return section("Team", null, "user", "accent", muted("Add reps under Manage people to see each rep here."));
    const rows = t.team.map((r) =>
      el(
        "tr",
        {},
        el("th", { scope: "row", text: r.name }),
        el("td", { text: String(r.calls) }),
        el("td", { text: r.average === null ? "–" : `${r.average}` }),
        el("td", { text: r.focus ? r.focus.title : "None yet" }),
        el("td", { text: String(r.open_follow_ups) }),
        el("td", {}, el("button", { type: "button", class: "small", text: "View as", on: { click: () => S.setViewer(r.id) } }))
      )
    );
    return section(
      "Team",
      "Average scorecard over each rep's last 10 reviewed calls, and the skill each rep is focusing on.",
      "user",
      "accent",
      el(
        "table",
        { class: "team-table" },
        el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Rep" }), el("th", { scope: "col", text: "Calls" }), el("th", { scope: "col", text: "Average score" }), el("th", { scope: "col", text: "Focus" }), el("th", { scope: "col", text: "Open follow-ups" }), el("th", { scope: "col", text: "" }))),
        el("tbody", {}, ...rows)
      )
    );
  }

  function unassigned(t) {
    const reps = S.people().filter((p) => p.role === "rep");
    const rows = t.unassigned.map((c) => {
      const sel = el("select", { class: "small-select", "aria-label": `Rep for ${callLabel(c)}` }, el("option", { value: "", text: "Choose rep" }), ...reps.map((p) => el("option", { value: p.id, text: p.name })));
      sel.addEventListener("change", async () => {
        if (!sel.value) return;
        sel.disabled = true;
        try {
          await api(`/api/calls/${enc(c.id)}/rep`, { method: "PUT", json: { rep_id: sel.value } });
          loadToday(true);
        } catch (e) {
          sel.disabled = false;
          sel.value = "";
          window.alert(e.message); // eslint-disable-line no-alert
        }
      });
      return el("li", { class: "page-row" }, icon("phone", 18), callLink(c), reps.length ? sel : null);
    });
    return section("Calls with no rep", "Say whose call each one is, so it counts towards their coaching.", "phone", "warn", el("ul", { class: "page-rows" }, ...rows));
  }

  function working(t) {
    const items = [
      ...t.in_progress.map((c) => el("li", { class: "page-row" }, icon("bolt", 18), callLink(c, `${stageText(c.stage)} · ${Math.round((c.progress || 0) * 100)}%`), pill("info", "Analysing"))),
      ...t.failed.map((c) => el("li", { class: "page-row" }, icon("alert", 18), callLink(c), pill("bad", "Failed"))),
    ];
    return items.length ? section("In progress", null, "bolt", "info", el("ul", { class: "page-rows" }, ...items)) : null;
  }

  function recent(t) {
    if (!t.recent.length) return null;
    const rows = t.recent.map((c) =>
      el(
        "li",
        { class: "page-row" },
        icon("phone", 18),
        callLink(c),
        c.overall === null || c.overall === undefined ? null : el("span", { class: "score-chip", text: `${c.overall}`, title: "Scorecard" }),
        c.source === "simulator" ? pill("violet", "Practice") : c.source === "fixture" ? pill("neutral", "Example") : null
      )
    );
    return section("Recent calls", null, "lines", "accent", el("ul", { class: "page-rows" }, ...rows));
  }

  // ---------- choosing who you are ----------

  function personForm(onDone, submitLabel) {
    const name = el("input", { type: "text", id: "p-name", autocomplete: "name", required: true, maxlength: "80" });
    const role = el("select", { id: "p-role" }, el("option", { value: "rep", text: "Rep" }), el("option", { value: "manager", text: "Manager" }));
    const err = el("p", { class: "form-error", role: "alert", hidden: true });
    const btn = el("button", { type: "submit", class: "primary", text: submitLabel });
    const form = el("form", { class: "person-form", novalidate: true }, S.formField("p-name", "Name", name), S.formField("p-role", "Role", role), err, el("div", { class: "row" }, btn));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.hidden = true;
      if (!name.value.trim()) {
        err.textContent = "Enter a name.";
        err.hidden = false;
        return;
      }
      btn.disabled = true;
      try {
        const person = await api("/api/people", { method: "POST", json: { name: name.value.trim(), role: role.value } });
        name.value = "";
        await S.refreshPeople();
        onDone(person);
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      }
      btn.disabled = false;
    });
    return form;
  }

  function onboarding() {
    return el(
      "section",
      { class: "welcome" },
      el("p", { class: "eyebrow", text: "Set up" }),
      el("h1", { text: "Who uses Sel here?" }),
      el("p", { class: "lede", text: "Add yourself first. Sel keeps each rep's calls, follow-ups and coaching together. There is no login: each browser chooses who it is viewing as." }),
      personForm((p) => S.setViewer(p.id), "Add and continue")
    );
  }

  function chooseViewer(people) {
    const btn = (p) => el("button", { type: "button", class: "person-pick", on: { click: () => S.setViewer(p.id) } }, el("span", { class: "strong", text: p.name }), el("span", { class: "muted small", text: ROLE[p.role] }));
    return el(
      "section",
      { class: "welcome" },
      el("p", { class: "eyebrow", text: todayDate() }),
      el("h1", { text: "Who are you?" }),
      el("p", { class: "lede", text: "Today shows your calls and follow-ups, or your team's if you are a manager." }),
      el("div", { class: "person-picks" }, ...people.map(btn)),
      el("a", { href: "#people", class: "small", text: "Someone missing? Manage people" })
    );
  }

  // ---------- People ----------

  function renderPeople(main) {
    const people = S.people();
    const me = S.viewer();
    const rows = people.map((p) => {
      const name = el("input", { type: "text", value: p.name, "aria-label": `Name of ${p.name}`, maxlength: "80" });
      const role = el("select", { "aria-label": `Role of ${p.name}` }, el("option", { value: "rep", text: "Rep" }), el("option", { value: "manager", text: "Manager" }));
      role.value = p.role;
      const msg = el("span", { class: "muted small", role: "status" });
      const save = async () => {
        if (name.value.trim() === p.name && role.value === p.role) return;
        try {
          await api(`/api/people/${enc(p.id)}`, { method: "PUT", json: { name: name.value.trim(), role: role.value } });
          await S.refreshPeople();
          page.todayFor = undefined;
        } catch (e) {
          msg.textContent = e.message;
          name.value = p.name;
          role.value = p.role;
        }
      };
      name.addEventListener("change", save);
      role.addEventListener("change", save);
      return el(
        "li",
        { class: "page-row person-row" },
        icon("user", 18),
        name,
        role,
        me && me.id === p.id ? pill("ok", "You") : el("button", { type: "button", class: "small", text: "View as", on: { click: () => S.setViewer(p.id) } }),
        msg
      );
    });
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Team", "People", "Reps and managers. Calls belong to a rep; managers see the whole team on Today. There is no login, so anyone on this computer can switch."),
        section("Add a person", null, "user", "accent", personForm((p) => (!S.viewer() ? S.setViewer(p.id) : null), "Add")),
        people.length ? section("The team", null, "user", "accent", el("ul", { class: "page-rows" }, ...rows)) : null
      )
    );
  }

  // ---------- Playbook ----------

  async function loadPack(id) {
    try {
      page.packs.set(id, await api(`/api/packs/${enc(id)}`));
    } catch (e) {
      page.packs.set(id, { error: e.message });
    }
    rerender();
  }

  function renderPlaybook(main, packId) {
    const packs = S.packs();
    if (!packs.length) {
      main.replaceChildren(el("div", { class: "page" }, header("Playbook", "Playbook", "No business packs found. Add one under packs/ (docs/business-pack.md).")));
      return;
    }
    const id = packId && packs.some((p) => p.id === packId) ? packId : packs[0].id;
    const detail = page.packs.get(id);
    const tabs = el(
      "nav",
      { class: "segmented", "aria-label": "Business packs" },
      ...packs.map((p) => el("a", { href: `#playbook/${enc(p.id)}`, class: p.id === id ? "is-on" : "", "aria-current": p.id === id ? "page" : null, text: p.name }))
    );
    if (!detail) {
      main.replaceChildren(el("div", { class: "page" }, header("Playbook", packs.find((p) => p.id === id).name, null), packs.length > 1 ? tabs : null, S.skeleton()));
      loadPack(id);
      return;
    }
    if (detail.error) {
      main.replaceChildren(el("div", { class: "page" }, header("Playbook", id, null), el("p", { class: "form-error", text: detail.error })));
      return;
    }
    const skillsList = el(
      "ul",
      { class: "skills" },
      ...detail.scorecard.map((c) =>
        el(
          "li",
          { class: "skill" },
          el("div", { class: "skill-head" }, el("span", { class: "strong", text: c.title }), pill(c.signal ? "info" : "neutral", c.signal ? "Scored by Sel" : "Coaching review"), el("span", { class: "muted small", text: `Weight ${c.weight}` })),
          el("p", { class: "small", text: c.description })
        )
      )
    );
    const facts = el(
      "ul",
      { class: "page-rows" },
      ...detail.facts.map((f) => el("li", { class: "page-row fact-row" }, icon("check", 16), el("div", { class: "page-row-main" }, el("span", { text: f.statement }), f.source_doc ? el("span", { class: "muted small", text: f.source_doc }) : null)))
    );
    const docs = el("ul", { class: "page-rows" }, ...detail.docs.map((ref) => docRow(id, ref)));
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header("Playbook", detail.name, detail.description, el("a", { class: "button", href: "#sim", text: "Practise a call" })),
        packs.length > 1 ? tabs : null,
        section("Skills", "The scorecard every call is graded on. Reps see these words when they fall short.", "target", "violet", skillsList),
        section("Approved facts", "Claims on calls are checked against these and the documents below.", "check", "ok", detail.facts.length ? facts : muted("No facts in this pack.")),
        section("Documents", null, "doc", "accent", detail.docs.length ? docs : muted("No documents in this pack.")),
        el("p", { class: "muted small", text: `Languages: ${detail.languages.join(", ") || "not set"} · week starts on ${detail.week_starts_on}` })
      )
    );
  }

  function docRow(packId, ref) {
    const key = `${packId}|${ref}`;
    const open = page.openDocs.has(key);
    const doc = page.docs.get(key);
    const toggle = el("button", { type: "button", class: "small", "aria-expanded": String(open), text: open ? "Hide" : "Read" });
    toggle.addEventListener("click", async () => {
      if (page.openDocs.has(key)) page.openDocs.delete(key);
      else {
        page.openDocs.add(key);
        if (!page.docs.has(key)) {
          try {
            page.docs.set(key, (await api(`/api/packs/${enc(packId)}/source?ref=${enc(ref)}`)).text);
          } catch (e) {
            page.docs.set(key, { error: e.message });
          }
        }
      }
      rerender();
    });
    return el(
      "li",
      { class: "page-row doc-row" },
      icon("doc", 18),
      el("span", { class: "page-row-main mono small", text: ref }),
      toggle,
      open ? (doc === undefined ? muted("Loading…") : doc && doc.error ? el("p", { class: "form-error", text: doc.error }) : el("pre", { class: "doc-text", text: doc })) : null
    );
  }

  S.coach = { render };
})();
