/* Sel review screen. Vanilla JS, no build step, no external resources.
 *
 * Every string from the server (transcript text, quotes, drafts, document
 * text) is inserted with textContent through el(). Nothing is parsed as HTML.
 */
(function () {
  "use strict";

  const MAX_UPLOAD = 500 * 1024 * 1024;
  const POLL_ACTIVE_MS = 2000;
  const POLL_IDLE_MS = 10000;
  const HEALTH_MS = 60000;

  const KIND_LABEL = { question: "Customer questions", objection: "Objections", coaching: "Coaching" };
  const KIND_TAG = {
    question: { cls: "info", label: "Question", icon: "question" },
    objection: { cls: "warn", label: "Objection", icon: "alert" },
    coaching: { cls: "violet", label: "Coaching", icon: "target" },
  };
  const ROLE_LABEL = { rep: "Rep", customer: "Customer", other: "Other" };
  const VERDICT = {
    contradicted: { cls: "bad", label: "Contradicted", icon: "x", rank: 0 },
    unverified: { cls: "neutral", label: "Unverified", icon: "question", rank: 1 },
    supported: { cls: "ok", label: "Supported", icon: "check", rank: 2 },
  };
  const CHANNEL_ICON = { email: "mail", call: "phone", meeting: "calendar", visit: "pin", other: "user" };
  const STEPS = ["Transcribe", "Analyse", "Review", "Approve drafts"];

  // Stroke icons, 24×24. Constant path data only; nothing from the server.
  const ICONS = {
    play: ["M7 4.5v15l13-7.5z"],
    phone: ["M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a1 1 0 0 1-1 1A16 16 0 0 1 4 5a1 1 0 0 1 1-1z"],
    target: ["M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z", "M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z"],
    alert: ["M12 4l9 16H3z", "M12 10v4M12 17v.01"],
    question: ["M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5v.01"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    x: ["M7 7l10 10M17 7L7 17"],
    mail: ["M5.5 5.5h13a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z", "M4 7l8 6 8-6"],
    calendar: ["M6 5h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "M4 10h16M9 3v4M15 3v4"],
    pin: ["M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z", "M12 7a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z"],
    user: ["M12 4.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z", "M5 20a7 7 0 0 1 14 0"],
    ban: ["M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z", "M6.5 6.5l11 11"],
    lines: ["M5 6h14M5 10h14M5 14h9M5 18h6"],
    doc: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
    lock: ["M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z", "M8 11V7a4 4 0 0 1 8 0v4"],
  };
  const FILLED_ICONS = new Set(["play"]);
  const DRAFT_STATUS = {
    needs_input: { cls: "warn", label: "Needs input" },
    ready_for_approval: { cls: "info", label: "Ready for approval" },
    approved: { cls: "ok", label: "Approved" },
  };
  const CALL_STATUS = {
    queued: { cls: "neutral", label: "Queued" },
    running: { cls: "info", label: "Running" },
    done: { cls: "ok", label: "Done" },
    error: { cls: "bad", label: "Failed" },
  };
  const DRAFT_FIELDS = {
    email: [["to", "To", "email"], ["send_by", "Send by", "date"], ["subject", "Subject", "text"], ["body", "Body", "textarea"]],
    calendar: [["title", "Title", "text"], ["date", "Date", "date"], ["time", "Time", "time"], ["body", "Body", "textarea"]],
  };
  const FIELD_LABEL = { to: "To", send_by: "Send by", subject: "Subject", body: "Body", title: "Title", date: "Date", time: "Time" };
  const TABS = [["coaching", "Coaching"], ["claims", "Claims"], ["followups", "Follow-ups"], ["refused", "Refused"]];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const REFUSED_EXPLAINER =
    "The model proposed these, but Sel could not find the cited words in the call or the cited passage in your documents, so they are not shown as findings.";
  const reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  // ---------- state ----------

  const state = {
    packList: [],
    packs: new Map(),
    packError: null,
    calls: [],
    listKey: null,
    view: "empty", // "empty" | "new" | "call"
    callId: null,
    call: null, // full view from GET /api/calls/{id}
    mainError: null,
    tab: "coaching",
    evidence: new Set(), // segment ids highlighted by a card
    focusItem: null, // id of the card whose evidence is highlighted
    current: null, // segment id under the playhead
    editing: null, // segment id in inline edit mode
    notice: null, // {kind, text} above the transcript
    draftEdits: {}, // unsaved draft form values, by draft id
    draftMsg: {}, // {kind, text} under a draft, by draft id
    openDocs: new Set(), // "pack|source" keys with the document panel open
    docs: new Map(), // "pack|source" -> {text} or {error}
    audio: null,
    mountKey: null,
    refs: null,
    segRows: new Map(),
    mapBlocks: new Map(), // segment id -> call-map block
    health: null,
    pollTimer: null,
  };

  // ---------- helpers ----------

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  const $ = (sel) => document.querySelector(sel);
  const enc = encodeURIComponent;
  const quoted = (s) => `“${s}”`;

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "on") {
          for (const [type, fn] of Object.entries(value)) node.addEventListener(type, fn);
        } else if (key === "dataset") {
          for (const [dk, dv] of Object.entries(value)) node.dataset[dk] = dv;
        } else if (["value", "checked", "disabled", "hidden", "readOnly"].includes(key)) {
          node[key] = value;
        } else {
          node.setAttribute(key, value === true ? "" : String(value));
        }
      }
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  // Element.append() and replaceChildren() turn null into the text "null", so
  // conditional children are filtered out before they reach the DOM.
  const compact = (items) => items.filter((x) => x !== null && x !== undefined && x !== false);
  const muted = (text) => el("p", { class: "muted", text });
  const pill = (cls, label) => el("span", { class: `pill pill-${cls}`, text: label });
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function icon(name, size = 18) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    const filled = FILLED_ICONS.has(name);
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("fill", filled ? "currentColor" : "none");
    if (!filled) {
      svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "1.8");
      svg.setAttribute("stroke-linecap", "round");
      svg.setAttribute("stroke-linejoin", "round");
    }
    for (const d of ICONS[name] || []) {
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      svg.append(path);
    }
    return svg;
  }

  const iconTile = (tone, name) => el("span", { class: `icon-tile tone-${tone}` }, icon(name));
  const cardTag = (cls, label) => el("span", { class: `pill pill-${cls} card-tag`, text: label });

  function cardHead(tone, iconName, summary, sub) {
    return el(
      "div",
      { class: "card-head" },
      iconTile(tone, iconName),
      el(
        "div",
        { class: "card-head-text" },
        el("p", { class: "summary", text: summary }),
        sub ? el("p", { class: "card-sub", text: sub }) : null
      )
    );
  }

  function mmss(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
  }

  function formatStarted(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/.exec(iso || "");
    if (!m) return iso || "";
    const [, y, mo, d, hh, mi, off] = m;
    const zone = !off || off === "Z" ? "UTC" : `UTC${off}`;
    return `${Number(d)} ${MONTHS[Number(mo) - 1]} ${y}, ${hh}:${mi} (${zone})`;
  }

  function normalizeOffset(raw) {
    const s = String(raw || "").trim();
    if (/^z$/i.test(s)) return "+00:00";
    const m = /^([+-])(\d{2}):?(\d{2})$/.exec(s);
    return m ? `${m[1]}${m[2]}:${m[3]}` : null;
  }

  const isActive = (status) => status === "queued" || status === "running";

  function stageText(stage) {
    if (!stage) return "Working";
    if (stage === "waiting") return "Waiting in the queue";
    if (stage === "starting") return "Starting";
    const s = String(stage).replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function scrollRow(node, block) {
    node.scrollIntoView({ block, behavior: reduceMotion ? "auto" : "smooth" });
  }

  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  async function api(path, { method = "GET", json, body, contentType } = {}) {
    const init = { method, headers: {}, cache: "no-store" };
    if (json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    } else if (body !== undefined) {
      init.headers["Content-Type"] = contentType || "application/octet-stream";
      init.body = body;
    }
    let res;
    let raw = "";
    try {
      res = await fetch(path, init);
      raw = await res.text();
    } catch (_) {
      throw new ApiError("Cannot reach the Sel server. Is it still running?", 0);
    }
    let data = null;
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch (_) {
        data = null;
      }
    }
    if (!res.ok) throw new ApiError((data && data.error) || `Request failed with HTTP ${res.status}`, res.status);
    return data;
  }

  // ---------- lookups ----------

  function packOf(id) {
    return state.packs.get(id) || null;
  }

  function packName(id) {
    const p = packOf(id);
    return p ? p.name : id || "";
  }

  function criterionTitle(packId, criterionId) {
    const p = packOf(packId);
    const c = p && p.scorecard.find((x) => x.id === criterionId);
    return c ? c.title : criterionId;
  }

  function segmentById(id) {
    return state.call.report.transcript.segments.find((s) => s.id === id) || null;
  }

  function speakerLabel(sp) {
    if (!sp) return "Unknown";
    return sp.name || ROLE_LABEL[sp.role] || "Speaker";
  }

  function speakerOf(segId) {
    const seg = segId ? segmentById(segId) : null;
    if (!seg) return null;
    return state.call.report.transcript.speakers.find((s) => s.id === seg.speaker) || null;
  }

  // "Said by Asha at 00:28", from the first segment a card cites.
  function saidBy(segmentIds, verb = "Said by") {
    const first = segmentIds && segmentIds.length ? segmentById(segmentIds[0]) : null;
    if (!first) return null;
    return `${verb} ${speakerLabel(speakerOf(first.id))} at ${mmss(first.start)}`;
  }

  function isDismissed(itemId) {
    return !!(state.call && state.call.review && state.call.review.dismissed.includes(itemId));
  }

  function sortDismissedLast(items, idOf) {
    return items
      .map((x, i) => ({ x, i, d: isDismissed(idOf(x)) ? 1 : 0 }))
      .sort((a, b) => a.d - b.d || a.i - b.i)
      .map((o) => o.x);
  }

  // ---------- routing and polling ----------

  function navigate(hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  }

  function resetCallUi() {
    state.tab = "coaching";
    state.evidence = new Set();
    state.focusItem = null;
    state.mapBlocks = new Map();
    state.current = null;
    state.editing = null;
    state.notice = null;
    state.draftEdits = {};
    state.draftMsg = {};
    state.openDocs = new Set();
    state.audio = null;
    state.segRows = new Map();
    state.mainError = null;
    state.refs = null;
    state.mountKey = null;
  }

  async function route() {
    const hash = decodeURIComponent(location.hash.replace(/^#/, ""));
    const m = /^call\/([a-z0-9-]{1,64})$/.exec(hash);
    if (hash === "new") {
      resetCallUi();
      state.view = "new";
      state.callId = null;
      state.call = null;
      renderList();
      renderMain();
      return;
    }
    if (m) {
      const id = m[1];
      if (state.view !== "call" || state.callId !== id) {
        resetCallUi();
        state.view = "call";
        state.callId = id;
        state.call = null;
      }
      renderList();
      renderMain();
      try {
        await loadCall(id);
      } catch (e) {
        if (state.callId === id) {
          state.mainError = e.message;
          renderMain();
        }
      }
      return;
    }
    resetCallUi();
    state.view = "empty";
    state.callId = null;
    state.call = null;
    renderList();
    renderMain();
  }

  async function loadCall(id) {
    const data = await api(`/api/calls/${enc(id)}`);
    if (state.view !== "call" || state.callId !== id) return;
    state.call = data;
    state.mainError = null;
    renderMain();
  }

  async function refreshList() {
    const list = await api("/api/calls");
    const key = JSON.stringify(list);
    if (key !== state.listKey) {
      state.listKey = key;
      state.calls = list;
      renderList();
    }
  }

  function setListError(message) {
    const node = $("#list-error");
    node.textContent = message || "";
    node.hidden = !message;
  }

  function schedule(ms) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(tick, ms);
  }

  async function tick() {
    try {
      await refreshList();
      setListError(null);
    } catch (e) {
      setListError(e.message);
    }
    if (state.view === "call" && state.callId) {
      const listed = state.calls.find((c) => c.id === state.callId);
      const shown = state.call && state.call.id === state.callId ? state.call : null;
      if (listed && (!shown || shown.status !== listed.status || isActive(listed.status))) {
        try {
          await loadCall(state.callId);
        } catch (e) {
          setListError(e.message);
        }
      }
    }
    schedule(state.calls.some((c) => isActive(c.status)) ? POLL_ACTIVE_MS : POLL_IDLE_MS);
  }

  function kickPoll() {
    schedule(0);
  }

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
    } catch (e) {
      state.health = { error: e.message };
    }
    renderHealth();
  }

  function chip(ok, label, detail) {
    const tail = ok ? "ready" : `not ready${detail ? ` (${detail})` : ""}`;
    return el(
      "span",
      { class: `chip ${ok ? "chip-ok" : "chip-bad"}` },
      el("span", { class: "dot", "aria-hidden": "true" }),
      el("span", { text: `${label}: ${tail}` })
    );
  }

  function renderHealth() {
    const node = $("#health");
    const h = state.health;
    if (!h) {
      node.textContent = "Checking models…";
      return;
    }
    if (h.error) {
      node.replaceChildren(chip(false, "Server", h.error));
      return;
    }
    const llm = h.llm || {};
    const asr = h.asr || {};
    node.replaceChildren(
      chip(!!asr.ok, `Speech ${asr.model || "(not set)"}`, asr.error),
      chip(!!llm.ok, `Analysis ${llm.name || "(not set)"}`, llm.error)
    );
  }

  // ---------- top bar: review progress ----------

  // Which of Transcribe / Analyse / Review / Approve drafts are done, worked
  // out from the call's status and its drafts.
  function stepState(call) {
    if (!call) return null;
    if (call.status === "done" && call.report) {
      const drafts = call.report.drafts || [];
      const allApproved = drafts.length > 0 && drafts.every((d) => d.status === "approved");
      return allApproved ? { done: 4, current: null } : { done: 2, current: 2 };
    }
    if (call.status === "error") return { done: 0, current: null };
    const stage = String(call.stage || "");
    const analysing = /analys|checking/i.test(stage);
    return { done: analysing ? 1 : 0, current: analysing ? 1 : 0 };
  }

  function renderSteps() {
    const list = $("#steps");
    const bar = list.closest(".topbar");
    const call = state.view === "call" && state.call && state.call.id === state.callId ? state.call : null;
    const s = stepState(call);
    list.hidden = !s;
    bar.hidden = !s;
    if (!s) return;
    const items = [];
    STEPS.forEach((label, i) => {
      if (i > 0) items.push(el("li", { class: `step-line${i <= s.done ? " is-done" : ""}`, "aria-hidden": "true" }));
      const done = i < s.done;
      const current = i === s.current;
      items.push(
        el(
          "li",
          { class: `step${done ? " is-done" : ""}${current ? " is-current" : ""}`, "aria-current": current ? "step" : null },
          el("span", { class: "step-num" }, done ? icon("check", 12) : String(i + 1)),
          el("span", { text: label }),
          done ? el("span", { class: "sr-only", text: " (done)" }) : null
        )
      );
    });
    list.replaceChildren(...items);
  }

  // ---------- sidebar ----------

  function renderList() {
    const selectedId = state.view === "call" ? state.callId : null;
    $("#call-list").replaceChildren(...state.calls.map((c) => callItem(c, c.id === selectedId)));
    $("#list-empty").hidden = state.calls.length > 0;
  }

  function callItem(c, selected) {
    const status = CALL_STATUS[c.status] || { cls: "neutral", label: c.status || "Unknown" };
    const title = c.customer_name || c.original_filename || c.id;
    const pct = Math.round((c.progress || 0) * 100);
    return el(
      "li",
      { class: "call-row" },
      el(
        "a",
        {
          href: `#call/${enc(c.id)}`,
          class: `call-link${selected ? " is-selected" : ""}`,
          "aria-current": selected ? "page" : null,
        },
        icon("phone", 20),
        el("span", { class: "call-title", text: title }),
        el("span", { class: "call-sub", text: `${packName(c.pack_id)} · ${formatStarted(c.started_at)}` }),
        el(
          "span",
          { class: "call-meta" },
          pill(status.cls, status.label),
          c.source === "fixture" ? pill("neutral", "Example") : null
        ),
        isActive(c.status)
          ? el(
              "span",
              { class: "call-progress" },
              el("progress", { max: "1", value: String(c.progress || 0), "aria-label": `${title}: analysis progress` }),
              el("span", { class: "call-stage", text: `${stageText(c.stage)} · ${pct}%` })
            )
          : null
      )
    );
  }

  // ---------- main pane dispatch ----------

  function renderMain() {
    const main = $("#main");
    renderSteps();
    main.classList.toggle(
      "is-call",
      state.view === "call" && !!state.call && state.call.id === state.callId && state.call.status === "done" && !!state.call.report
    );
    if (state.view === "new") {
      renderUpload(main);
      return;
    }
    if (state.view !== "call") {
      renderWelcome(main);
      return;
    }
    if (state.mainError && !state.call) {
      state.refs = null;
      state.mountKey = null;
      main.replaceChildren(el("div", { class: "error-box", role: "alert" }, el("p", { class: "strong", text: state.mainError })));
      return;
    }
    const call = state.call;
    if (!call || call.id !== state.callId) {
      state.refs = null;
      state.mountKey = null;
      main.replaceChildren(muted("Loading call…"));
      return;
    }
    if (call.status !== "done" || !call.report) {
      state.refs = null;
      state.mountKey = null;
      state.audio = null;
      main.replaceChildren(renderProgress(call));
      return;
    }
    const key = `${call.id}|${call.audio_url || ""}`;
    if (key !== state.mountKey) mountCall(main, key);
    renderCallParts();
  }

  function renderWelcome(main) {
    main.replaceChildren(
      el(
        "section",
        { class: "welcome" },
        el("h1", { text: "Sel" }),
        el("p", { class: "lede", text: "Review recorded sales calls on this computer. Pick a call on the left, or add a new recording." }),
        el("button", { type: "button", class: "primary", text: "New call", on: { click: () => navigate("#new") } })
      )
    );
  }

  function renderProgress(call) {
    const card = el(
      "section",
      { class: "progress-card" },
      el("p", { class: "eyebrow", text: packName(call.pack_id) }),
      el("h1", { text: call.customer_name || call.original_filename || call.id }),
      el("p", { class: "meta", text: formatStarted(call.started_at) }),
      el("p", {}, statusPill(call.status))
    );
    if (call.status === "error") {
      card.append(
        el(
          "div",
          { class: "error-box", role: "alert" },
          el("p", { class: "strong", text: "Analysis failed." }),
          el("p", { class: "mono", text: call.error || "No error message was recorded." }),
          el("div", { class: "row" }, el("button", { type: "button", class: "primary", text: "Retry", on: { click: () => retryCall(call.id) } }))
        )
      );
    } else if (call.status === "done") {
      card.append(el("div", { class: "error-box", role: "alert" }, el("p", { text: "This call finished without a report." })));
    } else {
      const pct = Math.round((call.progress || 0) * 100);
      const stage = call.status === "queued" ? "Queued" : `${stageText(call.stage)} · ${pct}%`;
      card.append(
        el("p", { class: "stage", text: stage }),
        el("progress", { max: "1", value: String(call.progress || 0), "aria-label": "Analysis progress" }),
        muted("This page updates by itself. Sel analyses one call at a time on this computer.")
      );
    }
    if (state.mainError) card.append(el("p", { class: "form-error", role: "alert", text: state.mainError }));
    return card;
  }

  function statusPill(status) {
    const s = CALL_STATUS[status] || { cls: "neutral", label: status || "Unknown" };
    return pill(s.cls, s.label);
  }

  async function retryCall(id) {
    try {
      const view = await api(`/api/calls/${enc(id)}/retry`, { method: "POST" });
      if (state.callId === id) {
        state.call = view;
        state.mainError = null;
        renderMain();
      }
      await refreshList().catch(() => {});
      kickPoll();
    } catch (e) {
      state.mainError = e.message;
      renderMain();
    }
  }

  // ---------- call view ----------

  function mountCall(main, key) {
    const call = state.call;
    const head = el("header", { class: "call-head" });
    const tiles = el("div", { class: "tiles" });
    const notice = el("div", { class: "notice", role: "status", hidden: true });
    const list = el("ol", { class: "transcript", "aria-label": "Transcript" });
    const txSub = el("p", { class: "card-sub" });
    const clear = el("button", {
      type: "button",
      class: "small",
      text: "Clear highlight",
      hidden: true,
      on: {
        click: () => {
          state.evidence = new Set();
          state.focusItem = null;
          markEvidence();
          markFocused();
        },
      },
    });
    const talk = el("section", { class: "talk", "aria-labelledby": "talk-title" });
    const tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Review" });
    const panel = el("div", { class: "panel", id: "panel", role: "tabpanel" });
    const map = el("div", { class: "callmap", "aria-hidden": "true", title: "Call map: click to play from that moment" });
    map.addEventListener("click", (e) => seekFromMap(e, map));
    const player = el(
      "section",
      { class: "player", "aria-label": "Recording" },
      map,
      el(
        "div",
        { class: "legend" },
        el("span", {}, el("i", { class: "role-rep" }), "Rep"),
        el("span", {}, el("i", { class: "role-customer" }), "Customer"),
        el("span", {}, el("i", { class: "is-evidence" }), "Evidence for the selected card")
      )
    );

    if (call.audio_url) {
      const audio = el("audio", { controls: true, preload: "metadata", src: call.audio_url });
      audio.addEventListener("timeupdate", onTimeUpdate);
      state.audio = audio;
      player.append(audio);
    } else {
      state.audio = null;
      player.append(el("p", { class: "no-audio", text: "No recording for this call (example data)." }));
    }

    state.refs = { head, tiles, notice, list, txSub, clear, talk, tabs, panel, map };
    state.mountKey = key;
    state.segRows = new Map();
    state.mapBlocks = new Map();

    main.replaceChildren(
      el(
        "div",
        { class: "call-cols" },
        el(
          "div",
          { class: "col-left" },
          head,
          tiles,
          player,
          notice,
          el(
            "section",
            { class: "transcript-card", "aria-labelledby": "tx-title" },
            el(
              "div",
              { class: "section-head" },
              el(
                "div",
                { class: "section-head-main" },
                iconTile("accent", "lines"),
                el("div", {}, el("h2", { class: "section-title", id: "tx-title", text: "Transcript" }), txSub)
              ),
              clear
            ),
            list
          )
        ),
        el(
          "aside",
          { class: "col-right", "aria-label": "Review" },
          el(
            "div",
            { class: "inspector-head" },
            el("h2", { text: "Call review" }),
            el("p", { class: "card-sub", text: "Findings, claim checks and drafts" })
          ),
          talk,
          tabs,
          panel
        )
      )
    );
  }

  function renderCallParts() {
    const r = state.refs;
    if (!r || !state.call) return;
    renderHead(r.head);
    renderSummary(r.tiles);
    renderNotice(r.notice);
    renderCallMap(r.map);
    renderTranscript(r.list);
    renderTalk(r.talk);
    renderTabs(r.tabs);
    renderPanel();
  }

  // ---------- summary tiles and talk time ----------

  function renderSummary(tiles) {
    const r = state.call.report;
    const count = (kind) => r.findings.filter((f) => f.kind === kind).length;
    const verdicts = { supported: 0, contradicted: 0, unverified: 0 };
    for (const c of r.claims) if (c.verdict in verdicts) verdicts[c.verdict] += 1;
    const drafts = r.drafts || [];
    const needs = drafts.filter((d) => d.status === "needs_input").length;
    const ready = drafts.filter((d) => d.status === "ready_for_approval").length;
    const approved = drafts.filter((d) => d.status === "approved").length;

    let claimLine;
    let claimPill;
    if (!r.claims.length) {
      claimLine = "No claims checked";
      claimPill = ["neutral", "Nothing to compare"];
    } else if (verdicts.contradicted || verdicts.unverified) {
      claimLine = [
        verdicts.contradicted ? `${verdicts.contradicted} contradicted` : null,
        verdicts.unverified ? `${verdicts.unverified} unverified` : null,
      ].filter(Boolean).join(", ");
      claimPill = [verdicts.contradicted ? "bad" : "neutral", `${verdicts.supported} of ${r.claims.length} supported`];
    } else {
      claimLine = "All supported";
      claimPill = ["ok", plural(r.claims.length, "claim matches the pack", "claims match the pack")];
    }

    let draftPill;
    if (needs) draftPill = ["warn", `${needs} need${needs === 1 ? "s" : ""} input`];
    else if (ready) draftPill = ["info", `${ready} ready to approve`];
    else if (approved) draftPill = ["ok", `${approved} approved`];
    else draftPill = ["neutral", "No drafts"];

    const spec = [
      ["coaching", "What happened?", `${plural(count("question"), "question", "questions")}, ${plural(count("objection"), "objection", "objections")}`, ["violet", plural(count("coaching"), "coaching point", "coaching points")]],
      ["claims", "What did we claim?", claimLine, claimPill],
      ["followups", "What did we promise?", plural(r.commitments.length, "promise", "promises"), draftPill],
    ];
    tiles.replaceChildren(
      ...spec.map(([tab, q, a, [cls, sub]]) =>
        el(
          "button",
          {
            type: "button",
            class: "tile",
            "aria-pressed": state.tab === tab ? "true" : "false",
            on: { click: () => setTab(tab) },
          },
          el("span", { class: "tile-q", text: q }),
          el("span", { class: "tile-a", text: a }),
          pill(cls, sub)
        )
      )
    );
  }

  function renderTalk(node) {
    const { segments, speakers } = state.call.report.transcript;
    const secs = new Map();
    for (const seg of segments) secs.set(seg.speaker, (secs.get(seg.speaker) || 0) + Math.max(0, seg.end - seg.start));
    const total = [...secs.values()].reduce((a, b) => a + b, 0);
    if (!total) {
      node.hidden = true;
      return;
    }
    node.hidden = false;
    const rows = speakers
      .filter((sp) => secs.get(sp.id))
      .sort((a, b) => (a.role === "rep" ? -1 : 0) - (b.role === "rep" ? -1 : 0));
    const share = (sp) => Math.round((secs.get(sp.id) / total) * 100);
    node.replaceChildren(
      el("h3", { id: "talk-title", text: "Talk time" }),
      el(
        "div",
        { class: "talk-figures" },
        ...rows.map((sp) =>
          el(
            "div",
            { class: "talk-figure" },
            el("span", { class: "k", text: `${speakerLabel(sp)} · ${(ROLE_LABEL[sp.role] || "Speaker").toLowerCase()}` }),
            el("span", { class: "v" }, `${Math.round(secs.get(sp.id))}s`, el("small", { text: `${share(sp)}%` }))
          )
        )
      ),
      el(
        "div",
        { class: "talk-bar", "aria-hidden": "true" },
        ...rows.map((sp) =>
          el("span", {
            style: `flex: ${secs.get(sp.id)} 1 0; background: var(${sp.role === "rep" ? "--accent" : sp.role === "customer" ? "--customer" : "--muted"})`,
          })
        )
      )
    );
  }

  // ---------- call map ----------

  function renderCallMap(map) {
    const { segments, speakers } = state.call.report.transcript;
    const byId = new Map(speakers.map((s) => [s.id, s]));
    state.mapBlocks = new Map();
    const blocks = segments.map((seg) => {
      const sp = byId.get(seg.speaker);
      const b = el("span", {
        class: `callmap-block role-${sp ? sp.role : "other"}`,
        style: `flex: ${Math.max(0.5, seg.end - seg.start)} 1 0`,
        dataset: { start: String(seg.start) },
      });
      state.mapBlocks.set(seg.id, b);
      return b;
    });
    map.replaceChildren(...blocks);
  }

  function seekFromMap(e, map) {
    const block = e.target.closest(".callmap-block");
    if (block) seekTo(Number(block.dataset.start));
  }

  function renderHead(head) {
    const call = state.call;
    const report = call.report;
    const rep = report.transcript.speakers.find((s) => s.role === "rep");
    const cust = report.transcript.speakers.find((s) => s.role === "customer");
    const title = call.customer_name || (cust && cust.name) || call.original_filename || call.id;
    const meta = report.meta || call.report_meta || {};
    const timings = Object.entries(meta.timings || {})
      .map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "number" ? `${v.toFixed(1)} s` : v}`)
      .join(" · ");
    const when = [formatStarted(call.started_at), rep && rep.name ? `Rep: ${rep.name}` : null].filter(Boolean).join(" · ");
    head.replaceChildren(
      ...compact([
        el(
          "div",
          { class: "head-main" },
          el("p", { class: "eyebrow", text: packName(call.pack_id) }),
          el("h1", { text: title }),
          el("p", { class: "meta", text: when }),
          el("p", { class: "meta small", text: `Transcribed by ${meta.transcriber || "unknown"} · analysed by ${meta.analyzer || "unknown"}` }),
          timings ? el("p", { class: "meta small", text: `Timings: ${timings}` }) : null
        ),
        call.source === "fixture"
          ? el("div", { class: "banner", role: "note" }, el("strong", { text: "Example data:" }), " hand-written fixture, not model output")
          : null,
      ])
    );
  }

  function renderNotice(node) {
    node.replaceChildren();
    if (!state.notice) {
      node.hidden = true;
      return;
    }
    node.hidden = false;
    node.className = `notice notice-${state.notice.kind}`;
    node.append(
      el("p", { text: state.notice.text }),
      el("button", {
        type: "button",
        class: "link small",
        text: "Dismiss",
        on: {
          click: () => {
            state.notice = null;
            renderNotice(node);
          },
        },
      })
    );
  }

  // ---------- transcript and playback ----------

  function renderTranscript(list) {
    const { segments, speakers } = state.call.report.transcript;
    const byId = new Map(speakers.map((s) => [s.id, s]));
    state.segRows = new Map();
    const rows = segments.map((seg) => {
      const li = segmentRow(seg, byId.get(seg.speaker) || null);
      state.segRows.set(seg.id, li);
      return li;
    });
    list.replaceChildren(...rows);
    if (state.refs && state.refs.txSub) {
      state.refs.txSub.textContent = `${plural(segments.length, "line", "lines")} · choose a time to play from there`;
    }
    markPlayback();
    markEvidence();
  }

  function segmentRow(seg, speaker) {
    const editing = state.editing === seg.id;
    const role = speaker ? speaker.role : "other";
    const stamp = mmss(seg.start);
    const li = el("li", { class: `seg role-${role}${editing ? " is-editing" : ""}`, dataset: { id: seg.id } });
    const body = el(
      "div",
      { class: "seg-body" },
      el("p", { class: "who" }, el("span", { text: speakerLabel(speaker) }), seg.lang ? el("span", { class: "lang", text: seg.lang }) : null)
    );
    if (editing) {
      body.append(editForm(seg));
    } else {
      body.append(el("p", { class: "text", text: seg.text }));
    }
    li.append(el("button", { type: "button", class: "ts", text: stamp, title: `Play from ${stamp}`, "aria-label": `Play from ${stamp}` }), body);
    if (!editing) {
      li.append(
        el("button", {
          type: "button",
          class: "edit-btn small",
          text: "Edit",
          "aria-label": `Edit the line at ${stamp}`,
          on: {
            click: (e) => {
              e.stopPropagation();
              startEdit(seg.id);
            },
          },
        })
      );
      li.addEventListener("click", () => seekTo(seg.start));
    }
    return li;
  }

  function seekTo(seconds) {
    const audio = state.audio;
    if (!audio) return;
    const apply = () => {
      audio.currentTime = seconds;
      const p = audio.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    };
    if (audio.readyState >= 1) apply();
    else audio.addEventListener("loadedmetadata", apply, { once: true });
  }

  function markPlayback() {
    for (const [id, row] of state.segRows) row.classList.toggle("is-current", id === state.current);
    for (const [id, b] of state.mapBlocks) b.classList.toggle("is-current", id === state.current);
  }

  function markEvidence() {
    for (const [id, row] of state.segRows) row.classList.toggle("is-evidence", state.evidence.has(id));
    for (const [id, b] of state.mapBlocks) b.classList.toggle("is-evidence", state.evidence.has(id));
    if (state.refs && state.refs.map) state.refs.map.classList.toggle("has-evidence", state.evidence.size > 0);
    if (state.refs && state.refs.clear) state.refs.clear.hidden = state.evidence.size === 0;
  }

  function markFocused() {
    if (!state.refs) return;
    for (const card of state.refs.panel.querySelectorAll("[data-item]")) {
      card.classList.toggle("is-focused", !!state.focusItem && card.dataset.item === state.focusItem);
    }
  }

  function onTimeUpdate() {
    if (!state.audio || !state.call) return;
    const t = state.audio.currentTime;
    let pick = null;
    for (const seg of state.call.report.transcript.segments) {
      if (seg.start <= t) pick = seg;
      else break;
    }
    const id = pick ? pick.id : null;
    if (id === state.current) return;
    state.current = id;
    markPlayback();
    const row = id ? state.segRows.get(id) : null;
    if (row && !state.audio.paused) scrollRow(row, "nearest");
  }

  function focusEvidence(segmentIds) {
    const known = (segmentIds || []).filter((id) => state.segRows.has(id));
    if (!known.length) return;
    state.evidence = new Set(known);
    markEvidence();
    scrollRow(state.segRows.get(known[0]), "center");
    seekTo(segmentById(known[0]).start);
  }

  function evidenceButton(segmentIds, itemId) {
    const first = segmentIds && segmentIds.length ? segmentById(segmentIds[0]) : null;
    if (!first) return null;
    const stamp = mmss(first.start);
    return el(
      "button",
      {
        type: "button",
        class: "ref-btn small",
        title: `Play from ${stamp} and highlight the evidence`,
        "aria-label": `Play from ${stamp} and highlight the evidence in the transcript`,
        on: {
          click: () => {
            state.focusItem = itemId || null;
            markFocused();
            focusEvidence(segmentIds);
          },
        },
      },
      icon("play", 11),
      stamp
    );
  }

  // ---------- inline segment edit ----------

  function startEdit(id) {
    state.editing = id;
    renderTranscript(state.refs.list);
    const ta = state.refs.list.querySelector(".edit-text");
    if (ta) {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
  }

  function cancelEdit() {
    state.editing = null;
    renderTranscript(state.refs.list);
  }

  function editForm(seg) {
    const ta = el("textarea", { class: "edit-text", rows: "3", "aria-label": "Corrected text for this line", value: seg.text });
    const save = el("button", { type: "button", class: "primary small", text: "Save" });
    const cancel = el("button", { type: "button", class: "small", text: "Cancel" });
    save.addEventListener("click", () => saveSegment(seg.id, ta.value, save));
    cancel.addEventListener("click", cancelEdit);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        cancelEdit();
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        saveSegment(seg.id, ta.value, save);
      }
    });
    return el(
      "div",
      { class: "edit-form" },
      ta,
      el("div", { class: "row" }, save, cancel),
      el("p", { class: "hint small", text: "Ctrl+Enter saves. Esc cancels." })
    );
  }

  function setFormError(form, message) {
    if (!form) return;
    let p = form.querySelector(".form-error");
    if (!p) {
      p = el("p", { class: "form-error", role: "alert" });
      form.insertBefore(p, form.querySelector(".row"));
    }
    p.textContent = message;
  }

  function describeEdit(data) {
    const parts = [];
    const n = data.newly_rejected || [];
    const m = data.restored || [];
    if (n.length) {
      parts.push(
        `${n.length === 1 ? "1 item no longer matches" : `${n.length} items no longer match`} the transcript and ` +
          `${n.length === 1 ? "was" : "were"} moved to Refused: ${n.join(", ")}.`
      );
    }
    if (m.length) {
      parts.push(
        `${m.length === 1 ? "1 item matches" : `${m.length} items match`} the transcript again and ` +
          `${m.length === 1 ? "was" : "were"} restored from Refused: ${m.join(", ")}.`
      );
    }
    return parts.length ? parts.join(" ") : "Saved. No findings or claims changed.";
  }

  async function saveSegment(id, value, btn) {
    const form = btn.closest(".edit-form");
    const text = value.trim();
    if (!text) {
      setFormError(form, "The line cannot be empty.");
      return;
    }
    const current = segmentById(id);
    if (current && text === current.text) {
      cancelEdit();
      return;
    }
    btn.disabled = true;
    try {
      const data = await api(`/api/calls/${enc(state.callId)}/segments/${enc(id)}`, { method: "PUT", json: { text } });
      state.call.report = data.report;
      state.editing = null;
      state.notice = { kind: "info", text: describeEdit(data) };
      renderCallParts();
    } catch (e) {
      btn.disabled = false;
      setFormError(form, e.message);
    }
  }

  // ---------- tabs ----------

  function renderTabs(tabs) {
    const r = state.call.report;
    const counts = {
      coaching: r.findings.length,
      claims: r.claims.length,
      followups: r.commitments.length,
      refused: r.rejected.length,
    };
    tabs.replaceChildren(
      ...TABS.map(([id, label], i) => {
        const on = state.tab === id;
        return el(
          "button",
          {
            type: "button",
            role: "tab",
            id: `tab-${id}`,
            class: on ? "tab is-selected" : "tab",
            "aria-selected": on ? "true" : "false",
            "aria-controls": "panel",
            tabindex: on ? "0" : "-1",
            on: {
              click: () => setTab(id),
              keydown: (e) => tabKey(e, i),
            },
          },
          label,
          " ",
          el("span", { class: "badge", text: String(counts[id]) })
        );
      })
    );
  }

  function setTab(id) {
    state.tab = id;
    renderSummary(state.refs.tiles);
    renderTabs(state.refs.tabs);
    renderPanel();
  }

  function tabKey(e, i) {
    let j = null;
    if (e.key === "ArrowRight") j = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = TABS.length - 1;
    if (j === null) return;
    e.preventDefault();
    setTab(TABS[j][0]);
    const btn = document.getElementById(`tab-${TABS[j][0]}`);
    if (btn) btn.focus();
  }

  function renderPanel() {
    const r = state.refs;
    if (!r || !state.call) return;
    const report = state.call.report;
    const views = { coaching: coachingView, claims: claimsView, followups: followupsView, refused: refusedView };
    r.panel.setAttribute("aria-labelledby", `tab-${state.tab}`);
    r.panel.replaceChildren(views[state.tab](report));
    markFocused();
    const mark = r.panel.querySelector(".doc-text mark");
    if (mark) {
      const box = mark.closest(".doc-text");
      box.scrollTop = Math.max(0, mark.offsetTop - box.clientHeight / 3);
    }
  }

  // ---------- coaching ----------

  function coachingView(report) {
    const wrap = el("div", { class: "stack" });
    if (!report.findings.length) wrap.append(muted("Sel found no questions, objections or coaching points in this call."));
    for (const kind of ["coaching", "objection", "question"]) {
      const items = sortDismissedLast(
        report.findings.filter((f) => f.kind === kind),
        (f) => f.id
      );
      if (!items.length) continue;
      wrap.append(
        el(
          "section",
          { class: "group" },
          el("h3", { class: "group-title", text: KIND_LABEL[kind] }),
          ...items.map(findingCard)
        )
      );
    }
    return wrap;
  }

  function labelled(label, value) {
    return el("p", { class: "labelled" }, el("span", { class: "label", text: `${label}: ` }), value);
  }

  function findingCard(f) {
    const dismissed = isDismissed(f.id);
    const segIds = f.evidence.segment_ids || [];
    const tag = KIND_TAG[f.kind] || { cls: "neutral", label: f.kind, icon: "target" };
    const sub = f.criterion
      ? `Scorecard: ${criterionTitle(state.call.pack_id, f.criterion)}`
      : saidBy(segIds, f.kind === "question" ? "Asked by" : "Raised by");
    return el(
      "article",
      { class: `card finding${dismissed ? " is-dismissed" : ""}`, dataset: { item: f.id } },
      cardTag(dismissed ? "neutral" : tag.cls, dismissed ? "Dismissed" : tag.label),
      cardHead(tag.cls, tag.icon, f.summary, sub),
      f.evidence.quote ? el("p", { class: "quote", text: quoted(f.evidence.quote) }) : null,
      f.suggestion ? labelled("Try next time", f.suggestion) : null,
      el(
        "div",
        { class: "card-actions" },
        evidenceButton(segIds, f.id),
        segIds.length > 1 ? pill("neutral", plural(segIds.length, "line", "lines")) : null,
        el("button", {
          type: "button",
          class: "small ghost push",
          text: dismissed ? "Restore" : "Dismiss",
          "aria-pressed": dismissed ? "true" : "false",
          on: { click: () => setDismissed(f.id, !dismissed) },
        })
      )
    );
  }

  async function setDismissed(itemId, dismiss) {
    const callId = state.callId;
    try {
      const review = await api(`/api/calls/${enc(callId)}/items/${enc(itemId)}/${dismiss ? "dismiss" : "restore"}`, { method: "POST" });
      if (state.callId === callId) {
        state.call.review = review;
        renderPanel();
      }
    } catch (e) {
      state.notice = { kind: "error", text: `Could not ${dismiss ? "dismiss" : "restore"} ${itemId}: ${e.message}` };
      renderNotice(state.refs.notice);
    }
  }

  // ---------- claims and source documents ----------

  function claimsView(report) {
    if (!report.claims.length) return el("div", { class: "stack" }, muted("No claims were checked in this call."));
    // Problems first: contradicted, then unverified, then supported.
    const rank = (c) => (VERDICT[c.verdict] ? VERDICT[c.verdict].rank : 1);
    const ordered = report.claims.map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i).map((o) => o.c);
    return el(
      "div",
      { class: "stack" },
      el("p", { class: "explain", text: "Each product claim from the call, checked against the pack's own documents. Problems first." }),
      ...ordered.map(claimCard)
    );
  }

  function claimCard(c) {
    const v = VERDICT[c.verdict] || { cls: "neutral", label: c.verdict, icon: "question" };
    const segIds = c.evidence.segment_ids || [];
    return el(
      "article",
      { class: "card claim", dataset: { item: c.id } },
      cardTag(v.cls, v.label),
      cardHead(v.cls, v.icon, c.claim, saidBy(segIds)),
      c.evidence.quote ? el("p", { class: "quote", text: quoted(c.evidence.quote) }) : null,
      c.explanation ? el("p", { class: "explanation", text: c.explanation }) : null,
      c.sources && c.sources.length ? el("div", { class: "sources" }, ...c.sources.map(sourceBlock)) : null,
      segIds.length ? el("div", { class: "card-actions" }, evidenceButton(segIds, c.id)) : null
    );
  }

  function sourceBlock(src) {
    const packId = state.call.pack_id;
    const key = `${packId}|${src.source}`;
    const open = state.openDocs.has(key);
    return el(
      "div",
      { class: "source" },
      el(
        "div",
        { class: "source-head" },
        el("span", { class: "source-name" }, icon("doc", 14), el("span", { text: src.source })),
        el("button", {
          type: "button",
          class: "small",
          text: open ? "Hide document" : "Show document",
          "aria-expanded": open ? "true" : "false",
          on: { click: () => toggleDocument(packId, src.source) },
        })
      ),
      el("blockquote", { text: src.quote }),
      open ? documentView(key, src) : null
    );
  }

  async function toggleDocument(packId, ref) {
    const key = `${packId}|${ref}`;
    const callId = state.callId;
    if (state.openDocs.has(key)) {
      state.openDocs.delete(key);
      renderPanel();
      return;
    }
    state.openDocs.add(key);
    renderPanel();
    if (state.docs.has(key)) return;
    try {
      const d = await api(`/api/packs/${enc(packId)}/source?ref=${enc(ref)}`);
      state.docs.set(key, { text: d.text });
    } catch (e) {
      state.docs.set(key, { error: e.message });
    }
    if (state.callId === callId && state.openDocs.has(key)) renderPanel();
  }

  function documentView(key, src) {
    const entry = state.docs.get(key);
    if (!entry) return muted("Loading document…");
    if (entry.error) return el("p", { class: "form-error", role: "alert", text: `Could not load ${src.source}: ${entry.error}` });
    return el(
      "div",
      { class: "doc" },
      el("p", { class: "doc-title", text: `Full text of ${src.source}` }),
      el("div", { class: "doc-text" }, highlightQuote(entry.text, src.quote))
    );
  }

  function highlightQuote(full, quote) {
    const words = String(quote || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return document.createTextNode(full);
    const m = new RegExp(words.map(escapeRe).join("\\s+"), "i").exec(full);
    if (!m) {
      return el(
        "span",
        {},
        full,
        el("span", { class: "muted small", text: "\n\n(The quoted passage was not found word for word in this document.)" })
      );
    }
    return el("span", {}, full.slice(0, m.index), el("mark", { text: m[0] }), full.slice(m.index + m[0].length));
  }

  // ---------- follow-ups and drafts ----------

  function followupsView(report) {
    const byId = new Map(report.transcript.speakers.map((s) => [s.id, s]));
    const drafts = new Map(report.drafts.map((d) => [d.commitment_id, d]));
    const wrap = el(
      "div",
      { class: "stack" },
      el("p", { class: "fine" }, icon("lock", 15), el("span", { text: "Sel never sends email or creates events. Approved drafts are for you to send." }))
    );
    if (!report.commitments.length) wrap.append(muted("No promises were found in this call."));
    for (const c of report.commitments) {
      wrap.append(commitmentCard(c, byId.get(c.owner) || null, drafts.get(c.id) || null));
    }
    return wrap;
  }

  function dueLine(due) {
    if (due && due.status === "exact") {
      const when = [due.date, due.time ? `at ${due.time}` : null, due.part_of_day || null].filter(Boolean).join(", ");
      return el("p", { class: "due" }, el("span", { class: "label", text: "Due: " }), when);
    }
    const parts = [el("p", { class: "due-reason" }, el("span", { class: "label", text: "No fixed date." }))];
    if (due && due.reason) parts.push(el("p", { class: "due-reason", text: `Reason: ${due.reason}` }));
    if (due && (due.window_start || due.window_end)) {
      const window = due.window_start && due.window_end
        ? `${due.window_start} to ${due.window_end}`
        : due.window_start ? `from ${due.window_start}` : `until ${due.window_end}`;
      parts.push(el("p", { class: "due-reason", text: `Window: ${window}` }));
    }
    return el("div", { class: "due-open" }, ...parts);
  }

  function commitmentCard(c, owner, draft) {
    const isRep = !!owner && owner.role === "rep";
    const who = owner
      ? `${ROLE_LABEL[owner.role] || "Speaker"}${owner.name ? ` · ${owner.name}` : ""}`
      : "Unknown speaker";
    let tail;
    let tag;
    if (!isRep) {
      tail = el("p", { class: "tracked", text: "Tracked, no draft: the customer promised this." });
      tag = { cls: "neutral", label: "Tracked" };
    } else if (draft) {
      tail = draftForm(draft);
      tag = DRAFT_STATUS[draft.status] || { cls: "neutral", label: draft.status };
    } else {
      tail = muted("No draft for this channel.");
      tag = c.due && c.due.status === "exact" ? { cls: "neutral", label: "No draft" } : { cls: "warn", label: "Needs a date" };
    }
    let tone = "ok";
    if (isRep) tone = tag.cls === "neutral" ? "accent" : tag.cls;
    const iconName = isRep ? CHANNEL_ICON[c.channel] || "user" : "user";
    const action = c.action ? c.action.charAt(0).toUpperCase() + c.action.slice(1) : c.action;
    const said = c.due_text ? `said ${quoted(c.due_text)}` : "no due phrase was said";
    return el(
      "article",
      { class: "card commitment", dataset: { item: c.id } },
      cardTag(tag.cls, tag.label),
      cardHead(tone, iconName, action, `${who} · ${c.channel} · ${said}`),
      dueLine(c.due),
      el("div", { class: "card-actions" }, evidenceButton(c.evidence.segment_ids || [], c.id)),
      tail
    );
  }

  function pickFields(fields, specs) {
    const out = {};
    for (const [key] of specs) out[key] = fields[key] || "";
    return out;
  }

  function readDraft(form, specs) {
    const out = {};
    for (const [key] of specs) {
      const node = form.elements.namedItem(key);
      out[key] = node ? node.value : "";
    }
    return out;
  }

  function setFormStatus(form, kind, text) {
    const node = form.querySelector(".form-status");
    if (!node) return;
    node.textContent = text;
    node.className = `form-status${kind ? ` is-${kind}` : ""}`;
  }

  function draftForm(draft) {
    const specs = DRAFT_FIELDS[draft.kind] || [];
    const isEmail = draft.kind === "email";
    const status = DRAFT_STATUS[draft.status] || { cls: "neutral", label: draft.status };
    const missing = new Set(draft.missing || []);
    const dirty = !!state.draftEdits[draft.id];
    const values = state.draftEdits[draft.id] || pickFields(draft.fields, specs);
    const msg = state.draftMsg[draft.id];
    const form = el("form", { class: "draft", "aria-label": isEmail ? "Email draft" : "Calendar draft", novalidate: true });

    const fieldNodes = specs.map(([key, label, type]) => {
      const id = `d-${draft.id}-${key}`;
      const common = {
        id,
        name: key,
        class: "input",
        "aria-invalid": missing.has(key) ? "true" : null,
        value: values[key] || "",
      };
      const input = type === "textarea" ? el("textarea", { ...common, rows: "6" }) : el("input", { type, ...common });
      input.addEventListener("input", () => {
        state.draftEdits[draft.id] = readDraft(form, specs);
        delete state.draftMsg[draft.id];
        const approve = form.querySelector(".approve-btn");
        if (approve) approve.disabled = true;
        setFormStatus(form, "", "Unsaved changes. Save before approving.");
      });
      return el("div", { class: `field${missing.has(key) ? " is-missing" : ""}` }, el("label", { for: id, text: label }), input);
    });

    const approve = el("button", {
      type: "button",
      class: "primary approve-btn",
      text: "Approve",
      disabled: draft.status !== "ready_for_approval" || dirty,
      on: { click: () => approveDraft(draft.id) },
    });
    const actions = [el("button", { type: "submit", class: "small", text: "Save changes" }), approve];
    if (isEmail) {
      actions.push(
        el("button", {
          type: "button",
          class: "small",
          text: "Copy email",
          on: { click: () => copyEmail(form, readDraft(form, specs)) },
        })
      );
    } else if (draft.ics) {
      actions.push(
        el("a", {
          class: "button small",
          href: `/api/calls/${enc(state.callId)}/drafts/${enc(draft.id)}/ics`,
          download: `${draft.id}.ics`,
          text: "Download .ics",
        })
      );
    }

    let statusText = "";
    let statusKind = "";
    if (dirty) {
      statusText = "Unsaved changes. Save before approving.";
    } else if (msg) {
      statusText = msg.text;
      statusKind = msg.kind;
    }

    form.append(
      ...compact([
        el("div", { class: "draft-head" }, el("h4", { text: isEmail ? "Email draft" : "Calendar draft" }), el("span", { class: "sr-only", text: status.label })),
        draft.fields.review_note
          ? el("div", { class: "review-note", role: "note" }, el("strong", { text: "Review note: " }), draft.fields.review_note)
          : null,
        ...fieldNodes,
        missing.size
          ? el("p", { class: "missing", text: `Missing: ${[...missing].map((k) => FIELD_LABEL[k] || k).join(", ")}` })
          : null,
        el("div", { class: "row actions" }, ...actions),
        el("p", { class: `form-status${statusKind ? ` is-${statusKind}` : ""}`, role: "status", text: statusText }),
      ])
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      saveDraft(draft.id, readDraft(form, specs));
    });
    return form;
  }

  function replaceDraft(updated) {
    const list = state.call.report.drafts;
    const i = list.findIndex((d) => d.id === updated.id);
    if (i >= 0) list[i] = updated;
  }

  async function saveDraft(draftId, fields) {
    const callId = state.callId;
    try {
      const updated = await api(`/api/calls/${enc(callId)}/drafts/${enc(draftId)}`, { method: "PUT", json: { fields } });
      if (state.callId !== callId) return;
      replaceDraft(updated);
      delete state.draftEdits[draftId];
      state.draftMsg[draftId] = { kind: "ok", text: "Saved." };
    } catch (e) {
      state.draftMsg[draftId] = { kind: "error", text: e.message };
    }
    renderPanel();
  }

  async function approveDraft(draftId) {
    const callId = state.callId;
    try {
      const updated = await api(`/api/calls/${enc(callId)}/drafts/${enc(draftId)}/approve`, { method: "POST" });
      if (state.callId !== callId) return;
      replaceDraft(updated);
      state.draftMsg[draftId] = { kind: "ok", text: "Approved. Sel does not send it; you do." };
    } catch (e) {
      state.draftMsg[draftId] = { kind: "error", text: e.message };
    }
    renderPanel();
  }

  function copyViaSelection(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.append(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      ok = false;
    }
    ta.remove();
    return ok;
  }

  async function copyEmail(form, v) {
    const text = `To: ${v.to}\nSubject: ${v.subject}\n\n${v.body}`;
    const old = form.querySelector(".copy-fallback");
    if (old) old.remove();
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch (_) {
      copied = false;
    }
    if (!copied) copied = copyViaSelection(text);
    if (copied) {
      setFormStatus(form, "ok", "Copied To, Subject and Body.");
      return;
    }
    const fallback = el("textarea", {
      class: "copy-fallback input",
      readOnly: true,
      rows: "8",
      "aria-label": "Email text, selected so you can copy it",
      value: text,
    });
    form.append(fallback);
    fallback.focus();
    fallback.select();
    setFormStatus(form, "info", "Copying is blocked here. The email text is selected below; press Ctrl+C to copy it.");
  }

  // ---------- refused ----------

  function refusedView(report) {
    const wrap = el("div", { class: "stack" }, el("p", { class: "explain", text: REFUSED_EXPLAINER }));
    if (!report.rejected.length) wrap.append(muted("Nothing was refused for this call."));
    for (const r of report.rejected) {
      // item_type is "finding:objection", "claim" or "commitment".
      const kind = String(r.item_type || "item").split(":").pop();
      const title = `Proposed ${kind.replace(/_/g, " ")}`;
      wrap.append(
        el(
          "article",
          { class: "card refused" },
          cardTag("bad", "Refused"),
          cardHead("bad", "ban", title, `${r.item_type} · ID ${r.item_id}`),
          el("ul", { class: "reasons" }, ...r.reasons.map((reason) => el("li", { text: reason })))
        )
      );
    }
    return wrap;
  }

  // ---------- new call form ----------

  function defaultStartValue() {
    // Wall clock in the default offset (+05:45), formatted for datetime-local.
    return new Date(Date.now() + 5.75 * 3600 * 1000).toISOString().slice(0, 16);
  }

  function formField(forId, labelText, control, hint) {
    return el("div", { class: "field" }, el("label", { for: forId, text: labelText }), control, hint ? el("p", { class: "hint small", text: hint }) : null);
  }

  function renderUpload(main) {
    const packSel = el("select", { id: "f-pack", name: "pack" });
    if (!state.packList.length) packSel.append(el("option", { value: "", text: state.packError ? "Packs could not be loaded" : "No business packs found" }));
    for (const p of state.packList) packSel.append(el("option", { value: p.id, text: p.name }));

    const channels = el(
      "select",
      { id: "f-channels", name: "channels" },
      el("option", { value: "auto", text: "Auto-detect" }),
      el("option", { value: "split", text: "Stereo — one side per channel" }),
      el("option", { value: "mono", text: "Mono" })
    );
    const repChannelSel = el(
      "select",
      { id: "f-rep-channel", name: "rep_channel" },
      el("option", { value: "0", text: "Left" }),
      el("option", { value: "1", text: "Right" })
    );
    const repChannelField = formField("f-rep-channel", "Rep is on", repChannelSel);
    repChannelField.hidden = true;
    channels.addEventListener("change", () => {
      repChannelField.hidden = channels.value !== "split";
    });

    const submit = el("button", { type: "submit", class: "primary", id: "upload-submit", text: "Upload and analyse", disabled: !state.packList.length });
    const form = el(
      "form",
      { class: "upload", id: "upload-form", novalidate: true },
      formField("f-audio", "Recording", el("input", { type: "file", id: "f-audio", name: "audio", accept: "audio/*" }), "Up to 500 MB. The file stays on this computer."),
      formField("f-pack", "Business pack", packSel),
      formField("f-start", "Call started", el("input", { type: "datetime-local", id: "f-start", name: "started", step: "60", value: defaultStartValue() })),
      formField("f-offset", "UTC offset", el("input", { type: "text", id: "f-offset", name: "offset", value: "+05:45", autocomplete: "off" }), "For example +05:45 or -03:00."),
      formField("f-rep", "Rep name", el("input", { type: "text", id: "f-rep", name: "rep_name", autocomplete: "off" })),
      formField("f-cust", "Customer name", el("input", { type: "text", id: "f-cust", name: "customer_name", autocomplete: "off" })),
      formField("f-cust-email", "Customer email", el("input", { type: "email", id: "f-cust-email", name: "customer_email", autocomplete: "off" })),
      formField("f-channels", "Recording type", channels),
      repChannelField,
      el("p", { id: "upload-error", class: "form-error", role: "alert", hidden: true }),
      el("div", { class: "row" }, submit)
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitUpload(form);
    });
    main.replaceChildren(
      el(
        "section",
        { class: "upload-page" },
        el("h1", { text: "New call" }),
        el("p", { class: "lede", text: "Add a recording. Sel transcribes it and analyses it with the configured language model." }),
        state.packError ? el("p", { class: "form-error", text: `Could not load packs: ${state.packError}` }) : null,
        form
      )
    );
  }

  async function submitUpload(form) {
    const errBox = form.querySelector("#upload-error");
    const submit = form.querySelector("#upload-submit");
    const val = (sel) => form.querySelector(sel).value.trim();
    const fail = (message) => {
      errBox.textContent = message;
      errBox.hidden = false;
    };
    errBox.hidden = true;

    const file = form.querySelector("#f-audio").files[0];
    const pack = form.querySelector("#f-pack").value;
    const when = form.querySelector("#f-start").value;
    const offset = normalizeOffset(val("#f-offset"));
    if (!file) return fail("Choose a recording file first.");
    if (file.size > MAX_UPLOAD) return fail("This recording is larger than 500 MB.");
    if (!pack) return fail("Choose a business pack.");
    if (!when) return fail("Enter when the call started.");
    if (!offset) return fail("The UTC offset should look like +05:45.");

    const startedAt = `${when.length === 16 ? `${when}:00` : when}${offset}`;
    const params = new URLSearchParams({
      pack,
      started_at: startedAt,
      filename: file.name,
      rep_name: val("#f-rep"),
      customer_name: val("#f-cust"),
      customer_email: val("#f-cust-email"),
      channels: val("#f-channels") || "auto",
      rep_channel: val("#f-rep-channel") || "0",
    });

    submit.disabled = true;
    submit.textContent = "Uploading…";
    try {
      const created = await api(`/api/calls?${params.toString()}`, {
        method: "POST",
        body: file,
        contentType: file.type || "application/octet-stream",
      });
      await refreshList().catch(() => {});
      navigate(`#call/${enc(created.id)}`);
      kickPoll();
    } catch (e) {
      fail(e.message);
      submit.disabled = false;
      submit.textContent = "Upload and analyse";
    }
  }

  // ---------- boot ----------

  async function loadPacks() {
    try {
      state.packList = await api("/api/packs");
      state.packs = new Map(state.packList.map((p) => [p.id, p]));
      state.packError = null;
    } catch (e) {
      state.packError = e.message;
    }
  }

  async function boot() {
    $("#new-call").addEventListener("click", () => navigate("#new"));
    window.addEventListener("hashchange", () => route());
    loadHealth();
    setInterval(loadHealth, HEALTH_MS);
    await loadPacks();
    await route();
    tick();
  }

  boot();
})();
