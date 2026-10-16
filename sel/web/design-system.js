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
    $('#motion-status').textContent = disabled ? 'Reduced motion · instant feedback' : '280 ms · gentle settle';
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
  const navLinks = [...document.querySelectorAll('.ds-sidebar nav a')];
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (!visible.length) return;
    navLinks.forEach((a) => { if (a.hash === `#${visible[0].target.id}`) a.setAttribute('aria-current', 'location'); else a.removeAttribute('aria-current'); });
  }, { rootMargin: '-5% 0px -65% 0px' });
  document.querySelectorAll('.ds-section, .ds-hero').forEach((s) => observer.observe(s));
})();
