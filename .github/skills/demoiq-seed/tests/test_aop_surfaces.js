// test_aop_surfaces.js - Copilot Code / Copilot Autopilot AoP surfaces: the engine,
// the Config Builder's baked surface list and generate.js must agree, and selecting
// the surfaces must change what the seeder and the content prompts say. Node
// built-ins only.
//
// Covers:
//   1. --list: both surfaces exist with the agreed ids / headings / implies and sit
//      after the classic seven (the config hand-off contract is the ids).
//   2. Config Builder DATA.aopSurfaces == the engine list - the builder's list is
//      baked by hand, so this is the sync check.
//   3. The engine picker has the two checkboxes.
//   4. Seeder: a fourth AoP batch (Code + Autopilot) with its catalog, both
//      headings, the "Before you demo" availability notes and gold-standard
//      examples 9/10 - only when selected. Classic selections and a legacy config
//      with no aopSurfaces at all keep the original batches (preview surfaces are
//      opt-in, never part of the "nothing ticked = everything" default).
//   5. Content prompts: the email / Excel / meeting hints appear only when the
//      matching surface is selected.
//
// generate.js records no telemetry in the GitHub Copilot adaptation; run_tests.ps1
// also isolates HOME, so every child process stays inside temp folders.
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const here = __dirname;
const root = path.resolve(here, '..');
const generateJs = path.join(root, 'scripts', 'generate.js');
const engineHtml = path.join(root, 'engine', 'DemoIQ.html');
const builderHtml = path.join(root, 'DemoIQ-Config-Builder.html');

let failures = 0;
function check(name, ok, detail) {
  if (ok) { console.log('  ok - ' + name); }
  else { failures++; console.log('  FAIL - ' + name + (detail ? ' :: ' + detail : '')); }
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'demoiq-aop-surfaces-'));
function tmpPath(name) { return path.join(tmp, name); }

function runGenerate(args) {
  return spawnSync(process.execPath, [generateJs, '--html', engineHtml].concat(args), {
    encoding: 'utf8', env: process.env, timeout: 180000
  });
}

const CLASSIC = ['outlook', 'teams', 'excel', 'ppt', 'word', 'agents', 'copilotapp'];
const EXPECTED = [
  { id: 'code', heading: 'Copilot Code', implies: ['excel'] },
  { id: 'autopilot', heading: 'Copilot Autopilot', implies: ['emails', 'meetings'] }
];

// ------------------------------------------------------------------ 1. --list
console.log('1. generate.js --list carries the two surfaces after the classic seven');
let listed = [];
{
  const r = runGenerate(['--list']);
  check('--list exits 0', r.status === 0, 'exit=' + r.status + ' stderr=' + String(r.stderr).slice(0, 300));
  let j = null;
  try { j = JSON.parse(r.stdout); } catch (e) {}
  listed = (j && Array.isArray(j.aopSurfaces)) ? j.aopSurfaces : [];
  check('aopSurfaces present', listed.length > 0);
  const ids = listed.map(s => s.id);
  check('order = classic seven, then code, autopilot', JSON.stringify(ids) === JSON.stringify(CLASSIC.concat(['code', 'autopilot'])), JSON.stringify(ids));
  EXPECTED.forEach(e => {
    const s = listed.find(x => x.id === e.id);
    check(e.id + ': heading + implies as agreed', !!s && s.heading === e.heading && JSON.stringify(s.implies) === JSON.stringify(e.implies), JSON.stringify(s));
  });
}

// ------------------------------------------------- 2. Config Builder sync
console.log('2. Config Builder DATA.aopSurfaces matches the engine');
{
  const src = fs.readFileSync(builderHtml, 'utf8');
  const line = src.split(/\r?\n/).find(l => l.startsWith('const DATA = '));
  let data = null;
  try { data = JSON.parse(line.slice('const DATA = '.length).replace(/;\s*$/, '')); } catch (e) {}
  check('builder DATA parses', !!data && Array.isArray(data.aopSurfaces), line ? line.slice(0, 80) : '(no DATA line)');
  if (data) {
    const norm = a => JSON.stringify(a.map(s => ({ id: s.id, heading: s.heading, implies: s.implies })));
    check('builder surfaces == engine surfaces (ids, headings, implies)', norm(data.aopSurfaces) === norm(listed), norm(data.aopSurfaces));
  }
  check('builder shows the preview-surface hint', /preview surfaces/i.test(src));
}

// --------------------------------------------------- 3. engine picker
console.log('3. engine picker has the two checkboxes');
{
  const eng = fs.readFileSync(engineHtml, 'utf8');
  check('aop_code checkbox', /id="aop_code"/.test(eng));
  check('aop_autopilot checkbox', /id="aop_autopilot"/.test(eng));
}

// Builder-shaped prefs; obviously fake tenant + creds (never a real value).
const base = {
  adminUpn: 'admin@fakedemo.onmicrosoft.com',
  adminPwd: 'x',
  existingAppId: '00000000-0000-0000-0000-000000000001',
  existingCertThumbprint: '0'.repeat(40),
  demoLanguage: 'English',
  industry: 'Technology & Software',
  companyName: 'Northwind Traders',
  customScenarioText: 'Title: The Launch Readiness Standoff\nDescription: The leadership team argues over whether the platform is ready to launch while a competitor circles.\nExcel: Launch_Readiness_Tracker.xlsx\nWord: Launch_Readiness_Brief.docx\nPowerPoint: Launch_Readiness_Review.pptx',
  contentTypes: ['emails', 'meetings', 'excel', 'aop'],
  users: [1, 2, 3, 4, 5].map(i => ({ upn: 'user' + i + '@fakedemo.onmicrosoft.com', pwd: 'x' }))
};
function prefsFile(name, extra) {
  const f = tmpPath('prefs-' + name + '.json');
  fs.writeFileSync(f, JSON.stringify(Object.assign({}, base, extra || {})), 'utf8');
  return f;
}
function ps1For(name, extra) {
  const out = tmpPath('out-' + name);
  const r = runGenerate(['--prefs', prefsFile(name, extra), '--emit', 'ps1', '--out-dir', out, '--script-name', 'seed.ps1']);
  check(name + ': --emit ps1 exits 0', r.status === 0, 'exit=' + r.status + ' stderr=' + String(r.stderr).slice(0, 300));
  const f = path.join(out, 'seed.ps1');
  return fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '';
}
function promptsFor(name, extra) {
  const r = runGenerate(['--prefs', prefsFile(name, extra), '--emit', 'prompts']);
  check(name + ': --emit prompts exits 0', r.status === 0, 'exit=' + r.status + ' stderr=' + String(r.stderr).slice(0, 300));
  let d = null;
  try { d = JSON.parse(r.stdout); } catch (e) {}
  return (d && d.prompts) ? d.prompts : {};
}
const BATCH4 = /\(Code \+ Autopilot\)'/;   // the batch NAME, not the catalog comment

// ---------------------------------------------- 4. seeder: fourth batch
console.log('4. seeder: the Code + Autopilot batch exists only when selected');
{
  const on = ps1For('on', { aopSurfaces: ['outlook', 'code', 'autopilot'] });
  check('batch list carries (Code + Autopilot)', BATCH4.test(on));
  check('scope names both headings with 3-5 demos', on.indexOf('## Copilot Code (3-5 demos)') >= 0 && on.indexOf('## Copilot Autopilot (3-5 demos)') >= 0);
  check('$_cat4 catalog present with its Learn citations', on.indexOf('$_cat4 = @"') >= 0 && on.indexOf('learn.microsoft.com/azure/foundry/agents/concepts/autopilot-overview') >= 0 && on.indexOf('learn.microsoft.com/microsoft-365/managed-apps/developer/quickstart-github-copilot') >= 0);
  check('both surfaces are always-include (never skipped for lack of a seeded file)', on.indexOf('For Copilot Code and Copilot Autopilot, you should ALWAYS include demos') >= 0);
  check('"Before you demo" availability notes for both sections', on.indexOf('Copilot Code is a preview surface') >= 0 && on.indexOf('Copilot Autopilot is in preview') >= 0);
  check('gold-standard examples 9 and 10 present', on.indexOf('Gold-standard example 9: Copilot Code') >= 0 && on.indexOf('Gold-standard example 10: Copilot Autopilot') >= 0);
  check('runbook icons for the new headings', on.indexOf("return '[CODE]'") >= 0 && on.indexOf("return '[AUTO]'") >= 0);

  const classic = ps1For('classic', { aopSurfaces: ['outlook', 'teams'] });
  check('classic selection has no Code + Autopilot batch', !BATCH4.test(classic));

  const legacy = ps1For('legacy', {});   // no aopSurfaces key at all
  check('legacy config (no aopSurfaces) keeps the classic default: Agents + Copilot App batch, no preview batch', /\(Agents \+ Copilot App\)'/.test(legacy) && !BATCH4.test(legacy));
}

// ------------------------------------------ 5. content prompts adapt
console.log('5. content prompts adapt to the selected surfaces');
{
  const on = promptsFor('p-on', { aopSurfaces: ['code', 'autopilot'] });
  check('email prompt: open loops for Autopilot', /OPEN LOOPS FOR AUTOPILOT/.test(on.comm_email || ''));
  check('excel prompt: app-ready data for Code', /APP-READY DATA/.test(on.excel || ''));
  check('meetings prompt: open action items for Autopilot', /ACTION ITEMS FOR AUTOPILOT/.test(on.meetings || ''));

  const off = promptsFor('p-off', { aopSurfaces: ['outlook'] });
  check('hints absent when the surfaces are not selected',
    !/OPEN LOOPS FOR AUTOPILOT/.test(off.comm_email || '') && !/APP-READY DATA/.test(off.excel || '') && !/ACTION ITEMS FOR AUTOPILOT/.test(off.meetings || ''));
}

console.log(failures ? ('FAILED: ' + failures + ' check(s)') : 'ALL OK');
try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
process.exit(failures ? 1 : 0);
