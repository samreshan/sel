/* Sel: Practice. Loaded by app.js the first time #practice is opened.
 *
 * #practice lists the rep's drills: short practice calls against an AI
 * customer built from a moment they fell short on in a real call. The Coach
 * creates them for the rep's focus; "Practise this" on a moment creates one too.
 *
 * #practice/<id> is one drill: the scenario (the real moment, the better line,
 * who the customer is), then a typed practice call in a simulator room whose
 * customer is the AI (sel/ai_customer.py), then the debrief: the verdict on the
 * drill's skill with the line it cites, and the real answer, the practice
 * answer and the better line side by side. Everything the AI says or writes is
 * labelled as such. Free practice with a person stays in the simulator (#sim).
 */
(function () {
  "use strict";

  const S = window.Sel;
  const { el, icon, iconTile, pill, muted, compact, plural, mmss, quoted, api, navigate, formatStarted } = S;
  const M = S.motion;
  const enc = encodeURIComponent;
  const KEY = "sel.drill";
  const VERDICT = {
    pass: ["ok", "Passed"],
    fail: ["bad", "Not yet"],
    not_applicable: ["neutral", "Did not come up"],
    unsure: ["warn", "Unclear"],
  };

  const page = {
    main: null,
    list: null, // GET /api/drills?person= for the viewer
    listFor: undefined,
    coach: null, // GET /api/coach/<viewer>
    drills: new Map(), // id -> full drill
    session: load(), // {code, client, drillId}
    room: null, // the drill room view
    lines: [],
    live: null,
    error: null,
    busy: false,
    refs: null,
  };

  function load() {
    try {
      return JSON.parse(sessionStorage.getItem(KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function save(s) {
    page.session = s;
    try {
      if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
      else sessionStorage.removeItem(KEY);
    } catch (_) {
      /* the drill still works until the tab reloads */
    }
    if (s) S.connectEvents({ room: s.code, client: s.client });
  }

  const route = () => S.coachRoute();
  const here = () => S.view() === "coach" && route().page === "practice";

  function rerender() {
    if (here() && page.main) render(page.main, route().arg);
  }

  // ---------- data ----------

  async function loadList(force) {
    const me = S.viewer();
    if (!me) return;
    if (!force && page.listFor === me.id && page.list) return;
    try {
      const [list, coach] = await Promise.all([api(`/api/drills?person=${enc(me.id)}`, { fresh: true }), api(`/api/coach/${enc(me.id)}`, { fresh: true })]);
      page.list = list;
      page.coach = coach;
      page.listFor = me.id;
      page.error = null;
    } catch (e) {
      page.error = e.message;
    }
    rerender();
  }

  async function loadDrill(id) {
    try {
      page.drills.set(id, await api(`/api/drills/${enc(id)}`, { fresh: true }));
      page.error = null;
    } catch (e) {
      page.drills.set(id, { error: e.message });
    }
    rerender();
  }

  S.on("drill", (d) => {
    page.listFor = undefined;
    if (page.drills.has(d.id) || (route().arg === d.id && here())) loadDrill(d.id);
    else if (here() && !route().arg) loadList(true);
  });
  S.on("coach", () => {
    page.listFor = undefined;
    if (here() && !route().arg) loadList(true);
  });
  S.on("viewer", () => {
    page.listFor = undefined;
    page.list = null;
    rerender();
  });
  S.on("people-changed", rerender); // the team arrives after the first render
  S.on("room", (v) => {
    if (!page.session || v.code !== page.session.code) return;
    const wasLive = page.room && page.room.state === "live";
    page.room = { ...page.room, ...v };
    if (v.live) page.live = v.live;
    paintRoom();
    // The AI customer can end the call; the rep's tab files it.
    if (wasLive && v.state === "ended") finish();
  });
  S.on("line", (line) => {
    if (!page.session || line.room !== page.session.code || page.lines.some((l) => l.id === line.id)) return;
    page.lines.push(line);
    page.lines.sort((a, b) => a.start - b.start);
    paintLines();
  });
  // The tab's event stream reconnects when the drill starts (to add the room) and after a network
  // blip; a line published meanwhile (often the AI customer's reply) would be lost. The first event
  // on every stream is a snapshot with the room and its lines: take the lines it has that we don't.
  S.on("hello", (data) => {
    const v = data && data.room;
    if (!page.session || !v || v.code !== page.session.code) return;
    const known = new Set(page.lines.map((l) => l.id));
    const missing = (v.lines || []).filter((l) => !known.has(l.id));
    page.room = { ...page.room, ...v };
    delete page.room.lines;
    if (v.live) page.live = v.live;
    if (missing.length) {
      page.lines = page.lines.concat(missing).sort((a, b) => a.start - b.start);
      paintLines();
    }
    paintRoom();
  });
  S.on("live", (snap) => {
    if (!page.session || !page.room) return;
    page.live = snap;
    paintLive();
  });

  // ---------- dispatch ----------

  function render(main, arg) {
    page.main = main;
    if (!S.peopleReady()) {
      main.replaceChildren(el("div", { class: "page", "aria-busy": "true" }, S.skeleton()));
      return;
    }
    const me = S.viewer();
    if (!me || me.role !== "rep") {
      if (location.hash !== "#sim") location.replace("#sim");
      return;
    }
    if (arg) return renderDrill(main, arg);
    renderList(main, me);
  }

  // `trail` is the breadcrumb back to Playground; the page's own title closes it.
  function header(trail, title, sub, ...extra) {
    return el(
      "header",
      { class: "page-head" },
      el("div", {}, S.crumbs([...trail, { text: title }]), el("h1", { text: title }), sub ? el("p", { class: "lede", text: sub }) : null),
      ...compact(extra)
    );
  }

  const PLAYGROUND = { href: "#playbook", text: "Playground" };
  const TO_PRACTICE = [PLAYGROUND, { href: "#practice", text: "Practice" }];
  const drillTrail = (d) => (d.origin === "playground" ? [{ href: `#playbook/${enc(d.pack_id)}`, text: "Playground" }] : TO_PRACTICE);

  function section(cls, title, sub, iconName, tone, ...body) {
    return el(
      "section",
      { class: `list-card page-section ${cls || ""}` },
      el("div", { class: "section-head" }, el("div", { class: "section-head-main" }, iconTile(tone, iconName), el("div", {}, el("h2", { class: "section-title", text: title }), sub ? el("p", { class: "card-sub", text: sub }) : null))),
      el("div", { class: "page-section-body" }, ...compact(body))
    );
  }

  function level(d) {
    return `Level ${d.difficulty || 1} of 3`;
  }

  // ---------- the list ----------

  function renderList(main, me) {
    if (page.listFor !== me.id || !page.list) {
      main.replaceChildren(el("div", { class: "page" }, header([PLAYGROUND], "Practice", null), el("section", { class: "card is-loading", "aria-busy": "true" }, muted("Finding your drills…"))));
      loadList();
      return;
    }
    const focus = page.coach && page.coach.focus;
    // Playground drills are practice only, so they never sit under the focus.
    const forFocus = focus ? page.list.filter((d) => d.origin !== "playground" && d.pack_id === focus.pack_id && d.criterion === focus.criterion) : [];
    const others = page.list.filter((d) => !forFocus.includes(d));
    const row = (d) => {
      const v = d.last_verdict ? VERDICT[d.last_verdict] : null;
      return el(
        "li",
        { class: "page-row" },
        icon("target", 18),
        el(
          "a",
          { class: "page-row-link", href: `#practice/${enc(d.id)}` },
          el("span", { class: "strong", text: d.title }),
          el("span", { class: "muted small", text: compact([d.origin === "playground" ? "Playground" : null, d.customer ? `AI customer: ${d.customer}` : null, level(d), d.attempts ? `${plural(d.attempts, "attempt", "attempts")}, ${d.passes} passed` : "Not tried yet"]).join(" · ") })
        ),
        d.status === "preparing" ? pill("info", "Preparing") : v ? pill(v[0], v[1]) : pill("neutral", "New")
      );
    };
    const nodes = [
      header([PLAYGROUND], "Practice", "Short practice calls with an AI customer built from your own calls. Each takes about 90 seconds.", el("a", { class: "button", href: "#sim", text: "Free practice with a person" })),
      page.error ? el("p", { class: "form-error", role: "alert", text: page.error }) : null,
      el(
        "div",
        { class: "bento" },
        section(
          "bento-anchor",
          "Your focus",
          focus ? `Set by ${focus.set_by_name || "Sel's coach"}` : null,
          "target",
          "violet",
          focus
            ? el("div", { class: "focus-body" }, el("p", { class: "focus-title", text: focus.title }), focus.reason ? muted(focus.reason) : null, forFocus.length ? el("ul", { class: "page-rows" }, ...forFocus.map(row)) : muted("Your coach is preparing a drill for this."))
            : muted("No focus yet. Sel's coach sets one after your calls are reviewed.")
        ),
        section("", "Other drills", null, "lines", "accent", others.length ? el("ul", { class: "page-rows" }, ...others.map(row)) : muted("Choose Practise this on a moment in a call's Coaching tab to add one."))
      ),
    ];
    main.replaceChildren(el("div", { class: "page" }, ...compact(nodes)));
  }

  // ---------- one drill ----------

  // After a reload: pick the open practice call back up.
  async function resume() {
    const s = page.session;
    if (!s || page.resuming) return;
    page.resuming = true;
    try {
      const v = await api(`/api/sim/rooms/${enc(s.code)}?client=${enc(s.client)}`);
      page.lines = (v.lines || []).slice();
      page.room = { ...v };
      delete page.room.lines;
      if (v.live) page.live = v.live;
      S.connectEvents({ room: s.code, client: s.client });
    } catch (_) {
      save(null); // the room is gone (the server restarted or it was filed long ago)
      S.connectEvents(null);
    }
    page.resuming = false;
    rerender();
  }

  function renderDrill(main, id) {
    if (page.session && page.session.drillId === id && !page.room) resume();
    const d = page.drills.get(id);
    if (!d) {
      main.replaceChildren(el("div", { class: "page" }, header(TO_PRACTICE, "Drill", null), el("section", { class: "card is-loading", "aria-busy": "true" }, muted("Loading the drill…"))));
      loadDrill(id);
      return;
    }
    if (d.error) {
      main.replaceChildren(el("div", { class: "page" }, header(TO_PRACTICE, "Drill", null), el("section", { class: "card is-error", role: "alert" }, el("p", { class: "strong", text: "This drill could not be loaded." }), muted(d.error), el("div", { class: "row" }, el("a", { class: "button", href: "#practice", text: "Back to Practice" })))));
      return;
    }
    const inRoom = page.session && page.session.drillId === id && page.room && !["saved", "failed"].includes(page.room.state);
    const attempts = d.attempts || [];
    const last = attempts[attempts.length - 1];
    const judging = page.session && page.session.drillId === id && page.room && page.room.state === "saved" && (!last || last.status !== "done");
    const nodes = [
      header(drillTrail(d), d.title, compact([d.persona ? `AI customer: ${d.persona.name}` : null, level(d), d.origin === "playground" ? "Playground: practice only" : d.moment && d.moment.customer_name ? `From your call with ${d.moment.customer_name}` : null]).join(" · "), el("a", { class: "button", href: d.origin === "playground" ? `#playbook/${enc(d.pack_id)}` : "#practice", text: d.origin === "playground" ? "Back to Playground" : "All drills" })),
      page.error ? el("p", { class: "form-error", role: "alert", text: page.error }) : null,
    ];
    if (inRoom) nodes.push(roomView(d));
    else if (judging || (last && last.status === "judging")) nodes.push(el("section", { class: "card is-loading", "aria-busy": "true", role: "status" }, el("p", { class: "strong", text: "Judging your practice call" }), muted("The debrief appears here in a moment.")));
    else if (last && last.debrief) nodes.push(debriefView(d, last));
    if (!inRoom) {
      const card = scenario(d, !!(last && last.debrief));
      if (d.status === "ready" && !judging && !last && !page.busy) {
        const actions = card.querySelector(".focus-body > .row");
        actions.classList.add("drill-start");
        const rep = S.people().find((person) => person.id === d.person_id) || S.viewer();
        actions.append(S.lineArt("rehearsal", rep));
      }
      nodes.push(card);
    }
    if (attempts.length > 1 && !inRoom) nodes.push(history(attempts));
    main.replaceChildren(el("div", { class: "page" }, ...compact(nodes)));
    if (inRoom) {
      paintLines(true);
      paintLive();
      const say = main.querySelector("#drill-say");
      if (say && !say.disabled) say.focus({ preventScroll: true });
    }
  }

  function said(customer, rep, repLabel) {
    return el(
      "div",
      { class: "said" },
      customer ? el("p", { class: "said-line role-customer" }, el("span", { class: "said-who", text: "Customer" }), quoted(customer)) : null,
      rep ? el("p", { class: "said-line role-rep" }, el("span", { class: "said-who", text: repLabel || "You" }), quoted(rep)) : null
    );
  }

  // Where a suggested line came from, in the words the Coaching tab uses.
  function lineTag(by) {
    const b = String(by || "");
    return b.startsWith("playbook") ? ["info", "Team answer"] : b.startsWith("fixture") ? ["neutral", "Example line"] : ["violet", "AI-suggested"];
  }

  function scenario(d, again) {
    if (d.origin === "playground") return objectionScenario(d, again);
    const m = d.moment || {};
    const p = d.persona;
    const start = el("button", { type: "button", class: "primary", text: again ? "Try again" : "Start the drill (about 90 s)", disabled: d.status !== "ready" || page.busy, on: { click: () => start_(d) } });
    return section(
      "bento-anchor",
      "The moment you are practising",
      compact([m.customer_name ? `Call with ${m.customer_name}` : null, m.started_at ? formatStarted(m.started_at) : null]).join(" · "),
      "target",
      "violet",
      el(
        "div",
        { class: "focus-body" },
        said(m.customer_said, m.quote, "You said"),
        m.why ? el("p", { class: "labelled" }, el("span", { class: "label", text: "What fell short: " }), m.why) : null,
        m.better_line
          ? el("div", { class: "better-line" }, el("div", { class: "better-line-head" }, el("span", { class: "label", text: "Better line" }), pill(String(m.better_line.by || "").startsWith("fixture") ? "neutral" : "violet", String(m.better_line.by || "").startsWith("fixture") ? "Example line" : "AI-suggested")), el("p", { class: "better-line-text", text: quoted(m.better_line.text) }))
          : null,
        p
          ? el(
              "div",
              { class: "drill-persona" },
              el("p", { class: "strong" }, `${p.name}, the AI customer `, pill("violet", String(p.by || "").startsWith("model") ? "AI persona" : "Built by Sel's rules")),
              el("p", { class: "muted small", text: compact([p.about, p.wants]).join(" ") }),
              el("p", { class: "small", text: "Anonymised from the real call: no names, numbers or prices from it reach the model." })
            )
          : el("p", { class: "muted", role: "status", text: "Preparing the AI customer from this moment…" }),
        el("div", { class: "row" }, start)
      )
    );
  }

  function objectionScenario(d, again) {
    const m = d.moment || {};
    const p = d.persona;
    const heard = !!(d.source && d.source.heard_id);
    const start = el("button", { type: "button", class: "primary", text: again ? "Try again" : "Start the drill (about 90 s)", disabled: d.status !== "ready" || page.busy, on: { click: () => start_(d) } });
    return section(
      "bento-anchor",
      "The objection you are practising",
      heard ? "Heard on one of your team's calls, anonymised" : "From your business pack",
      "target",
      "violet",
      el(
        "div",
        { class: "focus-body" },
        said(m.customer_said, null),
        m.context ? el("p", { class: "labelled" }, el("span", { class: "label", text: "Who raises it: " }), m.context) : null,
        p
          ? el(
              "div",
              { class: "drill-persona" },
              el("p", { class: "strong" }, `${p.name}, the AI customer `, pill("violet", String(p.by || "").startsWith("model") ? "AI persona" : "Built by Sel's rules")),
              el("p", { class: "muted small", text: compact([p.about, p.wants]).join(" ") }),
              el("p", { class: "small", text: heard ? "Anonymised from a real call: no names, numbers or prices from it reach the model." : "Built from your pack's objection; nothing from a real call." })
            )
          : el("p", { class: "muted", role: "status", text: "Preparing the AI customer…" }),
        el("p", { class: "muted small", text: "Practice only: this drill never changes your focus, your skills or your score." }),
        el("div", { class: "row" }, start)
      )
    );
  }

  function history(attempts) {
    return section(
      "",
      "Attempts",
      null,
      "lines",
      "accent",
      el(
        "ul",
        { class: "page-rows" },
        ...attempts
          .slice()
          .reverse()
          .map((a, i) => {
            const v = VERDICT[a.verdict] || ["neutral", a.status === "judging" ? "Judging" : "No verdict"];
            return el("li", { class: "page-row" }, icon("target", 18), el("div", { class: "page-row-main" }, el("span", { class: "strong", text: `Attempt ${attempts.length - i}` }), el("span", { class: "muted small", text: compact([formatStarted(a.at), `Level ${a.difficulty || 1}`, a.debrief && a.debrief.overall !== null && a.debrief.overall !== undefined ? `Score ${a.debrief.overall}/100` : null]).join(" · ") })), pill(v[0], v[1]));
          })
      )
    );
  }

  function debriefView(d, a) {
    const b = a.debrief;
    const v = VERDICT[b.verdict] || ["neutral", b.verdict];
    const practice = b.practice_answer && b.practice_answer.text;
    const lineBy = b.better_line_by || (d.moment && d.moment.better_line && d.moment.better_line.by);
    const compare = [
      ["On the real call you said", b.real_answer, null],
      ["In practice you said", practice, null],
      [lineTag(lineBy)[1] === "Team answer" ? "What the team says" : "Better line", b.better_line, lineTag(lineBy)],
      ["Your playbook suggests", b.hint || (d.moment && d.moment.hint), ["neutral", "From your pack"]],
    ].filter(([, text]) => text);
    return el(
      "section",
      { class: `card debrief${b.verdict === "pass" ? " is-success" : ""}`, "aria-labelledby": "debrief-title" },
      el("div", { class: "card-tags" }, pill(v[0], v[1]), b.by && b.by.startsWith("model") ? pill("violet", "Judged by AI and Sel's rules") : pill("neutral", "Judged by Sel's rules")),
      el("h2", { id: "debrief-title", class: "section-title", text: b.verdict === "pass" ? "You handled it." : b.verdict === "fail" ? "Not yet: try it once more." : "Debrief" }),
      b.why ? el("p", { text: b.why }) : null,
      el("ul", { class: "compare" }, ...compare.map(([label, text, tag]) => el("li", {}, el("div", { class: "better-line-head" }, el("span", { class: "label", text: label }), tag ? pill(tag[0], tag[1]) : null), el("p", { class: "compare-text", text: quoted(text) })))),
      el("p", { class: "muted small", text: compact([b.overall !== null && b.overall !== undefined ? `Practice score ${b.overall}/100` : null, b.criterion_detail, `${plural(b.lines || 0, "line", "lines")}`]).join(" · ") }),
      el("div", { class: "row" }, a.sim_call_id ? el("a", { class: "button small", href: `#call/${enc(a.sim_call_id)}`, text: "Open the practice call" }) : null)
    );
  }

  // ---------- the practice call ----------

  function roomView(d) {
    const say = el("input", { type: "text", id: "drill-say", autocomplete: "off", "aria-label": "What you say" });
    const send = el("button", { type: "submit", class: "small primary", "aria-label": "Say it" }, icon("send", 15));
    const composer = el("form", { class: "sim-composer", novalidate: true }, el("div", { class: "sim-say-row" }, say, send));
    composer.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = say.value.trim();
      if (!text) return;
      say.value = "";
      post("lines", { text }).then((line) => {
        if (line && !page.lines.some((l) => l.id === line.id)) {
          page.lines.push(line);
          paintLines();
        }
        say.focus();
      });
    });
    const refs = {
      lines: el("ol", { class: "sim-lines", "aria-label": "Practice call", "aria-live": "polite" }),
      status: el("p", { class: "card-sub drill-status", role: "status" }),
      skill: el("p", { class: "small drill-skill" }),
      controls: el("div", { class: "row" }),
      say,
      send,
    };
    page.refs = refs;
    const persona = d.persona || {};
    return el(
      "section",
      { class: "transcript-card drill-room", "aria-label": "Practice call" },
      el("div", { class: "section-head" }, el("div", { class: "section-head-main" }, iconTile("violet", "phone"), el("div", {}, el("h2", { class: "section-title", text: `Practice call with ${persona.name || "the AI customer"}` }), el("p", { class: "card-sub", text: "Type what you would say. The customer is played by AI; nothing here reaches a real customer." }))), refs.controls),
      refs.skill,
      refs.lines,
      refs.status,
      composer
    );
  }

  function paintRoom() {
    if (!here()) return;
    const id = route().arg;
    if (!page.refs || !page.main || !page.main.contains(page.refs.lines) || !page.room || !["ready", "live", "ended", "saving"].includes(page.room.state)) {
      rerender();
      return;
    }
    const r = page.refs;
    const live = page.room.state === "live";
    const ai = (page.room.participants || []).find((p) => p.ai);
    r.say.disabled = !live;
    r.send.disabled = !live;
    r.say.placeholder = live ? "What do you say? Press Enter" : page.room.state === "ready" ? "Start the call first" : "The call has ended";
    r.status.textContent = page.room.ai_busy ? `${(ai && ai.name) || "The customer"} is replying…` : page.room.state === "saving" ? "Saving the call…" : page.room.error || "";
    const btns = [];
    if (page.room.state === "ready") btns.push(el("button", { type: "button", class: "primary small", on: { click: begin } }, icon("phone", 15), "Start the call"));
    if (live) btns.push(el("button", { type: "button", class: "small", on: { click: finish } }, icon("hangup", 15), "End and get feedback"));
    r.controls.replaceChildren(...btns);
    syncTyping();
    if (id) paintLive();
  }

  function toBottom(list, smooth) {
    list.scrollTo({ top: list.scrollHeight, behavior: smooth && M.ok() ? "smooth" : "auto" });
  }

  // While the AI customer composes a reply, three dots wait where it will appear.
  // The status line above the composer says the same for screen readers.
  function syncTyping() {
    const r = page.refs;
    if (!r || !r.lines.isConnected) return;
    // Only while the customer owes a reply: the reply's line can arrive before the room says it is done.
    const last = page.lines[page.lines.length - 1];
    const busy = !!(page.room && page.room.ai_busy && page.room.state === "live") && (!last || last.role === "rep");
    if (!busy) {
      if (r.typing) r.typing.remove();
      r.typing = null;
      return;
    }
    if (!r.typing) {
      // Not a .sim-line: it is not a line of the call.
      const ai = (page.room.participants || []).find((p) => p.ai);
      r.typing = M.arrive(el("li", { class: "sim-typing", "aria-hidden": "true" }, el("div", { class: "sim-line-meta" }, el("span", { class: "who", text: (ai && ai.name) || "Customer" })), M.typing()));
    }
    if (r.typing !== r.lines.lastElementChild) {
      r.lines.append(r.typing);
      toBottom(r.lines, true);
    }
  }

  function paintLines(full) {
    const r = page.refs;
    if (!r || !page.main || !page.main.contains(r.lines)) return;
    const ai = page.room && (page.room.participants || []).find((p) => p.ai);
    // Lines that arrived since the last paint rise in; the first paint (or a resumed call) just shows.
    const first = !r.seen;
    const fresh = first ? [] : page.lines.filter((l) => !r.seen.has(l.id));
    const rising = new Set(fresh.length <= 3 ? fresh.map((l) => l.id) : []);
    r.seen = new Set(page.lines.map((l) => l.id));
    r.lines.replaceChildren(
      ...page.lines.map((l) => {
        const row = el(
          "li",
          { class: `sim-line role-${l.role}${l.role === "rep" ? " is-mine" : ""}` },
          el("div", { class: "sim-line-meta" }, el("span", { class: "who", text: l.role === "rep" ? "You" : (ai && ai.name) || "Customer" }), el("span", { class: "card-sub", text: mmss(l.start) }), l.source === "ai" ? el("span", { class: "lang", text: "AI" }) : null),
          el("p", { class: "text", text: l.text })
        );
        return rising.has(l.id) ? M.arrive(row) : row;
      })
    );
    if (!page.lines.length) r.lines.append(el("li", { class: "muted sim-lines-empty", text: page.room && page.room.state === "ready" ? "Start the call: the customer speaks first." : "…" }));
    syncTyping();
    toBottom(r.lines, !first);
    if (full) paintRoom();
  }

  function paintLive() {
    const r = page.refs;
    if (!r || !page.room) return;
    const d = page.drills.get(page.session && page.session.drillId);
    const c = d && page.live && page.live.score ? page.live.score.criteria.find((x) => x.id === d.criterion) : null;
    const st = c ? S.SCORE_STATUS[c.status] || { cls: "neutral", label: c.status } : null;
    r.skill.replaceChildren(...(c ? [el("span", { class: "label", text: `${c.title}: ` }), pill(st.cls, st.label), " ", el("span", { class: "muted", text: c.detail || "" })] : []));
  }

  async function post(path, json) {
    try {
      return await api(`/api/sim/rooms/${enc(page.session.code)}/${path}`, { method: "POST", json: { client: page.session.client, ...(json || {}) } });
    } catch (e) {
      page.error = e.message;
      rerender();
      return null;
    }
  }

  async function start_(d) {
    const me = S.viewer();
    page.busy = true;
    try {
      const r = await api(`/api/drills/${enc(d.id)}/start`, { method: "POST", json: { by: me ? me.id : null, offset: S.localOffset() } });
      page.lines = [];
      page.live = null;
      page.room = { ...r.room };
      delete page.room.lines;
      save({ code: r.room.code, client: r.client, drillId: d.id });
      page.error = null;
    } catch (e) {
      page.error = e.message;
    }
    page.busy = false;
    rerender();
  }

  async function begin() {
    const v = await post("start");
    if (!v) return;
    page.room = { ...v };
    page.lines = (v.lines || []).slice();
    delete page.room.lines;
    if (v.live) page.live = v.live;
    rerender();
  }

  async function finish() {
    if (!page.session || page.finishing) return;
    page.finishing = true;
    try {
      const v = await api(`/api/sim/rooms/${enc(page.session.code)}/finish?client=${enc(page.session.client)}`, { method: "POST" });
      page.room = { ...page.room, ...v };
    } catch (e) {
      page.error = e.message;
    }
    page.finishing = false;
    const id = page.session.drillId;
    loadDrill(id);
  }

  S.practice = { render };
})();
