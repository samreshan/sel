/* Sel review screen. Vanilla JS, no build step, no external resources.
 *
 * Every string from the server (transcript text, quotes, drafts, document
 * text) is inserted with textContent through el(). Nothing is parsed as HTML.
 *
 * Performance (numbers in CLAUDE.md):
 * - One Server-Sent Events stream per tab pushes call and simulator updates;
 *   polling is only a fallback while the stream is down.
 * - Call views are cached in memory and revalidated with ETags, and call
 *   links prefetch on hover, so opening a call renders immediately.
 * - Segment lookups go through an index; the playhead uses binary search and
 *   touches only the rows that change; editing a line replaces one row.
 * - The call simulator (sim.js) loads only when it is opened.
 */
(function () {
  "use strict";

  const MAX_UPLOAD = 500 * 1024 * 1024;
  const POLL_ACTIVE_MS = 2000;
  const POLL_FALLBACK_MS = 10000;
  const HEALTH_MS = 60000;
  const CACHE_MAX = 24;
  const TAB = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const SCRIPT = document.currentScript;
  const STREAM_EVENTS = ["hello", "call", "call-deleted", "call-changed", "coaching", "coach", "drill", "lead", "playbook", "people", "rooms", "room", "line", "live", "signal"];
  const COACH_PAGES = new Set(["today", "playbook", "people", "practice", "leads", "lead", "prep", "team", "settings"]);
  const TEAM_PAGES = new Set(["leads", "lead", "prep", "team", "settings"]); // team.js
  const NAV_OF = { lead: "leads", prep: "leads" };

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
  const SCORE_STATUS = {
    met: { cls: "ok", label: "Met" },
    partial: { cls: "warn", label: "Partly" },
    missed: { cls: "bad", label: "Missed" },
    open: { cls: "info", label: "Open" },
    na: { cls: "neutral", label: "Not raised" },
    not_scored: { cls: "neutral", label: "In coaching" },
  };

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
    callsLoaded: false,
    callLayout: "table",
    listError: null,
    view: "empty", // "empty" | "new" | "calls" | "call" | "sim" | "coach" (Today, Playbook, People: coach.js)
    coachPage: null, // "today" | "playbook" | "people"
    coachArg: null, // e.g. the pack id on #playbook/<id>
    people: [], // the team, from the event stream (people.json on the server)
    leads: null, // GET /api/leads, loaded on first need (lead pickers)
    viewerId: null, // who this browser is viewing as (localStorage "sel.viewer")
    modules: {}, // lazily loaded scripts: name -> promise
    callId: null,
    simCode: null,
    call: null, // full view from GET /api/calls/{id}
    cache: new Map(), // call id -> {data, etag}
    inflight: new Map(), // GET path -> promise
    mainError: null,
    tab: "coaching",
    evidence: new Set(), // segment ids highlighted by a card
    marked: new Set(), // segment ids currently drawn as evidence
    focusItem: null, // id of the card whose evidence is highlighted
    current: null, // segment id under the playhead
    currentRow: null,
    currentBlock: null,
    editing: null, // segment id in inline edit mode
    notice: null, // {kind, text} above the transcript
    draftEdits: {}, // unsaved draft form values, by draft id
    draftMsg: {}, // {kind, text} under a draft, by draft id
    momentForm: null, // {id, kind: "edit" | "note"}: the moment whose line or note is being written
    momentMsg: {}, // {kind, text} under a moment, by moment id
    momentBusy: new Set(), // moment ids with a request in flight
    openDocs: new Set(), // "pack|source" keys with the document panel open
    docs: new Map(), // "pack|source" -> {text} or {error}
    audio: null,
    raf: 0,
    mountKey: null,
    txKey: null,
    mapKey: null,
    refs: null,
    idx: null, // segment index for the shown report
    segRows: new Map(),
    mapBlocks: new Map(), // segment id -> call-map block
    health: null,
    pollTimer: null,
    stream: { es: null, qs: null, connected: false },
    sim: null, // simulator capabilities, from the event stream
    rooms: [], // open simulator rooms
    showKeys: false,
  };
  const listeners = new Map();

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

  // Locally bundled Solar Icons (@solar-icons/static 2.3.2): Linear, Bold playback.
  function icon(name, size = 18) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "solar-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(NS, "use");
    use.setAttribute("href", `/static/solar-icons.svg#${name}`);
    svg.append(use);
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

  // This browser's UTC offset as "+05:45".
  function localOffset() {
    const mins = -new Date().getTimezoneOffset();
    const sign = mins >= 0 ? "+" : "-";
    const a = Math.abs(mins);
    return `${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
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

  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {
      /* private mode or blocked storage: remembering is only a convenience */
    }
    return null;
  }

  function on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  }

  function emit(type, data) {
    for (const fn of listeners.get(type) || []) {
      try {
        fn(data);
      } catch (e) {
        console.error(e); // eslint-disable-line no-console
      }
    }
  }

  // ---------- server requests ----------

  // GETs revalidate through the browser's HTTP cache (the server sends ETags),
  // and concurrent GETs of the same path share one request.
  // `fresh` skips sharing: a request made because the server announced a change
  // must not reuse one that started before the change.
  function request(path, { method = "GET", json, body, contentType, fresh = false } = {}) {
    const get = method === "GET";
    if (get && !fresh && state.inflight.has(path)) return state.inflight.get(path);
    const p = (async () => {
      const init = { method, headers: {} };
      if (!get) init.headers["X-Sel-Tab"] = TAB;
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
      return { data, etag: res.headers.get("ETag") };
    })();
    if (get) {
      state.inflight.set(path, p);
      p.finally(() => state.inflight.delete(path)).catch(() => {});
    }
    return p;
  }

  const api = (path, opts) => request(path, opts).then((r) => r.data);

  // Upload with progress (fetch cannot report upload progress).
  function upload(path, blob, contentType, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", path);
      xhr.setRequestHeader("Content-Type", contentType || "application/octet-stream");
      xhr.setRequestHeader("X-Sel-Tab", TAB);
      if (onProgress) xhr.upload.addEventListener("progress", (e) => e.lengthComputable && onProgress(e.loaded / e.total));
      xhr.addEventListener("load", () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText || "null");
        } catch (_) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiError((data && data.error) || `Upload failed with HTTP ${xhr.status}`, xhr.status));
      });
      xhr.addEventListener("error", () => reject(new ApiError("Cannot reach the Sel server. Is it still running?", 0)));
      xhr.send(blob);
    });
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

  // Segment index for the report on screen; rebuilt only when the report object changes.
  function index() {
    const r = state.call && state.call.report;
    if (!r) return null;
    if (state.idx && state.idx.report === r) return state.idx;
    const list = r.transcript.segments;
    const segs = new Map();
    const starts = new Float64Array(list.length);
    list.forEach((s, i) => {
      segs.set(s.id, s);
      starts[i] = s.start;
    });
    state.idx = { report: r, list, segs, starts, speakers: new Map(r.transcript.speakers.map((s) => [s.id, s])) };
    return state.idx;
  }

  function segmentById(id) {
    const idx = index();
    return (idx && idx.segs.get(id)) || null;
  }

  // The segment playing at time t: the last one starting at or before t.
  function segmentAt(t) {
    const idx = index();
    if (!idx || !idx.list.length) return null;
    const s = idx.starts;
    let lo = 0;
    let hi = s.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (s[mid] <= t) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    return found >= 0 ? idx.list[found].id : null;
  }

  function speakerLabel(sp) {
    if (!sp) return "Unknown";
    return sp.name || ROLE_LABEL[sp.role] || "Speaker";
  }

  function speakerOf(segId) {
    const seg = segId ? segmentById(segId) : null;
    if (!seg) return null;
    return index().speakers.get(seg.speaker) || null;
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

  // ---------- routing ----------

  function navigate(hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  }

  function stopPlayLoop() {
    clearTimeout(state.raf);
    state.raf = 0;
  }

  function resetCallUi() {
    stopPlayLoop();
    if (state.audio) state.audio.pause();
    state.tab = "coaching";
    state.evidence = new Set();
    state.marked = new Set();
    state.focusItem = null;
    state.mapBlocks = new Map();
    state.current = null;
    state.currentRow = null;
    state.currentBlock = null;
    state.editing = null;
    state.notice = null;
    state.draftEdits = {};
    state.draftMsg = {};
    state.momentForm = null;
    state.momentMsg = {};
    state.momentBusy = new Set();
    state.openDocs = new Set();
    state.audio = null;
    state.segRows = new Map();
    state.mainError = null;
    state.refs = null;
    state.mountKey = null;
    state.txKey = null;
    state.mapKey = null;
    state.idx = null;
  }

  async function route() {
    const hash = decodeURIComponent(location.hash.replace(/^#/, "")) || "today";
    const m = /^call\/([a-z0-9-]{1,64})$/.exec(hash);
    const sim = /^sim(?:\/([a-z0-9]{6}))?$/.exec(hash);
    const coach = /^([a-z]+)(?:\/([A-Za-z0-9_-]{1,64}))?$/.exec(hash);
    if (coach && COACH_PAGES.has(coach[1])) {
      if (state.view === "call") resetCallUi();
      state.view = "coach";
      state.coachPage = coach[1];
      state.coachArg = coach[2] || null;
      state.callId = null;
      state.call = null;
      renderList();
      renderMain();
      return;
    }
    if (hash === "new" || hash === "calls" || sim) {
      if (state.view === "call") resetCallUi();
      state.view = sim ? "sim" : hash;
      state.simCode = sim ? sim[1] || null : null;
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
        const cached = state.cache.get(id);
        state.call = cached ? cached.data : null;
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

  // ---------- calls: fetching, caching, live updates ----------

  // Responses can arrive out of order; each carries the order its request
  // was made in, and an older one never replaces a newer one.
  let callSeq = 0;

  async function fetchCall(id, fresh = false) {
    const seq = ++callSeq;
    const r = await request(`/api/calls/${enc(id)}`, { fresh });
    const prev = state.cache.get(id);
    if (prev && prev.seq > seq) return { entry: prev, unchanged: true, stale: true };
    const unchanged = !!(prev && r.etag && prev.etag === r.etag);
    const entry = unchanged ? Object.assign(prev, { seq }) : { data: r.data, etag: r.etag, seq };
    state.cache.delete(id);
    state.cache.set(id, entry);
    while (state.cache.size > CACHE_MAX) state.cache.delete(state.cache.keys().next().value);
    return { entry, unchanged };
  }

  async function loadCall(id, fresh = false) {
    const { entry, unchanged } = await fetchCall(id, fresh);
    if (state.view !== "call" || state.callId !== id) return;
    if (unchanged && state.call === entry.data) return; // already on screen
    state.call = entry.data;
    state.mainError = null;
    renderMain();
  }

  function prefetchCall(id) {
    if (!id || state.cache.has(id) || state.inflight.has(`/api/calls/${enc(id)}`)) return;
    fetchCall(id).catch(() => {});
  }

  function sortCalls() {
    // Same order as the server: newest folder name first.
    state.calls.sort((a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
  }

  function applyList(list) {
    state.calls = list;
    state.callsLoaded = true;
    state.listError = null;
    renderList();
  }

  function upsertCall(summary) {
    const i = state.calls.findIndex((c) => c.id === summary.id);
    if (i >= 0) state.calls[i] = summary;
    else {
      state.calls.push(summary);
      sortCalls();
    }
    renderList();
  }

  function onCallEvent(summary) {
    upsertCall(summary);
    if (state.view === "call" && state.callId === summary.id) {
      const shown = state.call;
      if (!shown || shown.updated_at !== summary.updated_at || shown.status !== summary.status) {
        loadCall(summary.id, true).catch((e) => setListError(e.message));
      }
    }
  }

  async function refreshList() {
    const list = await api("/api/calls");
    if (!state.callsLoaded || JSON.stringify(list) !== JSON.stringify(state.calls)) applyList(list);
    setListError(null);
  }

  function setListError(message) {
    state.listError = message || null;
    if (state.view === "calls") renderCalls($("#main"));
  }

  // Fallback while the event stream is down (or unsupported): poll like before.
  function schedule(ms) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(tick, ms);
  }

  async function tick() {
    if (state.stream.connected) return;
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
          await loadCall(state.callId, true);
        } catch (e) {
          setListError(e.message);
        }
      }
    }
    if (!state.stream.connected) schedule(state.calls.some((c) => isActive(c.status)) ? POLL_ACTIVE_MS : POLL_FALLBACK_MS);
  }

  function kickPoll() {
    if (!state.stream.connected) schedule(0);
  }

  // One event stream per tab. The simulator passes its room and client so the
  // same connection also carries the room's updates and WebRTC signalling
  // (browsers allow only six connections per origin across all tabs).
  function connectEvents(params) {
    if (typeof EventSource === "undefined") {
      schedule(0);
      return;
    }
    const qs = new URLSearchParams({ tab: TAB, ...(params || {}) }).toString();
    if (state.stream.es && state.stream.qs === qs && state.stream.es.readyState !== 2) return;
    if (state.stream.es) state.stream.es.close();
    const es = new EventSource(`/api/events?${qs}`);
    state.stream = { es, qs, connected: false };
    es.addEventListener("open", () => {
      if (state.stream.es !== es) return;
      state.stream.connected = true;
      clearTimeout(state.pollTimer);
      setListError(null);
      emit("stream", true);
    });
    es.addEventListener("error", () => {
      if (state.stream.es !== es) return;
      state.stream.connected = false;
      emit("stream", false);
      schedule(POLL_ACTIVE_MS); // EventSource reconnects by itself; poll meanwhile
    });
    for (const type of STREAM_EVENTS) {
      es.addEventListener(type, (e) => {
        if (state.stream.es !== es) return;
        let data = null;
        try {
          data = JSON.parse(e.data);
        } catch (_) {
          return;
        }
        emit(type, data);
      });
    }
  }

  on("hello", (data) => {
    state.sim = data.sim || null;
    if (JSON.stringify(data.people || []) !== JSON.stringify(state.people)) applyPeople(data.people);
    state.rooms = data.rooms || [];
    if (JSON.stringify(data.calls) !== JSON.stringify(state.calls)) applyList(data.calls || []);
    // After a reconnect anything may have changed; on the first connect the page just loaded it.
    if (state.helloSeen && state.view === "call" && state.callId) loadCall(state.callId, true).catch(() => {});
    state.helloSeen = true;
  });
  on("call", onCallEvent);
  on("call-deleted", (data) => {
    state.calls = state.calls.filter((c) => c.id !== data.id);
    state.cache.delete(data.id);
    renderList();
  });
  on("call-changed", (data) => {
    if (state.view === "call" && state.callId === data.id) loadCall(data.id, true).catch(() => {});
    else state.cache.delete(data.id);
  });
  // Coaching finished, or someone acted on a moment in another tab.
  on("coaching", (data) => {
    if (state.view === "call" && state.callId === data.id) loadCall(data.id, true).catch(() => {});
    else state.cache.delete(data.id);
  });
  on("people", applyPeople);
  on("lead", () => {
    if (state.leads) loadLeads();
  });
  on("rooms", (rooms) => {
    state.rooms = rooms || [];
  });

  // ---------- health ----------

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
    } catch (e) {
      state.health = { error: e.message };
    }
    renderHealth();
  }

  // "openai-compatible:qwen2.5:7b@http://…" → "qwen2.5:7b". The address stays on the hover tip.
  function shortModel(raw) {
    if (!raw) return "";
    const head = String(raw).split("@")[0];
    const cut = head.indexOf(":");
    return (cut >= 0 ? head.slice(cut + 1) : head).trim();
  }

  function speechModel(model) {
    if (!model) return "";
    return /whisper/i.test(model) ? model : `Whisper ${model}`;
  }

  function statusLine(ok, text, model, detail) {
    return el(
      "p",
      { class: `chip ${ok ? "chip-ok" : "chip-bad"}`, title: detail || null },
      el("span", { class: "dot", "aria-hidden": "true" }),
      el("span", { class: "health-label", text }),
      model ? el("span", { class: "health-model", text: model }) : null
    );
  }

  function renderHealth() {
    const node = $("#health");
    const h = state.health;
    if (!h) {
      node.textContent = "Checking…";
      return;
    }
    if (h.error) {
      node.replaceChildren(statusLine(false, "Couldn't check this computer", "", h.error));
      return;
    }
    const asr = h.asr || {};
    const llm = h.llm || {};
    const speechName = speechModel(asr.model);
    const reviewName = shortModel(llm.name);
    const speech = asr.ok
      ? ["Transcripts ready", speechName, asr.model ? `Speech model: ${asr.model}` : "Transcription is available."]
      : ["Transcripts not set up", speechName, asr.error || "Transcription is not available on this computer."];
    // A missing language model does not stop review: Sel falls back to its rules.
    const review = llm.ok
      ? ["Call review ready", reviewName, llm.name || "The language model answered."]
      : ["Review uses simple rules", reviewName, llm.error || llm.name || "The language model did not answer. Calls are still reviewed with Sel's rules."];
    node.replaceChildren(
      statusLine(!!asr.ok, speech[0], speech[1], speech[2]),
      statusLine(!!llm.ok, review[0], review[1], review[2])
    );
  }

  // ---------- bottom dock: review progress ----------

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
    const bar = list.closest(".review-dock");
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

  // Rows are rebuilt only when their call changed, and moved rather than recreated.
  // ---------- people: who is viewing ----------

  function viewer() {
    return state.people.find((p) => p.id === state.viewerId) || null;
  }

  function personName(id) {
    const p = state.people.find((x) => x.id === id);
    return p ? p.name : null;
  }

  function setViewer(id) {
    state.viewerId = id || null;
    store("sel.viewer", state.viewerId);
    renderViewer();
    renderList();
    if (state.view === "coach") renderMain();
    const rep = state.view === "new" ? $("#f-rep-person") : null;
    const person = viewer();
    if (rep && person && person.role === "rep" && !rep.value) rep.value = person.id;
    emit("viewer", viewer());
  }

  function applyPeople(list) {
    const hadPeople = state.people.length > 0;
    state.people = list || [];
    if (state.viewerId && !viewer()) state.viewerId = null;
    renderViewer();
    renderList();
    if (state.view === "call" && state.refs) renderHead(state.refs.head);
    // The new-call form can paint before the team arrives. Refresh it once
    // people land, before anyone has a reason to have filled it in.
    if (state.view === "new" && !hadPeople && state.people.length) renderMain();
    emit("people-changed", state.people);
  }

  // Decorative characters are keyed to immutable person IDs, never inferred from a name.
  function personAvatar(person, size = "") {
    let hash = 0;
    for (const char of String(person.id)) hash = (Math.imul(31, hash) + char.charCodeAt(0)) >>> 0;
    return el("span", { class: `person-avatar portrait-${hash % 6}${size ? ` avatar-${size}` : ""}`, "aria-hidden": "true" });
  }

  function renderViewer() {
    const sel = $("#viewer");
    if (!sel) return;
    const groups = [
      ["rep", "Reps"],
      ["manager", "Managers"],
    ];
    const opts = [el("option", { value: "", text: state.people.length ? "Everyone (no one chosen)" : "No people yet" })];
    for (const [role, label] of groups) {
      const people = state.people.filter((p) => p.role === role);
      if (people.length) opts.push(el("optgroup", { label }, ...people.map((p) => el("option", { value: p.id, text: p.name }))));
    }
    sel.replaceChildren(...opts);
    sel.value = viewer() ? state.viewerId : "";
    const portrait = $("#viewer-portrait");
    if (portrait) portrait.replaceChildren(viewer() ? personAvatar(viewer()) : icon("user", 20));
  }

  function renderNav() {
    const team = document.querySelector(".primary-nav a[data-nav=team]");
    if (team) team.hidden = !(viewer() && viewer().role === "manager");
    const active = state.view === "coach" ? NAV_OF[state.coachPage] || state.coachPage : state.view === "sim" ? "practice" : state.view === "calls" || state.view === "call" ? "calls" : null;
    for (const a of document.querySelectorAll(".primary-nav a[data-nav]")) {
      const on_ = a.dataset.nav === active;
      a.classList.toggle("is-active", on_);
      if (on_) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    const neu = $("#new-call");
    if (neu) {
      const onNew = state.view === "new";
      neu.classList.toggle("is-current", onNew);
      if (onNew) neu.setAttribute("aria-current", "page");
      else neu.removeAttribute("aria-current");
    }
  }

  function renderList() {
    renderNav();
    if (state.view === "calls") renderCalls($("#main"));
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
          class: `call-link card is-interactive${selected ? " is-selected" : ""}`,
          "aria-current": selected ? "page" : null,
          dataset: { id: c.id },
        },
        icon("phone", 20),
        el("span", { class: "call-title", text: title }),
        el(
          "span",
          { class: "call-sub" },
          el("span", { class: "call-who", text: [personName(c.rep_id) || c.rep_name, packName(c.pack_id)].filter(Boolean).join(" · ") }),
          c.started_at ? el("span", { class: "call-when", text: formatStarted(c.started_at) }) : null
        ),
        el(
          "span",
          { class: "call-meta" },
          pill(status.cls, status.label),
          c.source === "fixture" ? pill("neutral", "Example") : null,
          c.source === "simulator" ? pill("violet", "Simulated") : null
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

  function callsTable() {
    const rows = state.calls.map((c) => {
      const status = CALL_STATUS[c.status] || { cls: "neutral", label: c.status || "Unknown" };
      const title = c.customer_name || c.original_filename || c.id;
      return el("tr", { on: { click: (e) => {
        if (e.target.closest("a, button, input, select, textarea") || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || window.getSelection()?.toString()) return;
        navigate(`#call/${enc(c.id)}`);
      } } },
        el("th", { scope: "row" },
          el("a", { class: "calls-table-link", href: `#call/${enc(c.id)}`, dataset: { id: c.id } }, icon("phone", 16), title),
          c.source === "fixture" ? pill("neutral", "Example") : c.source === "simulator" ? pill("violet", "Simulated") : null),
        el("td", { text: c.started_at ? formatStarted(c.started_at) : "Not recorded" }),
        el("td", { text: personName(c.rep_id) || c.rep_name || "Unassigned" }),
        el("td", { text: packName(c.pack_id) }),
        el("td", {}, pill(status.cls, status.label), isActive(c.status)
          ? el("div", { class: "call-progress" },
              el("progress", { max: "1", value: String(c.progress || 0), "aria-label": `${title}: analysis progress` }),
              el("span", { class: "call-stage", text: `${stageText(c.stage)} · ${Math.round((c.progress || 0) * 100)}%` })) : null));
    });
    return el("div", { class: "calls-table-scroll", tabindex: "0", role: "region", "aria-label": "All calls table" },
      el("table", { class: "team-table calls-table", "aria-label": "All calls" },
        el("thead", {}, el("tr", {}, ...["Call", "Date", "Rep", "Business pack", "Status"].map((label) => el("th", { scope: "col", text: label })))),
        el("tbody", {}, ...rows)));
  }

  function renderCalls(main) {
    const head = el("header", { class: "page-head calls-head" },
      el("div", {}, el("h1", { text: "Calls" }),
        el("p", { class: "lede", text: "All recorded calls, including examples and simulated calls. Open a call to review it." })),
      el("button", { type: "button", class: "primary", text: "New call", on: { click: () => navigate("#new") } }));
    const nodes = [head];
    if (state.listError) {
      nodes.push(el("section", { class: "card is-error", role: "alert" },
        el("p", { class: "strong", text: "Calls could not be refreshed." }),
        muted(state.listError),
        el("button", { type: "button", text: "Try again", on: { click: () => refreshList().catch((e) => setListError(e.message)) } })));
    }
    if (!state.callsLoaded && !state.listError) {
      nodes.push(el("section", { class: "card is-loading", "aria-busy": "true" }, muted("Loading calls…")));
    } else if (state.callsLoaded && !state.calls.length) {
      nodes.push(el("section", { class: "card is-empty" },
        el("h2", { text: "No calls yet" }), muted("Choose New call to add a recording.")));
    } else if (state.calls.length) {
      const toggle = el("div", { class: "segmented", role: "group", "aria-label": "Calls view" });
      for (const [value, label] of [["table", "Table"], ["card", "Card"]]) {
        toggle.append(
          el("input", { type: "radio", id: `calls-view-${value}`, name: "calls-view", value, checked: state.callLayout === value,
            on: { change: () => {
              state.callLayout = value;
              store("sel.calls.view", value);
              renderCalls(main);
              $(`#calls-view-${value}`).focus();
            } } }),
          el("label", { for: `calls-view-${value}`, text: label }));
      }
      nodes.push(el("div", { class: "page-head" },
        el("h2", { class: "section-title", text: plural(state.calls.length, "call", "calls") }), toggle));
      nodes.push(state.callLayout === "table"
        ? el("section", { class: "list-card page-section" }, callsTable())
        : el("ul", { class: "calls-page-list", "aria-label": "All calls" }, ...state.calls.map((c) => callItem(c, false))));
    }
    main.replaceChildren(el("div", { class: "page" }, ...nodes));
  }

  // ---------- main pane dispatch ----------

  function renderMain() {
    const main = $("#main");
    renderSteps();
    main.classList.toggle(
      "is-call",
      state.view === "call" && !!state.call && state.call.id === state.callId && state.call.status === "done" && !!state.call.report
    );
    main.classList.toggle("is-sim", state.view === "sim");
    main.classList.toggle("is-coach", state.view === "coach");
    if (state.view === "calls") {
      renderCalls(main);
      return;
    }
    if (state.view === "coach") {
      renderCoach(main);
      return;
    }
    if (state.view === "new") {
      renderUpload(main);
      return;
    }
    if (state.view === "sim") {
      renderSim(main);
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
      main.replaceChildren(skeleton());
      return;
    }
    if (call.status !== "done" || !call.report) {
      stopPlayLoop();
      state.refs = null;
      state.mountKey = null;
      state.txKey = null;
      state.mapKey = null;
      state.audio = null;
      main.replaceChildren(renderProgress(call));
      return;
    }
    const key = `${call.id}|${call.audio_url || ""}`;
    if (key !== state.mountKey) mountCall(main, key);
    renderCallParts();
  }

  function skeleton() {
    return el(
      "div",
      { class: "skeleton", "aria-busy": "true", "aria-label": "Loading call" },
      el("span", { class: "sk sk-title" }),
      el("span", { class: "sk sk-line" }),
      el("span", { class: "sk sk-block" }),
      el("span", { class: "sk sk-line" }),
      el("span", { class: "sk sk-line short" })
    );
  }

  function renderWelcome(main) {
    main.replaceChildren(
      el(
        "section",
        { class: "welcome" },
        el("h1", { text: "Sel" }),
        el("p", { class: "lede", text: "Review recorded sales calls on this computer. Open Calls to pick a recording, add a new recording, or practise a call in the simulator." }),
        el(
          "div",
          { class: "row" },
          el("button", { type: "button", class: "primary", text: "New call", on: { click: () => navigate("#new") } }),
          el("button", { type: "button", text: "Call simulator", on: { click: () => navigate("#sim") } })
        )
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

  async function retryCall(id, transcribe) {
    try {
      const view = await api(`/api/calls/${enc(id)}/retry${transcribe ? `?transcribe=${enc(transcribe)}` : ""}`, { method: "POST" });
      state.cache.delete(id);
      if (state.callId === id) {
        state.call = view;
        state.mainError = null;
        renderMain();
      }
      kickPoll();
    } catch (e) {
      state.mainError = e.message;
      if (state.refs) {
        state.notice = { kind: "error", text: e.message };
        renderNotice(state.refs.notice);
      } else renderMain();
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
      on: { click: clearHighlight },
    });
    const talk = el("section", { class: "talk", "aria-labelledby": "talk-title" });
    const tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Review" });
    const panel = el("div", { class: "panel", id: "panel", role: "tabpanel" });
    const map = el("div", { class: "callmap", "aria-hidden": "true", title: "Call map: click to play from that moment" });
    map.addEventListener("click", (e) => seekFromMap(e));
    // One listener for every transcript row instead of two per row.
    list.addEventListener("click", onTranscriptClick);
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

    stopPlayLoop();
    if (call.audio_url) {
      const audio = el("audio", { controls: true, preload: "metadata", src: call.audio_url });
      audio.addEventListener("timeupdate", syncPlayhead);
      audio.addEventListener("seeked", syncPlayhead);
      audio.addEventListener("ratechange", syncPlayhead);
      audio.addEventListener("play", startPlayLoop);
      audio.addEventListener("pause", stopPlayLoop);
      audio.addEventListener("ended", stopPlayLoop);
      state.audio = audio;
      player.append(audio);
    } else {
      state.audio = null;
      player.append(el("p", { class: "no-audio", text: "No recording for this call." }));
    }

    const keysHint = el(
      "button",
      {
        type: "button",
        class: "keys-hint small ghost",
        "aria-expanded": "false",
        title: "Keyboard shortcuts",
        on: { click: () => toggleKeys() },
      },
      icon("keyboard", 16),
      "Keys"
    );
    const keys = el(
      "div",
      { class: "keys", hidden: true, role: "note" },
      el(
        "dl",
        {},
        ...[["Space", "Play or pause"], ["1 – 4", "Switch tab"], ["J / K", "Next or previous card"], ["Enter", "Play the focused card"], ["Esc", "Clear the highlight"]].flatMap(([k, v]) => [
          el("dt", {}, el("kbd", { text: k })),
          el("dd", { text: v }),
        ])
      )
    );

    state.refs = { head, tiles, notice, list, txSub, clear, talk, tabs, panel, map, keys, keysHint };
    state.mountKey = key;
    state.segRows = new Map();
    state.mapBlocks = new Map();
    state.txKey = null;
    state.mapKey = null;

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
            el("div", {}, el("h2", { text: "Call review" }), el("p", { class: "card-sub", text: "Findings, claim checks and drafts" })),
            keysHint
          ),
          keys,
          talk,
          tabs,
          panel
        )
      )
    );
  }

  function toggleKeys(force) {
    if (!state.refs) return;
    state.showKeys = force === undefined ? !state.showKeys : force;
    state.refs.keys.hidden = !state.showKeys;
    state.refs.keysHint.setAttribute("aria-expanded", state.showKeys ? "true" : "false");
  }

  function transcriptKey() {
    const t = state.call.report.transcript;
    const edits = (state.call.review && state.call.review.transcript_edits) || [];
    return `${state.call.id}|${t.segments.length}|${edits.length}`;
  }

  function mapKeyOf() {
    const segs = state.call.report.transcript.segments;
    return `${state.call.id}|${segs.length}|${segs.length ? segs[segs.length - 1].end : 0}`;
  }

  function renderCallParts() {
    const r = state.refs;
    if (!r || !state.call) return;
    index();
    renderHead(r.head);
    renderSummary(r.tiles);
    renderNotice(r.notice);
    const mk = mapKeyOf();
    if (mk !== state.mapKey) renderCallMap(r.map, mk);
    if (transcriptKey() !== state.txKey) renderTranscript(r.list);
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

    const coached = coachingOf();
    const weak = coached ? coached.moments.filter((m) => m.kind === "weak" && m.state !== "dismissed").length : 0;
    const spec = [
      ["coaching", "What happened?", `${plural(count("question"), "question", "questions")}, ${plural(count("objection"), "objection", "objections")}`, ["violet", coached ? plural(weak, "moment to improve", "moments to improve") : plural(count("coaching"), "coaching point", "coaching points")]],
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

  function renderCallMap(map, key) {
    const idx = index();
    state.mapBlocks = new Map();
    state.currentBlock = null;
    const frag = document.createDocumentFragment();
    for (const seg of idx.list) {
      const sp = idx.speakers.get(seg.speaker);
      const b = document.createElement("span");
      b.className = `callmap-block role-${sp ? sp.role : "other"}${state.evidence.has(seg.id) ? " is-evidence" : ""}`;
      b.style.flex = `${Math.max(0.5, seg.end - seg.start)} 1 0`;
      b.dataset.start = String(seg.start);
      state.mapBlocks.set(seg.id, b);
      frag.append(b);
    }
    map.replaceChildren(frag);
    state.mapKey = key;
    markPlayback();
  }

  function seekFromMap(e) {
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
    const owner = state.people.length ? repPicker(call) : null;
    const lead = call.kind !== "practice" ? leadPicker(call) : null;
    const rules = String(meta.analyzer || "").startsWith("rules");
    const sim = call.source === "simulator";
    head.replaceChildren(
      ...compact([
        el(
          "div",
          { class: "head-main" },
          el("p", { class: "eyebrow", text: packName(call.pack_id) }),
          el("h1", { text: title }),
          el("p", { class: "meta", text: when }),
          owner,
          lead,
          el("p", { class: "meta small", text: `Transcribed by ${meta.transcriber || "unknown"} · analysed by ${meta.analyzer || "unknown"}` }),
          timings ? el("p", { class: "meta small", text: `Timings: ${timings}` }) : null
        ),
        call.source === "fixture"
          ? el("div", { class: "banner", role: "note" }, el("strong", { text: "Example data:" }), " hand-written fixture, not model output")
          : null,
        sim || rules
          ? el(
              "div",
              { class: "banner banner-row", role: "note" },
              el(
                "span",
                {},
                sim ? el("strong", { text: "Recorded in the call simulator. " }) : null,
                rules
                  ? "Analysed by Sel's rules because no language model answered: claims are left unverified. Start a model and re-analyse for verdicts."
                  : "Transcript captured live during the call."
              ),
              el(
                "span",
                { class: "row" },
                sim || call.source === "audio"
                  ? el("button", { type: "button", class: "small", text: "Re-analyse", on: { click: () => retryCall(call.id) } })
                  : null,
                sim && call.audio_file
                  ? el("button", { type: "button", class: "small", text: "Re-transcribe recording", on: { click: () => retryCall(call.id, "recording") } })
                  : null
              )
            )
          : null,
      ])
    );
  }

  // Whose call this is, for Today and coaching. Changing it doesn't touch the transcript's speaker names.
  function repPicker(call) {
    const sel = el("select", { id: "call-rep", class: "small-select" }, el("option", { value: "", text: "No rep" }));
    for (const p of state.people.filter((x) => x.role === "rep" || x.id === call.rep_id)) sel.append(el("option", { value: p.id, text: p.name }));
    sel.value = call.rep_id || "";
    const msg = el("span", { class: "muted small", role: "status" });
    sel.addEventListener("change", async () => {
      sel.disabled = true;
      try {
        const summary = await api(`/api/calls/${enc(call.id)}/rep`, { method: "PUT", json: { rep_id: sel.value || null } });
        call.rep_id = summary.rep_id;
        upsertCall(summary);
        msg.textContent = "Saved.";
      } catch (e) {
        sel.value = call.rep_id || "";
        msg.textContent = e.message;
      }
      sel.disabled = false;
    });
    return el("p", { class: "meta rep-picker" }, el("label", { for: "call-rep", text: "Whose call: " }), sel, " ", msg);
  }

  // Leads for the pickers on the upload form and the call page; re-read on every `lead` event.
  async function loadLeads() {
    try {
      state.leads = await api("/api/leads", { fresh: true });
    } catch (_) {
      state.leads = state.leads || [];
    }
    fillLeadSelect($("#f-lead"));
    fillLeadSelect($("#call-lead"), state.call ? state.call.lead_id : null);
  }

  function fillLeadSelect(sel, current) {
    if (!sel) return;
    const keep = current !== undefined ? current : sel.value;
    const leads = state.leads || [];
    sel.replaceChildren(
      el("option", { value: "", text: state.leads === null ? "Loading leads…" : leads.length ? "No lead" : "No leads yet" }),
      ...leads.map((l) => el("option", { value: l.id, text: l.rep_name ? `${l.name} (${l.rep_name})` : l.name }))
    );
    sel.value = keep && leads.some((l) => l.id === keep) ? keep : "";
  }

  // Which lead this call was with: Prep reads the lead's earlier calls before the next one.
  function leadPicker(call) {
    const sel = el("select", { id: "call-lead", class: "small-select" });
    fillLeadSelect(sel, call.lead_id || "");
    if (state.leads === null) loadLeads();
    const msg = el("span", { class: "muted small", role: "status" });
    const prep = el("a", { class: "small", href: call.lead_id ? `#prep/${enc(call.lead_id)}` : "#leads", text: call.lead_id ? "Prep" : "Leads" });
    sel.addEventListener("change", async () => {
      sel.disabled = true;
      try {
        const summary = await api(`/api/calls/${enc(call.id)}/lead`, { method: "PUT", json: { lead_id: sel.value || null } });
        call.lead_id = summary.lead_id;
        upsertCall(summary);
        prep.href = call.lead_id ? `#prep/${enc(call.lead_id)}` : "#leads";
        prep.textContent = call.lead_id ? "Prep" : "Leads";
        msg.textContent = "Saved.";
        emit("lead", { id: call.lead_id }); // this tab's own change is not echoed on the stream
      } catch (e) {
        sel.value = call.lead_id || "";
        msg.textContent = e.message;
      }
      sel.disabled = false;
    });
    return el("p", { class: "meta rep-picker" }, el("label", { for: "call-lead", text: "Lead: " }), sel, " ", prep, " ", msg);
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

  // Rows go into chunks of TX_CHUNK; the browser skips layout and paint for
  // chunks that are off screen (content-visibility on .tx-chunk). Chunks, not
  // rows, so a layout elsewhere on the page walks a few dozen boxes, not
  // every line of a long call. The chunk wrappers have no list semantics.
  const TX_CHUNK = 40;

  function renderTranscript(list) {
    const idx = index();
    state.segRows = new Map();
    state.currentRow = null;
    const frag = document.createDocumentFragment();
    let rows = null;
    idx.list.forEach((seg, i) => {
      if (i % TX_CHUNK === 0) {
        rows = document.createElement("ol");
        rows.className = "tx-rows";
        rows.setAttribute("role", "none");
        const chunk = document.createElement("li");
        chunk.className = "tx-chunk";
        chunk.setAttribute("role", "none");
        chunk.style.containIntrinsicSize = `auto ${Math.min(TX_CHUNK, idx.list.length - i) * 64}px`;
        chunk.append(rows);
        frag.append(chunk);
      }
      const li = segmentRow(seg, idx.speakers.get(seg.speaker) || null);
      state.segRows.set(seg.id, li);
      rows.append(li);
    });
    list.replaceChildren(frag);
    state.marked = new Set([...state.evidence].filter((id) => state.segRows.has(id)));
    state.txKey = transcriptKey();
    if (state.refs && state.refs.txSub) {
      state.refs.txSub.textContent = `${plural(idx.list.length, "line", "lines")} · choose a time to play from there`;
    }
    markPlayback();
    markEvidenceChrome();
  }

  // Rows are cloned from this constant markup and filled with textContent;
  // about four times faster than building each element for a long call.
  const ROW = (() => {
    const t = document.createElement("template");
    t.innerHTML =
      '<li class="seg"><button type="button" class="ts"></button><div class="seg-body"><p class="who"><span></span></p>' +
      '<p class="text"></p></div><button type="button" class="edit-btn small">Edit</button></li>';
    return t.content.firstChild;
  })();

  function segmentRow(seg, speaker) {
    const editing = state.editing === seg.id;
    const role = speaker ? speaker.role : "other";
    const stamp = mmss(seg.start);
    const li = ROW.cloneNode(true);
    li.className = `seg role-${role}${editing ? " is-editing" : ""}${state.evidence.has(seg.id) ? " is-evidence" : ""}`;
    li.dataset.id = seg.id;
    const ts = li.firstChild;
    const body = ts.nextSibling;
    const edit = body.nextSibling;
    ts.textContent = stamp;
    ts.title = `Play from ${stamp}`;
    ts.setAttribute("aria-label", `Play from ${stamp}`);
    const who = body.firstChild;
    who.firstChild.textContent = speakerLabel(speaker);
    if (seg.lang) who.append(el("span", { class: "lang", text: seg.lang }));
    if (editing) {
      body.lastChild.replaceWith(editForm(seg));
      edit.remove();
    } else {
      body.lastChild.textContent = seg.text;
      edit.setAttribute("aria-label", `Edit the line at ${stamp}`);
    }
    return li;
  }

  // Replace one transcript row in place (editing, saving), keeping its highlight state.
  function replaceRow(id) {
    const old = state.segRows.get(id);
    const seg = segmentById(id);
    if (!old || !seg) return null;
    const row = segmentRow(seg, index().speakers.get(seg.speaker) || null);
    old.replaceWith(row);
    state.segRows.set(id, row);
    if (state.currentRow === old) {
      row.classList.add("is-current");
      state.currentRow = row;
    }
    return row;
  }

  function onTranscriptClick(e) {
    const row = e.target.closest(".seg");
    if (!row || !state.refs || !state.refs.list.contains(row)) return;
    const id = row.dataset.id;
    if (e.target.closest(".edit-btn")) {
      e.stopPropagation();
      startEdit(id);
      return;
    }
    if (row.classList.contains("is-editing")) return;
    const seg = segmentById(id);
    if (seg) seekTo(seg.start);
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

  // Moves the "current" mark between two rows; never walks the whole transcript.
  function markPlayback() {
    const row = state.current ? state.segRows.get(state.current) || null : null;
    if (state.currentRow !== row) {
      if (state.currentRow) state.currentRow.classList.remove("is-current");
      if (row) row.classList.add("is-current");
      state.currentRow = row;
    }
    const block = state.current ? state.mapBlocks.get(state.current) || null : null;
    if (state.currentBlock !== block) {
      if (state.currentBlock) state.currentBlock.classList.remove("is-current");
      if (block) block.classList.add("is-current");
      state.currentBlock = block;
    }
  }

  // Draws state.evidence by touching only the rows that changed.
  function markEvidence() {
    const next = state.evidence;
    for (const id of state.marked) {
      if (next.has(id)) continue;
      const row = state.segRows.get(id);
      const block = state.mapBlocks.get(id);
      if (row) row.classList.remove("is-evidence");
      if (block) block.classList.remove("is-evidence");
    }
    for (const id of next) {
      if (state.marked.has(id)) continue;
      const row = state.segRows.get(id);
      const block = state.mapBlocks.get(id);
      if (row) row.classList.add("is-evidence");
      if (block) block.classList.add("is-evidence");
    }
    state.marked = new Set(next);
    markEvidenceChrome();
  }

  function markEvidenceChrome() {
    if (state.refs && state.refs.map) state.refs.map.classList.toggle("has-evidence", state.evidence.size > 0);
    if (state.refs && state.refs.clear) state.refs.clear.hidden = state.evidence.size === 0;
  }

  function clearHighlight() {
    state.evidence = new Set();
    state.focusItem = null;
    markEvidence();
    markFocused();
  }

  function markFocused() {
    if (!state.refs) return;
    for (const card of state.refs.panel.querySelectorAll("[data-item]")) {
      card.classList.toggle("is-focused", !!state.focusItem && card.dataset.item === state.focusItem);
    }
  }

  // The highlight moves exactly when the next line starts: one timer per line
  // change, re-armed on play, seek and rate change. timeupdate (about four
  // times a second) is the safety net. Each update is a binary search plus
  // two class changes.
  function syncPlayhead() {
    if (!state.audio || !state.call || !state.call.report) return;
    const id = segmentAt(state.audio.currentTime);
    if (id !== state.current) {
      state.current = id;
      markPlayback();
      if (state.currentRow && !state.audio.paused) state.currentRow.scrollIntoView({ block: "nearest" });
    }
    if (!state.audio.paused) armPlayhead();
  }

  function armPlayhead() {
    stopPlayLoop();
    const audio = state.audio;
    const idx = index();
    if (!audio || !idx || audio.paused) return;
    const t = audio.currentTime;
    const at = state.current ? idx.list.findIndex((s) => s.id === state.current) : -1;
    let next = at + 1;
    if (at < 0) {
      next = 0;
      while (next < idx.list.length && idx.starts[next] <= t) next += 1;
    }
    if (next >= idx.list.length) return;
    const wait = ((idx.starts[next] - t) / (audio.playbackRate || 1)) * 1000;
    state.raf = setTimeout(syncPlayhead, Math.max(0, wait) + 15);
  }

  function startPlayLoop() {
    syncPlayhead();
    armPlayhead();
  }

  function focusEvidence(segmentIds, play = true) {
    const known = (segmentIds || []).filter((id) => state.segRows.has(id));
    if (!known.length) return;
    state.evidence = new Set(known);
    markEvidence();
    scrollRow(state.segRows.get(known[0]), "center");
    if (play) seekTo(segmentById(known[0]).start);
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
    const prev = state.editing;
    state.editing = id;
    if (prev && prev !== id) replaceRow(prev);
    const row = replaceRow(id);
    const ta = row && row.querySelector(".edit-text");
    if (ta) {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
  }

  function cancelEdit() {
    const id = state.editing;
    state.editing = null;
    if (id) replaceRow(id);
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
      if (state.call.review) state.call.review.transcript_edits.push({ segment_id: id, new: text });
      state.editing = null;
      state.notice = { kind: "info", text: describeEdit(data) };
      replaceRow(id);
      state.txKey = transcriptKey(); // only this row changed
      renderCallParts();
    } catch (e) {
      btn.disabled = false;
      setFormError(form, e.message);
    }
  }

  // ---------- tabs ----------

  function renderTabs(tabs) {
    const r = state.call.report;
    const coached = coachingOf();
    const counts = {
      coaching: coached ? coached.moments.length + r.findings.filter((f) => f.kind !== "coaching").length : r.findings.length,
      claims: r.claims.length,
      followups: r.commitments.length,
      refused: r.rejected.length,
    };
    tabs.replaceChildren(
      ...TABS.map(([id, label], i) => {
        const selected = state.tab === id;
        return el(
          "button",
          {
            type: "button",
            role: "tab",
            id: `tab-${id}`,
            class: selected ? "tab is-selected" : "tab",
            "aria-selected": selected ? "true" : "false",
            "aria-controls": "panel",
            tabindex: selected ? "0" : "-1",
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
    if (state.tab === id || !state.refs) return;
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

  // ---------- scorecard ----------

  function scorecardView(score, liveScore) {
    if (!score || !score.criteria || !score.criteria.length) return null;
    const overall = score.overall;
    const rows = score.criteria.map((c) => {
      const s = SCORE_STATUS[c.status] || { cls: "neutral", label: c.status };
      const seg = c.segment ? segmentById(c.segment) : null;
      return el(
        "li",
        { class: `score-row status-${c.status}` },
        el("div", { class: "score-row-head" }, el("span", { class: "score-title", text: c.title }), pill(s.cls, s.label)),
        c.detail ? el("p", { class: "score-detail", text: c.detail }) : null,
        seg
          ? el(
              "button",
              {
                type: "button",
                class: "ref-btn small",
                title: `Play from ${mmss(seg.start)}`,
                on: { click: () => focusEvidence([seg.id]) },
              },
              icon("play", 11),
              mmss(seg.start)
            )
          : null
      );
    });
    return el(
      "section",
      { class: "scorecard", "aria-labelledby": "scorecard-title" },
      el(
        "div",
        { class: "scorecard-head" },
        el(
          "div",
          {},
          el("h3", { id: "scorecard-title", class: "group-title", text: "Scorecard" }),
          el("p", { class: "card-sub", text: "Graded by code from the reviewed findings, against the pack's scorecard." })
        ),
        el(
          "div",
          { class: "score-big", "aria-label": overall === null ? "No score" : `Score ${overall} out of 100` },
          el("span", { text: overall === null ? "–" : String(overall) }),
          el("small", { text: "/100" })
        )
      ),
      liveScore && liveScore.overall !== null && liveScore.overall !== undefined
        ? el("p", { class: "card-sub", text: `Live score at hang-up: ${liveScore.overall}/100` })
        : null,
      el("ul", { class: "score-list" }, ...rows)
    );
  }

  // ---------- coaching ----------

  // coaching.json for the call on screen: moments, better lines, refused lines (sel/coach.py).
  function coachingOf() {
    const c = state.call && state.call.coaching;
    return c && Array.isArray(c.moments) ? c : null;
  }

  function coachingStatus() {
    const call = state.call;
    const st = call.coaching_status;
    if (st === "queued" || st === "running") {
      return el(
        "section",
        { class: "card is-loading", "aria-busy": "true", role: "status" },
        el("p", { class: "strong", text: "Sel is coaching this call" }),
        el("p", { class: "muted small", text: `${stageText(call.coaching_stage || "finding moments")}. The review below is ready; moments and better lines appear here when they are done.` })
      );
    }
    const c = coachingOf();
    if (c && c.error) {
      return el("section", { class: "card is-error", role: "alert" }, el("p", { class: "strong", text: "The coaching model did not finish." }), el("p", { class: "muted small", text: c.error }));
    }
    return null;
  }

  function coachingView(report) {
    const wrap = el("div", { class: "stack" });
    const card = scorecardView(report.score, state.call.live_score);
    if (card) wrap.append(card);
    const status = coachingStatus();
    if (status) wrap.append(status);
    const coached = coachingOf();
    if (coached) {
      const weak = sortDismissedLast(coached.moments.filter((m) => m.kind === "weak"), (m) => m.id);
      const strong = sortDismissedLast(coached.moments.filter((m) => m.kind === "strong"), (m) => m.id);
      if (!coached.moments.length) wrap.append(muted("Sel found no coaching moments in this call."));
      if (weak.length) {
        wrap.append(
          el(
            "section",
            { class: "group" },
            el("div", {}, el("h3", { class: "group-title", text: "Where to improve" }), el("p", { class: "card-sub", text: linesExplainer(coached) })),
            ...weak.map((m) => momentCard(m, coached))
          )
        );
      }
      if (strong.length) {
        wrap.append(el("section", { class: "group" }, el("h3", { class: "group-title", text: "What worked" }), ...strong.map((m) => momentCard(m, coached))));
      }
    } else if (!report.findings.length) wrap.append(muted("Sel found no questions, objections or coaching points in this call."));
    // With moments on screen, the analysis's own coaching points are the same advice twice.
    for (const kind of coached ? ["objection", "question"] : ["coaching", "objection", "question"]) {
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

  function linesExplainer(coached) {
    const by = (coached.by && coached.by.lines) || "";
    if (!by) return "No language model wrote lines for this call, so Sel shows what good looks like from your scorecard.";
    if (by.startsWith("fixture")) return "Example data: these better lines are hand-written stand-ins for the model, checked against your pack like model lines.";
    return "Better lines are AI-suggested. Sel checks every product fact in them against your pack and shows the lines it refused, with the reason.";
  }

  // ---------- coaching moments ----------

  const MOMENT_TAG = { weak: { cls: "warn", label: "Fell short", icon: "target" }, strong: { cls: "ok", label: "Did well", icon: "check" } };

  function lineOrigin(line) {
    const by = String(line.by || "");
    if (by.startsWith("person")) return ["neutral", "Written by a person"];
    if (by.startsWith("fixture")) return ["neutral", "Example line"];
    if (by.startsWith("playbook")) return ["info", "Team answer"];
    return ["violet", "AI-suggested"];
  }

  function lineCheck(line) {
    if (line.check === "rejected") return ["bad", "Refused"];
    if (!line.facts || !line.facts.length) return ["neutral", "No claims to check"];
    return ["ok", "Checked against your pack"];
  }

  function saidBlock(m) {
    const rep = speakerOf(m.evidence.segment_ids[0]);
    const cust = m.customer_said ? speakerOf(m.customer_said.segment_id) : null;
    return el(
      "div",
      { class: "said" },
      m.customer_said
        ? el("p", { class: "said-line role-customer" }, el("span", { class: "said-who", text: speakerLabel(cust) || "Customer" }), quoted(m.customer_said.quote))
        : null,
      el("p", { class: "said-line role-rep" }, el("span", { class: "said-who", text: speakerLabel(rep) || "Rep" }), quoted(m.evidence.quote))
    );
  }

  function betterLine(m, coached) {
    const line = m.better_line;
    if (!line) {
      const crit = packOf(state.call.pack_id) && packOf(state.call.pack_id).scorecard.find((c) => c.id === m.criterion);
      const text = m.rejected_lines && m.rejected_lines.length
        ? "Sel kept no line for this moment: every line it tried was refused against your pack."
        : coached.by && coached.by.lines
          ? "No better line was written for this moment."
          : `What good looks like: ${crit ? crit.description : m.title}`;
      return el("div", { class: "better-line is-empty" }, el("p", { class: "small", text }));
    }
    const [ocls, olabel] = lineOrigin(line);
    const [ccls, clabel] = lineCheck(line);
    return el(
      "div",
      { class: "better-line" },
      el("div", { class: "better-line-head" }, el("span", { class: "label", text: "Better line" }), pill(ocls, olabel), pill(ccls, clabel)),
      el("p", { class: "better-line-text", text: quoted(line.text) }),
      line.check === "rejected" && line.reasons && line.reasons.length ? el("ul", { class: "reasons" }, ...line.reasons.map((r) => el("li", { text: `Sel refused it: it ${r}` }))) : null,
      line.sources && line.sources.length ? el("div", { class: "sources" }, ...line.sources.map(sourceBlock)) : null
    );
  }

  function refusedLines(m) {
    const lines = m.rejected_lines || [];
    if (!lines.length) return null;
    return el(
      "details",
      { class: "disclosure" },
      el("summary", { text: `Sel refused ${plural(lines.length, "line", "lines")} for this moment` }),
      el(
        "ul",
        { class: "refused-lines" },
        ...lines.map((line) =>
          el(
            "li",
            {},
            el("div", { class: "better-line-head" }, pill(...lineOrigin(line)), pill("bad", "Refused")),
            line.text ? el("p", { class: "refused-text", text: quoted(line.text) }) : null,
            el("ul", { class: "reasons" }, ...(line.reasons || []).map((r) => el("li", { text: `It ${r}` })))
          )
        )
      )
    );
  }

  function momentForm(m) {
    const f = state.momentForm;
    if (!f || f.id !== m.id) return null;
    const isEdit = f.kind === "edit";
    const id = `mf-${m.id}`;
    const ta = el("textarea", {
      id,
      class: "input",
      rows: isEdit ? "3" : "2",
      maxlength: isEdit ? "400" : "500",
      value: isEdit ? (m.better_line ? m.better_line.text : "") : m.note || "",
    });
    const save = el("button", { type: "submit", class: "primary small", text: isEdit ? "Check and save" : "Save note" });
    const cancel = el("button", { type: "button", class: "small", text: "Cancel", on: { click: () => closeMomentForm() } });
    const form = el(
      "form",
      { class: "moment-form", novalidate: true },
      el("label", { for: id, text: isEdit ? "Your better line" : "Note for this moment" }),
      ta,
      isEdit ? el("p", { class: "hint small", text: "Sel checks any product fact in your line against the pack before keeping it." }) : null,
      el("div", { class: "row" }, save, cancel)
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const value = ta.value.trim();
      if (isEdit && !value) {
        state.momentMsg[m.id] = { kind: "error", text: "Write a line first." };
        renderPanel();
        return;
      }
      momentAction(m.id, f.kind, isEdit ? { text: value } : { note: value });
    });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeMomentForm();
      }
    });
    return form;
  }

  function closeMomentForm() {
    const { id, kind } = state.momentForm || {};
    state.momentForm = null;
    renderPanel();
    const btn = id && state.refs.panel.querySelector(`[data-item="${id}"] [data-action="${kind}"]`);
    if (btn) btn.focus();
  }

  function openMomentForm(id, kind) {
    state.momentForm = { id, kind };
    delete state.momentMsg[id];
    renderPanel();
    const ta = state.refs.panel.querySelector(`#mf-${id}`);
    if (ta) {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
  }

  function momentCard(m, coached) {
    const dismissed = m.state === "dismissed";
    const tag = MOMENT_TAG[m.kind] || MOMENT_TAG.weak;
    const segIds = m.evidence.segment_ids || [];
    const busy = state.momentBusy.has(m.id);
    const msg = state.momentMsg[m.id];
    const line = m.better_line;
    const endorsed = m.state === "endorsed";
    const actions = [evidenceButton(segIds, m.id)];
    if (m.kind === "weak" && !dismissed) {
      if (line && line.check === "ok") {
        actions.push(
          el("button", {
            type: "button",
            class: "small",
            text: endorsed ? "Endorsed" : "Endorse line",
            dataset: { action: "endorse" },
            "aria-pressed": endorsed ? "true" : "false",
            disabled: busy,
            on: { click: () => momentAction(m.id, "endorse", { on: !endorsed }) },
          })
        );
      }
      actions.push(el("button", { type: "button", class: "small moment-edit", text: line ? "Edit line" : "Write a line", dataset: { action: "edit" }, disabled: busy, on: { click: () => openMomentForm(m.id, "edit") } }));
      if (state.call.kind !== "practice") actions.push(el("button", { type: "button", class: "small", text: "Practise this (90 s)", dataset: { action: "drill" }, disabled: busy, on: { click: () => practise(m.id) } }));
    }
    if (!dismissed) actions.push(el("button", { type: "button", class: "small ghost", text: m.note ? "Edit note" : "Add note", dataset: { action: "note" }, disabled: busy, on: { click: () => openMomentForm(m.id, "note") } }));
    actions.push(
      el("button", {
        type: "button",
        class: "small ghost push",
        text: dismissed ? "Restore" : "Dismiss",
        "aria-pressed": dismissed ? "true" : "false",
        dataset: { action: "dismiss" },
        disabled: busy,
        on: { click: () => momentAction(m.id, "dismiss", { on: !dismissed }) },
      })
    );
    const stateTag = dismissed ? ["neutral", "Dismissed"] : endorsed ? ["ok", "Endorsed"] : m.state === "edited" ? ["info", "Edited"] : null;
    return el(
      "article",
      { class: `card moment${dismissed ? " is-dismissed" : ""}`, dataset: { item: m.id, segs: segIds.join(" ") }, tabindex: "-1", "aria-busy": busy ? "true" : null },
      el("div", { class: "card-tags" }, cardTag(dismissed ? "neutral" : tag.cls, dismissed ? "Dismissed" : tag.label), stateTag && !dismissed ? pill(...stateTag) : null),
      cardHead(dismissed ? "neutral" : tag.cls, tag.icon, m.title, saidBy(segIds)),
      saidBlock(m),
      m.why ? labelled(m.kind === "weak" ? "What fell short" : "What worked", m.why) : null,
      m.kind === "weak" && !dismissed ? betterLine(m, coached) : null,
      m.kind === "weak" && !dismissed ? refusedLines(m) : null,
      m.note && !(state.momentForm && state.momentForm.id === m.id && state.momentForm.kind === "note") ? labelled("Note", m.note) : null,
      momentForm(m),
      msg ? el("p", { class: `form-status is-${msg.kind}`, role: "status", text: msg.text }) : null,
      el("div", { class: "card-actions" }, ...actions)
    );
  }

  // A drill on this moment for the call's rep (or the viewer), then its page.
  async function practise(momentId) {
    const me = viewer();
    try {
      const d = await api(`/api/calls/${enc(state.callId)}/moments/${enc(momentId)}/drill`, { method: "POST", json: { by: me ? me.id : null } });
      navigate(`#practice/${enc(d.id)}`);
    } catch (e) {
      state.momentMsg[momentId] = { kind: "error", text: e.status === 400 ? `${e.message}. Choose whose call this is above.` : e.message };
      renderPanel();
    }
  }

  async function momentAction(momentId, action, body) {
    const callId = state.callId;
    const me = viewer();
    state.momentBusy.add(momentId);
    renderPanel();
    try {
      const updated = await api(`/api/calls/${enc(callId)}/moments/${enc(momentId)}/${action}`, { method: "POST", json: { ...body, by: me ? me.id : null } });
      if (state.callId !== callId || !coachingOf()) return;
      const list = state.call.coaching.moments;
      const i = list.findIndex((x) => x.id === momentId);
      if (i >= 0) list[i] = updated;
      if (action === "edit") {
        state.momentMsg[momentId] = updated.better_line && updated.better_line.check === "ok"
          ? { kind: "ok", text: "Saved. Your line passed the check against the pack." }
          : { kind: "error", text: "Saved, but Sel refused your line against the pack. The reasons are above." };
      } else delete state.momentMsg[momentId];
      if (action === "edit" || action === "note") state.momentForm = null;
    } catch (e) {
      if (state.callId !== callId) return;
      state.momentMsg[momentId] = { kind: "error", text: e.message };
    } finally {
      state.momentBusy.delete(momentId);
    }
    if (state.callId === callId && state.refs) {
      renderPanel();
      renderSummary(state.refs.tiles);
      // The panel was redrawn: give focus back to the control that was used.
      const btn = state.refs.panel.querySelector(`[data-item="${momentId}"] [data-action="${action}"]`);
      if (btn && (!document.activeElement || document.activeElement === document.body)) btn.focus();
    }
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
      { class: `card finding${dismissed ? " is-dismissed" : ""}`, dataset: { item: f.id, segs: segIds.join(" ") }, tabindex: "-1" },
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

  // Optimistic: the card changes at once and changes back if the server refuses.
  async function setDismissed(itemId, dismiss) {
    const callId = state.callId;
    const review = state.call.review;
    const before = review.dismissed.slice();
    review.dismissed = dismiss ? [...new Set([...before, itemId])].sort() : before.filter((x) => x !== itemId);
    renderPanel();
    try {
      const saved = await api(`/api/calls/${enc(callId)}/items/${enc(itemId)}/${dismiss ? "dismiss" : "restore"}`, { method: "POST" });
      if (state.callId === callId && state.call) state.call.review = saved;
    } catch (e) {
      if (state.callId !== callId) return;
      review.dismissed = before;
      renderPanel();
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
      { class: "card claim", dataset: { item: c.id, segs: segIds.join(" ") }, tabindex: "-1" },
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
    const segIds = c.evidence.segment_ids || [];
    return el(
      "article",
      { class: "card commitment", dataset: { item: c.id, segs: segIds.join(" ") }, tabindex: "-1" },
      cardTag(tag.cls, tag.label),
      cardHead(tone, iconName, action, `${who} · ${c.channel} · ${said}`),
      dueLine(c.due),
      el("div", { class: "card-actions" }, evidenceButton(segIds, c.id)),
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
    renderSummary(state.refs.tiles);
    renderSteps();
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
    renderSummary(state.refs.tiles);
    renderSteps();
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

  // ---------- keyboard ----------

  function moveCard(dir) {
    const cards = [...state.refs.panel.querySelectorAll("[data-item]")];
    if (!cards.length) return;
    const at = cards.findIndex((c) => c.dataset.item === state.focusItem);
    const next = cards[at < 0 ? (dir > 0 ? 0 : cards.length - 1) : Math.min(cards.length - 1, Math.max(0, at + dir))];
    state.focusItem = next.dataset.item;
    markFocused();
    next.focus({ preventScroll: true });
    scrollRow(next, "nearest");
    const segs = (next.dataset.segs || "").split(" ").filter(Boolean);
    if (segs.length) focusEvidence(segs, false);
  }

  function onKey(e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && t.closest && t.closest("input, textarea, select, [contenteditable]")) return;
    if (state.view !== "call" || !state.refs) return;
    const onControl = t && t.closest && t.closest("button, a, audio, summary");
    switch (e.key) {
      case " ":
        if (onControl || !state.audio) return;
        e.preventDefault();
        if (state.audio.paused) state.audio.play().catch(() => {});
        else state.audio.pause();
        break;
      case "1":
      case "2":
      case "3":
      case "4":
        e.preventDefault();
        setTab(TABS[Number(e.key) - 1][0]);
        break;
      case "j":
      case "k":
        e.preventDefault();
        moveCard(e.key === "j" ? 1 : -1);
        break;
      case "Enter": {
        const card = t && t.closest && t.closest("[data-item]");
        if (!card || onControl) return;
        e.preventDefault();
        const segs = (card.dataset.segs || "").split(" ").filter(Boolean);
        if (segs.length) focusEvidence(segs, true);
        break;
      }
      case "Escape":
        clearHighlight();
        toggleKeys(false);
        break;
      case "?":
        toggleKeys();
        break;
      default:
    }
  }

  // ---------- new call form ----------

  function defaultStartValue(offset) {
    // Wall clock in the chosen offset, formatted for datetime-local.
    const m = /^([+-])(\d{2}):(\d{2})$/.exec(offset || "+05:45");
    const mins = m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 345;
    return new Date(Date.now() + mins * 60 * 1000).toISOString().slice(0, 16);
  }

  function formField(forId, labelText, control, hint) {
    const hintNode = hint instanceof Node ? hint : hint ? el("p", { class: "hint small", text: hint }) : null;
    const label = typeof labelText === "string" ? el("label", { for: forId, text: labelText }) : el("label", { for: forId }, ...labelText);
    return el("div", { class: "field" }, label, control, hintNode);
  }

  function optionalLabel(text) {
    return [text, " ", el("span", { class: "muted", text: "(optional)" })];
  }

  function renderUpload(main) {
    const saved = store("sel.upload") || {};
    const packSel = el("select", { id: "f-pack", name: "pack" });
    if (!state.packList.length) packSel.append(el("option", { value: "", text: state.packError ? "Packs could not be loaded" : "No business packs found" }));
    for (const p of state.packList) packSel.append(el("option", { value: p.id, text: p.name }));
    if (saved.pack && state.packs.has(saved.pack)) packSel.value = saved.pack;

    const channels = el(
      "select",
      { id: "f-channels", name: "channels" },
      el("option", { value: "auto", text: "Auto-detect" }),
      el("option", { value: "split", text: "Stereo — one side per channel" }),
      el("option", { value: "mono", text: "Mono" })
    );
    if (saved.channels) channels.value = saved.channels;
    const repChannelSel = el(
      "select",
      { id: "f-rep-channel", name: "rep_channel" },
      el("option", { value: "0", text: "Left" }),
      el("option", { value: "1", text: "Right" })
    );
    if (saved.rep_channel) repChannelSel.value = saved.rep_channel;
    const repChannelField = formField("f-rep-channel", "Rep is on", repChannelSel);
    repChannelField.hidden = channels.value !== "split";
    channels.addEventListener("change", () => {
      repChannelField.hidden = channels.value !== "split";
    });

    const offset = saved.offset || localOffset();
    const me = viewer();
    const reps = state.people.filter((x) => x.role === "rep");
    const repSel = el("select", { id: "f-rep-person", name: "rep_id" }, el("option", { value: "", text: reps.length ? "Choose a rep" : "No reps yet" }));
    for (const p of reps) repSel.append(el("option", { value: p.id, text: p.name }));
    repSel.value = me && me.role === "rep" ? me.id : saved.rep_id && personName(saved.rep_id) ? saved.rep_id : "";
    const leadSel = el("select", { id: "f-lead", name: "lead_id" });
    fillLeadSelect(leadSel, "");
    if (state.leads === null) loadLeads();
    const fileInput = el("input", { type: "file", id: "f-audio", name: "audio", accept: "audio/*", class: "sr-file" });
    const fileName = el("span", { class: "drop-name", "aria-live": "polite", hidden: true });
    const mark = iconTile("neutral", "doc");
    mark.classList.add("drop-mark");
    const drop = el(
      "label",
      { class: "dropzone", for: "f-audio" },
      mark,
      el("span", { class: "drop-main", text: "Drop a recording here, or choose a file" }),
      el("span", { class: "drop-note", text: "Up to 500 MB. The file stays on this computer." }),
      fileName
    );
    fileInput.addEventListener("change", () => {
      const f = fileInput.files[0];
      fileName.hidden = !f;
      fileName.textContent = f ? `${f.name} · ${(f.size / 1024 / 1024).toFixed(1)} MB` : "";
      drop.classList.toggle("has-file", !!f);
      mark.classList.toggle("tone-neutral", !f);
      mark.classList.toggle("tone-ok", !!f);
    });

    const timeHint = el("p", { class: "hint small" });
    const showZone = (value) => {
      timeHint.textContent = `Times are read in ${value.trim() || offset}.`;
    };
    showZone(offset);
    const offsetInput = el("input", { type: "text", id: "f-offset", name: "offset", value: offset, autocomplete: "off", spellcheck: "false" });
    offsetInput.addEventListener("input", () => showZone(offsetInput.value));

    const submit = el("button", { type: "submit", class: "primary", id: "upload-submit", text: "Upload and analyse", disabled: !state.packList.length });
    const bar = el("progress", { class: "upload-progress", max: "1", value: "0", hidden: true, "aria-label": "Upload progress" });
    const errBox = el("p", { id: "upload-error", class: "form-error", role: "alert", hidden: true });
    const sectionHead = (title, sub, iconName) =>
      el(
        "div",
        { class: "section-head" },
        el(
          "div",
          { class: "section-head-main" },
          iconTile("neutral", iconName),
          el("div", {}, el("h2", { class: "section-title", text: title }), el("p", { class: "card-sub", text: sub }))
        )
      );
    const form = el(
      "form",
      { class: "upload", id: "upload-form", novalidate: true },
      el(
        "div",
        { class: "bento" },
        el(
          "section",
          { class: "list-card page-section bento-anchor upload-recording" },
          sectionHead("Recording", "The file Sel will transcribe.", "doc"),
          el("div", { class: "page-section-body" }, fileInput, drop)
        ),
        el(
          "section",
          { class: "list-card page-section upload-decide" },
          sectionHead("To file this call", "The pack and the time. Today and coaching follow the rep.", "lines"),
          el(
            "div",
            { class: "page-section-body" },
            formField("f-pack", "Business pack", packSel),
            formField("f-start", "Call started", el("input", { type: "datetime-local", id: "f-start", name: "started", step: "60", value: defaultStartValue(offset) }), timeHint),
            formField("f-rep-person", optionalLabel("Whose call is this?"), repSel, reps.length ? "Today and coaching follow this rep." : "Add a rep under Manage people to track whose call it is."),
            formField("f-lead", optionalLabel("Lead"), leadSel, "Prep reads a lead's earlier calls before the next one."),
            errBox,
            bar
          ),
          el("div", { class: "card-actions" }, submit)
        )
      ),
      el(
        "section",
        { class: "card upload-more" },
        el(
          "details",
          { class: "disclosure", open: Boolean(saved.rep) || (saved.channels && saved.channels !== "auto") },
          el(
            "summary",
            {},
            el("span", { class: "upload-more-title", text: "More about this call" }),
            el("span", { class: "upload-more-sub", text: "Names, email, time zone, and recording layout" })
          ),
          el(
            "div",
            { class: "upload-more-grid" },
            formField("f-rep", optionalLabel("Rep's name in the call"), el("input", { type: "text", id: "f-rep", name: "rep_name", autocomplete: "off", value: saved.rep || "" }), "Labels the transcript. Defaults to the rep chosen above."),
            formField("f-cust", optionalLabel("Customer name"), el("input", { type: "text", id: "f-cust", name: "customer_name", autocomplete: "off" })),
            formField("f-cust-email", optionalLabel("Customer email"), el("input", { type: "email", id: "f-cust-email", name: "customer_email", autocomplete: "off" }), "For the follow-up draft. Sel does not send email."),
            formField("f-offset", "Time zone", offsetInput, "For example +05:45 or -03:00."),
            formField("f-channels", "Recording layout", channels),
            repChannelField
          )
        )
      )
    );
    for (const type of ["dragenter", "dragover"]) {
      form.addEventListener(type, (e) => {
        e.preventDefault();
        drop.classList.add("is-over");
      });
    }
    for (const type of ["dragleave", "drop"]) {
      form.addEventListener(type, (e) => {
        if (type === "dragleave" && form.contains(e.relatedTarget)) return;
        drop.classList.remove("is-over");
      });
    }
    form.addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        fileInput.dispatchEvent(new Event("change"));
      }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitUpload(form);
    });
    main.replaceChildren(
      el(
        "div",
        { class: "page upload-page" },
        el(
          "header",
          { class: "page-head" },
          el(
            "div",
            {},
            el("h1", { text: "New call" }),
            el("p", { class: "lede", text: "Add a recording. It stays on this computer. Sel transcribes it and prepares the review." })
          )
        ),
        state.packError ? el("p", { class: "form-error", text: `Could not load packs: ${state.packError}` }) : null,
        form
      )
    );
  }

  async function submitUpload(form) {
    const errBox = form.querySelector("#upload-error");
    const submit = form.querySelector("#upload-submit");
    const bar = form.querySelector(".upload-progress");
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
    if (!offset) return fail("The time zone should look like +05:45.");

    store("sel.upload", { pack, offset, rep: val("#f-rep"), rep_id: val("#f-rep-person"), channels: val("#f-channels"), rep_channel: val("#f-rep-channel") });
    const startedAt = `${when.length === 16 ? `${when}:00` : when}${offset}`;
    const params = new URLSearchParams({
      pack,
      started_at: startedAt,
      filename: file.name,
      rep_name: val("#f-rep"),
      rep_id: val("#f-rep-person"),
      customer_name: val("#f-cust"),
      customer_email: val("#f-cust-email"),
      lead_id: val("#f-lead"),
      channels: val("#f-channels") || "auto",
      rep_channel: val("#f-rep-channel") || "0",
    });

    submit.disabled = true;
    submit.textContent = "Uploading…";
    bar.hidden = false;
    try {
      const created = await upload(`/api/calls?${params.toString()}`, file, file.type || "application/octet-stream", (f) => {
        bar.value = f;
        submit.textContent = `Uploading… ${Math.round(f * 100)}%`;
      });
      navigate(`#call/${enc(created.id)}`);
      kickPoll();
    } catch (e) {
      fail(e.message);
      submit.disabled = false;
      submit.textContent = "Upload and analyse";
      bar.hidden = true;
    }
  }

  // ---------- call simulator (sim.js, loaded on first use) ----------

  // Scripts loaded on first use: sim.js (the simulator) and coach.js (Today, Playbook, People).
  function loadModule(name, label) {
    if (window.Sel[name]) return Promise.resolve();
    if (!state.modules[name]) {
      state.modules[name] = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = (SCRIPT && SCRIPT.dataset[name]) || `/static/${name}.js`;
        s.onload = () => resolve();
        s.onerror = () => {
          delete state.modules[name];
          reject(new Error(`Could not load ${label}.`));
        };
        document.head.append(s);
      });
    }
    return state.modules[name];
  }

  function loadSim() {
    return loadModule("sim", "the call simulator");
  }

  function renderCoach(main) {
    if (TEAM_PAGES.has(state.coachPage)) {
      if (window.Sel.team) {
        window.Sel.team.render(main, state.coachPage, state.coachArg);
        return;
      }
      main.replaceChildren(skeleton());
      loadModule("team", "this page")
        .then(() => {
          if (state.view === "coach" && TEAM_PAGES.has(state.coachPage)) renderMain();
        })
        .catch((e) => main.replaceChildren(el("div", { class: "error-box", role: "alert" }, el("p", { class: "strong", text: e.message }))));
      return;
    }
    if (state.coachPage === "practice") {
      if (window.Sel.practice) {
        window.Sel.practice.render(main, state.coachArg);
        return;
      }
      main.replaceChildren(skeleton());
      loadModule("practice", "Practice")
        .then(() => {
          if (state.view === "coach" && state.coachPage === "practice") renderMain();
        })
        .catch((e) => main.replaceChildren(el("div", { class: "error-box", role: "alert" }, el("p", { class: "strong", text: e.message }))));
      return;
    }
    if (window.Sel.coach) {
      window.Sel.coach.render(main, state.coachPage, state.coachArg);
      return;
    }
    main.replaceChildren(skeleton());
    loadModule("coach", "this page")
      .then(() => {
        if (state.view === "coach") renderMain();
      })
      .catch((e) => {
        if (state.view === "coach") main.replaceChildren(el("div", { class: "error-box", role: "alert" }, el("p", { class: "strong", text: e.message })));
      });
  }

  function renderSim(main) {
    if (window.Sel.sim) {
      window.Sel.sim.render(main, state.simCode);
      return;
    }
    main.replaceChildren(skeleton());
    loadSim()
      .then(() => {
        if (state.view === "sim") window.Sel.sim.render(main, state.simCode);
      })
      .catch((e) => main.replaceChildren(el("div", { class: "error-box", role: "alert" }, el("p", { text: e.message }))));
  }

  // ---------- boot ----------

  async function loadPacks() {
    try {
      state.packList = await api("/api/packs");
      state.packs = new Map(state.packList.map((p) => [p.id, p]));
      state.packError = null;
      emit("packs", state.packList);
    } catch (e) {
      state.packError = e.message;
    }
  }

  window.Sel = {
    el,
    icon,
    iconTile,
    pill,
    muted,
    compact,
    plural,
    mmss,
    quoted,
    formatStarted,
    stageText,
    isActive,
    localOffset,
    store,
    api,
    request,
    upload,
    navigate,
    on,
    connectEvents,
    packName,
    packs: () => state.packList,
    calls: () => state.calls,
    people: () => state.people,
    // The server doesn't echo a tab's own changes back on the stream, so after one, re-read the list.
    refreshPeople: () => api("/api/people", { fresh: true }).then(applyPeople),
    viewer,
    setViewer,
    personName,
    personAvatar,
    skeleton,
    formField,
    coachRoute: () => ({ page: state.coachPage, arg: state.coachArg }),
    simInfo: () => state.sim,
    rooms: () => state.rooms,
    view: () => state.view,
    streamUp: () => state.stream.connected,
    CALL_STATUS,
    DRAFT_STATUS,
    SCORE_STATUS,
    ROLE_LABEL,
    reduceMotion,
    worklet: (SCRIPT && SCRIPT.dataset.worklet) || "/static/pcm-worklet.js",
    sim: null,
    coach: null,
    team: null,
    leads: () => state.leads,
    loadLeads,
    lineOrigin,
  };

  async function boot() {
    $("#new-call").addEventListener("click", () => navigate("#new"));
    state.viewerId = store("sel.viewer") || null;
    state.callLayout = store("sel.calls.view") === "card" ? "card" : "table";
    $("#viewer").addEventListener("change", (e) => setViewer(e.target.value));
    renderViewer();
    window.addEventListener("hashchange", () => route());
    document.addEventListener("keydown", onKey);
    const list = $("#main");
    const prefetch = (e) => {
      const a = e.target.closest && e.target.closest("a.call-link, a.calls-table-link");
      if (a && a.dataset.id !== state.callId) prefetchCall(a.dataset.id);
    };
    list.addEventListener("pointerover", prefetch);
    list.addEventListener("focusin", prefetch);

    // A simulator session in this tab joins the same event stream from the start.
    let simSession = null;
    try {
      simSession = JSON.parse(sessionStorage.getItem("sel.sim") || "null");
    } catch (_) {
      simSession = null;
    }
    connectEvents(simSession && simSession.code ? { room: simSession.code, client: simSession.client } : null);
    loadHealth();
    setInterval(loadHealth, HEALTH_MS);

    // Packs, the call list and the routed view load in parallel.
    const packs = loadPacks();
    const calls = refreshList().catch((e) => setListError(e.message));
    api("/api/people")
      .then(applyPeople)
      .catch(() => {});
    const routed = route();
    if (/^#sim/.test(location.hash)) loadSim().catch(() => {});
    await packs;
    renderList();
    if (state.view === "new") renderMain();
    else if (state.view === "call" && state.refs) renderCallParts();
    await Promise.all([calls, routed]);
    if (state.view === "coach" && state.coachPage === "playbook") renderMain();
  }

  boot();
})();
