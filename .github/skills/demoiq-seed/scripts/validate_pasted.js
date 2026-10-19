#!/usr/bin/env node
/*
 * DemoIQ pasted-content validator (Option B safety net).
 *
 * Validates a --pasted map { promptKey: responseText } BEFORE it is fed to
 * generate.js --pasted, so malformed agent-authored content is caught up front
 * instead of failing mid-seed. It mirrors the engine's runtime parser
 * (engine\DemoIQ.html ~line 3554-3561): find the `cdx_seed_json` marker, then
 * extract the first balanced { ... } object and JSON.parse it.
 *
 * Usage:
 *   node validate_pasted.js --pasted <map.json> [--keys comm_email,comm_chat,...]
 *                           [--prompts <demoiq-prompts.json>]
 *
 * --keys (optional): the content keys the run actually selected. Only these are
 * REQUIRED to be present; extras are still validated if present. If omitted, every
 * key present in the map is validated and nothing is treated as missing.
 *
 * --prompts (optional): the demoiq-prompts.json produced by generate.js --emit
 * prompts. When it carries `officeFiles` metadata (per-type expected file count +
 * primary file name), the Word/PPT/Excel responses are validated against the
 * config's EXACT expectations: delimiter/segment counts, the primary file name,
 * per-segment names/extensions/bodies. Without it (or with an older dump), the
 * validator still runs every structural check it can - existing invocations keep
 * working unchanged.
 *
 * Office multi-file contract (mirrors the seeder's Split-DemoDocSegments):
 * responses may hold several documents separated by '===== FILE: <name.ext> ====='
 * delimiter lines (CRLF and LF both valid). No delimiter = one document. A config
 * that expects N>1 files REQUIRES exactly N delimited segments - this validator
 * fails the exact defect where CRLF endings made the seeder parse 0 delimiters and
 * silently merge every document into one file.
 *
 * Prints one JSON line: { ok, checked[], missing[], errors[], warnings[], summary{...} }.
 * Exit code 0 = all good, 7 = validation failed (missing/invalid), 2 = bad input.
 */
'use strict';
const fs = require('fs');

// POSIX pipes are asynchronous in Node: output written right before process.exit()
// is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the pipe blocking.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}
const path = require('path');

function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : null;
}

const pastedPath = arg('pasted');
if (!pastedPath) { console.log(JSON.stringify({ ok: false, errors: ['missing --pasted'] })); process.exit(2); }

let map;
try { map = JSON.parse(fs.readFileSync(path.resolve(pastedPath), 'utf8')); }
catch (e) { console.log(JSON.stringify({ ok: false, errors: ['cannot read/parse --pasted file: ' + e.message] })); process.exit(2); }
if (!map || typeof map !== 'object') { console.log(JSON.stringify({ ok: false, errors: ['--pasted is not a JSON object'] })); process.exit(2); }

// Optional Office expectations from the prompts dump (generate.js --emit prompts).
let officeMeta = null;
const promptsPath = arg('prompts');
if (promptsPath) {
  let promptsDoc;
  try { promptsDoc = JSON.parse(fs.readFileSync(path.resolve(promptsPath), 'utf8')); }
  catch (e) { console.log(JSON.stringify({ ok: false, errors: ['cannot read/parse --prompts file: ' + e.message] })); process.exit(2); }
  officeMeta = (promptsDoc && typeof promptsDoc === 'object' && promptsDoc.officeFiles && typeof promptsDoc.officeFiles === 'object')
    ? promptsDoc.officeFiles : null;
}

// Content keys that MUST carry a parseable cdx_seed_json block, and the
// top-level array each must contain (matches the engine prompt schemas).
const JSON_KEYS = {
  comm_email:   'emails',
  comm_chat:    'chats',
  comm_channel: 'channels',
  meetings:     'meetings',
};
// Prose keys: the engine consumes these as readable text (Word/PPT/Excel doc
// bodies, the English audio transcript, the AoP runbook). Only require non-empty.
const PROSE_KEYS = ['word', 'ppt', 'excel', 'audiotranscript', 'aop'];
// Office prose keys get the deeper multi-file/segment validation below.
const OFFICE_TYPES = {
  word:  { ext: '.docx', label: 'WORD',  noun: 'documents' },
  ppt:   { ext: '.pptx', label: 'PPT',   noun: 'decks' },
  excel: { ext: '.xlsx', label: 'EXCEL', noun: 'workbooks' },
};

const selected = (arg('keys') || '').split(',').map(s => s.trim()).filter(Boolean);

// Extract the first balanced {...} after a cdx_seed_json marker (engine parser parity).
// Without a marker the engine's fallback (DemoIQ.html pasted-response scanner) only accepts
// the WHOLE trimmed text as JSON - it never hunts for a '{' buried after prose. Mirror that
// exactly, or content that validates here can silently seed nothing at runtime.
function extractSeedJson(text) {
  if (typeof text !== 'string') return null;
  const m = /cdx[-_ ]?seed[-_ ]?json/i.exec(text);
  let start;
  if (m) {
    start = text.indexOf('{', m.index);
  } else {
    start = text.trim().startsWith('{') ? text.indexOf('{') : -1;
  }
  if (start < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (esc) { esc = false; continue; }
    if (c === '\\') { esc = true; continue; }
    if (c === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { return text.slice(start, i + 1); } }
  }
  return null;
}

// JS mirror of the seeder's Split-DemoDocSegments (engine\DemoIQ.html): the SAME
// delimiter regex (incl. the \r? that makes CRLF content parse - a real past
// defect), the same substantial-preamble rule, the same name fallbacks. Keeping
// this in lockstep with the engine is what makes "validator says N segments" mean
// "the seeder will upload N files".
// NOT /gm with ^...$: JS multiline anchors match around a LONE \r (and U+2028/U+2029)
// while .NET (?m) anchors only match around \n - a CR-only-ended delimiter would
// validate here yet parse ZERO segments in the seeder. The lookbehind/lookahead pair
// reproduces the .NET semantics exactly: line start = input start or after \n;
// line end = optional \r, then \n or input end.
const FILE_DELIM_RE = /(?<=^|\n)[ \t]*={3,}[ \t]*FILE:[ \t]*([^=\r\n]*?)[ \t]*={3,}[ \t]*\r?(?=\n|$)/g;
function splitDocSegments(content, defaultName, ext) {
  const text = String(content == null ? '' : content);
  const ms = [...text.matchAll(FILE_DELIM_RE)];
  if (ms.length === 0) return { delimiters: 0, segments: [{ name: defaultName, rawName: null, body: text.trim() }] };
  // /i on the extension strips: PowerShell -replace is case-insensitive, so the
  // seeder turns 'Brief.DOCX' into 'Brief.docx' - the mirror must agree.
  const baseNoExt = defaultName.replace(/\.(docx|pptx|xlsx)$/i, '');
  const segs = [];
  if (ms[0].index > 0) {
    const pre = text.slice(0, ms[0].index).trim();
    // Engine rule: preamble becomes a segment only when substantial (multi-line AND
    // >= 400 chars); a chatty one-liner is dropped.
    if (pre && pre.length >= 400 && pre.includes('\n')) segs.push({ name: defaultName, rawName: null, body: pre });
  }
  for (let i = 0; i < ms.length; i++) {
    const start = ms[i].index + ms[i][0].length;
    const end = i + 1 < ms.length ? ms[i + 1].index : text.length;
    const body = text.slice(start, end).trim();
    const rawName = String(ms[i][1] || '').trim();
    let name = rawName.replace(/[\\/:*?"<>|]/g, '');
    if (name.length > 100) name = '';
    if (name) name = name.replace(/\.(docx|pptx|xlsx)$/i, '') + ext;
    if (!name) name = baseNoExt + '_' + (segs.length + 1) + ext;
    if (segs.length === 0) name = defaultName;
    // Engine parity note: the seeder DROPS empty-bodied segments silently; the
    // validator keeps them so it can report them as authoring errors instead.
    segs.push({ name, rawName, body });
  }
  return { delimiters: ms.length, segments: segs };
}

const errors = [];
const warnings = [];
const checked = [];
const summary = {};

// Determine which keys to require present.
const requireKeys = selected.length ? selected : Object.keys(map);
const missing = requireKeys.filter(k => !(k in map) || !String(map[k] || '').trim());

// Validate JSON-bearing keys.
for (const [key, arrName] of Object.entries(JSON_KEYS)) {
  if (!(key in map)) continue;
  const text = map[key];
  if (!String(text || '').trim()) { errors.push(`${key}: empty response`); continue; }
  const jsonStr = extractSeedJson(text);
  if (!jsonStr) { errors.push(`${key}: no cdx_seed_json block found (or unbalanced braces)`); continue; }
  let obj;
  try { obj = JSON.parse(jsonStr); }
  catch (e) { errors.push(`${key}: cdx_seed_json does not parse - ${e.message}`); continue; }
  const arr = obj && obj[arrName];
  if (!Array.isArray(arr)) { errors.push(`${key}: JSON missing "${arrName}" array`); continue; }
  if (arr.length === 0) { errors.push(`${key}: "${arrName}" array is empty`); continue; }
  summary[key] = { [arrName]: arr.length };
  checked.push(key);
}

// Validate prose keys (presence/non-empty only).
for (const key of PROSE_KEYS) {
  if (!(key in map)) continue;
  const text = map[key];
  if (!String(text || '').trim()) { errors.push(`${key}: empty response`); continue; }
  summary[key] = { chars: String(text).length };
  checked.push(key);
}

// ---- Office multi-file validation (word / ppt / excel) ----
// Splits each response exactly like the seeder will, then checks the result
// against the config's expectations (when --prompts metadata is available) and
// against the structural rules that hold regardless of metadata.
function stripOfficeExt(n) { return String(n || '').replace(/\.(docx|pptx|xlsx)$/i, ''); }

// A selected Office type with expected>0 whose key is ABSENT from the map must fail
// HERE: the per-key loop below can only validate keys that exist, and --keys (per
// the reseed flow) is often derived from the map's own keys - so without this check
// a truncated stored bundle would sail through the gate and only fail at seed time.
if (officeMeta) {
  for (const [key, t] of Object.entries(OFFICE_TYPES)) {
    const meta = officeMeta[key] && typeof officeMeta[key] === 'object' ? officeMeta[key] : null;
    if (!meta || !meta.selected || !(Number(meta.expected) > 0)) continue;
    if (!(key in map) || !String(map[key] || '').trim()) {
      errors.push(`[${t.label}] Expected ${Number(meta.expected)} ${t.noun} but the '${key}' key is missing or empty in the pasted map.`);
    }
  }
}

const officeSegs = {};
for (const [key, t] of Object.entries(OFFICE_TYPES)) {
  if (!(key in map) || !String(map[key] || '').trim()) continue;
  const meta = officeMeta && officeMeta[key] && typeof officeMeta[key] === 'object' ? officeMeta[key] : null;
  const expected = meta && meta.selected ? (Number(meta.expected) || 1) : null;
  const primaryName = meta && meta.primaryName ? String(meta.primaryName) : null;
  const defaultName = primaryName || ('Document' + t.ext);
  const parsed = splitDocSegments(map[key], defaultName, t.ext);
  officeSegs[key] = parsed;
  const segNames = parsed.segments.map(s => s.name);
  const nonEmpty = parsed.segments.filter(s => s.body);

  // Empty bodies: the seeder would silently drop these segments - always an error.
  parsed.segments.forEach((s, i) => {
    if (!s.body) errors.push(`[${t.label}] segment ${i + 1} ("${s.rawName || s.name}") has an EMPTY body - the seeder would silently drop it.`);
  });

  if (parsed.delimiters > 0) {
    // Per-delimiter name checks (only meaningful when the author used delimiters).
    const seen = new Map();
    parsed.segments.forEach((s, i) => {
      if (s.rawName === null) return; // preamble segment has no authored name
      if (!s.rawName) {
        errors.push(`[${t.label}] delimiter ${i + 1} has an EMPTY file name - give every '===== FILE: ... =====' line a real name.`);
        return;
      }
      if (!s.rawName.replace(/[\\/:*?"<>|]/g, '') || s.rawName.replace(/[\\/:*?"<>|]/g, '').length > 100) {
        errors.push(`[${t.label}] delimiter ${i + 1} name "${s.rawName}" is unsafe (empty after sanitization or over 100 chars).`);
      }
      if (!new RegExp('\\' + t.ext + '$', 'i').test(s.rawName)) {
        errors.push(`[${t.label}] delimiter ${i + 1} name "${s.rawName}" does not end in ${t.ext} - wrong extension for this content type.`);
      }
      const lower = stripOfficeExt(s.rawName).toLowerCase();
      if (seen.has(lower)) errors.push(`[${t.label}] duplicate file name "${s.rawName}" (also delimiter ${seen.get(lower)}) - every file needs a unique name.`);
      else seen.set(lower, i + 1);
    });
    // Primary-name contract: the FIRST delimiter must carry the exact scenario file
    // name (the seeder renames segment 0 to it regardless, so a mismatch means the
    // author scrambled the order or invented a different primary).
    const firstDelim = parsed.segments.find(s => s.rawName !== null);
    if (primaryName && firstDelim && firstDelim.rawName &&
        stripOfficeExt(firstDelim.rawName).toLowerCase() !== stripOfficeExt(primaryName).toLowerCase()) {
      errors.push(`[${t.label}] first delimiter name "${firstDelim.rawName}" is not the scenario primary file "${primaryName}" - the first segment must be the primary file.`);
    }
    // A substantial preamble becomes an EXTRA uploaded file - never intended when
    // the author is using explicit delimiters.
    if (parsed.segments.length > 0 && parsed.segments[0].rawName === null) {
      errors.push(`[${t.label}] substantial un-delimited content before the first '===== FILE:' line would upload as an EXTRA file - move it under a delimiter or delete it.`);
    }
  }

  if (expected != null) {
    if (nonEmpty.length !== expected) {
      errors.push(`[${t.label}] Expected ${expected} ${t.noun} but parsed ${nonEmpty.length}. Detected: ${segNames.join(', ') || '(none)'}${parsed.delimiters === 0 && expected > 1 ? " - no '===== FILE: <name> =====' delimiter lines found (required when the config asks for more than one file)." : ''}`);
    } else {
      summary[key] = Object.assign(summary[key] || {}, { files: nonEmpty.length, expected, names: nonEmpty.map(s => s.name) });
    }
  } else {
    summary[key] = Object.assign(summary[key] || {}, { files: nonEmpty.length, names: nonEmpty.map(s => s.name) });
  }
}

// Excel gets a deeper check: mirror the ENGINE's table parser (New-ExcelFile) so a
// workbook that would seed EMPTY is caught HERE, before the run - "no extractable data rows"
// once surfaced only after minutes of seeding. Parity rules: strip ```
// fences / --- rules / #headings / **bold** wrappers; blank line = table break;
// markdown pipe rows normalize to tabs; a line is DATA only with a tab or 2+ spaces.
function excelTableStats(text) {
  const tables = []; let current = [];
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
  for (let L of lines) {
    let t = L.trim();
    if (/^```/.test(t)) continue;
    if (/^---+$/.test(t)) continue;
    if (/^#{1,6}\s/.test(t)) continue;
    if (t.startsWith('**') && t.endsWith('**')) t = t.slice(2, -2).trim();
    if (/^\|.*\|$/.test(t)) {
      if (/^[|\s:.-]+$/.test(t)) continue; // |---|---| separator row
      t = t.replace(/^\||\|$/g, '').split('|').map(c => c.trim()).join('\t');
    } else if (/\s\|\s/.test(t)) {
      t = t.split('|').map(c => c.trim()).join('\t');
    }
    if (t === '') { if (current.length) { tables.push(current); current = []; } continue; }
    if (!/\t/.test(t) && !/\s{2,}/.test(t)) continue;
    // Engine parity (New-ExcelFile's header-repetition safety net): a repeated
    // header mid-stream - same cell count as the table's first row, every cell
    // <= 40 chars, digit-free, none of [.,$%/] - finalizes the current table and
    // starts a new one. Without this, two tables authored back-to-back with no
    // blank line count as ONE here but TWO in the seeder, and the per-workbook
    // excelTables check would false-reject content that seeds exactly as configured.
    if (current.length >= 2) {
      const testCells = t.split(/\t|  +/);
      const headerCells = current[0].split(/\t|  +/);
      let looksLikeHeader = testCells.length >= 2 && testCells.length === headerCells.length;
      if (looksLikeHeader) {
        for (const c of testCells) {
          const cc = c.trim();
          if (cc.length > 40 || /\d/.test(cc) || /[.,$%\/]/.test(cc)) { looksLikeHeader = false; break; }
        }
      }
      if (looksLikeHeader) { tables.push(current); current = []; }
    }
    current.push(t);
  }
  if (current.length) tables.push(current);
  const withData = tables.filter(tb => tb.length >= 2); // header + >=1 data row
  return { tables: tables.length, tablesWithData: withData.length };
}
// Per-WORKBOOK Excel validation: split into workbooks FIRST (exactly like the
// seeder), then require parseable data in EVERY workbook. Validating the whole
// response as one blob used to let "workbook 2 has no tables" hide behind
// "workbook 1 has plenty".
if ('excel' in map && String(map.excel || '').trim()) {
  const parsed = officeSegs.excel || splitDocSegments(map.excel, 'Workbook.xlsx', '.xlsx');
  const expectedTables = officeMeta && officeMeta.excelTables != null ? Number(officeMeta.excelTables) : null;
  const perWorkbook = [];
  parsed.segments.forEach((s, i) => {
    if (!s.body) return; // already reported as an empty-body error above
    const st = excelTableStats(s.body);
    perWorkbook.push({ name: s.name, tables: st.tablesWithData });
    if (st.tablesWithData === 0) {
      errors.push(`[EXCEL] workbook "${s.name}" has no parseable data rows - the engine would seed it EMPTY. ` +
        'Author each worksheet as ONE plain table: a header line then 5-10 data rows, columns ' +
        'separated by TABs, 2+ spaces, or a markdown pipe table (| col | col |). Single-space ' +
        'or comma-separated lines are dropped by the parser.');
    } else if (expectedTables != null && st.tablesWithData < expectedTables) {
      errors.push(`[EXCEL] workbook "${s.name}" has ${st.tablesWithData} parseable table(s) but the config expects ${expectedTables} per workbook.`);
    } else if (expectedTables != null && st.tablesWithData > expectedTables) {
      warnings.push(`[EXCEL] workbook "${s.name}" has ${st.tablesWithData} parseable tables (config expects ${expectedTables}) - extra sheets will seed; harmless but unplanned.`);
    }
  });
  if (summary.excel) summary.excel.workbooks = perWorkbook;
}

// Unknown keys are surfaced but not fatal.
const known = new Set([...Object.keys(JSON_KEYS), ...PROSE_KEYS]);
const unknown = Object.keys(map).filter(k => !known.has(k));
if (unknown.length) summary._unknownKeys = unknown;
if (promptsPath && !officeMeta) warnings.push('--prompts file has no officeFiles metadata (older generate.js dump?) - Office count/primary-name checks were skipped.');

const ok = errors.length === 0 && missing.length === 0;
console.log(JSON.stringify({ ok, checked, missing, errors, warnings, summary }, null, 2));
process.exit(ok ? 0 : 7);
