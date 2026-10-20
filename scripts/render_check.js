/**
 * Headless render harness for the WITNESS front end.
 *
 * Loads web/app.js in a minimal DOM shim and drives the pure render functions
 * against real API payloads captured from a running server. This catches the
 * class of failure that a static syntax check cannot: a render path that parses
 * but throws at runtime, leaving the user staring at an empty panel.
 *
 * Usage:
 *   node scripts/render_check.js <overview.json> <spec.json> <devices.json>
 *
 * Exit code 0 means every render path completed. Non-zero means one threw, and
 * the error is printed with the function name.
 */

const fs = require('fs');
const path = require('path');

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attrs = {};
    this.style = {};
    this.className = '';
    this._html = '';
  }
  set innerHTML(v) { this._html = v; if (v === '') this.children = []; }
  get innerHTML() { return this._html; }
  get textContent() { return this._text || ''; }
  set textContent(v) { this._text = v; }
  append(...nodes) {
    for (const n of nodes) {
      if (n === null || n === undefined || n === false) continue;
      this.children.push(n);
    }
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener() {}
  remove() {}
  focus() {}
  click() {}
}

const registry = new Map();
const document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => ({ text }),
  querySelector: (sel) => {
    if (!registry.has(sel)) registry.set(sel, new FakeNode('div'));
    return registry.get(sel);
  },
  querySelectorAll: () => [],
  getElementById: (id) => {
    if (!registry.has(id)) registry.set(id, new FakeNode('div'));
    return registry.get(id);
  },
  addEventListener: () => {},
};

global.document = document;
global.Node = FakeNode;
global.window = { location: { href: 'http://localhost/' } };
global.fetch = () => Promise.reject(new Error('fetch disabled in render harness'));

const [, , overviewPath, specPath, devicesPath, verdictCasesPath] = process.argv;
if (!overviewPath || !specPath) {
  console.error('usage: node scripts/render_check.js <overview.json> <spec.json> [devices.json]');
  process.exit(2);
}

const appPath = path.join(__dirname, '..', 'web', 'app.js');
let source = fs.readFileSync(appPath, 'utf8');

// Expose the module's internals so the render functions can be driven directly.
source = source.replace(/^const state =/m, 'globalThis.state =');
source = source.replace(/document\.addEventListener\('DOMContentLoaded'[\s\S]*$/m, '');

// Function declarations inside eval stay in the eval scope, so re-export them.
const EXPORTS = [
  'renderSourceStrip', 'renderOverview', 'renderCapabilityTable', 'renderUnknownList',
  'assessmentCallout', 'deviceCard', 'timelineItem', 'openDrawer', 'closeDrawer',
  'stat', 'pill', 'fmtTime', 'fmtAge', 'shortHash', 'esc', 'el', 'renderIntegration',
  'verdictFor',
];
source += `\n;globalThis.__witness = { ${EXPORTS.join(', ')} };\n`;

try {
  eval(source);
} catch (error) {
  console.error(`FAIL: app.js did not evaluate: ${error.message}`);
  process.exit(1);
}
Object.assign(globalThis, globalThis.__witness);

const overview = JSON.parse(fs.readFileSync(overviewPath, 'utf8'));
const spec = JSON.parse(fs.readFileSync(specPath, 'utf8'));
const devices = devicesPath && devicesPath !== '-' ? JSON.parse(fs.readFileSync(devicesPath, 'utf8')) : null;

globalThis.state.sources = overview.sources;
globalThis.state.statusSpec = Object.fromEntries(spec.statuses.map((s) => [s.status, s]));
globalThis.state.sourceId = overview.reconciliations['ring-simulated'] ? 'ring-simulated' : overview.sources[0].source_id;
globalThis.state.overview = overview;

const checks = [
  ['renderSourceStrip', () => globalThis.renderSourceStrip()],
  ['renderOverview', () => globalThis.renderOverview(overview)],
  ['renderCapabilityTable', () => globalThis.renderCapabilityTable(overview.sources[0])],
  ['renderUnknownList', () => globalThis.renderUnknownList(overview.reconciliations[globalThis.state.sourceId])],
  ['assessmentCallout', () => {
    const recon = overview.reconciliations[globalThis.state.sourceId];
    const target = recon.source_assessment
      || (recon.devices[0] && recon.devices[0].presence)
      || (recon.limitations[0]);
    if (target) globalThis.assessmentCallout(target);
  }],
  ['deviceCard', () => {
    if (devices && devices.devices.length) globalThis.deviceCard(devices.devices[0]);
  }],
  ['timelineItem', () => {
    if (devices) return;
    globalThis.timelineItem({
      observation_id: 'obs-1', observed_at: new Date().toISOString(), source_kind: 'simulated',
      inventory_ok: false, inventory_failure: { kind: 'SERVER_ERROR' }, device_count: 0,
      event_count: 0, devices: [],
    });
    globalThis.timelineItem({
      observation_id: 'obs-2', observed_at: new Date().toISOString(), source_kind: 'live',
      inventory_ok: true, device_count: 2, event_count: 1,
      devices: [{ device_id: 'd1', device_label: 'Front Door' }],
    });
  }],
  ['openDrawer', () => {
    const recon = overview.reconciliations[globalThis.state.sourceId];
    const target = recon.source_assessment || (recon.devices[0] && recon.devices[0].content)
      || (recon.limitations[0]);
    if (target) globalThis.openDrawer(target);
  }],
  ['stat', () => globalThis.stat('Observed', 2, 'ok', 'note')],
  ['pill', () => globalThis.pill('OBSERVED', 'ok', 'Observed')],
  ['fmtTime', () => { globalThis.fmtTime(null); globalThis.fmtTime('2026-10-20T12:00:00Z'); globalThis.fmtTime('nonsense'); }],
  ['fmtAge', () => { globalThis.fmtAge(null); globalThis.fmtAge(10); globalThis.fmtAge(9000); }],
  ['shortHash', () => { globalThis.shortHash(null); globalThis.shortHash('a'.repeat(64)); }],
  ['verdictFor', () => {
    const recon = overview.reconciliations[globalThis.state.sourceId];
    const verdict = globalThis.verdictFor(recon);
    if (!verdict || typeof verdict.line !== 'string') throw new Error('no verdict line');
    // A warning that says "0 not fully accounted for" contradicts itself: it
    // warns while reporting nothing unresolved. This is the frame-1 shape
    // (every device observed, one limitation open).
    if (verdict.tone === 'warn' && /;\s*0 not fully accounted for/.test(verdict.line)) {
      throw new Error(`self-contradictory verdict: ${verdict.line}`);
    }
  }],
];

let failures = 0;
for (const [name, fn] of checks) {
  try {
    fn();
    console.log(`  ok    ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`  THREW ${name}: ${error.message}`);
  }
}

// Optional: drive verdictFor over named reconciliation shapes supplied as JSON.
// The overview payload only ever holds one source's current shape, so the
// frame-by-frame wording is checked here instead.
if (verdictCasesPath) {
  const cases = JSON.parse(fs.readFileSync(verdictCasesPath, 'utf8'));
  for (const [name, recon] of Object.entries(cases)) {
    try {
      const verdict = globalThis.verdictFor(recon);
      if (!verdict || typeof verdict.line !== 'string') {
        throw new Error('no verdict line');
      }
      if (verdict.tone === 'warn' && /;\s*0 not fully accounted for/.test(verdict.line)) {
        throw new Error(`self-contradictory verdict: ${verdict.line}`);
      }
      console.log(`  ok    verdict:${name} [${verdict.tone}] ${verdict.line}`);
    } catch (error) {
      failures += 1;
      console.error(`  THREW verdict:${name}: ${error.message}`);
    }
  }
}

if (failures) {
  console.error(`\nFAIL: ${failures} render path(s) threw`);
  process.exit(1);
}
console.log(`\nOK: all ${checks.length} render paths completed`);
