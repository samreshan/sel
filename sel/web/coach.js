/* Sel: Today, Playbook and People. Loaded by app.js the first time one of them is opened.
 *
 * Today answers "what needs my attention?" for whoever this browser is
 * viewing as: their follow-ups, the scorecard skills they fell short on
 * across recent calls, and calls still being analysed. A manager sees the
 * team instead. Everything comes from GET /api/today, which reads the calls'
 * own files; nothing here is guessed.
 *
 * Playbook shows a business pack as reps use it: the skills the scorecard
 * checks, the approved facts and the documents claims are checked against,
 * and the answers the team approved (the playbook overlay, GET /api/playbook/<pack>);
 * a manager also sees the answers the Librarian proposed, to approve or reject.
 * People is the team list behind "Viewing as" (no login; see docs/decisions.md).
 */
(function () {
  "use strict";

  const S = window.Sel;
  const { el, icon, iconTile, pill, muted, compact, plural, api, navigate, formatStarted, stageText, DRAFT_STATUS, SCORE_STATUS } = S;
  const M = S.motion;
  const enc = encodeURIComponent;
  const ROLE = { rep: "Rep", manager: "Manager" };

  const page = {
    today: null, // last /api/today response
    todayFor: undefined, // viewer id it was loaded for
    todayErr: null,
    loading: false,
    refetch: 0, // debounce timer for call events
    packs: new Map(), // pack id -> detail
    answers: new Map(), // pack id -> GET /api/playbook/<pack> (team answers) or {error}
    librarian: null, // status line after "Ask the Librarian"
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
    if (S.view() === "coach" && page.main && ["today", "playbook", "people"].includes(r.page)) render(page.main, r.page, r.arg);
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
  // The Librarian proposed answers, or a manager approved or rejected one (here or in another tab).
  S.on("playbook", (d) => {
    if (d && d.pack_id) page.answers.delete(d.pack_id);
    if (d && d.proposed !== undefined && page.librarian) page.librarian = `The Librarian proposed ${plural(d.proposed, "answer", "answers")}${d.refused ? `; Sel refused ${d.refused}` : ""}.`;
    if (S.view() === "coach" && S.coachRoute().page === "playbook") rerender();
  });
  S.on("viewer", () => {
    if (S.view() === "coach" && S.coachRoute().page === "playbook") rerender();
  });
  // Leaving a page means the next visit counts as arriving (see render).
  window.addEventListener("hashchange", () => {
    const h = location.hash.replace(/^#/, "") || "today";
    if (h.split("/")[0] !== page.shown) page.shown = null;
  });
  S.on("viewer", () => {
    page.todayFor = undefined;
  });

  // `eyebrow` is a line of text, or a breadcrumb trail ({href, text} items) for a page opened from another.
  function header(eyebrow, title, sub, ...extra) {
    const above = Array.isArray(eyebrow) ? S.crumbs([...eyebrow, { text: title }]) : eyebrow ? el("p", { class: "eyebrow", text: eyebrow }) : null;
    return el(
      "header",
      { class: "page-head" },
      el("div", {}, above, el("h1", { text: title }), sub ? el("p", { class: "lede", text: sub }) : null),
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
      el("div", { class: "today-toolbar" },
        el("span", { class: "today-location" }, icon("calendar", 16), "Workspace", el("span", { "aria-hidden": "true", text: "/" }), el("strong", { text: "Today" })),
        el("span", { class: "muted small", text: todayDate() })),
      todayHero(me, manager),
      page.todayErr ? el("p", { class: "form-error", role: "alert", text: page.todayErr }) : null,
      counts(t),
      el("div", { class: "today-work" },
        el("div", { class: "today-primary" },
          manager ? escalations(t) : null,
          t.focus ? focusCard(t.focus) : skills(t, manager),
          t.focus && t.skills.length ? skills(t, manager, false) : null,
          recent(t)),
        el("div", { class: "today-side" },
          peopleCard(me),
          followUps(t, manager),
          manager && t.unassigned.length ? unassigned(t) : null)),
      working(t),
    ];
    main.replaceChildren(el("div", { class: "page today-page" }, ...compact(nodes)));
  }

  function heroPeriod() {
    const hour = new Date().getHours();
    return hour < 6 || hour >= 18 ? "night" : "day";
  }

  // Update just the decorative scene; keep focus and live dashboard data intact.
  function syncHeroPeriod() {
    const hero = document.querySelector(".today-page .today-hero");
    if (hero) hero.dataset.period = heroPeriod();
  }
  setInterval(syncHeroPeriod, 60000);
  document.addEventListener("visibilitychange", syncHeroPeriod);

  function todayHero(me, manager) {
    return el("header", { class: "today-hero today-illustrated", "data-period": heroPeriod() },
      el("div", { class: `today-scene portrait-${S.portraitFor(me)}`, "aria-hidden": "true" },
        el("span", { class: "scene-art scene-day" }),
        el("span", { class: "scene-art scene-night" })),
      el("div", { class: "today-hero-copy" },
        el("p", { class: "eyebrow", text: `A little better, every call · ${me.name.split(" ")[0]}` }),
        el("h1", { text: manager ? "Your team today" : `Today, ${me.name.split(" ")[0]}` }),
        el("p", { class: "lede", text: manager ? "Good conversations. Clear next steps. Make room for your team's next small win." : "A little practice. A clearer next step. Build on what your last conversation taught you." }),
        el("div", { class: "row today-hero-actions" },
          el("a", { class: "button primary", href: "#new" }, icon("phone", 16), "New call"),
          el("a", { class: "button", href: "#practice" }, icon("target", 16), "Start practising"))));
  }

  function peopleCard(me) {
    const people = S.people();
    const rows = people.map((p) => el("li", { class: "today-person" },
      S.personAvatar(p, "large"),
      el("div", { class: "today-person-copy" }, el("strong", { text: p.name }), el("span", { class: "muted small", text: ROLE[p.role] })),
      p.id === me.id ? pill("neutral", "You") : el("button", { type: "button", class: "small ghost", text: "View", "aria-label": `View as ${p.name}`, on: { click: () => S.setViewer(p.id) } })));
    return el("section", { class: "list-card today-people", "aria-labelledby": "today-people-title" },
      el("div", { class: "today-section-heading" }, el("h2", { id: "today-people-title", class: "section-title", text: "Your people" }), el("span", { class: "muted small", text: plural(people.length, "member", "members") })),
      el("ul", { class: "today-people-list" }, ...rows),
      el("a", { class: "today-people-manage", href: "#people" }, icon("user", 16), "Manage people"));
  }

  // A passive summary: numbers, not buttons.
  function counts(t) {
    // A count that changed since this person last saw Today rolls to its new value.
    const stat = (label, n, name, tone) => el("div", { class: `stat${tone === "warn" && n > 0 ? " is-warn" : ""}` }, el("dt", {}, iconTile(tone, name), el("span", { text: label })), el("dd", {}, M.number(String(n), `today:${page.todayFor || ""}:${name}`)));
    return el(
      "dl",
      { class: "stat-strip", "aria-label": "Summary" },
      stat("Follow-ups needing input", t.counts.needs_input, "calendar", "warn"),
      stat("Ready for approval", t.counts.ready_for_approval, "mail", "info"),
      stat("Being analysed", t.in_progress.length, "bolt", "accent"),
      stat(t.scope === "team" ? "Team calls" : "Your calls", t.counts.calls, "phone", "ok")
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
          el("div", { class: "row" }, practiseButton(f), el("a", { class: "button small", href: `#call/${enc(m.call_id)}`, text: "Open the call" }))
        )
      );
    } else body.push(muted("The moments behind this focus will show here after your next reviewed call."));
    if (page.today && page.today.transfer && page.today.transfer.length) body.push(lastTransfer(page.today));
    return sectionOf("list-card page-section bento-anchor focus-card", "This week's focus", sub, "target", "violet", ...body);
  }

  // The focus's drill if the Coach made one, else a new one from its latest moment.
  function practiseButton(f) {
    const drills = (page.today && page.today.drills) || [];
    const d = drills.find((x) => x.pack_id === f.pack_id && x.criterion === f.criterion);
    if (d) return el("a", { class: "button primary small", href: `#practice/${enc(d.id)}`, text: d.attempts ? "Practise again" : "Practise this (90 s)" });
    const btn = el("button", { type: "button", class: "primary small", text: "Practise this (90 s)" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const nd = await api(`/api/calls/${enc(f.latest.call_id)}/moments/${enc(f.latest.moment_id)}/drill`, { method: "POST", json: { by: S.viewer() ? S.viewer().id : null } });
        navigate(`#practice/${enc(nd.id)}`);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = e.message;
      }
    });
    return f.latest ? btn : null;
  }

  function lastTransfer(t) {
    const x = (t.transfer || [])[t.transfer.length - 1];
    if (!x) return null;
    const label = { pass: ["ok", "Showed up on a real call"], fail: ["bad", "Not yet on a real call"], not_applicable: ["neutral", "Did not come up on your last call"], unsure: ["warn", "Unclear on your last call"] }[x.verdict] || ["neutral", x.verdict];
    return el("div", { class: "focus-moment" }, el("div", { class: "better-line-head" }, el("span", { class: "label", text: "On your real calls" }), pill(label[0], label[1])), x.quote ? el("p", { class: "said-line role-rep" }, el("span", { class: "said-who", text: `You, on the call with ${x.customer_name || "a customer"}` }), S.quoted(x.quote)) : null, x.why ? el("p", { class: "muted small", text: x.why }) : null);
  }

  function escalations(t) {
    if (!t.escalations || !t.escalations.length) return null;
    const why = { contradicted_claim: "A contradicted claim reached a customer", failed_drill: "Failed the same drill three times", change_manager_focus: "Wants to move on from the focus you set" };
    const rows = t.escalations.map((e) =>
      el(
        "li",
        { class: "page-row" },
        icon("alert", 18),
        el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `${e.person_name}: ${why[e.reason] || e.reason}` }), el("span", { class: "muted small", text: e.note || "" })),
        e.call_id ? el("a", { class: "button small", href: `#call/${enc(e.call_id)}`, text: "Open the call" }) : e.drill_id ? el("a", { class: "button small", href: `#practice/${enc(e.drill_id)}`, text: "Open the drill" }) : null
      )
    );
    return section("Needs you", "Sel's coach escalates only these three things; everything else it handles itself.", "alert", "warn", el("ul", { class: "page-rows" }, ...rows));
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
        `list-card page-section today-skills${anchor ? " bento-anchor" : ""} is-empty`,
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
          el("button", { type: "button", class: "small ghost", text: "Practise this", on: { click: () => navigate("#practice") } }),
          !manager && !(t.focus && t.focus.pack_id === s.pack_id && t.focus.criterion === s.criterion_id)
            ? el("button", { type: "button", class: "small ghost", text: t.focus ? "Make this my focus" : "Focus on this", on: { click: (e) => setFocus(s, e.currentTarget) } })
            : null
        )
      );
    });
    return sectionOf(
      `list-card page-section today-skills${anchor ? " bento-anchor" : ""}`,
      "Skills to work on",
      manager ? "Scorecard skills the team fell short on in recent calls, from the pack's scorecard." : "Scorecard skills your recent calls fell short on. Practise one, then watch it on your next calls.",
      "target",
      "violet",
      el("ul", { class: "skills" }, ...rows)
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
    const name = el("input", { type: "text", id: "p-name", autocomplete: "name", required: true, maxlength: "80", placeholder: "Full name" });
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
      { class: "welcome welcome-setup" },
      el("div", { class: "welcome-copy" },
        el("p", { class: "eyebrow", text: "Set up" }),
        el("h1", { text: "Who uses Sel here?" }),
        el("p", { class: "lede", text: "Add yourself first. Sel keeps each rep's calls, follow-ups and coaching together. There is no login: each browser chooses who it is viewing as." })),
      personForm((p) => S.setViewer(p.id), "Add and continue"),
      S.lineArt("welcome")
    );
  }

  function chooseViewer(people) {
    const btn = (p) => el("button", { type: "button", class: "person-pick", on: { click: () => S.setViewer(p.id) } }, S.personAvatar(p, "large"), el("span", { class: "strong", text: p.name }), el("span", { class: "muted small", text: ROLE[p.role] }));
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
      const name = el("input", { type: "text", value: p.name, "aria-label": `Name of ${p.name}`, maxlength: "80", placeholder: "Full name" });
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
      const face = el(
        "button",
        { type: "button", class: "portrait-btn", id: `portrait-${p.id}`, "aria-label": `Choose a portrait for ${p.name}`, title: "Choose a portrait", on: { click: () => portraitDialog(p) } },
        S.personAvatar(p)
      );
      return el(
        "li",
        { class: "page-row person-row" },
        face,
        name,
        role,
        el("span", { class: "person-row-action" }, me && me.id === p.id ? pill("neutral", "You") : el("button", { type: "button", class: "small", text: "View as", on: { click: () => S.setViewer(p.id) } })),
        msg
      );
    });
    main.replaceChildren(
      el(
        "div",
        { class: "page" },
        header([me && me.role === "manager" ? { href: "#team", text: "Team" } : { href: "#today", text: "Today" }], "People", "Reps and managers. Calls belong to a rep; managers see the whole team on Today. There is no login, so anyone on this computer can switch."),
        el(
          "div",
          { class: "people-grid" },
          people.length ? section("The team", plural(people.length, "person", "people"), "users", "accent", el("ul", { class: "page-rows" }, ...rows)) : null,
          section("Add a person", "They can choose themselves under Viewing as.", "user", "accent", personForm((p) => (!S.viewer() ? S.setViewer(p.id) : null), "Add"))
        )
      )
    );
  }

  // Someone's portrait is their choice, never guessed from a name. The dialog lives on the
  // body, so the page can redraw beneath it; focus goes back to the portrait it came from.
  function portraitDialog(p) {
    const err = el("p", { class: "form-error", role: "alert", hidden: true });
    const submit = el("button", { type: "submit", class: "primary", text: "Save portrait" });
    const form = el(
      "form",
      { class: "dialog-form", novalidate: true },
      el("p", { class: "muted", id: "portrait-desc", text: "The drawing beside their name on Today, Team and their calls. Automatic picks one for them." }),
      S.portraitPicker("person-portrait", p.portrait, S.portraitOf(p.id)),
      err,
      el("div", { class: "row dialog-actions" }, el("button", { type: "button", text: "Cancel", on: { click: () => dialog.close() } }), submit)
    );
    const dialog = el(
      "dialog",
      { class: "dialog", "aria-labelledby": "portrait-title", "aria-describedby": "portrait-desc" },
      el("div", { class: "dialog-head" }, el("h2", { id: "portrait-title", text: `Portrait for ${p.name}` }), el("button", { type: "button", class: "ghost small", "aria-label": "Close", on: { click: () => dialog.close() } }, icon("x", 18))),
      form
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      const back = document.getElementById(`portrait-${p.id}`);
      if (back) back.focus();
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      submit.disabled = true;
      try {
        await api(`/api/people/${enc(p.id)}`, { method: "PUT", json: { portrait: S.pickedPortrait(form, "person-portrait") } });
        await S.refreshPeople();
        page.todayFor = undefined;
        dialog.close();
      } catch (ex) {
        submit.disabled = false;
        err.textContent = ex.message;
        err.hidden = false;
      }
    });
    document.body.append(dialog);
    dialog.showModal();
    const checked = form.querySelector("input:checked");
    if (checked) checked.focus();
  }

  const playground = { tab: "practise", data: new Map(), pending: new Set(), objections: new Map(), exercise: null, busy: false, starting: null, error: null, result: null };
  const playgroundKey = (id) => `${S.viewer()?.id || "none"}|${id}`;
  const draftKey = (id) => `sel.playground.${playgroundKey(id)}`;
  function saveExercise(id) {
    try { localStorage.setItem(draftKey(id), JSON.stringify(playground.exercise)); }
    catch (_) { playground.error = "This browser could not save your unfinished response. Keep this page open until you submit."; }
  }
  function openExercise(id, mode, ref = "", topic = "pricing") {
    playground.exercise = { key: playgroundKey(id), mode, ref, topic, response: "" };
    playground.result = null;
    playground.error = null;
    playground.tab = "practise";
    saveExercise(id);
    rerender();
    page.main.querySelector("textarea")?.focus();
  }
  async function loadPlayground(id) {
    const me = S.viewer();
    if (!me || me.role !== "rep") return;
    const key = playgroundKey(id);
    if (playground.pending.has(key)) return;
    playground.pending.add(key);
    try {
      const [attempts, drills] = await Promise.all([api(`/api/playground?person=${enc(me.id)}&pack=${enc(id)}`, { fresh: true }), api(`/api/drills?person=${enc(me.id)}`, { fresh: true })]);
      playground.data.set(key, { attempts, drills: drills.filter((d) => d.pack_id === id) });
    } catch (e) { playground.data.set(key, { error: e.message }); }
    finally { playground.pending.delete(key); rerender(); }
  }
  S.on("drill", () => { playground.data.clear(); playground.objections.clear(); if (S.coachRoute().page === "playbook") rerender(); });

  // Objections to practise with the AI customer: the pack's own and those heard on the team's calls.
  async function loadObjections(id) {
    const me = S.viewer();
    const key = `objections|${playgroundKey(id)}`;
    if (playground.pending.has(key)) return;
    playground.pending.add(key);
    try {
      playground.objections.set(playgroundKey(id), await api(`/api/playground/objections?pack=${enc(id)}${me ? `&person=${enc(me.id)}` : ""}`, { fresh: true }));
    } catch (e) { playground.objections.set(playgroundKey(id), { error: e.message }); }
    finally { playground.pending.delete(key); rerender(); }
  }
  async function practiseObjection(id, item, heard) {
    const me = S.viewer();
    if (!me || me.role !== "rep" || playground.starting) return;
    playground.starting = item.id; playground.error = null; rerender();
    try {
      const d = await api("/api/playground/objections", { method: "POST", json: { person_id: me.id, pack_id: id, [heard ? "heard_id" : "objection_id"]: item.id } });
      playground.objections.delete(playgroundKey(id));
      navigate(`#practice/${enc(d.id)}`);
    } catch (e) { playground.error = e.message; }
    finally { playground.starting = null; if (S.coachRoute().page === "playbook") rerender(); }
  }
  function objectionRow(id, item, heard, canPractise) {
    const d = item.drill;
    const tried = d && d.attempts ? `${plural(d.attempts, "attempt", "attempts")}, ${d.passes} passed` : null;
    return el("li", { class: "page-row objection-row" },
      el("div", { class: "page-row-main" },
        el("span", { class: "muted small", text: compact([item.topic, heard ? "Heard on a call · anonymised" : null]).join(" · ") }),
        el("span", { class: "objection-says", text: S.quoted(item.says) }),
        el("span", { class: "muted small", text: compact([`Judged on: ${item.criterion_title}`, tried]).join(" · ") })),
      canPractise ? el("button", { type: "button", class: "small", text: playground.starting === item.id ? "Opening…" : d ? "Practise again" : "Practise",
        disabled: !!playground.starting, "aria-label": `${d ? "Practise again" : "Practise"} with the AI customer: ${item.says}`,
        on: { click: () => practiseObjection(id, item, heard) } }) : null);
  }
  function objectionSection(id) {
    const me = S.viewer();
    const canPractise = !!me && me.role === "rep";
    const data = playground.objections.get(playgroundKey(id));
    if (!data) loadObjections(id);
    const body = !data ? [el("p", { class: "muted", "aria-busy": "true", text: "Loading objections…" })]
      : data.error ? [el("div", { class: "card is-error", role: "alert" }, muted(data.error), el("button", { type: "button", text: "Try again", on: { click: () => loadObjections(id) } }))]
      : [
          data.pack.length ? el("ul", { class: "page-rows" }, ...data.pack.map((o) => objectionRow(id, o, false, canPractise)))
            : muted("This pack lists no objections yet. Add [[objections]] to its pack.toml (docs/business-pack.md)."),
          data.heard.length ? el("h3", { class: "group-title", text: "Heard on your team's calls" }) : null,
          data.heard.length ? el("ul", { class: "page-rows" }, ...data.heard.map((o) => objectionRow(id, o, true, canPractise))) : null,
          canPractise ? null : muted("Choose a rep using Viewing as to practise."),
        ];
    return sectionOf("list-card page-section playground-objections", "Practise an objection",
      "A short typed call with the AI customer, then a debrief. Practice only: it never changes a focus or a score.",
      "lines", "accent", ...compact(body));
  }
  function playgroundTabs() {
    const rail = el("div", { class: "scope-toggle", "aria-label": "Playground sections" }, ...[["practise", "Practise"], ["library", "Library"], ["progress", "My progress"]].map(([id, label]) =>
      el("button", { type: "button", class: `scope-btn${playground.tab === id ? " is-on" : ""}`, text: label, "aria-pressed": String(playground.tab === id), on: { click: () => { playground.tab = id; rerender(); page.main.querySelector(`[aria-label="Playground sections"] button[aria-pressed="true"]`)?.focus(); } } })));
    M.glide(rail);
    return rail;
  }
  function exerciseForm(id, detail) {
    const ex = playground.exercise;
    const me = S.viewer();
    const input = el("textarea", { id: "playground-response", rows: "6", maxlength: "4000", placeholder: "Write what you would say to the buyer…", disabled: playground.busy });
    input.value = ex.response;
    input.addEventListener("input", () => { ex.response = input.value; saveExercise(id); });
    const feedback = playground.result;
    const status = el("p", { class: "muted small", role: "status", text: playground.busy ? "Saving your response and preparing feedback…" : "Your draft is saved in this browser. Submitted attempts are saved for this rep." });
    const submit = el("button", { type: "submit", class: "primary", text: feedback ? "Submit another attempt" : "Get feedback", disabled: playground.busy || !me || me.role !== "rep" });
    const form = el("form", { class: "playground-form" },
      el("label", { for: "playground-response", text: "Your response (English)" }), input,
      el("div", { class: "row" }, submit, el("button", { type: "button", text: "Back to exercises", disabled: playground.busy, on: { click: () => { playground.exercise = null; playground.result = null; try { localStorage.removeItem(draftKey(id)); } catch (_) {} rerender(); } } })), status);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (playground.busy) return;
      if (!ex.response.trim()) { input.setCustomValidity("Write your response first."); input.reportValidity(); input.setCustomValidity(""); return; }
      const key = playgroundKey(id);
      playground.busy = true; playground.error = null; rerender();
      try {
        const result = await api("/api/playground", { method: "POST", json: { person_id: me.id, pack_id: id, mode: ex.mode, topic: ex.topic, ref: ex.ref, response: ex.response } });
        playground.data.delete(key);
        if (playgroundKey(id) === key && playground.exercise === ex) playground.result = result;
      } catch (e) { playground.error = e.message; }
      finally { playground.busy = false; rerender(); }
    });
    const source = ex.ref ? el("ul", { class: "page-rows" }, docRow(id, ex.ref, false)) : muted("This is a generic buyer scenario. Do not invent product details.");
    return section("Practice response", "A short exercise. This does not change your real-call focus.", "target", "violet",
      pill("neutral", "Document practice"),
      el("p", { class: "focus-title", text: "Explain the main point to a buyer, including relevant conditions." }), source, form,
      playground.error ? el("p", { class: "form-error", role: "alert", text: playground.error }) : null,
      feedback ? el("section", { class: "card debrief", "aria-label": "Exercise feedback", role: "status" },
        pill("info", feedback.feedback.by), el("h3", { text: "One thing to work on" }), el("p", { text: feedback.feedback.tip }),
        el("blockquote", { text: feedback.feedback.response_quote }), feedback.feedback.source_quote ? el("blockquote", { class: "source", text: feedback.feedback.source_quote }) : null, feedback.feedback.model_status ? muted(feedback.feedback.model_status) : null, muted(feedback.feedback.observation), muted(feedback.feedback.limitation),
        el("button", { type: "button", text: "Try again", on: { click: () => { playground.result = null; rerender(); page.main.querySelector("textarea")?.focus(); } } })) : null);
  }
  function playgroundBody(id, detail) {
    const me = S.viewer();
    const key = playgroundKey(id);
    if (playground.exercise?.key !== key) {
      playground.exercise = null; playground.result = null;
      try { const saved = JSON.parse(localStorage.getItem(draftKey(id)) || "null"); if (saved?.key === key && saved.mode === "document" && typeof saved.response === "string") playground.exercise = saved; } catch (_) {}
    }
    const data = playground.data.get(key);
    if (me?.role === "rep" && !data) loadPlayground(id);
    const retry = data?.error ? el("div", { class: "card is-error", role: "alert" }, muted(data.error), el("button", { type: "button", text: "Retry loading practice", on: { click: () => loadPlayground(id) } })) : null;
    if (playground.tab === "progress") {
      if (!me || me.role !== "rep") return section("My progress", "Choose a rep using Viewing as to see their attempts.", "target", "violet");
      if (!data) return section("My progress", "Loading your attempts…", "target", "violet");
      if (retry) return retry;
      return el("div", { class: "playbook-side" },
        section("Short exercises", "Practice history only. These attempts do not verify improvement on real calls.", "target", "violet",
          data.attempts.length ? el("ul", { class: "skills" }, ...data.attempts.map((a) => el("li", { class: "skill" },
            el("div", { class: "skill-head" }, el("span", { class: "strong", text: a.mode === "document" ? `Explain: ${a.ref}` : `Objection: ${a.topic}` }), pill("neutral", "Attempt saved")),
            el("p", { class: "muted small", text: formatStarted(a.created_at) }),
            el("details", { class: "disclosure" }, el("summary", { text: "Review response and feedback" }), el("p", { text: a.prompt }), el("blockquote", { text: a.response }), pill("info", a.feedback.by), el("p", { text: a.feedback.tip }), muted(a.feedback.limitation), a.source_text ? el("pre", { class: "doc-text", text: a.source_text }) : null),
            el("button", { type: "button", class: "small", text: "Practise again", on: { click: () => openExercise(id, a.mode, a.ref, a.topic) } })))) : muted("No submitted exercises yet. Start in Practise.")),
        section("Objection drills", "Practice with the AI customer on your pack's objections. Separate from real-call transfer checks.", "lines", "accent", drillLinks(data.drills.filter((d) => d.origin === "playground"), "No objection drills yet. Start one in Practise.")),
        section("Call-moment drills", "Results from the existing practice room, separate from real-call transfer checks.", "lines", "accent", drillLinks(data.drills.filter((d) => d.origin !== "playground"))),
        el("a", { class: "button", href: "#today", text: "Review real-call coaching" }));
    }
    if (playground.exercise) return exerciseForm(id, detail);
    const drills = (data?.drills || []).filter((d) => d.origin !== "playground");
    return el("div", { class: "playground-home" }, retry, playground.error ? el("p", { class: "form-error", role: "alert", text: playground.error }) : null,
      !me || me.role !== "rep" ? el("p", { class: "muted small playground-notice", text: "Choose a rep using Viewing as to save exercises and see personal progress." }) : !data ? el("p", { class: "muted small playground-notice", text: "Loading your call drills…" }) : null,
      el("div", { class: "playground-grid" },
        el("section", { class: "card playground-conversation" },
          el("div", { class: "conversation-intro" },
            el("div", {}, el("h2", { text: "Practise a conversation" }), el("p", { class: "muted", text: "Turn what you know into a better conversation. Start a call, invite a partner, and get feedback as you practise." })),
            S.lineArt("conversation", me)),
          el("div", { class: "row" }, el("a", { class: "button primary", href: "#sim", text: "Start practice" }), el("span", { class: "muted small", text: "Talk or type · Share a call code" }))),
        sectionOf("list-card page-section playground-reading", "Read & practise", "Build confidence in your product knowledge.", "doc", "accent",
          el("div", { class: "conversation-intro reading-intro" },
            el("p", { class: "muted", text: "Explore your business documents, then practise explaining them to a buyer." }), S.lineArt("reading", me)),
          el("button", { type: "button", text: "Open Library", on: { click: () => { playground.tab = "library"; rerender(); } } })),
        objectionSection(id),
        sectionOf("list-card page-section playground-moments", "Retry a call moment", "Build on a conversation you have already had.", "target", "violet",
          drillLinks(drills), el("a", { class: "button", href: "#calls", text: "Find a moment in Calls" }))));
  }

  function drillLinks(drills, empty) {
    return drills.length ? el("ul", { class: "page-rows" }, ...drills.map((d) => el("li", { class: "page-row" },
      el("a", { class: "page-row-link", href: `#practice/${enc(d.id)}`, text: d.title }),
      el("span", { class: "muted small", text: `${d.attempts || 0} attempts · ${d.passes || 0} passed${d.status === "preparing" ? " · Preparing" : ""}` })))) : muted(empty || "No call-moment drills in this pack yet. Open a call's Coaching tab and choose Practise this.");
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

  async function loadAnswers(id) {
    try {
      page.answers.set(id, await api(`/api/playbook/${enc(id)}`, { fresh: true }));
    } catch (e) {
      page.answers.set(id, { error: e.message });
    }
    rerender();
  }

  // Keyed for motion: an answer a manager approves moves from the review queue into
  // Team answers, and its check draws once.
  function answerItem(a, actions) {
    const approved = a.status === "approved";
    const justApproved = M.changed(`answer:${a.id}`, a.status) && approved;
    return el(
      "li",
      { class: "skill", dataset: { flip: a.id } },
      el("div", { class: "skill-head" }, el("span", { class: "strong", text: a.objection }), approved ? el("span", { class: "pill pill-info pill-check" }, M.check(justApproved, 12), "Team answer") : pill("warn", "Proposed")),
      el("p", { class: "better-line-text", text: S.quoted(a.answer) }),
      el("p", { class: "muted small", text: `${a.answer_from === "rep" ? "Said by a rep on a real call" : "A better line a rep endorsed"} · ${a.check && a.check.check === "ok" ? "Checked against your pack" : "Refused"}` }),
      actions || null
    );
  }

  function teamAnswers(id) {
    const data = page.answers.get(id);
    if (!data) {
      loadAnswers(id);
      return section("Team answers", null, "check", "info", muted("Loading…"));
    }
    if (data.error) return section("Team answers", null, "check", "info", el("p", { class: "form-error", text: data.error }));
    return section(
      "Team answers",
      "Answers a manager approved from the team's own calls. They become the better line, Prep's answer and the rehearsal's model answer.",
      "check",
      "info",
      data.approved.length ? el("ul", { class: "skills" }, ...data.approved.map((a) => answerItem(a))) : muted("None approved yet. A manager approves what the Librarian proposes.")
    );
  }

  function reviewQueue(id) {
    const me = S.viewer();
    const data = page.answers.get(id);
    if (!me || me.role !== "manager" || !data || data.error) return null;
    const msg = el("p", { class: "form-status", role: "status", text: page.librarian || "" });
    const run = el("button", { type: "button", class: "small", text: "Ask the Librarian" });
    run.addEventListener("click", async () => {
      run.disabled = true;
      page.librarian = "The Librarian is reading the team's strong moments and endorsed lines…";
      msg.textContent = page.librarian;
      try {
        await api(`/api/playbook/${enc(id)}/answers`, { method: "POST", json: { action: "propose", by: me.id } });
      } catch (e) {
        page.librarian = e.message;
        msg.textContent = e.message;
        run.disabled = false;
      }
    });
    const decide = (a, action) => async (ev) => {
      ev.currentTarget.disabled = true;
      const moves = M.flip(page.main);
      try {
        await api(`/api/playbook/${enc(id)}/answers`, { method: "POST", json: { action, id: a.id, by: me.id } });
        page.librarian = action === "approve" ? "Approved for the team." : "Rejected.";
        await loadAnswers(id); // this tab's own change is not echoed back
        moves.play();
      } catch (e) {
        page.librarian = e.message;
        rerender();
      }
    };
    const rows = data.proposed.map((a) =>
      answerItem(
        a,
        el(
          "div",
          { class: "row" },
          el("button", { type: "button", class: "small primary", text: "Approve for the team", on: { click: decide(a, "approve") } }),
          el("button", { type: "button", class: "small ghost", text: "Reject", on: { click: decide(a, "reject") } }),
          a.source && a.source.call_id ? el("a", { class: "button small", href: `#call/${enc(a.source.call_id)}`, text: "Open the call" }) : null
        )
      )
    );
    const refused = data.refused.length
      ? el(
          "details",
          { class: "disclosure" },
          el("summary", { text: `Sel refused ${plural(data.refused.length, "answer", "answers")}` }),
          el("ul", { class: "refused-lines" }, ...data.refused.map((a) => el("li", {}, el("p", { text: S.quoted(a.answer) }), muted((a.check.reasons || []).join("; ")))))
        )
      : null;
    return section("Review queue", "For managers: answers the Librarian found in the team's real calls, checked against the pack.", "lines", "accent", el("div", { class: "row" }, run), msg, rows.length ? el("ul", { class: "skills" }, ...rows) : muted("Nothing waiting."), refused);
  }

  function renderPlaybook(main, packId) {
    const packs = S.packs();
    if (!packs.length) {
      main.replaceChildren(el("div", { class: "page playground-page" }, header("Playground", "Playground", "No business packs found. Add one under packs/ (docs/business-pack.md).")));
      return;
    }
    const id = packId && packs.some((p) => p.id === packId) ? packId : packs[0].id;
    const detail = page.packs.get(id);
    if (!detail) {
      main.replaceChildren(el("div", { class: "page playground-page" }, playbookHead(packs, id, null), S.skeleton()));
      loadPack(id);
      return;
    }
    if (detail.error) {
      main.replaceChildren(el("div", { class: "page playground-page" }, playbookHead(packs, id, null), el("p", { class: "form-error", text: detail.error })));
      return;
    }
    if (playground.tab !== "library") {
      main.replaceChildren(el("div", { class: "page playground-page" }, playbookHead(packs, id, "Read, practise, and build on your conversations."), playgroundTabs(), playgroundBody(id, detail)));
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
    const skillsCard = sectionOf("list-card page-section bento-anchor library-skills", "Skills", "The scorecard every call is graded on. Reps see these words when they fall short.", "target", "violet", skillsList);
    skillsCard.querySelector(".section-head").append(S.lineArt("reading", S.viewer()));
    const facts = el(
      "ul",
      { class: "page-rows" },
      ...detail.facts.map((f) => el("li", { class: "page-row fact-row" }, icon("check", 16), el("div", { class: "page-row-main" }, el("span", { text: f.statement }), f.source_doc ? el("span", { class: "muted small", text: f.source_doc }) : null)))
    );
    const docs = el("ul", { class: "page-rows" }, ...detail.docs.map((ref) => docRow(id, ref)));
    main.replaceChildren(
      el(
        "div",
        { class: "page playground-page" },
        playbookHead(packs, id, detail.description),
        playgroundTabs(),
        el(
          "div",
          { class: "bento" },
          skillsCard,
          el(
            "div",
            { class: "playbook-side" },
            teamAnswers(id),
            section("Approved facts", "Claims on calls are checked against these and the documents below.", "check", "ok", detail.facts.length ? facts : muted("No facts in this pack.")),
            section("Documents", null, "doc", "accent", detail.docs.length ? docs : muted("No documents in this pack."))
          )
        ),
        reviewQueue(id),
        el("p", { class: "muted small", text: `Languages: ${detail.languages.join(", ") || "not set"} · week starts on ${detail.week_starts_on}` })
      )
    );
  }

  function playbookHead(packs, id, description, ...extra) {
    const current = packs.find((p) => p.id === id) || packs[0];
    const select = el(
      "select",
      {
        id: "pack-switch",
        class: "playground-pack-select",
        "aria-label": "Business pack",
        on: { change: () => navigate(`#playbook/${enc(select.value)}`) },
      },
      ...packs.map((p) => el("option", { value: p.id, text: p.name }))
    );
    select.value = id;
    return el(
      "header",
      { class: "page-head playground-head" },
      el(
        "div",
        {},
        el("p", { class: "eyebrow", text: "Learn and rehearse" }),
        el("h1", { text: "Playground" }),
        description ? el("p", { class: "lede", text: description }) : null
      ),
      el("div", { class: "field playground-pack" }, el("label", { for: "pack-switch", text: "Business pack" }), packs.length > 1 ? select : el("p", { class: "muted", text: current.name })),
      ...compact(extra)
    );
  }

  function docRow(packId, ref, practise = true) {
    const key = `${packId}|${ref}`;
    const open = page.openDocs.has(key);
    const doc = page.docs.get(key);
    const toggle = el("button", { type: "button", class: "small", "aria-expanded": String(open), text: open ? "Hide" : "Read" });
    toggle.addEventListener("click", async () => {
      if (page.openDocs.has(key)) page.openDocs.delete(key);
      else {
        page.openDocs.add(key);
        rerender();
        if (!page.docs.has(key) || page.docs.get(key)?.error) {
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
      practise ? el("button", { type: "button", class: "small", text: "Practise this", on: { click: () => openExercise(packId, "document", ref) } }) : null,
      open ? (doc === undefined ? muted("Loading…") : doc && doc.error ? el("p", { class: "form-error", text: doc.error }) : el("pre", { class: "doc-text", text: doc })) : null
    );
  }

  S.coach = { render };
})();
