/* Isolated, illustrative interactions. No product API calls or persisted state. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  let toastTimer;
  function notify(message) {
    clearTimeout(toastTimer);
    $('#toast-text').textContent = message;
    $('#ds-toast').hidden = false;
    toastTimer = setTimeout(() => { $('#ds-toast').hidden = true; }, 5000);
  }
  $('#close-toast').addEventListener('click', () => { $('#ds-toast').hidden = true; });
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      notify(`Copied ${text}`);
    } catch {
      notify(`Copy this reference: ${text}`);
    }
  }
  document.querySelectorAll('[data-token]').forEach((button) => {
    const token = button.dataset.token;
    button.querySelector('.ds-color-value').textContent = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    button.addEventListener('click', () => copy(`var(${token})`));
  });
  document.querySelectorAll('[data-icon]').forEach((button) => button.addEventListener('click', () => copy(`/static/solar-icons.svg#${button.dataset.icon}`)));
  document.querySelectorAll('[data-notice]').forEach((button) => button.addEventListener('click', () => notify(button.dataset.notice)));

  const states = {
    default: ['','neutral','Coaching','Make room for the customer.','Pause after an open question. A little space gives the customer time to tell you what matters.','A resting surface. Content stays still; only actionable cards lift on hover.','Preview evidence'],
    selected: ['is-selected','info','Selected','This is your current selection.','A quiet blue outline identifies the selected card without hiding its content.','Selection stays distinct from the amber treatment used for source evidence.','Clear selection'],
    evidence: ['is-focused','warn','Evidence','A finding with a source.','“What would make this worth switching for your team?”','Warm emphasis connects the finding to its exact transcript moment. Keyboard focus remains visible.','Clear evidence'],
    loading: ['is-loading','neutral','Processing','Looking through the conversation.','The transcript is being prepared. Keep the layout stable while the result arrives.','Show a descriptive label as well as motion. Do not show an invented percentage.','Preparing…'],
    empty: ['is-empty','neutral','No findings yet','Start with a conversation.','Add a recording to see coaching points and their supporting evidence here.','Explain what will appear here and give a clear next step.','Preview upload'],
    error: ['is-error','bad','Could not load','The recording could not be opened.','Your existing review is safe. Try loading this example again.','Describe the problem and the recovery action. Keep entered information intact.','Try again'],
    success: ['is-success','ok','Saved','Your review is saved.','Your changes are ready on this device. Nothing has been sent.','Confirm what happened with a label and a restrained semantic border.','Reset example'],
    dismissed: ['is-dismissed','neutral','Dismissed','Make room for the customer.','This point is set aside. You can restore it whenever you need it.','A dashed border signals an inactive finding. Keep the restore action available.','Restore example']
  };
  let currentState = 'default';
  function setState(name) {
    currentState = name;
    const [className, tone, label, title, description, help, action] = states[name];
    $('#state-card').className = `card ${className}`;
    $('#state-card').setAttribute('aria-busy', String(name === 'loading'));
    $('#state-pill').className = `pill pill-${tone} card-tag`;
    $('#state-pill').textContent = label;
    $('#state-card-title').textContent = title;
    $('#state-description').textContent = description;
    $('#card-state-help').textContent = help;
    $('#state-action').textContent = action;
    $('#state-action').disabled = name === 'loading';
    $('#state-meta').textContent = name === 'evidence' ? 'Transcript · 02:34' : 'Interactive example';
    document.querySelectorAll('[data-card-state]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.cardState === name)));
  }
  document.querySelectorAll('[data-card-state]').forEach((b) => b.addEventListener('click', () => setState(b.dataset.cardState)));
  $('#state-action').addEventListener('click', () => {
    if (currentState === 'empty') { notify('In Sel, New call opens the recording upload form. This is a design example.'); return; }
    setState(currentState === 'default' ? 'evidence' : currentState === 'error' ? 'success' : 'default');
  });
  $('#select-card').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const selected = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', String(selected));
    button.querySelector('.ds-text-link').textContent = selected ? 'Selected · click to clear' : 'Select recording';
  });
  $('#bento-recording').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const selected = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', String(selected));
    button.querySelector('.ds-bento-action').firstChild.textContent = selected ? 'Selected · click to clear ' : 'Select example ';
  });
  $('#jump-form').addEventListener('click', () => { location.hash = 'forms'; $('#example-name').focus(); });

  $('#loading-demo').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    spinner.setAttribute('aria-hidden', 'true');
    button.replaceChildren(spinner, document.createTextNode('Saving…'));
    await new Promise((resolve) => setTimeout(resolve, 1100));
    button.textContent = 'Saved';
    button.setAttribute('aria-busy', 'false');
    notify('Example saved. Nothing was sent.');
    setTimeout(() => { button.textContent = 'Save example'; button.disabled = false; }, 1600);
  });

  const name = $('#example-name');
  function validateName() {
    const valid = Boolean(name.value.trim());
    name.setAttribute('aria-invalid', String(!valid));
    $('#name-error').hidden = valid;
    return valid;
  }
  $('#example-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!validateName()) { $('#form-status').textContent = ''; name.focus(); return; }
    $('#form-status').textContent = `Saved for ${name.value.trim()}. Preview only.`;
    notify('Example saved in this preview.');
  });
  name.addEventListener('input', () => { if (name.hasAttribute('aria-invalid')) validateName(); $('#form-status').textContent = ''; });

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  function syncMotion() {
    const disabled = reducedMotion.matches || !$('#motion-toggle').checked;
    document.body.classList.toggle('no-motion', disabled);
    // motion.js reads this too, so the preview toggle reaches the shared helpers.
    if (disabled) document.documentElement.dataset.motion = 'reduce';
    else delete document.documentElement.dataset.motion;
    $('#motion-status').textContent = disabled ? 'Reduced motion · short fades only' : '280 ms · spring settle';
  }
  $('#motion-toggle').addEventListener('change', syncMotion);
  reducedMotion.addEventListener('change', syncMotion);
  syncMotion();
  $('#motion-replay').addEventListener('click', () => {
    if (document.body.classList.contains('no-motion')) { notify('Motion is reduced. The completed state is shown immediately.'); return; }
    const object = $('#motion-object');
    object.classList.remove('is-replaying');
    requestAnimationFrame(() => requestAnimationFrame(() => object.classList.add('is-replaying')));
  });
  $('#toast-demo').addEventListener('click', () => notify('Your changes are saved on this device. Example only.'));

  const dialog = $('#preview-dialog');
  let dialogTrigger;
  function openDialog(event) { dialogTrigger = event.currentTarget; dialog.showModal(); }
  function closeDialog() { dialog.close(); }
  $('#hero-review').addEventListener('click', openDialog);
  $('#dialog-demo').addEventListener('click', openDialog);
  $('#close-dialog').addEventListener('click', closeDialog);
  $('#cancel-dialog').addEventListener('click', closeDialog);
  dialog.addEventListener('close', () => dialogTrigger?.focus());
  $('#approve-example').addEventListener('click', () => { closeDialog(); notify('Example approved. Sel never sends it for you.'); });

  // Only constant, local specimen markup; user input is always textContent.
  const patterns = {
    evidence: '<div class="ds-pattern-body"><div class="ds-evidence-row"><p class="who">Customer <span class="lang">EN</span></p><p>“What would the installation include?”</p><p class="muted small">02:34 · Example transcript</p></div><div class="ds-pattern-copy"><span class="pill pill-warn">Evidence</span><h3>Keep the source in sight.</h3><p>A warm highlight connects a finding to the words behind it. Playback retains its own indicator.</p></div></div>',
    claims: '<div class="ds-pattern-body"><article class="card"><span class="pill pill-bad">Contradicted · example</span><h3>Check the warranty period.</h3><p>The stated warranty differs from the approved product documentation.</p><div class="labelled">Source: example business pack</div></article><div class="ds-pattern-copy"><h3>Confidence needs context.</h3><p>A verdict is paired with its source. Supported, contradicted, and ambiguous findings stay visually distinct.</p></div></div>',
    followup: '<div class="ds-pattern-body"><article class="card"><span class="pill pill-warn">Needs input</span><h3>Choose a time for the visit.</h3><p>“Next week” does not confirm a date or time.</p><div class="labelled">Approval waits until the missing details are resolved.</div></article><div class="ds-pattern-copy"><h3>Human approval is the final step.</h3><p>A draft communicates readiness, missing information, and approval separately. Approval never implies that an email was sent.</p></div></div>'
  };
  const tabs = [...document.querySelectorAll('[data-pattern]')];
  function selectPattern(tab) {
    tabs.forEach((t) => { const active = t === tab; t.classList.toggle('is-selected', active); t.setAttribute('aria-selected', String(active)); t.tabIndex = active ? 0 : -1; });
    $('#pattern-panel').innerHTML = patterns[tab.dataset.pattern];
    $('#pattern-panel').setAttribute('aria-labelledby', tab.id);
    if (window.SelMotion) window.SelMotion.glide(tab.parentElement);
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => selectPattern(tab));
    tab.addEventListener('keydown', (event) => {
      let next;
      if (event.key === 'ArrowRight') next = (i + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (i - 1 + tabs.length) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault(); selectPattern(tabs[next]); tabs[next].focus();
    });
  });
  selectPattern(tabs[0]);

  // Motion patterns, drawn with the app's helpers (motion.js). Constant example text only.
  const M = window.SelMotion;
  if (M) {
    document.querySelectorAll('.ds-page .segmented').forEach((rail) => M.glide(rail));
    document.querySelectorAll('.ds-page .scope-toggle').forEach((rail) => {
      const buttons = [...rail.querySelectorAll('.scope-btn')];
      buttons.forEach((b) => b.addEventListener('click', () => {
        buttons.forEach((x) => { x.classList.toggle('is-on', x === b); x.setAttribute('aria-pressed', String(x === b)); });
        M.glide(rail);
      }));
      M.glide(rail);
    });

    let score = 72;
    $('#ds-num-change').addEventListener('click', () => {
      score = score >= 90 ? 64 : score + 9;
      const node = M.number(String(score), 'ds:score');
      node.id = 'ds-num';
      $('#ds-num').replaceWith(node);
    });
    M.remember('ds:score', '72');

    $('#ds-mic').addEventListener('click', (event) => {
      const button = event.currentTarget;
      const muted = button.getAttribute('aria-pressed') !== 'true';
      button.setAttribute('aria-pressed', String(muted));
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      for (const [k, v] of [['class', 'solar-icon'], ['width', '16'], ['height', '16'], ['viewBox', '0 0 24 24'], ['aria-hidden', 'true'], ['focusable', 'false']]) svg.setAttribute(k, v);
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      use.setAttribute('href', `/static/solar-icons.svg#${muted ? 'micoff' : 'mic'}`);
      svg.append(use);
      button.replaceChildren(M.swap(svg, 'ds:mic', muted), muted ? 'Unmute' : 'Mute');
    });
    M.remember('ds:mic', false);

    let approved = false;
    $('#ds-approve').addEventListener('click', (event) => {
      approved = !approved;
      event.currentTarget.textContent = approved ? 'Reset example' : 'Approve example';
      const status = $('#ds-approve-status');
      status.className = `form-status${approved ? ' is-ok' : ''}`;
      status.replaceChildren(...(approved ? [M.check(true, 14), 'Approved. Sel does not send it; you do.'] : ['Ready for approval.']));
    });

    const lines = $('#ds-lines');
    const said = ['Around eight thousand, more in winter.', 'Mostly the water heater, I think.', 'Can you show me what that would save?'];
    let next = 0;
    $('#ds-reply').addEventListener('click', (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      const typing = M.arrive(Object.assign(document.createElement('li'), { className: 'sim-typing' }));
      typing.setAttribute('aria-hidden', 'true');
      typing.append(M.typing());
      lines.append(typing);
      lines.scrollTo({ top: lines.scrollHeight, behavior: M.ok() ? 'smooth' : 'auto' });
      setTimeout(() => {
        typing.remove();
        const row = document.createElement('li');
        row.className = 'sim-line role-customer';
        const meta = document.createElement('div');
        meta.className = 'sim-line-meta';
        meta.append(Object.assign(document.createElement('span'), { className: 'who', textContent: 'Customer' }));
        row.append(meta, Object.assign(document.createElement('p'), { className: 'text', textContent: said[next % said.length] }));
        next += 1;
        lines.append(M.arrive(row));
        lines.scrollTo({ top: lines.scrollHeight, behavior: M.ok() ? 'smooth' : 'auto' });
        button.disabled = false;
      }, M.ok() ? 900 : 0);
    });

    const flipList = $('#ds-flip');
    flipList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-flip-toggle]');
      if (!button) return;
      const row = button.closest('.page-row');
      const moves = M.flip(flipList);
      const dismissed = !row.classList.contains('is-dismissed');
      row.classList.toggle('is-dismissed', dismissed);
      button.textContent = dismissed ? 'Restore' : 'Dismiss';
      // Dismissed rows sink to the end, as review cards do.
      const rows = [...flipList.children];
      rows.filter((r) => !r.classList.contains('is-dismissed')).concat(rows.filter((r) => r.classList.contains('is-dismissed'))).forEach((r) => flipList.append(r));
      moves.play();
      button.focus();
    });
  }

  // Call page recording: a local clock moves the playhead over the example lanes.
  // No audio, no call data, nothing stored.
  const recording = document.querySelector('.ds-call-example .player');
  if (recording) {
    const RATES = [1, 1.25, 1.5, 2];
    const play = recording.querySelector('.play-btn');
    const clock = recording.querySelector('.play-time');
    const speed = recording.querySelector('.speed-btn');
    const head = recording.querySelector('.playhead');
    const seek = recording.querySelector('.seek');
    const END = Number(seek.max); // the clip's length in seconds, as the markup sets it
    // Each block's span in seconds, read back from the percentages that place it.
    const blocks = [...recording.querySelectorAll('.callmap-block')].map((node) => {
      const start = (parseFloat(node.style.left) / 100) * END;
      return { node, start, end: start + (parseFloat(node.style.width) / 100) * END };
    });
    const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
    const symbol = (name) => {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      for (const [k, v] of [['class', 'solar-icon'], ['width', '16'], ['height', '16'], ['viewBox', '0 0 24 24'], ['aria-hidden', 'true'], ['focusable', 'false']]) svg.setAttribute(k, v);
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      use.setAttribute('href', `/static/solar-icons.svg#${name}`);
      svg.append(use);
      return svg;
    };
    let t = 0;
    let rate = 1;
    let timer = null;
    let current = null;
    const sync = () => {
      head.style.left = `${(t / END) * 100}%`;
      seek.value = String(t);
      seek.setAttribute('aria-valuetext', `${mmss(t)} of ${mmss(END)}`);
      clock.textContent = `${mmss(t)} / ${mmss(END)}`;
      // The playing block keeps its ring while paused, as in the product; nothing is marked before the first play.
      const now = t > 0 ? blocks.find((b) => t >= b.start && t < b.end) : null;
      const node = now ? now.node : null;
      if (node === current) return;
      if (current) current.classList.remove('is-current');
      if (node) node.classList.add('is-current');
      current = node;
    };
    const paint = (on) => {
      const icon = symbol(on ? 'pause' : 'play');
      play.replaceChildren(M ? M.swap(icon, 'ds:play', on) : icon);
      play.setAttribute('aria-label', on ? 'Pause the example recording' : 'Play the example recording');
    };
    const stop = () => {
      clearInterval(timer);
      timer = null;
      paint(false);
    };
    const start = () => {
      if (t >= END) t = 0;
      paint(true);
      // Four ticks a second, like the audio element's timeupdate.
      timer = setInterval(() => {
        t = Math.min(END, t + 0.25 * rate);
        sync();
        if (t >= END) stop();
      }, 250);
      sync();
    };
    play.addEventListener('click', () => (timer ? stop() : start()));
    seek.addEventListener('input', () => { t = Number(seek.value); sync(); });
    speed.addEventListener('click', () => {
      rate = RATES[(RATES.indexOf(rate) + 1) % RATES.length];
      speed.textContent = `${rate}×`;
      speed.setAttribute('aria-label', `Playback speed ${rate}×`);
    });
    if (M) M.remember('ds:play', false);
  }

  const navLinks =[...document.querySelectorAll('.ds-sidebar nav a')];
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (!visible.length) return;
    navLinks.forEach((a) => { if (a.hash === `#${visible[0].target.id}`) a.setAttribute('aria-current', 'location'); else a.removeAttribute('aria-current'); });
  }, { rootMargin: '-5% 0px -65% 0px' });
  document.querySelectorAll('.ds-section, .ds-hero').forEach((s) => observer.observe(s));
})();
