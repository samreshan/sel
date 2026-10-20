/* One menu for every single-choice select. The select stays in the page so
   labels, forms and existing value/change code keep working; only the popup
   is drawn by Sel. */
(() => {
  "use strict";

  const menu = document.createElement("div");
  menu.id = "sel-dropdown-menu";
  menu.className = "dropdown-menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;

  let openSelect = null;
  let items = [];
  let activeIndex = -1;
  let typed = "";
  let typedAt = 0;

  const optionObserver = new MutationObserver(() => {
    if (!openSelect) return;
    if (openSelect.disabled || !openSelect.isConnected) {
      close();
      return;
    }
    const keep = activeIndex;
    render(openSelect);
    place();
    const still = items[keep] && items[keep].type === "option" && !items[keep].disabled;
    setActive(still ? keep : selectedItemIndex(openSelect));
  });

  function closest(node, selector) {
    const el = node && node.nodeType === 1 ? node : node && node.parentElement;
    return el && el.closest ? el.closest(selector) : null;
  }

  function usable(select) {
    if (!select || select.tagName !== "SELECT" || select.multiple || select.size > 1) return null;
    return select;
  }

  function selectFrom(target) {
    const direct = usable(closest(target, "select"));
    if (direct) return direct;
    const label = closest(target, "label");
    return label ? usable(label.control) : null;
  }

  function collect(select) {
    const out = [];
    const addOption = (opt, groupDisabled) => {
      if (!opt || opt.tagName !== "OPTION") return;
      out.push({
        type: "option",
        value: opt.value,
        label: opt.textContent,
        disabled: opt.disabled || groupDisabled,
      });
    };
    for (const node of select.children) {
      if (node.tagName === "OPTGROUP") {
        out.push({ type: "group", label: node.label || "" });
        for (const opt of node.children) addOption(opt, node.disabled);
      } else {
        addOption(node, false);
      }
    }
    return out;
  }

  function checkIcon() {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "solar-icon dropdown-check");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(NS, "use");
    use.setAttribute("href", "/static/solar-icons.svg#check");
    svg.append(use);
    return svg;
  }

  function menuName(select) {
    const labelled = select.getAttribute("aria-label");
    if (labelled) return labelled;
    if (select.id) {
      const label = document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
      if (label) return label.textContent.replace(/\s+/g, " ").trim();
    }
    return "Options";
  }

  function selectedItemIndex(select) {
    let n = 0;
    const want = select.selectedIndex;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type !== "option") continue;
      if (n === want) return i;
      n += 1;
    }
    return -1;
  }

  function enabledIndexes() {
    const found = [];
    items.forEach((item, i) => {
      if (item.type === "option" && !item.disabled) found.push(i);
    });
    return found;
  }

  function render(select) {
    items = collect(select);
    menu.replaceChildren();
    menu.setAttribute("aria-label", menuName(select));
    const chosen = selectedItemIndex(select);
    let group = null;
    items.forEach((item, index) => {
      if (item.type === "group") {
        group = document.createElement("div");
        group.setAttribute("role", "group");
        group.setAttribute("aria-label", item.label);
        const cap = document.createElement("div");
        cap.className = "dropdown-group";
        cap.setAttribute("aria-hidden", "true");
        cap.textContent = item.label;
        group.append(cap);
        menu.append(group);
        return;
      }
      const row = document.createElement("div");
      row.className = "dropdown-option";
      row.id = `sel-dropdown-opt-${index}`;
      row.dataset.index = String(index);
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", index === chosen ? "true" : "false");
      if (index === chosen) row.classList.add("is-selected");
      if (item.disabled) {
        row.classList.add("is-disabled");
        row.setAttribute("aria-disabled", "true");
      }
      const text = document.createElement("span");
      text.className = "dropdown-option-label";
      text.textContent = item.label;
      row.append(text);
      if (index === chosen) row.append(checkIcon());
      (group || menu).append(row);
    });
    if (!items.some((item) => item.type === "option")) {
      const empty = document.createElement("p");
      empty.className = "dropdown-empty";
      empty.textContent = "No options";
      menu.append(empty);
    }
  }

  function setActive(index) {
    const enabled = enabledIndexes();
    if (!enabled.length) {
      activeIndex = -1;
      if (openSelect) openSelect.removeAttribute("aria-activedescendant");
      return;
    }
    if (!enabled.includes(index)) index = enabled[0];
    activeIndex = index;
    menu.querySelectorAll(".dropdown-option").forEach((el) => {
      el.classList.toggle("is-active", Number(el.dataset.index) === index);
    });
    const el = document.getElementById(`sel-dropdown-opt-${index}`);
    if (!el || !openSelect) return;
    openSelect.setAttribute("aria-activedescendant", el.id);
    const top = el.offsetTop;
    const bottom = top + el.offsetHeight;
    if (top < menu.scrollTop) menu.scrollTop = top;
    else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight;
  }

  function place() {
    if (!openSelect) return;
    const rect = openSelect.getBoundingClientRect();
    const gap = 6;
    const margin = 8;
    menu.style.left = "0px";
    menu.style.top = "0px";
    menu.style.maxHeight = "280px";
    menu.style.minWidth = `${Math.round(rect.width)}px`;
    menu.style.width = "max-content";
    menu.style.maxWidth = `${Math.max(Math.round(rect.width), Math.min(360, window.innerWidth - margin * 2))}px`;
    menu.classList.remove("is-up");

    const spaceBelow = window.innerHeight - rect.bottom - gap - margin;
    const spaceAbove = rect.top - gap - margin;
    const natural = menu.scrollHeight;
    let top;
    if (spaceBelow >= Math.min(natural, 160) || spaceBelow >= spaceAbove) {
      menu.style.maxHeight = `${Math.max(120, Math.min(280, spaceBelow))}px`;
      top = rect.bottom + gap;
    } else {
      const maxH = Math.max(120, Math.min(280, spaceAbove));
      menu.style.maxHeight = `${maxH}px`;
      top = Math.max(margin, rect.top - gap - Math.min(menu.scrollHeight, maxH));
      menu.classList.add("is-up");
    }
    let left = rect.left;
    const width = menu.offsetWidth;
    if (left + width > window.innerWidth - margin) left = Math.max(margin, window.innerWidth - margin - width);
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
  }

  function open(select) {
    if (!select || select.disabled) return;
    if (openSelect === select) return;
    close();
    openSelect = select;
    select.classList.add("is-open");
    select.setAttribute("aria-haspopup", "listbox");
    select.setAttribute("aria-expanded", "true");
    select.setAttribute("aria-controls", menu.id);
    typed = "";
    render(select);
    menu.hidden = false;
    place();
    setActive(selectedItemIndex(select));
    optionObserver.observe(select, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["disabled", "label"],
    });
  }

  // The menu closes at once; a copy of it fades out where it was.
  function fadeOut() {
    const motion = window.SelMotion;
    if (menu.hidden || !menu.parentNode || !motion || !motion.ok()) return;
    const ghost = menu.cloneNode(true);
    ghost.removeAttribute("id");
    ghost.removeAttribute("role");
    for (const n of ghost.querySelectorAll("[id]")) n.removeAttribute("id");
    ghost.setAttribute("aria-hidden", "true");
    ghost.inert = true;
    ghost.classList.add("is-leaving");
    menu.parentNode.append(ghost);
    ghost.scrollTop = menu.scrollTop;
    const drop = () => ghost.remove();
    ghost.addEventListener("animationend", drop, { once: true });
    setTimeout(drop, 400);
  }

  function close() {
    optionObserver.disconnect();
    fadeOut();
    if (openSelect) {
      openSelect.classList.remove("is-open");
      openSelect.setAttribute("aria-expanded", "false");
      openSelect.removeAttribute("aria-activedescendant");
    }
    openSelect = null;
    items = [];
    activeIndex = -1;
    menu.hidden = true;
    menu.replaceChildren();
  }

  function commit(index) {
    const select = openSelect;
    const item = items[index];
    if (!select || !item || item.type !== "option" || item.disabled) return;
    const changed = select.value !== item.value;
    close();
    if (changed) {
      select.value = item.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    select.focus({ preventScroll: true });
  }

  function move(delta) {
    const enabled = enabledIndexes();
    if (!enabled.length) return;
    const at = enabled.indexOf(activeIndex);
    const next = enabled[Math.max(0, Math.min(enabled.length - 1, (at < 0 ? 0 : at) + delta))];
    setActive(next);
  }

  function typeahead(char) {
    const now = performance.now();
    if (now - typedAt > 650) typed = "";
    typedAt = now;
    typed += char.toLowerCase();
    const enabled = enabledIndexes();
    if (!enabled.length) return;
    const start = Math.max(0, enabled.indexOf(activeIndex));
    const ordered = typed.length === 1
      ? enabled.slice(start + 1).concat(enabled.slice(0, start + 1))
      : enabled.slice(start).concat(enabled.slice(0, start));
    const hit = ordered.find((i) => items[i].label.trim().toLowerCase().startsWith(typed));
    if (hit !== undefined) setActive(hit);
  }

  function prepare(select) {
    if (!usable(select) || select.dataset.dropdownReady) return;
    select.dataset.dropdownReady = "1";
    select.setAttribute("aria-haspopup", "listbox");
    select.setAttribute("aria-expanded", "false");
  }

  function prepareTree(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.tagName === "SELECT") prepare(root);
    if (root.querySelectorAll) root.querySelectorAll("select").forEach(prepare);
  }

  document.addEventListener("mousedown", (event) => {
    const select = selectFrom(event.target);
    if (!select || select.disabled) return;
    event.preventDefault();
  }, true);

  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const select = selectFrom(event.target);
    if (select) {
      if (select.disabled) return;
      event.preventDefault();
      if (openSelect === select) close();
      else open(select);
      select.focus({ preventScroll: true });
      return;
    }
    if (openSelect && !menu.contains(event.target)) close();
  }, true);

  menu.addEventListener("pointerdown", (event) => {
    const row = closest(event.target, ".dropdown-option");
    if (!row) return;
    event.preventDefault();
    event.stopPropagation();
    if (row.getAttribute("aria-disabled") === "true") return;
    commit(Number(row.dataset.index));
  });

  menu.addEventListener("pointermove", (event) => {
    const row = closest(event.target, ".dropdown-option");
    if (!row || row.getAttribute("aria-disabled") === "true") return;
    const index = Number(row.dataset.index);
    if (index !== activeIndex) setActive(index);
  });

  document.addEventListener("keydown", (event) => {
    const select = event.target && event.target.tagName === "SELECT" ? usable(event.target) : null;
    if (!openSelect) {
      if (!select || select.disabled) return;
      const openKey = event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " " || event.key === "F4" || (event.altKey && event.key === "ArrowDown");
      if (!openKey || (event.altKey && event.key === "ArrowUp")) return;
      event.preventDefault();
      event.stopPropagation();
      open(select);
      return;
    }
    if (event.target !== openSelect && !menu.contains(event.target)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      const selectToFocus = openSelect;
      close();
      if (selectToFocus) selectToFocus.focus({ preventScroll: true });
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopPropagation();
      move(1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopPropagation();
      move(-1);
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      event.stopPropagation();
      const enabled = enabledIndexes();
      if (enabled.length) setActive(enabled[0]);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      event.stopPropagation();
      const enabled = enabledIndexes();
      if (enabled.length) setActive(enabled[enabled.length - 1]);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      commit(activeIndex);
      return;
    }
    if (event.key === "Tab") {
      close();
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      event.stopPropagation();
      typeahead(event.key);
    }
  }, true);

  document.addEventListener("focusin", (event) => {
    if (!openSelect) return;
    if (event.target === openSelect || menu.contains(event.target)) return;
    close();
  });

  window.addEventListener("resize", () => {
    if (openSelect) place();
  });

  document.addEventListener("scroll", (event) => {
    if (!openSelect || event.target === menu) return;
    place();
  }, true);

  function boot() {
    if (!document.body || menu.isConnected) return;
    document.body.append(menu);
    prepareTree(document);
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) prepareTree(node);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
