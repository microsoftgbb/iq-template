#!/usr/bin/env node
/*
 * test_validator.js - tests for scripts/validate_pasted.js.
 * Dependency-free (Node built-ins only). Run via tests/run_tests.ps1 or directly:
 *   node tests/test_validator.js
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repo = path.resolve(__dirname, '..');
const validator = path.join(repo, 'scripts', 'validate_pasted.js');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'demoiq-validator-test-'));

let pass = 0, fail = 0;
function check(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

function writeJson(name, obj) {
  const p = path.join(work, name);
  fs.writeFileSync(p, JSON.stringify(obj), 'utf8');
  return p;
}
function runValidator(args) {
  const r = spawnSync(process.execPath, [validator, ...args], { encoding: 'utf8' });
  let out = null;
  try { out = JSON.parse(r.stdout); } catch (e) { /* leave null */ }
  return { code: r.status, out };
}

const CRLF = '\r\n', LF = '\n';
function doc(nl, names, body) {
  return names.map(n => `===== FILE: ${n} =====${nl}${body || `Body of ${n} with plenty of realistic content.${nl}Second line.`}`).join(nl) + nl;
}
const TABLE = 'Metric\tQ1\tQ2' + LF + 'Adoption\t10\t20' + LF + 'Licenses\t5\t9';
const meta = (w, p, x, tables) => writeJson('meta-' + w + p + x + '.json', {
  ok: true,
  officeFiles: {
    word: { selected: true, expected: w, primaryName: 'Primary_Report.docx' },
    ppt: { selected: true, expected: p, primaryName: 'Primary_Deck.pptx' },
    excel: { selected: true, expected: x, primaryName: 'Primary_Workbook.xlsx' },
    excelTables: tables == null ? null : tables,
  },
});

try {
  // 1. expected 2 / authored 2 succeeds - CRLF (the defect's line endings)
  let p = writeJson('ok-crlf.json', {
    word: doc(CRLF, ['Primary_Report.docx', 'Companion_Brief.docx']),
    ppt: doc(CRLF, ['Primary_Deck.pptx', 'Second_Deck.pptx']),
    excel: doc(CRLF, ['Primary_Workbook.xlsx', 'Second_Workbook.xlsx'], TABLE.replace(/\n/g, CRLF)),
  });
  let r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word,ppt,excel']);
  check(r.code === 0 && r.out && r.out.ok === true, 'expected 2 / authored 2 (CRLF) passes with exit 0');
  check(r.out && r.out.summary.word.files === 2 && r.out.summary.word.expected === 2, 'summary reports word files 2/2 with names');

  // 2. same content with LF endings also succeeds
  p = writeJson('ok-lf.json', {
    word: doc(LF, ['Primary_Report.docx', 'Companion_Brief.docx']),
    ppt: doc(LF, ['Primary_Deck.pptx', 'Second_Deck.pptx']),
    excel: doc(LF, ['Primary_Workbook.xlsx', 'Second_Workbook.xlsx'], TABLE),
  });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word,ppt,excel']);
  check(r.code === 0 && r.out && r.out.ok === true, 'expected 2 / authored 2 (LF) passes');

  // 3. expected 2, delimiters missing -> component-specific failure, exit 7
  p = writeJson('no-delims.json', {
    word: 'One long merged document body with no delimiter lines at all.' + CRLF + 'Second line.',
    ppt: doc(CRLF, ['Primary_Deck.pptx', 'Second_Deck.pptx']),
    excel: doc(CRLF, ['Primary_Workbook.xlsx', 'Second_Workbook.xlsx'], TABLE.replace(/\n/g, CRLF)),
  });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word,ppt,excel']);
  check(r.code === 7 && r.out && r.out.ok === false, 'expected 2 / missing delimiters fails with exit 7');
  check(r.out && r.out.errors.some(e => e.startsWith('[WORD] Expected 2 documents but parsed 1')), 'error is component-specific with expected/actual counts');

  // 4. duplicate file names fail
  p = writeJson('dupes.json', { word: doc(CRLF, ['Primary_Report.docx', 'Primary_Report.docx']) });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word']);
  check(r.code === 7 && r.out.errors.some(e => /duplicate file name/i.test(e)), 'duplicate names fail');

  // 5. wrong extension for the content type fails
  p = writeJson('wrong-ext.json', { word: doc(CRLF, ['Primary_Report.docx', 'Companion.xlsx']) });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word']);
  check(r.code === 7 && r.out.errors.some(e => /does not end in \.docx/.test(e)), 'wrong extension fails');

  // 6. empty secondary body fails (the seeder would silently drop the segment)
  p = writeJson('empty-body.json', {
    word: `===== FILE: Primary_Report.docx =====${CRLF}Real body content.${CRLF}===== FILE: Companion_Brief.docx =====${CRLF}${CRLF}`,
  });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word']);
  check(r.code === 7 && r.out.errors.some(e => /EMPTY body/.test(e)), 'empty secondary body fails');

  // 7. wrong primary name (order scrambled) fails
  p = writeJson('wrong-primary.json', { word: doc(CRLF, ['Companion_Brief.docx', 'Primary_Report.docx']) });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word']);
  check(r.code === 7 && r.out.errors.some(e => /not the scenario primary file/.test(e)), 'first delimiter must carry the primary name');

  // 8. Excel workbooks are validated SEPARATELY: workbook 2 with no parseable
  //    table must fail even though workbook 1 has plenty (the old whole-response
  //    check let exactly this through).
  p = writeJson('excel-wb2-empty.json', {
    excel: `===== FILE: Primary_Workbook.xlsx =====${CRLF}${TABLE.replace(/\n/g, CRLF)}${CRLF}${CRLF}${TABLE.replace(/\n/g, CRLF)}${CRLF}===== FILE: Second_Workbook.xlsx =====${CRLF}Just a prose paragraph, no table rows here.${CRLF}`,
  });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'excel']);
  check(r.code === 7 && r.out.errors.some(e => /workbook "Second_Workbook\.xlsx" has no parseable data rows/.test(e)), 'each Excel workbook is validated separately');

  // 9. per-workbook table-count check against excelTables metadata
  p = writeJson('excel-few-tables.json', {
    excel: `===== FILE: Primary_Workbook.xlsx =====${CRLF}${TABLE.replace(/\n/g, CRLF)}${CRLF}===== FILE: Second_Workbook.xlsx =====${CRLF}${TABLE.replace(/\n/g, CRLF)}${CRLF}`,
  });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2, 2), '--keys', 'excel']);
  check(r.code === 7 && r.out.errors.some(e => /has 1 parseable table\(s\) but the config expects 2/.test(e)), 'workbook with fewer tables than excelTables fails');

  // 10. backward compatible: no --prompts, single-file prose content still validates
  p = writeJson('legacy.json', { word: 'Plain single document body.' + LF + 'Second line.', excel: TABLE });
  r = runValidator(['--pasted', p, '--keys', 'word,excel']);
  check(r.code === 0 && r.out.ok === true, 'legacy invocation (no --prompts, single-file) still passes');

  // 11. single-file response with expected=1 needs no delimiter (metadata selects
  //     ONLY word here: with officeFiles present, every selected Office key must
  //     exist in the map - see test 15)
  const wordOnlyMeta = writeJson('meta-word-only.json', {
    ok: true,
    officeFiles: {
      word: { selected: true, expected: 1, primaryName: 'Primary_Report.docx' },
      ppt: { selected: false, expected: 0, primaryName: null },
      excel: { selected: false, expected: 0, primaryName: null },
      excelTables: null,
    },
  });
  p = writeJson('one-expected.json', { word: 'A single document, classic shape, no delimiters.' + CRLF + 'More content.' });
  r = runValidator(['--pasted', p, '--prompts', wordOnlyMeta, '--keys', 'word']);
  check(r.code === 0 && r.out.ok === true, 'expected 1 / no delimiter passes (classic single-file)');

  // 12. expected 1 but multiple delimited files -> config/content disagreement fails
  p = writeJson('one-expected-two-files.json', { word: doc(CRLF, ['Primary_Report.docx', 'Companion_Brief.docx']) });
  r = runValidator(['--pasted', p, '--prompts', meta(1, 1, 1), '--keys', 'word']);
  check(r.code === 7 && r.out.errors.some(e => e.startsWith('[WORD] Expected 1 documents but parsed 2')), 'expected 1 / authored 2 fails');

  // 13. CR-only line endings: the .NET regex parses 1 segment, so the mirror must
  //     TOO (JS multiline $ would wrongly see 2) - validator fails the count, exactly
  //     like the seeder's preflight would.
  p = writeJson('cr-only.json', { word: doc(CRLF, ['Primary_Report.docx', 'Companion_Brief.docx']).replace(/\r\n/g, '\r') });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'word']);
  check(r.code === 7 && r.out.errors.some(e => e.startsWith('[WORD] Expected 2 documents but parsed 1')), 'CR-only endings parse like the seeder (1 segment) and fail the count');

  // 14. uppercase extension: PS -replace is case-insensitive, so '.DOCX' normalizes
  //     to '.docx' - the mirror must agree (and not reject the extension).
  //     (word-only metadata: the map deliberately holds just the word key)
  const wordOnly2Meta = writeJson('meta-word-only-2.json', {
    ok: true,
    officeFiles: {
      word: { selected: true, expected: 2, primaryName: 'Primary_Report.docx' },
      ppt: { selected: false, expected: 0, primaryName: null },
      excel: { selected: false, expected: 0, primaryName: null },
      excelTables: null,
    },
  });
  p = writeJson('upper-ext.json', { word: doc(CRLF, ['Primary_Report.docx', 'Companion_Brief.DOCX']) });
  r = runValidator(['--pasted', p, '--prompts', wordOnly2Meta, '--keys', 'word']);
  check(r.code === 0 && r.out.summary.word.names[1] === 'Companion_Brief.docx', 'uppercase .DOCX normalizes to .docx like the seeder');

  // 15. metadata says word selected/expected 2 but the key is ABSENT from the map
  //     (and --keys, derived from the map, does not name it - the reseed-flow hole)
  p = writeJson('word-absent.json', { excel: doc(CRLF, ['Primary_Workbook.xlsx', 'Second_Workbook.xlsx'], TABLE.replace(/\n/g, CRLF)) });
  r = runValidator(['--pasted', p, '--prompts', meta(2, 2, 2), '--keys', 'excel']);
  check(r.code === 7 && r.out.errors.some(e => /\[WORD\] Expected 2 documents but the 'word' key is missing/.test(e)), 'selected Office key absent from the map fails');

  // 16. engine header-split parity: two tables back-to-back with NO blank line but a
  //     repeated alphabetic header count as TWO tables (the engine's mid-stream
  //     header safety net) - excelTables=2 must PASS, not false-reject.
  //     (excel-only metadata: the map deliberately holds just the excel key)
  const excelOnlyMeta = writeJson('meta-excel-only.json', {
    ok: true,
    officeFiles: {
      word: { selected: false, expected: 0, primaryName: null },
      ppt: { selected: false, expected: 0, primaryName: null },
      excel: { selected: true, expected: 2, primaryName: 'Primary_Workbook.xlsx' },
      excelTables: 2,
    },
  });
  const T2 = 'Region\tOwner\tStatus' + CRLF + 'North\tAlice\t12' + CRLF + 'South\tBob\t9' + CRLF +
             'Region\tOwner\tStatus' + CRLF + 'East\tCara\t7' + CRLF + 'West\tDan\t5';
  p = writeJson('excel-header-split.json', {
    excel: `===== FILE: Primary_Workbook.xlsx =====${CRLF}${T2}${CRLF}===== FILE: Second_Workbook.xlsx =====${CRLF}${T2}${CRLF}`,
  });
  r = runValidator(['--pasted', p, '--prompts', excelOnlyMeta, '--keys', 'excel']);
  check(r.code === 0 && r.out.summary.excel.workbooks.every(w => w.tables === 2), 'back-to-back tables with repeated header split like the engine (2 per workbook)');
} finally {
  fs.rmSync(work, { recursive: true, force: true });
}

console.log('');
console.log(`validator: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
