/* ==========================================================================
   WITNESS — front end
   Plain ES modules-free JS, no build step, no framework. Everything rendered
   here comes from the deterministic engine over the API. The UI never invents
   a status, a device, a statistic or an event.
   ========================================================================== */

const state = {
  sourceId: null,
  sources: [],
  overview: null,
  statusSpec: {},
  busy: false,
};

/* ------------------------------- utilities ------------------------------ */

const $ = (sel) => document.querySelector(sel);

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  // Callers routinely pass an explicit null when a node has no attributes, so
  // coalesce here rather than forcing every call site to write {}.
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { Accept: 'application/json' }, ...options });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { const body = await response.json(); detail = body.detail || detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  return response.json();
}

function toast(message, tone = 'unknown') {
  const node = $('#toast');
  node.textContent = message;
  node.dataset.tone = tone;
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, 6000);
}

function fmtTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z');
}

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return 'never';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function shortHash(hash) {
  if (!hash) return '—';
  return `${hash.slice(0, 10)}…${hash.slice(-6)}`;
}

function pill(status, tone, label) {
  return el('span', { class: `pill tone-${tone}`, dataset: { tone } }, label || status);
}

/* --------------------------------- boot --------------------------------- */

async function boot() {
  try {
    const [sources, spec] = await Promise.all([api('/api/sources'), api('/api/status-spec')]);
    state.sources = sources.sources;
    state.statusSpec = Object.fromEntries(spec.statuses.map((s) => [s.status, s]));

    const select = $('#source-select');
    select.innerHTML = '';
    for (const source of state.sources) {
      select.append(el('option', { value: source.source_id },
        `${source.display_name}${source.is_simulated ? '  · simulated' : ''}${source.configured ? '' : '  · not configured'}`));
    }

    const preferred = state.sources.find((s) => s.source_id === 'ring-simulated') || state.sources[0];
    state.sourceId = preferred.source_id;
    select.value = state.sourceId;

    select.addEventListener('change', async () => {
      state.sourceId = select.value;
      await refreshAll();
    });

    $('#observe-btn').addEventListener('click', observeNow);
    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-scrim').addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeDrawer();
    });

    setupTabs();
    await refreshAll();
  } catch (error) {
    renderFatal(error);
  }
}

function setupTabs() {
  const tabs = Array.from(document.querySelectorAll('.tab'));
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => selectTab(tab.id));
    tab.addEventListener('keydown', (event) => {
      const index = tabs.indexOf(tab);
      let next = null;
      if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
      if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
      if (event.key === 'Home') next = tabs[0];
      if (event.key === 'End') next = tabs[tabs.length - 1];
      if (next) { event.preventDefault(); next.focus(); selectTab(next.id); }
    });
  });
}

function selectTab(tabId) {
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach((tab) => {
    const selected = tab.id === tabId;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute('aria-controls'));
    if (panel) panel.hidden = !selected;
  });
}

/* ------------------------------- refresh -------------------------------- */

async function refreshAll() {
  await Promise.all([refreshOverview(), renderTimeline(), renderIntegrity(), renderIntegration(), renderMethod()]);
  renderSourceStrip();
  $('#footer-meta').textContent =
    `Source: ${state.sourceId} · Rendered ${fmtTime(new Date().toISOString())}`;
}

function currentSource() {
  return state.sources.find((s) => s.source_id === state.sourceId);
}

function renderSourceStrip() {
  const source = currentSource();
  if (!source) return;
  const strip = $('#source-hint');
  strip.innerHTML = '';
  strip.append(
    el('span', {}, source.source_kind === 'simulated'
      ? 'SIMULATED SOURCE — generated by WITNESS, does not contact Ring'
      : 'LIVE SOURCE — calls the Ring Partner API'),
    el('span', {}, source.configured ? 'credentials: configured' : 'credentials: not configured'),
    el('span', {}, `capabilities: ${Object.keys(source.capabilities || {}).length}`),
  );
  $('#sim-banner').hidden = source.source_kind !== 'simulated';
}

async function observeNow() {
  if (state.busy) return;
  state.busy = true;
  const button = $('#observe-btn');
  button.classList.add('is-busy');
  button.disabled = true;
  try {
    const result = await api(`/api/observe/${state.sourceId}`, { method: 'POST' });
    const recon = result.reconciliation;
    const observed = recon.counts.observed;
    if (!recon.latest_inventory_ok) {
      toast('Observation recorded. The source request failed, so no device conclusions were drawn.', 'error');
    } else {
      toast(`Observation recorded. ${observed} device(s) observed. Ledger +${result.ledger_appended} records.`, 'ok');
    }
    await refreshAll();
  } catch (error) {
    toast(`Observation failed: ${error.message}`, 'error');
  } finally {
    state.busy = false;
    button.classList.remove('is-busy');
    button.disabled = false;
  }
}

/* ------------------------------- overview ------------------------------- */

async function refreshOverview() {
  const container = $('#overview-content');
  try {
    const overview = await api('/api/overview');
    state.overview = overview;
    renderOverview(overview);
  } catch (error) {
    container.innerHTML = '';
    container.append(el('div', { class: 'error-state' }, `Could not load overview: ${error.message}`));
  }
}

/**
 * The overview verdict, as a pure function of a reconciliation payload.
 *
 * Extracted so the wording can be tested directly. The tone and the sentence
 * have to agree: an earlier version warned "0 not fully accounted for" while
 * claiming the rest was left unresolved, which is a contradiction a reader can
 * see on the first screen.
 */
function verdictFor(recon) {
  const hasObservation = Boolean(recon.latest_observation_at);
  if (!hasObservation) {
    return {
      tone: 'unknown',
      line: 'WITNESS has not observed this source yet.',
      sub: 'Nothing has been claimed. Run an observation to establish what this source can currently be asked.',
    };
  }
  if (!recon.latest_inventory_ok) {
    return {
      tone: 'error',
      line: 'The last request to this source failed. No device conclusions can be drawn.',
      sub: 'A failed request is not evidence that a device is offline, missing, or that nothing happened. '
        + 'It is evidence that WITNESS could not look.',
    };
  }
  const unresolved = recon.counts.not_observed + recon.counts.unknown
    + recon.counts.withdrawn + recon.counts.unavailable;
  if (unresolved > 0) {
    return {
      tone: 'warn',
      line: `${recon.counts.observed} of ${recon.counts.total_devices} device(s) observed; `
        + `${unresolved} not fully accounted for.`,
      sub: 'WITNESS is reporting what it can establish and is leaving the rest explicitly unresolved '
        + 'rather than filling the gap with a plausible answer.',
    };
  }
  if (recon.limitations.length > 0) {
    // Every device is accounted for, but a question WITNESS cannot answer
    // remains. Counting devices here would print "0 not fully accounted for"
    // directly above a warning, so count the open questions instead.
    const n = recon.limitations.length;
    return {
      tone: 'warn',
      line: `All ${recon.counts.observed} device(s) observed; `
        + `${n} open question${n === 1 ? '' : 's'} WITNESS will not guess at.`,
      sub: 'Every device the source returned is accounted for, but WITNESS is still reporting '
        + 'what it cannot establish rather than filling the gap with a plausible answer.',
    };
  }
  return {
    tone: 'ok',
    line: `All ${recon.counts.observed} device(s) observed. No unresolved visibility conditions.`,
    sub: 'Every device the source returned is accounted for in this observation.',
  };
}

function renderOverview(overview) {
  const container = $('#overview-content');
  container.innerHTML = '';

  const recon = overview.reconciliations[state.sourceId];
  const source = currentSource();
  if (!recon) {
    container.append(el('div', { class: 'empty' },
      el('div', { class: 'empty-title' }, 'Source not available'),
      el('div', { class: 'empty-body' }, 'This source is not registered.')));
    return;
  }

  /* The first screen must answer three questions immediately. */
  const hasObservation = Boolean(recon.latest_observation_at);
  const stale = recon.staleness?.stale;
  const verdict = verdictFor(recon);
  const verdictTone = verdict.tone;
  const verdictLine = verdict.line;
  const verdictSub = verdict.sub;

  container.append(
    el('div', { class: `verdict tone-${verdictTone}` },
      el('p', { class: 'eyebrow' }, 'Current assessment'),
      el('p', { class: 'verdict-line' }, verdictLine),
      el('p', { class: 'verdict-sub' }, verdictSub)),
  );

  if (stale && hasObservation) {
    container.append(el('div', { class: 'stale-banner', role: 'status' },
      el('span', { 'aria-hidden': 'true' }, '▲'),
      el('div', null,
        el('strong', null, 'This evidence is stale. '),
        `The most recent successful observation was ${fmtAge(recon.staleness.age_seconds)}. `
        + `WITNESS marks evidence stale after ${Math.round(recon.staleness.staleness_threshold_seconds / 60)} minutes `
        + 'and will not present it as current.')));
  }

  /* Three questions: what can we inspect, what does it establish, what is unknown. */
  container.append(el('div', { class: 'section' },
    el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'What WITNESS can inspect'),
      el('p', { class: 'section-note' },
        `${source?.display_name ?? state.sourceId} — ${source?.source_kind === 'simulated' ? 'simulated source' : 'live source'}. `
        + `Last successful observation: ${fmtTime(recon.latest_observation_at)}.`)),
    renderCapabilityTable(source)));

  container.append(el('div', { class: 'section' },
    el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'What the evidence establishes'),
      el('p', { class: 'section-note' }, 'Counts are of devices, not of events. Each device carries its own explanation.')),
    el('div', { class: 'stat-row' },
      stat('Observed', recon.counts.observed, 'ok', 'returned by the source'),
      stat('Not observed', recon.counts.not_observed, recon.counts.not_observed ? 'unknown' : 'ok', 'absent, below threshold'),
      stat('No longer exposed', recon.counts.withdrawn, recon.counts.withdrawn ? 'warn' : 'ok', 'withdrawal asserted'),
      stat('Source unavailable', recon.counts.unavailable, recon.counts.unavailable ? 'error' : 'ok', 'request failed'),
      stat('Encrypted', recon.counts.encrypted, recon.counts.encrypted ? 'warn' : 'ok', 'content unreadable'),
      stat('Limitations', recon.counts.limitations, recon.counts.limitations ? 'unknown' : 'ok', 'questions we cannot answer'))));

  container.append(el('div', { class: 'section' },
    el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'What remains unknown'),
      el('p', { class: 'section-note' },
        'Every item below is a question WITNESS was asked or is obliged to consider, and cannot answer from the available evidence.')),
    renderUnknownList(recon)));

  container.append(el('div', { class: 'section' },
    el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'Decision policy'),
      el('p', { class: 'section-note' }, 'These thresholds are the whole of WITNESS\u2019s policy. They are explicit and inspectable.')),
    el('dl', { class: 'meta-grid' },
      el('dt', null, 'withdrawal threshold'),
      el('dd', null, `${overview.policy.withdrawal_threshold} consecutive successful absences`),
      el('dt', null, 'event lateness threshold'),
      el('dd', null, `${Math.round(overview.policy.event_lateness_threshold_seconds / 60)} minutes`),
      el('dt', null, 'staleness threshold'),
      el('dd', null, `${Math.round(overview.policy.staleness_threshold_seconds / 60)} minutes`),
      el('dt', null, 'reconciliation window'),
      el('dd', null, `${overview.policy.reconciliation_window} observations`),
      el('dt', null, 'ledger records'),
      el('dd', null, String(overview.ledger.record_count)),
      el('dt', null, 'ledger chain'),
      el('dd', null, overview.ledger.verification.valid ? 'verified' : 'FAILED VERIFICATION'))));
}

function stat(label, value, tone, note) {
  return el('div', { class: 'stat', dataset: { tone } },
    el('span', { class: 'stat-label' }, label),
    el('span', { class: 'stat-value' }, String(value)),
    el('span', { class: 'stat-note' }, note));
}

function renderCapabilityTable(source) {
  if (!source || !source.capabilities) return el('div', { class: 'empty' }, 'No capability data.');
  const rows = Object.entries(source.capabilities).map(([name, level]) => {
    const tone = { VERIFIED: 'ok', DOCUMENTED: 'unknown', SIMULATED: 'warn', UNAVAILABLE: 'error' }[level] || 'unknown';
    return el('tr', null,
      el('td', { class: 'mono' }, name.replace(/_/g, ' ')),
      el('td', null, pill(level, tone, level)));
  });
  return el('div', { class: 'table-wrap' },
    el('table', null,
      el('thead', null, el('tr', null, el('th', null, 'Capability'), el('th', null, 'Status'))),
      el('tbody', null, ...rows)));
}

function renderUnknownList(recon) {
  const items = [];

  if (recon.source_assessment) {
    items.push(assessmentCallout(recon.source_assessment));
  }
  for (const limitation of recon.limitations) {
    items.push(assessmentCallout(limitation));
  }
  for (const device of recon.devices) {
    if (device.presence.status !== 'OBSERVED') items.push(assessmentCallout(device.presence));
    if (device.content.status === 'SOURCE_ENCRYPTED' || device.content.status === 'CONTENT_UNAVAILABLE') {
      items.push(assessmentCallout(device.content));
    }
  }
  for (const event of recon.events) {
    if (event.status !== 'EVENT_OBSERVED') items.push(assessmentCallout(event));
  }

  if (items.length === 0) {
    return el('div', { class: 'empty' },
      el('div', { class: 'empty-title' }, 'Nothing is currently unresolved'),
      el('div', { class: 'empty-body' },
        'Every device returned by this source is accounted for, and no limitation applies to the current evidence. '
        + 'This does not mean the source reported everything that happened \u2014 only that WITNESS has no open question about what it did report.'));
  }
  const wrapper = el('div', null);
  items.forEach((item) => wrapper.append(item));
  return wrapper;
}

function assessmentCallout(assessment) {
  const spec = state.statusSpec[assessment.status] || {};
  const tone = assessment.tone || 'unknown';
  const node = el('div', { class: `callout tone-${tone}` },
    el('div', { class: 'callout-title' },
      pill(assessment.status, tone, assessment.label || spec.label || assessment.status),
      ' ',
      el('span', { class: 'rule-id' }, assessment.rule_id)),
    el('p', { class: 'callout-body' }, assessment.explanation));

  if (assessment.does_not_establish && assessment.does_not_establish.length) {
    node.append(el('div', { class: 'not-establish' },
      el('div', { class: 'not-establish-title' }, 'This does not establish'),
      el('ul', null, ...assessment.does_not_establish.map((item) => el('li', null, item)))));
  }
  node.append(el('button', {
    class: 'btn btn-sm',
    type: 'button',
    onclick: () => openDrawer(assessment),
  }, 'Evidence detail'));
  return node;
}

/* -------------------------------- devices ------------------------------- */

async function renderDevices() {
  const container = $('#devices-content');
  container.innerHTML = '';
  container.append(el('div', { class: 'loading' }, 'Loading devices\u2026'));
  try {
    const body = await api(`/api/devices/${state.sourceId}`);
    container.innerHTML = '';

    if (!body.devices.length) {
      container.append(el('div', { class: 'empty' },
        el('div', { class: 'empty-title' }, 'No devices are known'),
        el('div', { class: 'empty-body' },
          body.latest_observation_ok === false
            ? 'The last request to this source failed, so WITNESS has no device list. A failed request is not an empty device list.'
            : 'The source has not yet returned any devices to WITNESS. If the source returned an empty list, that is recorded as a successful response with no devices \u2014 not as a failure.')));
      return;
    }

    container.append(el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, `${body.devices.length} device(s)`),
      el('p', { class: 'section-note' }, `Source ${body.source_id} · last observation ${fmtTime(body.generated_at)}`)));

    const grid = el('div', { class: 'device-grid' });
    for (const device of body.devices) grid.append(deviceCard(device));
    container.append(grid);
  } catch (error) {
    container.innerHTML = '';
    container.append(el('div', { class: 'error-state' }, `Could not load devices: ${error.message}`));
  }
}

function deviceCard(device) {
  const presenceTone = device.presence.tone || 'unknown';
  const contentTone = device.content.tone || 'unknown';
  const card = el('article', { class: `device tone-${presenceTone}` });

  card.append(el('div', { class: 'device-head' },
    el('div', null,
      el('h3', { class: 'device-name' }, device.device_label),
      el('p', { class: 'device-id' }, device.device_id)),
    el('div', { class: 'device-pills' },
      pill(device.presence.status, presenceTone, device.presence.label),
      pill(device.content.status, contentTone, device.content.label))));

  card.append(el('p', { class: 'device-explain' }, device.presence.explanation));

  if (device.staleness?.stale) {
    card.append(el('p', { class: 'device-explain' },
      el('strong', null, 'Stale evidence. '),
      `Observed ${fmtAge(device.staleness.age_seconds)}; WITNESS will not present this as current.`));
  }

  card.append(el('dl', { class: 'meta-grid' },
    el('dt', null, 'first seen'), el('dd', null, fmtTime(device.first_seen_at)),
    el('dt', null, 'last seen'), el('dd', null, fmtTime(device.last_seen_at)),
    el('dt', null, 'observations'), el('dd', null, String(device.observation_count)),
    el('dt', null, 'rule'), el('dd', null, el('span', { class: 'rule-id' }, device.presence.rule_id))));

  card.append(el('div', { style: 'margin-top:0.7rem; display:flex; gap:0.4rem; flex-wrap:wrap;' },
    el('button', { class: 'btn btn-sm', type: 'button', onclick: () => openDrawer(device.presence) }, 'Presence evidence'),
    el('button', { class: 'btn btn-sm', type: 'button', onclick: () => openDrawer(device.content) }, 'Content evidence')));

  return card;
}

/* ------------------------------- timeline ------------------------------- */

async function renderTimeline() {
  const container = $('#timeline-content');
  container.innerHTML = '';
  try {
    const body = await api(`/api/timeline/${state.sourceId}`);
    const entries = [...body.entries].reverse();

    container.append(el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'Evidence timeline'),
      el('p', { class: 'section-note' },
        'Every observation WITNESS has taken, newest first. Source failures, inventory changes and local test events are labelled distinctly.')));

    if (!entries.length) {
      container.append(el('div', { class: 'empty' },
        el('div', { class: 'empty-title' }, 'No observations recorded'),
        el('div', { class: 'empty-body' }, 'Run an observation to begin building the evidence history.')));
      return;
    }

    const list = el('div', { class: 'timeline' });
    for (const entry of entries) list.append(timelineItem(entry));
    container.append(list);
  } catch (error) {
    container.innerHTML = '';
    container.append(el('div', { class: 'error-state' }, `Could not load timeline: ${error.message}`));
  }
}

function timelineItem(entry) {
  const simulated = entry.source_kind === 'simulated';
  const failed = entry.inventory_ok === false;
  const tone = failed ? 'error' : (simulated ? 'warn' : 'ok');

  const item = el('div', { class: `tl-item tone-${tone}` });
  item.append(el('div', { class: 'tl-head' },
    el('span', { class: 'tl-time' }, fmtTime(entry.observed_at)),
    el('span', { class: 'tl-title' }, failed ? 'Source request failed' : 'Observation succeeded'),
    pill(failed ? 'SOURCE_UNAVAILABLE' : 'OBSERVED', tone, simulated ? 'SIMULATED' : 'LIVE')));

  const body = el('div', { class: 'tl-body' });
  if (failed) {
    body.append(el('p', null,
      `The inventory request did not complete (${entry.inventory_failure?.kind || 'unknown'}). `
      + 'No device conclusion was drawn from this observation.'));
  } else {
    body.append(el('p', null, `${entry.device_count} device(s) returned, ${entry.event_count} event(s) retrieved.`));
    if (entry.devices?.length) {
      body.append(el('ul', null, ...entry.devices.map((d) => el('li', null, `${d.device_label} (${d.device_id})`))));
    } else {
      body.append(el('p', null, 'The source returned an empty device list. This is a successful response, not a failure.'));
    }
  }
  if (simulated) {
    body.append(el('p', { class: 'mono', style: 'color:var(--warn)' }, 'Generated locally from the scripted scenario. Not Ring data.'));
  }
  item.append(body);
  return item;
}

/* ------------------------------- integrity ------------------------------ */

async function renderIntegrity() {
  const container = $('#integrity-content');
  container.innerHTML = '';
  try {
    const ledger = await api('/api/ledger?limit=60');
    const verification = await api('/api/ledger/verify');

    container.append(el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'Evidence ledger'),
      el('p', { class: 'section-note' },
        'Every observation and every assessment is appended to a hash-linked chain. Each record commits to the digest of the record before it.')));

    const banner = verification.valid
      ? el('div', { class: 'verify-ok', role: 'status' },
          el('span', { class: 'verify-glyph', 'aria-hidden': 'true' }, '✓'),
          el('div', null,
            el('strong', null, `Chain verified — ${verification.record_count} records intact. `),
            `Verified at ${fmtTime(verification.verified_at)}. No record has been modified, inserted, removed or reordered within this chain.`))
      : el('div', { class: 'verify-bad', role: 'alert' },
          el('span', { class: 'verify-glyph', 'aria-hidden': 'true' }, '✕'),
          el('div', null,
            el('strong', null, 'Chain verification FAILED. '),
            `${verification.reason} at index ${verification.first_bad_index}. ${verification.detail || ''} `
            + 'The evidence below cannot be trusted.'));

    container.append(banner);

    container.append(el('dl', { class: 'meta-grid', style: 'margin:1rem 0' },
      el('dt', null, 'records'), el('dd', null, String(ledger.record_count)),
      el('dt', null, 'head hash'), el('dd', null, shortHash(ledger.head_hash)),
      el('dt', null, 'algorithm'), el('dd', null, 'SHA-256, canonical JSON'),
      el('dt', null, 'hashed fields'), el('dd', null, 'seq, record_id, prev_hash, recorded_at, kind, subject_id, rule_id, status, scope, evidence_refs, payload')));

    container.append(el('div', { class: 'section-head' },
      el('h3', { class: 'section-title', style: 'font-size:var(--step-1)' }, 'Recent records'),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: exportLedger }, 'Export ledger')));

    if (!ledger.records.length) {
      container.append(el('div', { class: 'empty' },
        el('div', { class: 'empty-title' }, 'The ledger is empty'),
        el('div', { class: 'empty-body' }, 'Run an observation to append the first evidence record.')));
    } else {
      const chain = el('div', { class: 'chain' });
      for (const record of ledger.records.slice(-30).reverse()) {
        chain.append(el('div', { class: 'chain-row' },
          el('span', { class: 'chain-seq' }, String(record.seq).padStart(4, '0')),
          el('span', { class: 'chain-kind' }, `${record.kind} · ${record.subject_id}${record.status ? ` · ${record.status}` : ''}`),
          el('span', { class: 'chain-hash', title: record.hash }, shortHash(record.hash))));
      }
      container.append(chain);
    }

    /* The honest limitation. Stated plainly, not buried. */
    container.append(el('div', { class: 'limit-note' },
      el('p', null, el('strong', null, 'What this ledger does and does not prove.')),
      el('p', null,
        'It proves that any modification, insertion, deletion or reordering of the records above is detectable: the chain will stop reproducing and verification will name the first bad index. '
        + 'It does not prove the ledger was never rewritten as a whole. An operator with write access to the database and knowledge of this format could regenerate a fresh, internally consistent chain. '
        + 'Detecting that requires an independently anchored checkpoint \u2014 a digest held somewhere the operator does not control. WITNESS therefore describes this ledger as tamper-evident, never as immutable or independently certified.')));
  } catch (error) {
    container.innerHTML = '';
    container.append(el('div', { class: 'error-state' }, `Could not load the ledger: ${error.message}`));
  }
}

async function exportLedger() {
  try {
    const response = await fetch('/api/ledger/export');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: 'witness-evidence-ledger.json' });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast('Evidence ledger exported.', 'ok');
  } catch (error) {
    toast(`Export failed: ${error.message}`, 'error');
  }
}

/* ------------------------------ integration ----------------------------- */

async function renderIntegration() {
  const container = $('#integration-content');
  container.innerHTML = '';
  try {
    const [ring, sim] = await Promise.all([
      api('/api/ring/connection'),
      api('/api/simulated/state'),
    ]);

    container.append(el('div', { class: 'section-head' },
      el('h2', { class: 'section-title' }, 'Ring Partner API'),
      el('p', { class: 'section-note' }, 'The live integration. Read-only, bearer-token authenticated.')));

    container.append(el('div', { class: `callout tone-${ring.configured ? 'ok' : 'warn'}` },
      el('div', { class: 'callout-title' }, ring.configured ? 'Credentials configured' : 'No credentials configured'),
      el('p', { class: 'callout-body' },
        ring.configured
          ? `A Ring access token is loaded (fingerprint ${ring.token_fingerprint}). WITNESS will call the live API on the next observation.`
          : 'WITNESS has no Ring access token, so it will not contact the Ring API. It reports this as a source that could not be inspected rather than as an empty device list.'),
      el('p', { class: 'callout-body mono' }, ring.verification_note)));

    container.append(el('div', { class: 'section' },
      el('h3', { class: 'section-title', style: 'font-size:var(--step-1)' }, 'Documented capabilities'),
      renderCapabilityTable({ capabilities: ring.capabilities })));

    container.append(el('div', { class: 'section' },
      el('div', { class: 'section-head' },
        el('h2', { class: 'section-title' }, 'Simulated source'),
        el('p', { class: 'section-note' },
          'The scripted scenario used to exercise conditions the Ring Playground cannot produce.')),
      el('div', { class: 'callout tone-warn' },
        el('div', { class: 'callout-title' }, 'This is not Ring'),
        el('p', { class: 'callout-body' },
          'WITNESS generates this data from a scenario table. It does not contact Ring and does not replay a recorded Ring session. '
          + 'It is labelled simulated everywhere it appears.')),
      el('dl', { class: 'meta-grid' },
        el('dt', null, 'frame'), el('dd', null, `${sim.cursor + 1} of ${sim.frames}`),
        el('dt', null, 'frame note'), el('dd', null, sim.frame_note || '—')),
      el('div', { style: 'display:flex; gap:0.45rem; flex-wrap:wrap; margin-top:0.8rem' },
        el('button', { class: 'btn btn-sm', type: 'button', onclick: () => stepSimulated('advance') }, 'Advance scenario'),
        el('button', { class: 'btn btn-sm', type: 'button', onclick: () => stepSimulated('reset') }, 'Reset scenario'),
        el('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: () => observeNow() }, 'Observe now'))));
  } catch (error) {
    container.innerHTML = '';
    container.append(el('div', { class: 'error-state' }, `Could not load integration status: ${error.message}`));
  }
}

async function stepSimulated(action) {
  try {
    const result = await api(`/api/simulated/${action}`, { method: 'POST' });
    toast(result.frame_note ? `Scenario at frame ${result.cursor + 1}: ${result.frame_note}` : `Scenario ${action}d.`, 'warn');
    await refreshAll();
  } catch (error) {
    toast(`Could not ${action} the scenario: ${error.message}`, 'error');
  }
}

/* -------------------------------- method -------------------------------- */

async function renderMethod() {
  const container = $('#method-content');
  if (container.dataset.rendered === '1') return;
  container.innerHTML = '';
  container.dataset.rendered = '1';

  container.append(el('div', { class: 'section-head' },
    el('h2', { class: 'section-title' }, 'Status taxonomy'),
    el('p', { class: 'section-note' },
      'Every status WITNESS can assign, with the exact condition that triggers it and the conclusions it does not license. This page is generated from the engine\u2019s own registry, not written by hand.')));

  const groups = [
    ['Presence', ['OBSERVED', 'NOT_OBSERVED', 'SOURCE_WITHDRAWN', 'SOURCE_UNAVAILABLE']],
    ['Content', ['CONTENT_VISIBLE', 'SOURCE_ENCRYPTED', 'CONTENT_UNAVAILABLE']],
    ['Events', ['EVENT_OBSERVED', 'EVENT_LATE', 'EVENT_DUPLICATE']],
    ['Limitations', ['CLASSIFICATION_UNAVAILABLE', 'INSUFFICIENT_EVIDENCE', 'UNKNOWN']],
  ];

  for (const [groupName, statuses] of groups) {
    const wrapper = el('div', { class: 'section' });
    wrapper.append(el('h3', { class: 'section-title', style: 'font-size:var(--step-1)' }, groupName));
    for (const key of statuses) {
      const spec = state.statusSpec[key];
      if (!spec) continue;
      wrapper.append(el('div', { class: `callout tone-${spec.tone}` },
        el('div', { class: 'callout-title' }, pill(spec.status, spec.tone, spec.label)),
        el('p', { class: 'callout-body' }, spec.definition),
        el('dl', { class: 'meta-grid' },
          el('dt', null, 'trigger'), el('dd', null, spec.trigger),
          el('dt', null, 'required evidence'), el('dd', null, spec.required_evidence.join(', ') || 'none')),
        el('div', { class: 'not-establish' },
          el('div', { class: 'not-establish-title' }, 'This does not establish'),
          el('ul', null, ...spec.does_not_establish.map((item) => el('li', null, item))))));
    }
    container.append(wrapper);
  }

  container.append(el('div', { class: 'limit-note' },
    el('p', null, el('strong', null, 'The core principle.')),
    el('p', null,
      'Missing evidence is not proof that nothing happened. WITNESS distinguishes a successful response that contains no record from a request that failed, '
      + 'and it never converts the absence of a record into an assertion that an event did not occur.')));
}

/* --------------------------------- drawer ------------------------------- */

function openDrawer(assessment) {
  const drawer = $('#drawer');
  const body = $('#drawer-body');
  const spec = state.statusSpec[assessment.status] || {};
  const tone = assessment.tone || 'unknown';

  $('#drawer-title').textContent = assessment.subject_label || assessment.subject_id;

  body.innerHTML = '';
  body.append(el('div', { class: `callout tone-${tone}` },
    el('div', { class: 'callout-title' }, pill(assessment.status, tone, assessment.label || spec.label)),
    el('p', { class: 'callout-body' }, assessment.explanation)));

  body.append(el('dl', { class: 'kv' },
    el('dt', null, 'subject'), el('dd', null, `${assessment.subject_type} · ${assessment.subject_id}`),
    el('dt', null, 'axis'), el('dd', null, assessment.axis),
    el('dt', null, 'rule'), el('dd', null, assessment.rule_id),
    el('dt', null, 'scope'), el('dd', null, assessment.scope),
    el('dt', null, 'observed at'), el('dd', null, fmtTime(assessment.observed_at)),
    el('dt', null, 'evidence refs'), el('dd', null, (assessment.evidence_refs || []).join(', ') || 'none')));

  if (assessment.detail && Object.keys(assessment.detail).length) {
    body.append(el('p', { class: 'eyebrow' }, 'Rule detail'));
    body.append(el('pre', { html: esc(JSON.stringify(assessment.detail, null, 2)) }));
  }

  if (spec.definition) {
    body.append(el('p', { class: 'eyebrow' }, 'Definition'));
    body.append(el('p', { class: 'callout-body' }, spec.definition));
  }
  if (spec.required_evidence?.length) {
    body.append(el('p', { class: 'eyebrow' }, 'Required evidence'));
    body.append(el('p', { class: 'callout-body mono' }, spec.required_evidence.join(', ')));
  }
  if (assessment.does_not_establish?.length) {
    body.append(el('p', { class: 'eyebrow' }, 'Does not establish'));
    body.append(el('ul', { class: 'callout-body' },
      ...assessment.does_not_establish.map((item) => el('li', null, item))));
  }

  body.append(el('p', { class: 'eyebrow' }, 'Reproducing this'));
  body.append(el('p', { class: 'callout-body' },
    'This assessment is the output of the named rule applied to the referenced observations. '
    + 'The rule is pure: replaying it against the stored observations produces this same result. '
    + 'The observation and this assessment are both recorded in the evidence ledger, where their integrity can be verified.'));

  $('#drawer-scrim').hidden = false;
  drawer.hidden = false;
  $('#drawer-close').focus();
}

function closeDrawer() {
  $('#drawer').hidden = true;
  $('#drawer-scrim').hidden = true;
}

/* --------------------------------- fatal -------------------------------- */

function renderFatal(error) {
  const main = $('#main');
  main.innerHTML = '';
  main.append(el('div', { class: 'error-state' },
    el('p', null, el('strong', null, 'WITNESS could not start.')),
    el('p', null, error.message),
    el('p', null, 'Check that the API is running and reload.')));
}

/* ------------------------- devices tab lazy render ---------------------- */

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('tab-devices').addEventListener('click', renderDevices);
  boot();
});
