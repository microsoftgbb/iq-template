#!/usr/bin/env node
/*
 * test_verifier.js - tests for scripts/verify_seeded_files.js.
 * Dependency-free (Node built-ins only). Run via tests/run_tests.ps1 or directly:
 *   node tests/test_verifier.js
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repo = path.resolve(__dirname, '..');
const verifier = path.join(repo, 'scripts', 'verify_seeded_files.js');

let pass = 0, fail = 0;
function check(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

function aopHtml(entries) {
  // Minimal AoP shape the verifier parses: <li class="sm-item">Label <a href=...><code>name</code></a></li>
  const items = entries.map(e =>
    `<li class="sm-item">${e.label} <a href="${e.url}"><code>${e.name}</code></a></li>`).join('\n');
  return `<html><body><ul>\n${items}\n</ul></body></html>`;
}
const GOOD_URL = 'https://tenant.sharepoint.com/:w:/r/_layouts/15/Doc.aspx?sourcedoc=%7Bx%7D&amp;web=1';
const BAD_URL = 'https://tenant.sharepoint.com/personal/user/Documents/File.docx?download=1';

const SIX = [
  { label: 'Word', name: 'Primary_Report.docx' },
  { label: 'Word', name: 'Companion_Brief.docx' },
  { label: 'PowerPoint', name: 'Primary_Deck.pptx' },
  { label: 'PowerPoint', name: 'Second_Deck.pptx' },
  { label: 'Excel', name: 'Primary_Workbook.xlsx' },
  { label: 'Excel', name: 'Second_Workbook.xlsx' },
];
const THREE = [SIX[0], SIX[2], SIX[4]];

const IDENTITY = {
  appId: '00000000-0000-0000-0000-000000000001',
  tenant: 'fixturetest.onmicrosoft.com',
  expectedOfficeFiles: {
    word: 2, ppt: 2, excel: 2, total: 6,
    primaryNames: { word: 'Primary_Report.docx', ppt: 'Primary_Deck.pptx', excel: 'Primary_Workbook.xlsx' },
  },
};

// Build a fixture "run folder": <run>/run_identity.json + <run>/DemoIQ-Output/{aop,cleanup.config}
function makeRun(name, { seeded, aopEntries, identity }) {
  const run = fs.mkdtempSync(path.join(os.tmpdir(), 'demoiq-verify-' + name + '-'));
  const outDir = path.join(run, 'DemoIQ-Output');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'cleanup.config'),
    JSON.stringify({ files: seeded.map(e => ({ name: e.name, owner: 'user@fixturetest.onmicrosoft.com' })) }), 'utf8');
  fs.writeFileSync(path.join(outDir, 'aop_demo_runbook_test.html'), aopHtml(aopEntries), 'utf8');
  if (identity) fs.writeFileSync(path.join(run, 'run_identity.json'), JSON.stringify(identity, null, 2), 'utf8');
  return { run, outDir };
}
function runVerifier(outDir, extra) {
  const r = spawnSync(process.execPath, [verifier, '--dir', outDir, ...(extra || [])], { encoding: 'utf8' });
  const lines = r.stdout.trim().split('\n');
  let out = null;
  try { out = JSON.parse(lines[lines.length - 1]); } catch (e) { /* leave null */ }
  return { code: r.status, out };
}

const dirs = [];
try {
  // 1. expected 6 / actual 6, all linked -> allGood true
  let fx = makeRun('all6', { seeded: SIX, aopEntries: SIX.map(e => ({ ...e, url: GOOD_URL })), identity: IDENTITY });
  dirs.push(fx.run);
  let r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === true && r.out.expected === 6 && r.out.actual === 6, 'expected 6 / actual 6 -> allGood true');
  check(r.out && r.out.missingByType.word === 0 && r.out.missingByType.ppt === 0 && r.out.missingByType.excel === 0, 'no missing files reported when complete');

  // 2. expected 6 / actual 3 (the affected-run shape) -> allGood false, 1 missing per type
  fx = makeRun('only3', { seeded: THREE, aopEntries: THREE.map(e => ({ ...e, url: GOOD_URL })), identity: IDENTITY });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === false && r.out.expected === 6 && r.out.actual === 3, 'expected 6 / actual 3 -> allGood false');
  check(r.out && r.out.missingByType.word === 1 && r.out.missingByType.ppt === 1 && r.out.missingByType.excel === 1, 'missingByType reports 1/1/1');
  check(r.out && r.out.files.every(f => f.status === 'ok'), 'the 3 present files are individually fine (counts alone fail the run)');

  // 3. a seeded file missing its AoP link -> allGood false even at full count
  const aop5 = SIX.slice(0, 5).map(e => ({ ...e, url: GOOD_URL }));
  fx = makeRun('nolink', { seeded: SIX, aopEntries: aop5, identity: IDENTITY });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === false && r.out.files.some(f => f.status === 'missing-link'), 'manifest file absent from AoP -> allGood false');

  // 4. full count but one link is not browser-open -> allGood false
  const aopBad = SIX.map((e, i) => ({ ...e, url: i === 3 ? BAD_URL : GOOD_URL }));
  fx = makeRun('badlink', { seeded: SIX, aopEntries: aopBad, identity: IDENTITY });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === false && r.out.files.some(f => f.status === 'no-web-link'), 'non-browser-open link -> allGood false');

  // 5. legacy run (no run_identity.json / no expectedOfficeFiles): old behavior preserved
  fx = makeRun('legacy', { seeded: THREE, aopEntries: THREE.map(e => ({ ...e, url: GOOD_URL })), identity: null });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === true && r.out.expected === null, 'legacy run without expectations still verifies as before');

  // 6. --identity override pointing at an explicit metadata file
  fx = makeRun('explicit', { seeded: THREE, aopEntries: THREE.map(e => ({ ...e, url: GOOD_URL })), identity: null });
  dirs.push(fx.run);
  const idPath = path.join(fx.run, 'custom_identity.json');
  fs.writeFileSync(idPath, JSON.stringify(IDENTITY), 'utf8');
  r = runVerifier(fx.outDir, ['--identity', idPath]);
  check(r.out && r.out.allGood === false && r.out.expected === 6 && r.out.actual === 3, '--identity override applies expected counts');

  // 7. collision-renamed primary (Name_20260611_0909.docx) still satisfies the expectation
  const renamed = [
    { label: 'Word', name: 'Primary_Report_20260720_1201.docx' }, SIX[1], SIX[2], SIX[3], SIX[4], SIX[5],
  ];
  fx = makeRun('renamed', { seeded: renamed, aopEntries: renamed.map(e => ({ ...e, url: GOOD_URL })), identity: IDENTITY });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === true && r.out.actual === 6, 'collision-renamed upload still counts toward the expected set');

  // 8. renamed primary present + companion missing: missingExpected must name the
  //    COMPANION, not the primary (drives the collision-rename primary match)
  const shortRenamed = [
    { label: 'Word', name: 'Primary_Report_20260720_1201.docx' }, SIX[2], SIX[3], SIX[4], SIX[5],
  ];
  fx = makeRun('renamed-short', { seeded: shortRenamed, aopEntries: shortRenamed.map(e => ({ ...e, url: GOOD_URL })), identity: IDENTITY });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === false && r.out.missingByType.word === 1 &&
    r.out.missingExpected.length === 1 && /companion/.test(r.out.missingExpected[0]) && !/primary/.test(r.out.missingExpected[0]),
    'renamed primary + missing companion reports the COMPANION missing, not the primary');

  // 9. primary genuinely missing while a fallback-named companion shares its prefix:
  //    missingExpected must name the PRIMARY (a loose startsWith would hide it)
  const primaryGone = [
    { label: 'Word', name: 'Primary_Report_2.docx' }, SIX[2], SIX[3], SIX[4], SIX[5],
  ];
  fx = makeRun('primary-gone', { seeded: primaryGone, aopEntries: primaryGone.map(e => ({ ...e, url: GOOD_URL })), identity: IDENTITY });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir);
  check(r.out && r.out.allGood === false &&
    r.out.missingExpected.some(m => /Word primary "Primary_Report\.docx"/.test(m)),
    'missing primary is named even when a prefix-sharing companion seeded');

  // 10. an explicit --identity path that does not exist must fail loudly (exit 2),
  //     never silently verify without expectations
  fx = makeRun('bad-identity', { seeded: THREE, aopEntries: THREE.map(e => ({ ...e, url: GOOD_URL })), identity: null });
  dirs.push(fx.run);
  r = runVerifier(fx.outDir, ['--identity', path.join(fx.run, 'no_such_identity.json')]);
  check(r.code === 2 && r.out && r.out.ok === false, 'nonexistent --identity fails with exit 2 instead of silently passing');
} finally {
  for (const d of dirs) fs.rmSync(d, { recursive: true, force: true });
}

console.log('');
console.log(`verifier: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
