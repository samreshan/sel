/* Sel motion: small helpers that make a change easy to follow. Vanilla JS, no
 * dependencies, shared by the app and the design-system reference.
 *
 * The rules (DESIGN.md "Effects and animation", docs/motion-plan.md):
 * - Motion explains a change; it never delays one. Every helper puts the end
 *   state in the DOM at once, and animation only carries the eye there.
 * - Movement uses a critically damped spring (no overshoot), mirrored by the
 *   --ease-spring token. Durations come from the --motion-* tokens.
 * - Reduced motion (the system setting, or data-motion="reduce" on <html>)
 *   skips movement; the stylesheet keeps short fades instead.
 * - Re-rendered views are new nodes, so helpers that animate "from the last
 *   value" remember it under a key the caller chooses.
 */
(() => {
  "use strict";

  const root = document.documentElement;
  const query = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const ok = () => !(query && query.matches) && root.dataset.motion !== "reduce";
  const LINEAR = !!(window.CSS && CSS.supports && CSS.supports("transition-timing-function", "linear(0, 1)"));
  const SPRING = LINEAR
    ? "linear(0, .08, .238, .406, .554, .674, .767, .835, .885, .921, .946, .963, .975, .983, .989, .992, 1)"
    : "cubic-bezier(.22, 1, .36, 1)";
  const SPRITE = "/static/solar-icons.svg";

  // Durations in ms, read once from the tokens so CSS stays the source of truth.
  let tokens = null;
  function ms(name) {
    if (!tokens) {
      const css = getComputedStyle(root);
      const read = (n, fallback) => {
        const raw = css.getPropertyValue(n).trim();
        const v = parseFloat(raw);
        return Number.isFinite(v) ? (raw.endsWith("ms") ? v : v * 1000) : fallback;
      };
      tokens = { fast: read("--motion-fast", 120), base: read("--motion", 180), slow: read("--motion-slow", 280), glide: read("--motion-glide", 320) };
    }
    return tokens[name];
  }

  // ---------- memory: what was on screen last time ----------

  const MEMORY_MAX = 2000;
  const memory = new Map();
  const seen = new Set();

  // The value last shown under `key` (undefined the first time); remembers `value`.
  function remember(key, value) {
    const prev = memory.get(key);
    memory.delete(key);
    memory.set(key, value);
    if (memory.size > MEMORY_MAX) memory.delete(memory.keys().next().value);
    return prev;
  }

  // True when `key` was shown before with a different value.
  function changed(key, value) {
    const had = memory.has(key);
    const prev = remember(key, value);
    return had && prev !== value;
  }

  // True the first time `key` is asked about (until forget).
  function once(key) {
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }

  function forget(key) {
    seen.delete(key);
    memory.delete(key);
  }

  // ---------- primitives ----------

  // Animate `node` from `keyframe` to its current style (WAAPI, implicit end).
  function from(node, keyframe, duration = ms("slow"), easing = SPRING) {
    if (!node || !node.animate || !ok()) return null;
    try {
      return node.animate([{ ...keyframe, offset: 0 }], { duration, easing });
    } catch (_) {
      return null; // a browser that can't animate this property just shows the end state
    }
  }

  // Adds a one-shot class (an entrance), removed when its animation ends.
  function cue(node, cls) {
    if (!node || !ok()) return node;
    node.classList.add(cls);
    const done = () => node.classList.remove(cls);
    node.addEventListener("animationend", done, { once: true });
    setTimeout(done, ms("glide") + ms("slow"));
    return node;
  }

  function sprite(name, size) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "solar-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(NS, "use");
    use.setAttribute("href", `${SPRITE}#${name}`);
    svg.append(use);
    return svg;
  }

  // A check mark. `draw`: the stroke draws itself once (a confirmation).
  function check(draw, size = 14) {
    const svg = sprite("check", size);
    if (draw && ok()) svg.classList.add("is-drawing");
    return svg;
  }

  // An icon whose meaning changed (mic on → muted) swaps in instead of jumping.
  function swap(svg, key, value) {
    if (changed(key, value)) cue(svg, "is-swapping");
    return svg;
  }

  // A row that arrived while someone watched (a live transcript line).
  const arrive = (node) => cue(node, "is-arriving");

  // Three dots: someone is about to speak.
  function typing() {
    const node = document.createElement("span");
    node.className = "typing";
    node.setAttribute("aria-hidden", "true");
    node.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    return node;
  }

  // ---------- numbers that roll ----------

  const numeric = (s) => parseFloat(String(s).replace(/[^\d.-]/g, ""));

  // A number as text. When `key` last showed a different value, the digits
  // that changed roll (up for a larger number, down for a smaller one).
  function number(text, key) {
    text = String(text);
    const node = document.createElement("span");
    node.className = "num";
    const prev = key ? remember(key, text) : undefined;
    if (prev === undefined || prev === text || !ok()) {
      node.textContent = text;
      return node;
    }
    const a = numeric(text);
    const b = numeric(prev);
    node.classList.add(Number.isFinite(a) && Number.isFinite(b) && a < b ? "is-down" : "is-up");
    const shift = prev.length - text.length;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      const old = prev[i + shift];
      if (ch === old) {
        node.append(ch);
        continue;
      }
      const cell = document.createElement("span");
      cell.className = "num-d";
      const now = document.createElement("span");
      now.className = "num-new";
      now.textContent = ch;
      cell.append(now);
      if (old !== undefined) {
        const gone = document.createElement("span");
        gone.className = "num-old";
        gone.setAttribute("aria-hidden", "true");
        gone.textContent = old;
        const drop = () => gone.remove();
        gone.addEventListener("animationend", drop, { once: true });
        setTimeout(drop, ms("slow") * 2);
        cell.append(gone);
      }
      node.append(cell);
    }
    return node;
  }

  // ---------- rails: the selection thumb slides ----------

  // Tabs, segmented controls and the sidebar keep one thumb per rail that moves
  // to the selected item, like a segmented control on iOS. The selected item
  // keeps its own text colour; the thumb paints its surface.
  const SELECTED = ":scope > .is-selected, :scope > input:checked + label, :scope > .is-on, :scope > .is-active";
  const railState = new Map(); // key -> {el, index, x, y, w, h, t}
  const railSize = new WeakMap();
  const rails = new Set();
  let observer = null;

  const railKey = (rail) => rail.dataset.rail || rail.getAttribute("aria-label") || rail.className.replace(/\bhas-thumb\b/, "").trim();

  function setThumb(thumb, g) {
    thumb.style.width = `${g.w}px`;
    thumb.style.height = `${g.h}px`;
    thumb.style.transform = `translate(${g.x}px, ${g.y}px)`;
  }

  function place(rail, instant) {
    if (!rail.isConnected) return;
    let thumb = rail.querySelector(":scope > .rail-thumb");
    const fresh = !thumb;
    if (fresh) {
      thumb = document.createElement("span");
      thumb.className = "rail-thumb";
      thumb.setAttribute("aria-hidden", "true");
      rail.prepend(thumb);
    }
    const item = rail.querySelector(SELECTED);
    if (!item || !item.offsetWidth) {
      // Nothing to measure (no selection, or the rail is hidden): the item paints itself.
      thumb.style.opacity = "0";
      rail.classList.remove("has-thumb");
      return;
    }
    rail.classList.add("has-thumb");
    const g = { x: item.offsetLeft, y: item.offsetTop, w: item.offsetWidth, h: item.offsetHeight };
    const index = Array.prototype.indexOf.call(rail.children, item);
    const key = railKey(rail);
    const last = railState.get(key);
    const now = performance.now();
    // A rail whose items were redrawn (or a whole rail redrawn by the click
    // that changed it) continues from where its thumb was.
    const start = !instant && fresh && ok() && last && last.index !== index && (last.el === rail || now - last.t < 1200) ? last : null;
    if (fresh || instant) {
      thumb.style.transition = "none";
      setThumb(thumb, start || g);
      void thumb.offsetWidth; // commit the start before moving
      thumb.style.transition = "";
      if (start) setThumb(thumb, g);
    } else {
      setThumb(thumb, g);
    }
    thumb.style.opacity = "";
    railState.set(key, { el: rail, index, ...g, t: now });
  }

  function watch(rail) {
    if (rails.has(rail)) return;
    for (const r of rails) if (!r.isConnected) rails.delete(r);
    rails.add(rail);
    // Radio rails change without a re-render.
    rail.addEventListener("change", () => place(rail, false));
    if (!window.ResizeObserver) return;
    if (!observer) {
      observer = new ResizeObserver((entries) => {
        for (const e of entries) {
          const r = e.target;
          if (!r.isConnected) {
            observer.unobserve(r);
            rails.delete(r);
            continue;
          }
          const size = `${Math.round(e.contentRect.width)}x${Math.round(e.contentRect.height)}`;
          const was = railSize.get(r);
          railSize.set(r, size);
          if (was !== undefined && was !== size) place(r, true);
        }
      });
    }
    observer.observe(rail);
  }

  // Call after rendering or changing a rail's selection.
  function glide(rail) {
    if (!rail) return;
    watch(rail);
    queueMicrotask(() => place(rail, false));
  }

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      for (const r of rails) place(r, true);
    });
  }

  // ---------- lists: items move to their new place ----------

  function ghost(was) {
    const node = was.node.cloneNode(true);
    node.removeAttribute("id");
    for (const n of node.querySelectorAll("[id]")) n.removeAttribute("id");
    node.setAttribute("aria-hidden", "true");
    node.inert = true;
    node.classList.add("motion-ghost");
    const r = was.rect;
    Object.assign(node.style, { position: "fixed", left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`, margin: "0" });
    document.body.append(node);
    const anim = node.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(.97)" }], { duration: ms("base"), easing: "ease-out", fill: "forwards" });
    anim.onfinish = () => node.remove();
    setTimeout(() => node.remove(), ms("slow") * 2);
  }

  // FLIP: snapshot the keyed items now, re-render, then play(): items slide from
  // where they were, new ones fade in, removed ones fade out where they stood.
  function flip(scope, selector = "[data-flip]", keyOf = (n) => n.dataset.flip) {
    if (!scope || !ok() || !document.body.animate) return { play() {} };
    const before = new Map();
    for (const node of scope.querySelectorAll(selector)) {
      const key = keyOf(node);
      if (key && !before.has(key)) before.set(key, { node, rect: node.getBoundingClientRect() });
    }
    return {
      play() {
        if (!ok()) return;
        const kept = new Set();
        for (const node of scope.querySelectorAll(selector)) {
          const key = keyOf(node);
          if (!key || kept.has(key)) continue;
          kept.add(key);
          const was = before.get(key);
          if (!was) {
            if (before.size) from(node, { opacity: 0, transform: "translateY(8px) scale(.98)" });
            continue;
          }
          const now = node.getBoundingClientRect();
          const dx = was.rect.left - now.left;
          const dy = was.rect.top - now.top;
          if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
          from(node, { transform: `translate(${dx}px, ${dy}px)` }, ms("glide"));
        }
        for (const [key, was] of before) if (!kept.has(key) && !was.node.isConnected) ghost(was);
      },
    };
  }

  // ---------- screens: view transitions ----------

  let current = null;

  // Runs `update` inside a same-document view transition when the browser has
  // them; `kind` is "push" (deeper), "pop" (back up) or "fade" (sideways).
  function transition(kind, update) {
    if (!kind || !document.startViewTransition || document.visibilityState !== "visible") {
      update();
      return null;
    }
    root.dataset.vt = kind;
    let ran = false;
    const run = () => {
      if (ran) return;
      ran = true;
      try {
        update();
      } catch (e) {
        console.error(e); // eslint-disable-line no-console
      }
    };
    let vt;
    try {
      vt = document.startViewTransition(run);
    } catch (_) {
      delete root.dataset.vt;
      run();
      return null;
    }
    current = vt;
    const done = () => {
      if (current === vt) {
        current = null;
        delete root.dataset.vt;
      }
    };
    // The browser runs the update on its next frame. A page that is not
    // painting (a hidden pane, a stalled main thread) must not hold the
    // screen back: after a short wait, skip the animation and update now.
    const guard = setTimeout(() => {
      if (ran) return;
      try {
        vt.skipTransition();
      } catch (_) {
        /* already finished */
      }
      run();
      done();
    }, 250);
    const quiet = () => {};
    vt.ready.catch(quiet);
    vt.updateCallbackDone.catch(quiet).then(() => clearTimeout(guard));
    vt.finished.catch(quiet).then(done);
    return vt;
  }

  window.SelMotion = { ok, spring: SPRING, ms, remember, changed, once, forget, from, cue, check, swap, arrive, typing, number, glide, flip, transition };
})();
