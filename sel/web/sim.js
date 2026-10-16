/* Sel call simulator. Loaded by app.js the first time #sim is opened.
 *
 * Two open Sel sessions (tabs, windows or computers) join one call. Audio
 * goes browser to browser over WebRTC; the Sel server relays only the
 * connection setup, through the tab's event stream. The rep's browser
 * records the call in stereo (rep left, customer right). Each browser
 * streams its own microphone to Sel for live transcription when the server
 * has faster-whisper; otherwise people type their lines. Every line is scored live; on hang-up the recording
 * and transcript become a normal Sel call with follow-up drafts.
 *
 * Rendering is batched to one animation frame and done per part; new lines
 * are inserted into the transcript rather than redrawing it.
 */
(function () {
  "use strict";

  const S = window.Sel;
  const { el, icon, pill, muted, compact, plural, mmss, quoted, api, upload, navigate } = S;
  const enc = encodeURIComponent;

  const KEY = "sel.sim";
  const PROFILE = "sel.sim.profile";
  const ROOM_STATE = {
    waiting: ["neutral", "Waiting for the other side"],
    ready: ["info", "Ready to start"],
    live: ["bad", "Live"],
    ended: ["neutral", "Ended"],
    saving: ["info", "Saving"],
    saved: ["ok", "Saved"],
    failed: ["bad", "Not saved"],
  };
  const FLOW = ["Join", "Talk", "Score live", "Save", "Analyse", "Next actions"];
  const SOURCE_LABEL = { speech: "speech", typed: "typed" };
  const EVENT_LABEL = {
    question: ["info", "Question"],
    objection: ["warn", "Objection"],
    claim: ["violet", "Claim"],
    commitment: ["ok", "Promise"],
    price: ["neutral", "Price"],
  };
  const ACTION_TONE = { answer: "info", objection: "warn", claim: "bad", date: "warn", discovery: "violet", talk: "neutral", next_step: "accent" };
  const ACTION_ICON = { answer: "question", objection: "alert", claim: "doc", date: "calendar", discovery: "target", talk: "user", next_step: "check" };

  const sim = {
    session: loadSession(), // {code, client, role}
    room: null, // latest room view (without lines)
    lines: [],
    lineIds: new Set(),
    live: null,
    error: null,
    // media
    mic: "off", // off | asking | on | muted | denied | unavailable
    localStream: null,
    remoteStream: null,
    remoteAudio: null,
    pc: null,
    transceiver: null,
    pendingIce: [],
    ctx: null,
    merger: null,
    recorder: null,
    chunks: [],
    recMime: "",
    localRec: null,
    remoteRec: null,
    meters: { local: null, remote: null },
    pcm: null,
    pcmQueue: [],
    pcmBusy: false,
    pcmBase: null,
    uploadFrac: null,
    clock: { at: 0, elapsed: 0 },
    timer: 0,
    meterRaf: 0,
    refs: null,
    lobby: null,
    report: null, // the filed call's report, for next actions
    reportFor: null,
    dirty: new Set(),
    frame: 0,
  };

  function loadSession() {
    try {
      return JSON.parse(sessionStorage.getItem(KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function saveSession(s) {
    sim.session = s;
    try {
      if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
      else sessionStorage.removeItem(KEY);
    } catch (_) {
      /* the session still works until the tab reloads */
    }
    S.connectEvents(s ? { room: s.code, client: s.client } : null);
  }

  const mine = () => sim.session && sim.room && sim.room.code === sim.session.code;
  const myRole = () => (sim.session ? sim.session.role : null);
  const other = () => (sim.room ? sim.room.participants.find((p) => p.role !== myRole()) || null : null);
  const live = () => !!sim.room && sim.room.state === "live";
  const speechLive = () => !!(sim.room && sim.room.speech && sim.room.speech.live);
  const canMic = () => !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  function callNow() {
    if (!sim.room) return 0;
    if (!live()) return sim.room.elapsed || 0;
    return sim.clock.elapsed + (performance.now() - sim.clock.at) / 1000;
  }

  // ---------- rendering, batched per frame ----------

  function dirty(...parts) {
    for (const p of parts) sim.dirty.add(p);
    if (sim.frame) return;
    sim.frame = requestAnimationFrame(() => {
      sim.frame = 0;
      const parts = sim.dirty;
      sim.dirty = new Set();
      if (sim.lobby && document.body.contains(sim.lobby.root)) {
        if (parts.has("rooms")) renderRooms();
        if (parts.has("caps")) renderCaps(sim.lobby.caps);
      }
      if (!sim.refs || !document.body.contains(sim.refs.root)) return;
      if (parts.has("room")) {
        renderHead();
        renderFlow();
        renderPeople();
        renderControls();
        renderComposer();
        renderAfter();
      }
      if (parts.has("people")) renderPeople();
      if (parts.has("controls")) renderControls();
      if (parts.has("lines")) renderLines();
      if (parts.has("live")) renderLive();
      if (parts.has("after")) renderAfter();
      if (parts.has("flow")) renderFlow();
      if (parts.has("composer")) renderComposer();
    });
  }

  function render(main, code) {
    if (code && sim.session && sim.session.code === code) {
      if (!sim.room || sim.room.code !== code) fetchRoom(code);
      mountRoom(main);
      return;
    }
    if (code) {
      mountJoin(main, code);
      return;
    }
    if (sim.session && !(sim.room && ["saved", "failed"].includes(sim.room.state))) {
      navigate(`#sim/${sim.session.code}`);
      return;
    }
    mountLobby(main);
  }

  async function fetchRoom(code) {
    try {
      const view = await api(`/api/sim/rooms/${enc(code)}?client=${enc(sim.session.client)}`);
      if (!view.you) {
        saveSession(null);
        navigate("#sim");
        return;
      }
      applyRoom(view);
    } catch (e) {
      if (e.status === 404) {
        saveSession(null);
        sim.error = "That call is no longer open (the server may have restarted).";
        navigate("#sim");
      } else setError(e.message);
    }
  }

  function setError(message) {
    sim.error = message || null;
    if (sim.refs && sim.refs.error) {
      sim.refs.error.textContent = sim.error || "";
      sim.refs.error.hidden = !sim.error;
    }
    if (sim.lobby && sim.lobby.error) {
      sim.lobby.error.textContent = sim.error || "";
      sim.lobby.error.hidden = !sim.error;
    }
  }

  // ---------- lobby ----------

  function profile() {
    return S.store(PROFILE) || {};
  }

  // The person this browser is viewing as: a practice call by a rep is filed under that rep.
  function personId() {
    const me = S.viewer();
    return me ? me.id : null;
  }

  function mountLobby(main) {
    sim.refs = null;
    const p = profile();
    const me = S.viewer();
    const name = el("input", { id: "sim-name", type: "text", autocomplete: "name", value: (me && me.name) || p.name || "", placeholder: "How the other side sees you" });
    const email = el("input", { id: "sim-email", type: "email", autocomplete: "email", value: p.email || "", placeholder: "Used as the To: of follow-up drafts" });
    const packSel = el("select", { id: "sim-pack" });
    for (const pk of S.packs()) packSel.append(el("option", { value: pk.id, text: pk.name }));
    if (p.pack) packSel.value = p.pack;
    const roleRep = el("input", { type: "radio", name: "sim-role", id: "sim-role-rep", value: "rep", checked: (p.role || "rep") === "rep" });
    const roleCust = el("input", { type: "radio", name: "sim-role", id: "sim-role-cust", value: "customer", checked: p.role === "customer" });
    const roles = el(
      "div",
      { class: "segmented", role: "radiogroup", "aria-label": "Your side of the call" },
      roleRep,
      el("label", { for: "sim-role-rep", text: "Rep" }),
      roleCust,
      el("label", { for: "sim-role-cust", text: "Customer" })
    );
    const error = el("p", { class: "form-error", role: "alert", hidden: !sim.error, text: sim.error || "" });
    const caps = el("div", { class: "sim-caps" });
    const rooms = el("ul", { class: "sim-rooms", "aria-live": "polite" });
    const code = el("input", { id: "sim-code", type: "text", inputmode: "text", maxlength: "6", autocomplete: "off", placeholder: "abc234", "aria-label": "Call code" });
    const startBtn = el("button", { type: "submit", class: "primary", text: "Open a call", disabled: !S.packs().length });

    const details = () => {
      const role = roleCust.checked ? "customer" : "rep";
      S.store(PROFILE, { name: name.value.trim(), email: email.value.trim(), pack: packSel.value, role });
      return { name: name.value.trim(), email: email.value.trim(), role, pack_id: packSel.value };
    };

    const start = el(
      "form",
      { class: "card sim-start", novalidate: true },
      el("h2", { class: "section-title", text: "Start a call" }),
      el("div", { class: "field" }, el("label", { for: "sim-pack", text: "Business pack" }), packSel),
      el("div", { class: "field" }, el("span", { class: "label-like", text: "You play" }), roles),
      startBtn
    );
    start.addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = details();
      if (!d.name) {
        setError("Enter your name first.");
        name.focus();
        return;
      }
      startBtn.disabled = true;
      try {
        const r = await api("/api/sim/rooms", {
          method: "POST",
          json: { pack_id: d.pack_id, role: d.role, name: d.name, email: d.role === "customer" ? d.email : null, offset: S.localOffset(), person_id: personId() },
        });
        enterRoom(r);
      } catch (err) {
        setError(err.message);
        startBtn.disabled = false;
      }
    });

    const joinCode = el(
      "form",
      { class: "row sim-code-form", novalidate: true },
      code,
      el("button", { type: "submit", text: "Join" })
    );
    joinCode.addEventListener("submit", (e) => {
      e.preventDefault();
      const c = code.value.trim().toLowerCase();
      if (!/^[a-z0-9]{6}$/.test(c)) {
        setError("A call code has six letters and digits.");
        return;
      }
      join(c, null, details());
    });

    const root = el(
      "section",
      { class: "sim-lobby" },
      el(
        "header",
        { class: "sim-lobby-head" },
        el("p", { class: "eyebrow", text: "Practice and live review" }),
        el("h1", { text: "Call simulator" }),
        el("p", {
          class: "lede",
          text: "Open Sel in a second window, or on another computer on your network. Start a call in one, join it from the other, and talk. Sel records the call, scores it live against the pack's scorecard, and when you hang up it files the call with follow-up drafts ready for approval.",
        })
      ),
      caps,
      error,
      el(
        "section",
        { class: "card sim-you" },
        el("h2", { class: "section-title", text: "You" }),
        el(
          "div",
          { class: "sim-you-grid" },
          el("div", { class: "field" }, el("label", { for: "sim-name", text: "Your name" }), name),
          el("div", { class: "field" }, el("label", { for: "sim-email", text: "Your email, if you play the customer" }), email)
        )
      ),
      el(
        "div",
        { class: "sim-lobby-grid" },
        start,
        el(
          "section",
          { class: "card sim-open" },
          el("h2", { class: "section-title", text: "Join a call" }),
          rooms,
          el("p", { class: "card-sub", text: "Or type the code someone gave you:" }),
          joinCode
        )
      ),
      el("p", { class: "fine" }, icon("lock", 15), el("span", { text: "Audio goes directly between the two browsers. Recordings and transcripts stay on the computer running Sel. Use headphones if both windows are on one computer." }))
    );
    sim.lobby = { root, rooms, caps, error, details, name };
    main.replaceChildren(root);
    renderCaps(caps);
    renderRooms();
  }

  function renderCaps(node) {
    const info = (sim.room && sim.room.speech) || (S.simInfo() && S.simInfo().speech) || null;
    const chips = [];
    if (info && info.live) chips.push(capChip(true, `Live transcription on this computer${info.model ? ` (Whisper ${info.model})` : ""}`));
    else chips.push(capChip(false, `Type your lines: ${(info && info.reason) || "live speech recognition is not installed"}`));
    chips.push(
      canMic()
        ? capChip(true, "Microphone available")
        : capChip(false, "Microphone needs https or localhost; typing still works")
    );
    if (window.MediaRecorder === undefined) chips.push(capChip(false, "This browser cannot record; the transcript is still filed"));
    node.replaceChildren(...chips);
  }

  function capChip(ok, text) {
    return el("span", { class: `chip ${ok ? "chip-ok" : "chip-bad"}` }, el("span", { class: "dot", "aria-hidden": "true" }), el("span", { text }));
  }

  function renderRooms() {
    if (!sim.lobby) return;
    const rooms = S.rooms();
    if (!rooms.length) {
      sim.lobby.rooms.replaceChildren(el("li", { class: "sim-room-empty muted", text: "No calls are waiting. Start one, or start it from the other window." }));
      return;
    }
    sim.lobby.rooms.replaceChildren(
      ...rooms.map((r) =>
        el(
          "li",
          { class: "sim-room-item" },
          el(
            "div",
            { class: "sim-room-text" },
            el("span", { class: "strong", text: `${r.host.name || "Someone"} (${S.ROLE_LABEL[r.host.role] || r.host.role})` }),
            el("span", { class: "card-sub", text: `${r.pack_name} · code ${r.code}` })
          ),
          el("button", {
            type: "button",
            class: "small primary",
            text: `Join as ${S.ROLE_LABEL[r.free_role].toLowerCase()}`,
            on: { click: () => join(r.code, r.free_role, sim.lobby.details()) },
          })
        )
      )
    );
  }

  async function join(code, role, d) {
    if (!d.name) {
      setError("Enter your name first.");
      if (sim.lobby) sim.lobby.name.focus();
      return;
    }
    try {
      const r = await api(`/api/sim/rooms/${enc(code)}/join`, {
        method: "POST",
        json: { role, name: d.name, email: d.email || null, person_id: personId() },
      });
      enterRoom(r);
    } catch (err) {
      setError(err.message);
    }
  }

  function enterRoom(r) {
    setError(null);
    resetCall();
    saveSession({ code: r.room.code, client: r.client, role: r.room.you });
    applyRoom(r.room);
    navigate(`#sim/${r.room.code}`);
  }

  // Joining from a shared link (#sim/<code>) without a session in this tab.
  function mountJoin(main, code) {
    sim.refs = null;
    sim.lobby = null;
    const p = profile();
    const name = el("input", { id: "sim-jname", type: "text", value: p.name || "", autocomplete: "name" });
    const email = el("input", { id: "sim-jemail", type: "email", value: p.email || "", autocomplete: "email" });
    const error = el("p", { class: "form-error", role: "alert", hidden: true });
    const form = el(
      "form",
      { class: "card sim-join", novalidate: true },
      el("h1", { text: `Join call ${code}` }),
      el("p", { class: "lede", text: "You take whichever side is free." }),
      el("div", { class: "field" }, el("label", { for: "sim-jname", text: "Your name" }), name),
      el("div", { class: "field" }, el("label", { for: "sim-jemail", text: "Your email, if you play the customer" }), email),
      error,
      el("div", { class: "row" }, el("button", { type: "submit", class: "primary", text: "Join the call" }), el("a", { class: "button", href: "#sim", text: "Back" }))
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!name.value.trim()) {
        error.textContent = "Enter your name first.";
        error.hidden = false;
        return;
      }
      S.store(PROFILE, { ...profile(), name: name.value.trim(), email: email.value.trim() });
      try {
        const r = await api(`/api/sim/rooms/${enc(code)}/join`, { method: "POST", json: { name: name.value.trim(), email: email.value.trim() || null, person_id: personId() } });
        enterRoom(r);
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      }
    });
    main.replaceChildren(form);
  }

  // ---------- room ----------

  function mountRoom(main) {
    sim.lobby = null;
    const refs = {
      title: el("h1", {}),
      eyebrow: el("p", { class: "eyebrow" }),
      statePill: el("span", {}),
      timer: el("span", { class: "sim-timer", "aria-live": "off" }),
      copy: el("button", { type: "button", class: "small ghost", on: { click: copyLink } }, icon("link", 15), "Copy invite link"),
      flow: el("ol", { class: "flow", "aria-label": "Call workflow" }),
      people: el("div", { class: "sim-people" }),
      controls: el("div", { class: "sim-controls" }),
      devNote: el("p", { class: "card-sub sim-devnote" }),
      error: el("p", { class: "form-error", role: "alert", hidden: !sim.error, text: sim.error || "" }),
      lines: el("ol", { class: "sim-lines", "aria-label": "Live transcript", "aria-live": "polite" }),
      empty: el("p", { class: "muted sim-lines-empty" }),
      composer: el("form", { class: "sim-composer", novalidate: true }),
      say: el("input", { type: "text", id: "sim-say", autocomplete: "off", "aria-label": "Your line" }),
      send: el("button", { type: "submit", class: "small primary", "aria-label": "Add line" }, icon("send", 15)),
      score: el("section", { class: "sim-score", "aria-labelledby": "sim-score-title" }),
      actions: el("section", { class: "sim-actions", "aria-labelledby": "sim-actions-title" }),
      feed: el("section", { class: "sim-feed", "aria-labelledby": "sim-feed-title" }),
      after: el("section", { class: "sim-after", hidden: true, "aria-labelledby": "sim-after-title" }),
      lineRows: new Map(),
    };
    refs.root = el(
      "div",
      { class: "sim-room" },
      el(
        "div",
        { class: "sim-main" },
        el(
          "header",
          { class: "sim-head" },
          el("div", {}, refs.eyebrow, el("div", { class: "sim-title-row" }, refs.title, refs.statePill, refs.timer)),
          refs.copy
        ),
        refs.flow,
        refs.error,
        refs.after,
        el("section", { class: "card sim-call" }, refs.people, refs.controls, refs.devNote),
        el(
          "section",
          { class: "transcript-card sim-transcript", "aria-labelledby": "sim-tx-title" },
          el(
            "div",
            { class: "section-head" },
            el(
              "div",
              { class: "section-head-main" },
              S.iconTile("accent", "lines"),
              el("div", {}, el("h2", { class: "section-title", id: "sim-tx-title", text: "Live transcript" }), el("p", { class: "card-sub", text: "Lines appear as they are recognised or typed, scored as they arrive." }))
            )
          ),
          refs.lines,
          refs.empty,
          refs.composer
        )
      ),
      el("aside", { class: "sim-side", "aria-label": "Live review" }, refs.score, refs.actions, refs.feed)
    );
    refs.composer.append(el("div", { class: "sim-say-row" }, refs.say, refs.send));
    refs.composer.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = refs.say.value.trim();
      if (!text) return;
      refs.say.value = "";
      postLine(text, null, null, "typed").then(() => refs.say.focus());
    });
    sim.refs = refs;
    main.replaceChildren(refs.root);
    if (sim.room) {
      renderHead();
      renderFlow();
      renderPeople();
      renderControls();
      renderComposer();
      renderLines(true);
      renderLive();
      renderAfter();
    } else {
      refs.title.textContent = "Connecting…";
    }
    tickTimer();
  }

  function applyRoom(v) {
    const prev = sim.room;
    const prevOther = prev ? prev.participants.find((p) => p.role !== myRole()) : null;
    sim.room = { ...v };
    delete sim.room.lines;
    if (v.lines) {
      sim.lines = v.lines.slice();
      sim.lineIds = new Set(v.lines.map((l) => l.id));
      sim.pendingLines = null; // full redraw
      dirty("lines");
    }
    if (v.live) sim.live = v.live;
    sim.clock = { at: performance.now(), elapsed: v.elapsed || 0 };
    const wasLive = prev && prev.state === "live";
    if (v.state === "live" && !wasLive) onLive();
    if (["ended", "saving", "saved", "failed"].includes(v.state) && (wasLive || (prev && prev.state === "ready"))) onEnded();
    if (v.state === "saved" && v.call_id && sim.reportFor !== v.call_id) loadReport();
    // The rep's browser starts (and restarts) the audio connection.
    const o = other();
    if (myRole() === "rep" && o && o.online && ["ready", "live"].includes(v.state)) {
      const broken = !sim.pc || ["failed", "closed"].includes(sim.pc.connectionState);
      if (broken || !prevOther || !prevOther.online) makeOffer();
    }
    if (o && !o.online && sim.pc && !["ready", "live"].includes(v.state)) closePeer();
    document.getElementById("open-sim")?.classList.toggle("is-live", v.state === "live");
    dirty("room", "live");
    tickTimer();
  }

  function renderHead() {
    const r = sim.refs;
    const room = sim.room;
    if (!room) return;
    r.eyebrow.textContent = room.pack_name;
    r.title.textContent = `Call ${room.code}`;
    const [cls, label] = ROOM_STATE[room.state] || ["neutral", room.state];
    r.statePill.replaceChildren(pill(cls, label));
    r.copy.hidden = room.state !== "waiting";
    r.error.textContent = room.error || sim.error || "";
    r.error.hidden = !(room.error || sim.error);
  }

  function tickTimer() {
    clearInterval(sim.timer);
    const paint = () => {
      if (!sim.refs || !sim.room) return;
      const showing = ["live", "ended", "saving", "saved"].includes(sim.room.state);
      sim.refs.timer.textContent = showing ? mmss(callNow()) : "";
      sim.refs.timer.classList.toggle("is-live", live());
    };
    paint();
    if (live()) sim.timer = setInterval(paint, 500);
  }

  function copyLink() {
    const url = `${location.origin}${location.pathname}#sim/${sim.room.code}`;
    const done = () => {
      sim.refs.copy.lastChild.textContent = "Link copied";
      setTimeout(() => sim.refs && (sim.refs.copy.lastChild.textContent = "Copy invite link"), 1500);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, () => prompt("Invite link", url));
    else prompt("Invite link", url);
  }

  // Workflow rail: Join → Talk → Score live → Save → Analyse → Next actions.
  function flow() {
    const room = sim.room;
    const st = room.state;
    const call = room.call_id ? S.calls().find((c) => c.id === room.call_id) || null : null;
    const order = ["waiting", "ready", "live", "ended", "saving", "saved", "failed"];
    const at = order.indexOf(st);
    const steps = FLOW.map((label) => ({ label, status: "todo", note: "" }));
    const set = (i, status, note) => Object.assign(steps[i], { status, note: note || "" });
    set(0, at === 0 ? "current" : "done", at === 0 ? "Waiting for the other side" : `${room.participants.length} joined`);
    if (at >= 1) set(1, at <= 2 ? "current" : "done", at === 1 ? "Press Start when ready" : at === 2 ? "On the call" : mmss(room.elapsed));
    if (at >= 2) set(2, at === 2 ? "current" : "done", sim.live && sim.live.score && sim.live.score.overall !== null ? `${sim.live.score.overall}/100` : "");
    if (at >= 3) {
      if (st === "failed") set(3, "error", "Not saved");
      else set(3, st === "saved" ? "done" : "current", st === "saved" ? "Filed" : sim.uploadFrac !== null ? `Uploading ${Math.round(sim.uploadFrac * 100)}%` : "Saving");
    }
    if (st === "saved") {
      if (!call || S.isActive(call.status)) set(4, "current", call ? `${S.stageText(call.stage)} · ${Math.round((call.progress || 0) * 100)}%` : "Queued");
      else if (call.status === "error") set(4, "error", "Analysis failed");
      else {
        set(4, "done", "");
        const drafts = (sim.report && sim.report.drafts) || [];
        const open = drafts.filter((d) => d.status !== "approved").length;
        set(5, drafts.length && !open ? "done" : "current", drafts.length ? (open ? `${open} waiting for you` : "All approved") : "Nothing to send");
      }
    }
    return steps;
  }

  function renderFlow() {
    if (!sim.room) return;
    sim.refs.flow.replaceChildren(
      ...flow().map((s, i) =>
        el(
          "li",
          { class: `flow-step is-${s.status}`, "aria-current": s.status === "current" ? "step" : null },
          el("span", { class: "flow-num" }, s.status === "done" ? icon("check", 12) : s.status === "error" ? icon("x", 12) : String(i + 1)),
          el("span", { class: "flow-text" }, el("span", { class: "flow-label", text: s.label }), s.note ? el("span", { class: "flow-note", text: s.note }) : null)
        )
      )
    );
  }

  function renderPeople() {
    if (!sim.room) return;
    const tiles = ["rep", "customer"].map((role) => {
      const p = sim.room.participants.find((x) => x.role === role);
      const me = role === myRole();
      const meter = el("span", { class: "meter", "aria-hidden": "true" }, el("i", {}));
      if (me) sim.meters.localEl = meter;
      else sim.meters.remoteEl = meter;
      return el(
        "div",
        { class: `person role-${role}${p && p.online ? " is-online" : ""}${me ? " is-me" : ""}` },
        el("span", { class: `avatar role-${role}`, "aria-hidden": "true" }, (p && p.name ? p.name : S.ROLE_LABEL[role]).charAt(0).toUpperCase()),
        el(
          "div",
          { class: "person-text" },
          el("span", { class: "strong", text: p ? `${p.name || S.ROLE_LABEL[role]}${me ? " (you)" : ""}` : "Not joined yet" }),
          el(
            "span",
            { class: "card-sub" },
            S.ROLE_LABEL[role],
            " · ",
            p ? (p.online ? "online" : "offline") : "waiting",
            p && p.online ? ` · mic ${p.mic ? "on" : "off"}` : ""
          )
        ),
        meter
      );
    });
    sim.refs.people.replaceChildren(...tiles);
  }

  function renderControls() {
    if (!sim.room) return;
    const st = sim.room.state;
    const r = sim.refs;
    const btns = [];
    if (["waiting", "ready", "live"].includes(st)) {
      if (sim.mic === "on" || sim.mic === "muted") {
        btns.push(
          el(
            "button",
            { type: "button", class: "small", "aria-pressed": sim.mic === "muted" ? "true" : "false", on: { click: toggleMute } },
            icon(sim.mic === "muted" ? "micoff" : "mic", 16),
            sim.mic === "muted" ? "Unmute" : "Mute"
          )
        );
      } else {
        btns.push(
          el(
            "button",
            { type: "button", class: "small", disabled: !canMic() || sim.mic === "asking", on: { click: enableMic } },
            icon("mic", 16),
            sim.mic === "asking" ? "Allow the microphone…" : "Turn on microphone"
          )
        );
      }
    }
    if (st === "ready") btns.push(el("button", { type: "button", class: "primary", on: { click: startCall } }, icon("phone", 16), "Start the call"));
    if (st === "live") btns.push(el("button", { type: "button", class: "danger", on: { click: hangUp } }, icon("hangup", 16), "Hang up"));
    if (st === "waiting" || st === "ready") btns.push(el("button", { type: "button", class: "small ghost", text: "Leave", on: { click: leave } }));
    if (st === "ended" && myRole() !== "rep" && other() && !other().online) {
      btns.push(el("button", { type: "button", class: "small", text: "Save without the recording", on: { click: () => finish(null) } }));
    }
    if (["saved", "failed"].includes(st)) {
      btns.push(el("button", { type: "button", class: "small", text: "New call", on: { click: () => { resetCall(); saveSession(null); navigate("#sim"); } } }));
      if (st === "failed") btns.push(el("button", { type: "button", class: "small primary", text: "Try saving again", on: { click: () => finish(sim.lastBlob || null) } }));
    }
    r.controls.replaceChildren(...btns);
    const notes = [];
    if (!canMic()) notes.push("The microphone needs https or localhost, so type your lines below.");
    else if (sim.mic === "denied") notes.push("The microphone was blocked. Allow it in the browser's site settings, or type your lines.");
    else if (sim.mic === "unavailable") notes.push("No microphone was found. Type your lines below.");
    if (st === "live" && myRole() === "rep") notes.push(sim.recorder ? "Recording: you on the left channel, the customer on the right." : "This browser is not recording; the transcript is still filed.");
    if (st === "live" && myRole() !== "rep") notes.push("The rep's browser records the call.");
    if (st === "live" && sim.mic === "on" && !speechLive()) notes.push("Speech is not transcribed on this server: type what you say.");
    if (sim.pc && st !== "saved") notes.push(`Audio link: ${sim.pc.connectionState}.`);
    if (st === "ended") notes.push(myRole() === "rep" ? "Saving the recording…" : "Waiting for the rep's browser to save the recording…");
    r.devNote.textContent = notes.join(" ");
    r.devNote.hidden = !notes.length;
  }

  function renderComposer() {
    if (!sim.room) return;
    const r = sim.refs;
    const isLive = live();
    const wasDisabled = r.say.disabled;
    r.say.disabled = !isLive;
    r.send.disabled = !isLive;
    r.say.placeholder = isLive
      ? `Type what you say as the ${(S.ROLE_LABEL[myRole()] || "").toLowerCase()}, then press Enter`
      : "Lines can be added once the call has started";
    if (isLive && wasDisabled) r.say.focus({ preventScroll: true });
  }

  // ---------- transcript lines ----------

  function lineRow(line) {
    const p = sim.room ? sim.room.participants.find((x) => x.role === line.role) : null;
    return el(
      "li",
      { class: `sim-line role-${line.role}${line.role === myRole() ? " is-mine" : ""}`, dataset: { id: line.id } },
      el(
        "div",
        { class: "sim-line-meta" },
        el("span", { class: "who", text: (p && p.name) || S.ROLE_LABEL[line.role] }),
        el("span", { class: "card-sub", text: mmss(line.start) }),
        line.source !== "speech" ? el("span", { class: "lang", text: SOURCE_LABEL[line.source] || line.source }) : null
      ),
      el("p", { class: "text", text: line.text })
    );
  }

  function addLine(line) {
    if (!mine() || sim.lineIds.has(line.id)) return;
    sim.lineIds.add(line.id);
    // Keep time order; speech can be recognised out of order.
    let i = sim.lines.length;
    while (i > 0 && sim.lines[i - 1].start > line.start) i -= 1;
    sim.lines.splice(i, 0, line);
    sim.pendingLines = (sim.pendingLines || []).concat([line]);
    dirty("lines");
  }

  function renderLines(full) {
    const r = sim.refs;
    const list = r.lines;
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    if (full || !sim.pendingLines || r.lineRows.size + (sim.pendingLines || []).length !== sim.lines.length) {
      r.lineRows = new Map();
      const frag = document.createDocumentFragment();
      for (const line of sim.lines) {
        const row = lineRow(line);
        r.lineRows.set(line.id, row);
        frag.append(row);
      }
      list.replaceChildren(frag);
    } else {
      const pending = sim.pendingLines.map((line) => ({ line, i: sim.lines.indexOf(line) })).sort((a, b) => b.i - a.i);
      for (const { line, i } of pending) {
        const row = lineRow(line);
        const next = sim.lines[i + 1];
        list.insertBefore(row, next ? r.lineRows.get(next.id) || null : null);
        r.lineRows.set(line.id, row);
      }
    }
    sim.pendingLines = [];
    r.empty.hidden = sim.lines.length > 0;
    r.empty.textContent = live() ? "Say something, or type a line below." : sim.room && sim.room.state === "ready" ? "The transcript starts when the call starts." : "No lines yet.";
    if (nearBottom || full) list.scrollTop = list.scrollHeight;
  }

  // ---------- live score, next actions, feed ----------

  function renderLive() {
    const r = sim.refs;
    const snap = sim.live;
    const score = snap && snap.score;
    const final = sim.room && sim.room.live && sim.room.live.final_score;
    const shown = final || score;
    if (!shown) {
      r.score.replaceChildren(el("h2", { id: "sim-score-title", class: "section-title", text: "Live score" }), muted("The score starts with the call."));
      r.actions.replaceChildren();
      r.feed.replaceChildren();
      return;
    }
    const overall = shown.overall;
    const talk = shown.talk || {};
    const share = talk.rep_share;
    r.score.replaceChildren(
      el(
        "div",
        { class: "sim-score-head" },
        el(
          "div",
          {},
          el("h2", { id: "sim-score-title", class: "section-title", text: final ? "Score at hang-up" : "Live score" }),
          el("p", { class: "card-sub", text: "Rules over the transcript, graded against the pack's scorecard. The full review follows the call." })
        ),
        gauge(overall)
      ),
      el(
        "ul",
        { class: "score-list" },
        ...shown.criteria.map((c) => {
          const s = S.SCORE_STATUS[c.status] || { cls: "neutral", label: c.status };
          return el(
            "li",
            { class: `score-row status-${c.status}` },
            el("div", { class: "score-row-head" }, el("span", { class: "score-title", text: c.title }), pill(s.cls, s.label)),
            c.detail ? el("p", { class: "score-detail", text: c.detail }) : null,
            el("span", { class: "score-bar", "aria-hidden": "true" }, el("i", { style: `width:${c.points === null ? 0 : Math.round(c.points * 100)}%` }))
          );
        })
      ),
      share !== null && share !== undefined
        ? el(
            "div",
            { class: "sim-talk" },
            el("span", { class: "card-sub", text: `Talk time: rep ${Math.round(share * 100)}% · customer ${100 - Math.round(share * 100)}%` }),
            el(
              "div",
              { class: "talk-bar", "aria-hidden": "true" },
              el("span", { style: `flex:${share} 1 0;background:var(--accent)` }),
              el("span", { style: `flex:${1 - share} 1 0;background:var(--customer)` })
            )
          )
        : null
    );

    const actions = (snap && snap.actions) || [];
    r.actions.hidden = !!final;
    r.actions.replaceChildren(
      el("h3", { id: "sim-actions-title", class: "group-title", text: "Next best actions" }),
      actions.length
        ? el(
            "ol",
            { class: "sim-action-list" },
            ...actions.map((a) =>
              el(
                "li",
                { class: `sim-action prio-${a.priority}` },
                S.iconTile(ACTION_TONE[a.kind] || "accent", ACTION_ICON[a.kind] || "bolt"),
                el(
                  "div",
                  {},
                  el("p", { class: "strong", text: a.title }),
                  el("p", { class: "card-sub", text: a.text }),
                  a.quote ? el("blockquote", { text: `${a.source}: ${a.quote}` }) : null
                )
              )
            )
          )
        : muted(live() ? "Nothing urgent. Keep going." : "No actions.")
    );

    const events = ((snap && snap.events) || []).slice(-30).reverse();
    r.feed.replaceChildren(
      el("h3", { id: "sim-feed-title", class: "group-title", text: "Noticed" }),
      events.length
        ? el(
            "ol",
            { class: "sim-feed-list" },
            ...events.map((e) => {
              const [cls, label] = EVENT_LABEL[e.kind] || ["neutral", e.kind];
              let tail = null;
              if (e.kind === "claim") tail = el("span", { class: "card-sub", text: e.note || "" });
              if (e.kind === "commitment") {
                const due = e.due || {};
                tail = el("span", { class: "card-sub", text: due.status === "exact" ? `due ${due.date}${due.time ? ` ${due.time}` : ""}` : e.due_text ? `${quoted(e.due_text)}: no fixed day` : "no day said" });
              }
              if (e.kind === "objection") tail = el("span", { class: "card-sub", text: e.handled === true ? "answered with a question" : e.handled === false ? "answered without asking" : "waiting for the rep" });
              return el(
                "li",
                { class: "sim-feed-item" },
                el("span", { class: "card-sub", text: mmss(e.start) }),
                pill(cls, label),
                el("span", { class: "sim-feed-text", text: e.text }),
                tail
              );
            })
          )
        : muted("Questions, objections, claims and promises show up here as they are said.")
    );
  }

  function gauge(value) {
    const pct = value === null || value === undefined ? 0 : value;
    const tone = value === null || value === undefined ? "neutral" : pct >= 75 ? "ok" : pct >= 50 ? "warn" : "bad";
    return el(
      "div",
      { class: `gauge tone-${tone}`, style: `--pct:${pct}`, role: "img", "aria-label": value === null || value === undefined ? "No score yet" : `Score ${pct} out of 100` },
      el("span", { class: "gauge-num", text: value === null || value === undefined ? "–" : String(pct) }),
      el("small", { text: "/100" })
    );
  }

  // ---------- after the call: next actions from the filed report ----------

  async function loadReport() {
    const id = sim.room && sim.room.call_id;
    if (!id) return;
    const call = S.calls().find((c) => c.id === id);
    if (call && call.status !== "done") return; // the "call" event brings us back when it is
    sim.reportFor = id;
    try {
      const view = await api(`/api/calls/${enc(id)}`);
      if (view.status !== "done") {
        sim.reportFor = null;
        return;
      }
      sim.report = view.report;
      sim.reportCall = view;
      dirty("after", "flow");
    } catch (e) {
      sim.reportFor = null;
      setError(e.message);
    }
  }

  function renderAfter() {
    const r = sim.refs;
    const room = sim.room;
    if (!room || !["saving", "saved", "failed"].includes(room.state)) {
      r.after.hidden = true;
      return;
    }
    r.after.hidden = false;
    const call = room.call_id ? S.calls().find((c) => c.id === room.call_id) : null;
    const head = el("h2", { id: "sim-after-title", class: "section-title", text: "After the call" });
    if (room.state === "saving") {
      r.after.replaceChildren(
        head,
        el("p", { text: sim.uploadFrac !== null ? `Uploading the recording… ${Math.round(sim.uploadFrac * 100)}%` : "Saving the call and finishing the live transcript…" }),
        el("progress", { max: "1", value: String(sim.uploadFrac || 0), "aria-label": "Upload progress" })
      );
      return;
    }
    if (room.state === "failed") {
      r.after.replaceChildren(head, el("p", { class: "form-error", text: room.error || "The call could not be saved." }));
      return;
    }
    if (!sim.report || !call || call.status !== "done") {
      const pct = call ? Math.round((call.progress || 0) * 100) : 0;
      r.after.replaceChildren(
        head,
        el("p", { text: call && call.status === "error" ? "The analysis failed. Open the call to see why and retry." : `Analysing the call: ${call ? S.stageText(call.stage) : "queued"} · ${pct}%` }),
        el("progress", { max: "1", value: String(call ? call.progress || 0 : 0), "aria-label": "Analysis progress" }),
        el("a", { class: "button small", href: `#call/${enc(room.call_id)}`, text: "Open the call" })
      );
      return;
    }
    const rep = sim.report;
    const meta = rep.meta || {};
    const drafts = rep.drafts || [];
    const byCommit = new Map(rep.commitments.map((c) => [c.id, c]));
    const roles = new Map(rep.transcript.speakers.map((s) => [s.id, s.role]));
    const done = compact([
      sim.reportCall && sim.reportCall.audio_file ? "Recording saved (rep left, customer right)" : null,
      `Transcript filed: ${plural(rep.transcript.segments.length, "line", "lines")}`,
      `Analysed by ${meta.analyzer || "Sel"}`,
      `${plural(rep.findings.filter((f) => f.kind !== "coaching").length, "question or objection", "questions and objections")}, ${plural(rep.claims.length, "claim", "claims")} checked`,
      drafts.length ? `${plural(drafts.length, "follow-up draft", "follow-up drafts")} written, due dates worked out from what was said` : "No follow-ups to draft",
    ]);
    const waiting = drafts.map((d) => {
      const c = byCommit.get(d.commitment_id);
      const status = S.DRAFT_STATUS[d.status] || { cls: "neutral", label: d.status };
      const action = (c && c.action) || "follow-up";
      const what = d.kind === "email" ? `Email: ${d.fields.subject || action}` : `Calendar: ${d.fields.title || action}`;
      const when = d.kind === "email" ? d.fields.send_by : [d.fields.date, d.fields.time].filter(Boolean).join(" ");
      return el(
        "li",
        { class: "next-item" },
        S.iconTile(d.status === "needs_input" ? "warn" : d.status === "approved" ? "ok" : "info", d.kind === "email" ? "mail" : "calendar"),
        el(
          "div",
          {},
          el("p", { class: "strong", text: what }),
          el("p", { class: "card-sub", text: d.status === "needs_input" ? `Fill in: ${d.missing.join(", ")}${c && c.due && c.due.reason ? ` (${c.due.reason})` : ""}` : `${when ? `By ${when}. ` : ""}Approve it, then send it yourself.` })
        ),
        pill(status.cls, status.label)
      );
    });
    const tracked = rep.commitments
      .filter((c) => roles.get(c.owner) !== "rep")
      .map((c) =>
        el(
          "li",
          { class: "next-item" },
          S.iconTile("neutral", "user"),
          el("div", {}, el("p", { class: "strong", text: `Customer: ${c.action}` }), el("p", { class: "card-sub", text: c.due && c.due.status === "exact" ? `Expected ${c.due.date}` : "No day fixed" })),
          pill("neutral", "Tracked")
        )
      );
    const coaching = rep.findings.filter((f) => f.kind === "coaching").slice(0, 3);
    r.after.replaceChildren(
      head,
      el("div", { class: "after-grid" },
        el(
          "div",
          {},
          el("h3", { class: "group-title", text: "Done by Sel" }),
          el("ul", { class: "done-list" }, ...done.map((t) => el("li", {}, icon("check", 14), el("span", { text: t }))))
        ),
        el(
          "div",
          {},
          el("h3", { class: "group-title", text: "Waiting for you" }),
          waiting.length || tracked.length ? el("ul", { class: "next-list" }, ...waiting, ...tracked) : muted("Nothing to send: no promises were made on this call."),
          coaching.length
            ? el("div", {}, el("h3", { class: "group-title", text: "Try next time" }), el("ul", { class: "coach-list" }, ...coaching.map((f) => el("li", {}, el("strong", { text: `${f.summary} ` }), f.suggestion || ""))))
            : null
        )
      ),
      el("div", { class: "row" }, el("a", { class: "button primary", href: `#call/${enc(room.call_id)}`, text: "Open the full review" })),
      el("p", { class: "fine" }, icon("lock", 15), el("span", { text: "Sel never sends email or creates events. Drafts wait for your approval." }))
    );
  }

  // ---------- actions ----------

  async function post(path, json) {
    return api(`/api/sim/rooms/${enc(sim.session.code)}/${path}`, { method: "POST", json: { client: sim.session.client, ...(json || {}) } });
  }

  async function startCall() {
    audioCtx(); // created inside the click, so the browser lets it play
    try {
      applyRoom(await post("start"));
    } catch (e) {
      setError(e.message);
    }
  }

  async function hangUp() {
    try {
      applyRoom(await post("end"));
    } catch (e) {
      setError(e.message);
    }
  }

  async function leave() {
    try {
      await post("leave");
    } catch (_) {
      /* leaving anyway */
    }
    resetCall();
    saveSession(null);
    navigate("#sim");
  }

  async function postLine(text, start, end, source) {
    try {
      const line = await post("lines", { text, start, end, source });
      addLine(line);
    } catch (e) {
      setError(e.message);
    }
  }

  function onLive() {
    sim.clock = { at: performance.now(), elapsed: sim.room.elapsed || 0 };
    if (myRole() === "rep") startRecording();
    startPcm();
  }

  async function onEnded() {
    stopPcm();
    if (myRole() !== "rep" || sim.finishing || ["saved", "saving"].includes(sim.room.state)) return;
    sim.finishing = true;
    const blob = await stopRecording();
    sim.lastBlob = blob;
    await finish(blob);
    sim.finishing = false;
  }

  async function finish(blob) {
    const base = `/api/sim/rooms/${enc(sim.session.code)}/finish?client=${enc(sim.session.client)}`;
    try {
      let view;
      if (blob && blob.size) {
        const ext = /ogg/.test(blob.type) ? "ogg" : /mp4/.test(blob.type) ? "m4a" : "webm";
        sim.uploadFrac = 0;
        dirty("after", "flow");
        view = await upload(`${base}&filename=recording.${ext}`, blob, blob.type || "audio/webm", (f) => {
          sim.uploadFrac = f;
          dirty("after", "flow");
        });
      } else {
        view = await api(base, { method: "POST" });
      }
      sim.uploadFrac = null;
      sim.lastBlob = null;
      applyRoom(view);
    } catch (e) {
      sim.uploadFrac = null;
      setError(e.message);
      dirty("room");
    }
  }

  // ---------- microphone ----------

  async function enableMic() {
    if (!canMic()) return;
    sim.mic = "asking";
    dirty("controls");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      sim.localStream = stream;
      sim.mic = "on";
      const track = stream.getAudioTracks()[0];
      if (sim.transceiver) await sim.transceiver.sender.replaceTrack(track);
      audioCtx();
      sim.meters.local = analyser(stream);
      connectRecorderSources();
      if (live()) startPcm();
      startMeters();
      post("mic", { mic: true }).catch(() => {});
    } catch (e) {
      sim.mic = e && e.name === "NotFoundError" ? "unavailable" : "denied";
    }
    dirty("controls", "people");
  }

  function toggleMute() {
    const track = sim.localStream && sim.localStream.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    sim.mic = track.enabled ? "on" : "muted";
    post("mic", { mic: track.enabled }).catch(() => {});
    dirty("controls");
  }

  function audioCtx() {
    if (!sim.ctx) {
      const C = window.AudioContext || window.webkitAudioContext;
      if (!C) return null;
      sim.ctx = new C();
    }
    if (sim.ctx.state === "suspended") sim.ctx.resume().catch(() => {});
    return sim.ctx;
  }

  function analyser(stream) {
    const ctx = audioCtx();
    if (!ctx) return null;
    const a = ctx.createAnalyser();
    a.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(a);
    return { node: a, buf: new Uint8Array(a.fftSize) };
  }

  // Speaking indicators, about 15 times a second.
  function startMeters() {
    if (sim.meterRaf) return;
    let last = 0;
    const step = (t) => {
      sim.meterRaf = requestAnimationFrame(step);
      if (t - last < 66) return;
      last = t;
      for (const [key, elKey] of [["local", "localEl"], ["remote", "remoteEl"]]) {
        const m = sim.meters[key];
        const node = sim.meters[elKey];
        if (!m || !node || !node.isConnected) continue;
        m.node.getByteTimeDomainData(m.buf);
        let peak = 0;
        for (let i = 0; i < m.buf.length; i += 4) peak = Math.max(peak, Math.abs(m.buf[i] - 128));
        node.firstChild.style.transform = `scaleX(${Math.min(1, peak / 64).toFixed(2)})`;
      }
    };
    sim.meterRaf = requestAnimationFrame(step);
  }

  // ---------- WebRTC (rep offers, customer answers) ----------

  function signal(data) {
    return post("signal", { data }).catch(() => {});
  }

  function newPeer() {
    const pc = new RTCPeerConnection({ iceServers: (sim.room && sim.room.ice_servers) || [] });
    pc.addEventListener("icecandidate", (e) => {
      if (e.candidate) signal({ type: "candidate", candidate: e.candidate.toJSON() });
    });
    pc.addEventListener("track", (e) => attachRemote(e.streams[0] || new MediaStream([e.track])));
    pc.addEventListener("connectionstatechange", () => {
      if (sim.pc !== pc) return;
      if (pc.connectionState === "failed" && myRole() === "rep" && other() && other().online) setTimeout(() => sim.pc === pc && makeOffer(), 1000);
      dirty("controls");
    });
    sim.pc = pc;
    sim.pendingIce = [];
    return pc;
  }

  function closePeer() {
    if (sim.pc) {
      try {
        sim.pc.close();
      } catch (_) {
        /* already closed */
      }
    }
    sim.pc = null;
    sim.transceiver = null;
  }

  async function makeOffer() {
    if (typeof RTCPeerConnection === "undefined") return;
    closePeer();
    const pc = newPeer();
    const tr = pc.addTransceiver("audio", { direction: "sendrecv" });
    sim.transceiver = tr;
    const track = sim.localStream && sim.localStream.getAudioTracks()[0];
    if (track) await tr.sender.replaceTrack(track);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    signal({ type: "offer", sdp: pc.localDescription.sdp });
  }

  async function onSignal(msg) {
    if (!sim.session || typeof RTCPeerConnection === "undefined") return;
    const data = msg.data || {};
    try {
      if (data.type === "offer" && myRole() !== "rep") {
        closePeer();
        const pc = newPeer();
        await pc.setRemoteDescription({ type: "offer", sdp: data.sdp });
        const tr = pc.getTransceivers()[0];
        if (tr) {
          tr.direction = "sendrecv";
          sim.transceiver = tr;
          const track = sim.localStream && sim.localStream.getAudioTracks()[0];
          if (track) await tr.sender.replaceTrack(track);
        }
        await pc.setLocalDescription(await pc.createAnswer());
        signal({ type: "answer", sdp: pc.localDescription.sdp });
        await flushIce();
      } else if (data.type === "answer" && sim.pc && myRole() === "rep") {
        await sim.pc.setRemoteDescription({ type: "answer", sdp: data.sdp });
        await flushIce();
      } else if (data.type === "candidate" && data.candidate) {
        if (sim.pc && sim.pc.remoteDescription) await sim.pc.addIceCandidate(data.candidate);
        else sim.pendingIce.push(data.candidate);
      }
    } catch (e) {
      console.warn("WebRTC signalling:", e); // eslint-disable-line no-console
    }
    dirty("controls");
  }

  async function flushIce() {
    const items = sim.pendingIce.splice(0);
    for (const c of items) {
      try {
        await sim.pc.addIceCandidate(c);
      } catch (_) {
        /* stale candidate */
      }
    }
  }

  function attachRemote(stream) {
    sim.remoteStream = stream;
    if (!sim.remoteAudio) {
      // Playing the stream in a media element is also what lets Chrome feed it to Web Audio.
      sim.remoteAudio = el("audio", { autoplay: true, class: "sr-only", "aria-hidden": "true" });
      document.body.append(sim.remoteAudio);
    }
    sim.remoteAudio.srcObject = stream;
    const p = sim.remoteAudio.play();
    if (p && p.catch) p.catch(() => {});
    if (audioCtx()) {
      sim.meters.remote = analyser(stream);
      sim.remoteRec = null;
      connectRecorderSources();
      startMeters();
    }
  }

  // ---------- recording (rep's browser): stereo, rep left, customer right ----------

  function startRecording() {
    if (sim.recorder || typeof MediaRecorder === "undefined") return;
    const ctx = audioCtx();
    if (!ctx) return;
    const dest = ctx.createMediaStreamDestination();
    dest.channelCount = 2;
    const merger = ctx.createChannelMerger(2);
    merger.connect(dest);
    // A silent source keeps the file running even before anyone speaks.
    const silence = ctx.createConstantSource();
    silence.offset.value = 0;
    silence.connect(merger, 0, 0);
    silence.start();
    sim.merger = merger;
    sim.localRec = null;
    sim.remoteRec = null;
    connectRecorderSources();
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    const rec = new MediaRecorder(dest.stream, mime ? { mimeType: mime, audioBitsPerSecond: 96000 } : undefined);
    sim.chunks = [];
    rec.addEventListener("dataavailable", (e) => {
      if (e.data && e.data.size) sim.chunks.push(e.data);
    });
    rec.start(1000);
    sim.recorder = rec;
    sim.recMime = rec.mimeType || mime || "audio/webm";
  }

  function connectRecorderSources() {
    if (!sim.merger || !sim.ctx) return;
    if (sim.localStream && !sim.localRec) {
      sim.localRec = sim.ctx.createMediaStreamSource(sim.localStream);
      sim.localRec.connect(sim.merger, 0, 0);
    }
    if (sim.remoteStream && !sim.remoteRec) {
      sim.remoteRec = sim.ctx.createMediaStreamSource(sim.remoteStream);
      sim.remoteRec.connect(sim.merger, 0, 1);
    }
  }

  function stopRecording() {
    const rec = sim.recorder;
    sim.recorder = null;
    if (!rec || rec.state === "inactive") return Promise.resolve(null);
    return new Promise((resolve) => {
      rec.addEventListener("stop", () => resolve(new Blob(sim.chunks, { type: sim.recMime })), { once: true });
      rec.stop();
    });
  }

  // ---------- live speech: own microphone → 16 kHz PCM → Sel ----------

  async function startPcm() {
    if (sim.pcm || !speechLive() || !sim.localStream || !live()) return;
    const ctx = audioCtx();
    if (!ctx || !ctx.audioWorklet) return;
    try {
      if (!sim.workletReady) sim.workletReady = ctx.audioWorklet.addModule(S.worklet);
      await sim.workletReady;
    } catch (e) {
      sim.workletReady = null;
      setError(`Live transcription could not start: ${e.message}`);
      return;
    }
    if (sim.pcm || !live()) return;
    const node = new AudioWorkletNode(ctx, "sel-pcm", { processorOptions: { chunk: 8000 } });
    const src = ctx.createMediaStreamSource(sim.localStream);
    const mute = ctx.createGain();
    mute.gain.value = 0;
    src.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    sim.pcmBase = { ctx: ctx.currentTime, call: callNow() };
    node.port.onmessage = (e) => {
      const t = sim.pcmBase.call + (e.data.time - sim.pcmBase.ctx);
      sim.pcmQueue.push({ pcm: e.data.pcm, t });
      if (sim.pcmQueue.length > 20) sim.pcmQueue.shift(); // falling behind: the server sees the gap
      pumpPcm();
    };
    sim.pcm = { node, src, mute };
  }

  function stopPcm() {
    if (!sim.pcm) return;
    sim.pcm.node.port.postMessage("flush");
    const { node, src, mute } = sim.pcm;
    setTimeout(() => {
      try {
        src.disconnect();
        node.disconnect();
        mute.disconnect();
      } catch (_) {
        /* already disconnected */
      }
    }, 300);
    sim.pcm = null;
  }

  async function pumpPcm() {
    if (sim.pcmBusy) return;
    sim.pcmBusy = true;
    while (sim.pcmQueue.length && sim.session) {
      const { pcm, t } = sim.pcmQueue.shift();
      try {
        const res = await fetch(`/api/sim/rooms/${enc(sim.session.code)}/audio?client=${enc(sim.session.client)}&t=${t.toFixed(3)}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: pcm,
        });
        if (res.status === 409 || res.status === 404) {
          sim.pcmQueue = [];
          break;
        }
      } catch (_) {
        break;
      }
    }
    sim.pcmBusy = false;
  }

  // ---------- teardown ----------

  function resetCall() {
    stopPcm();
    if (sim.recorder && sim.recorder.state !== "inactive") {
      try {
        sim.recorder.stop();
      } catch (_) {
        /* stopped */
      }
    }
    sim.recorder = null;
    closePeer();
    if (sim.localStream) sim.localStream.getTracks().forEach((t) => t.stop());
    sim.localStream = null;
    sim.remoteStream = null;
    if (sim.remoteAudio) sim.remoteAudio.srcObject = null;
    cancelAnimationFrame(sim.meterRaf);
    sim.meterRaf = 0;
    sim.meters = { local: null, remote: null };
    sim.merger = null;
    sim.localRec = null;
    sim.remoteRec = null;
    sim.mic = "off";
    sim.room = null;
    sim.lines = [];
    sim.lineIds = new Set();
    sim.pendingLines = [];
    sim.live = null;
    sim.report = null;
    sim.reportFor = null;
    sim.reportCall = null;
    sim.uploadFrac = null;
    sim.finishing = false;
    clearInterval(sim.timer);
    document.getElementById("open-sim")?.classList.remove("is-live");
  }

  window.addEventListener("beforeunload", (e) => {
    if (live()) {
      e.preventDefault();
      e.returnValue = "Leaving now stops the recording.";
    }
  });

  // ---------- events from the tab's stream ----------

  S.on("hello", (data) => {
    if (data.room && sim.session && data.room.code === sim.session.code) applyRoom(data.room);
    dirty("rooms", "caps");
  });
  S.on("room", (v) => {
    if (sim.session && v.code === sim.session.code) applyRoom({ ...v, you: myRole() });
  });
  S.on("line", (line) => {
    if (!line.room || (sim.session && line.room === sim.session.code)) addLine(line);
  });
  S.on("live", (snap) => {
    if (!mine()) return;
    sim.live = snap;
    dirty("live", "flow");
  });
  S.on("signal", onSignal);
  S.on("rooms", () => dirty("rooms"));
  S.on("packs", (packs) => {
    const sel = sim.lobby && sim.lobby.root.isConnected && sim.lobby.root.querySelector("#sim-pack");
    if (!sel) return;
    const keep = sel.value || profile().pack;
    sel.replaceChildren(...packs.map((pk) => el("option", { value: pk.id, text: pk.name })));
    if (keep && packs.some((pk) => pk.id === keep)) sel.value = keep;
    const btn = sim.lobby.root.querySelector(".sim-start button[type=submit]");
    if (btn) btn.disabled = !packs.length;
  });
  S.on("call", (summary) => {
    if (!sim.room || summary.id !== sim.room.call_id) return;
    if (summary.status === "done" && sim.reportFor !== summary.id) loadReport();
    dirty("after", "flow");
  });

  // A reloaded tab picks its call back up.
  if (sim.session && S.view() !== "sim") fetchRoom(sim.session.code);

  S.sim = { render };
})();
