#!/usr/bin/env node
/*
 * DemoIQ content preview (Option B "see it before you seed it").
 *
 * Renders an agent-authored --pasted map { promptKey: responseText } as a
 * human-readable preview BEFORE the full seed, so the user can sanity-check
 * tone / industry fit / language quality without any tenant writes - especially
 * valuable for non-English runs. It makes NO tenant calls, generates NO seeder,
 * and changes nothing: it only reads and renders the SAME content that would be
 * seeded, so what you preview is exactly what gets seeded (no drift).
 *
 * It parses each content key the SAME way the engine/validator does (find the
 * cdx_seed_json marker, then the first balanced { ... } and JSON.parse it), so
 * the preview reflects the real parse - a key that previews wrong would seed wrong.
 *
 * Two usage shapes:
 *   1. Quick pre-author taste test - author a SMALL sample map (e.g. 1 email
 *      thread + 1 chat + the AoP outline), preview it, get a thumbs-up on the
 *      direction, THEN author the full set. Saves ~10-15 min if direction is off.
 *   2. Pre-seed review - render the COMPLETE authored map right before
 *      generating/seeding, as a final visual confirmation. (--max may trim the
 *      markdown skim, but the HTML preview is always complete.)
 *
 * Usage:
 *   node preview_content.js --pasted <map.json> [--keys comm_email,comm_chat,...]
 *        [--max <n>] [--html-out <path>]
 *
 *   --keys     (optional) only render these keys (csv). Default: every key present.
 *   --max      (optional) caps the MARKDOWN (stdout) skim ONLY: fewer items per
 *              key and truncated text, so the agent's chat rendering stays
 *              bounded on big runs. The HTML preview written by --html-out is
 *              ALWAYS complete regardless of --max - every item, full text,
 *              never truncated - because the HTML is the artifact the user
 *              reviews and approves at the pre-seed gate. The full render is
 *              what makes a re-authored preview visibly different.
 *   --html-out (optional) also write a self-contained HTML preview to this path
 *              (open via file:// to view formatted email/chat/channel cards).
 * Prints markdown to stdout, then a final JSON line:
 *   { ok, rendered[], counts{ key: n }, htmlOut|null, warnings[] }
 * Exit 0 on a completed render (even with per-key warnings), 2 on bad input.
 */
'use strict';
const fs = require('fs');
const path = require('path');

// POSIX pipes are asynchronous in Node: output written right before process.exit()
// is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the pipe blocking.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}

function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : null;
}


const pastedPath = arg('pasted');
if (!pastedPath) { console.log(JSON.stringify({ ok: false, error: 'missing --pasted' })); process.exit(2); }

let map;
try { map = JSON.parse(fs.readFileSync(path.resolve(pastedPath), 'utf8')); }
catch (e) { console.log(JSON.stringify({ ok: false, error: 'cannot read/parse --pasted file: ' + e.message })); process.exit(2); }
if (!map || typeof map !== 'object') { console.log(JSON.stringify({ ok: false, error: '--pasted is not a JSON object' })); process.exit(2); }

const onlyKeys = (arg('keys') || '').split(',').map(s => s.trim()).filter(Boolean);
const maxRaw = arg('max');
const MAX = maxRaw != null && /^\d+$/.test(maxRaw) ? parseInt(maxRaw, 10) : Infinity;
const htmlOut = arg('html-out');

// JSON-bearing keys and their top-level array (matches the engine prompt schemas).
const JSON_KEYS = { comm_email: 'emails', comm_chat: 'chats', comm_channel: 'channels', meetings: 'meetings' };
const PROSE_KEYS = ['word', 'ppt', 'excel', 'audiotranscript', 'aop'];
const KEY_LABEL = {
  comm_email: 'Emails', comm_chat: 'Teams group chat', comm_channel: 'Teams channel posts',
  meetings: 'Meetings', word: 'Word document', ppt: 'PowerPoint deck', excel: 'Excel workbook',
  audiotranscript: 'Meeting audio transcript', aop: 'Art-of-the-Possible runbook',
};

// Extract the first balanced {...} after a cdx_seed_json marker (engine parser parity).
// Without a marker the engine's fallback only accepts the WHOLE trimmed text as JSON -
// mirror that exactly so the preview matches what the seeder will actually parse.
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
    else if (c === '}') { depth--; if (depth === 0) return text.slice(start, i + 1); }
  }
  return null;
}

function stripHtml(s) {
  return String(s == null ? '' : s)
    .replace(/<\/(h[1-6]|p|li|ol|ul|div)>/gi, ' ')
    .replace(/<li[^>]*>/gi, ' - ')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ').trim();
}
function snippet(s, n) {
  const t = stripHtml(s).replace(/\\n/g, ' ');
  // snippet() feeds ONLY the markdown/stdout skim, and truncates only in --max
  // mode. The HTML preview never goes through here - it always renders fullText
  // (a cut-off preview once hid content edits from the reviewer).
  return (MAX !== Infinity && t.length > n) ? t.slice(0, n).trimEnd() + '...' : t;
}
// Full-fidelity text for the HTML preview: strip markup but KEEP line breaks
// (block-level tags and escaped \n become real newlines - the cards render
// with white-space:pre-wrap, so bodies read like the real email/chat).
function fullText(s) {
  return String(s == null ? '' : s)
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(h[1-6]|p|li|ol|ul|div|tr)>/gi, '\n')
    .replace(/<li[^>]*>/gi, ' - ')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ')
    .replace(/\\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/ ?\n ?/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
// HTML card body: ALWAYS the whole text - --max never touches the HTML preview.
function htmlBody(s) { return fullText(s); }
// Total characters of a set of bodies - surfaced per section so a lengthened
// re-author is visible at a glance even before reading.
function charsOf(items, pick) { return items.reduce((n, it) => n + stripHtml(pick(it)).length, 0); }
function fmtChars(n) { return n.toLocaleString('en-US') + ' chars'; }
function shortDate(s) {
  if (!s) return '';
  const d = new Date(s);
  if (isNaN(d.getTime())) return String(s);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}
function localPart(upn) { return String(upn || '').replace(/[<>]/g, '').trim(); }

const md = [];
const counts = {};
const rendered = [];
const warnings = [];
const htmlSections = [];

md.push('# DemoIQ content preview');
md.push('');
md.push('_Read-only preview of the authored content - nothing is seeded. ' +
  'What you see here is exactly what would be written to the tenant._');
if (MAX !== Infinity) md.push('', `_This markdown skim shows up to ${MAX} item(s) per content type; the HTML preview always shows everything in full._`);
md.push('');

function parseArr(key) {
  const text = map[key];
  if (!String(text || '').trim()) { warnings.push(`${key}: empty - nothing to preview`); return null; }
  const jsonStr = extractSeedJson(text);
  if (!jsonStr) { warnings.push(`${key}: no cdx_seed_json block found`); return null; }
  let obj; try { obj = JSON.parse(jsonStr); }
  catch (e) { warnings.push(`${key}: cdx_seed_json does not parse - ${e.message}`); return null; }
  const arr = obj[JSON_KEYS[key]];
  if (!Array.isArray(arr)) { warnings.push(`${key}: JSON missing "${JSON_KEYS[key]}" array`); return null; }
  return arr;
}

function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

// ---- Emails: group by normalized subject so a thread reads as a thread ------
function renderEmails() {
  const arr = parseArr('comm_email'); if (!arr) return;
  const threads = new Map();
  for (const e of arr) {
    const subjKey = String(e.subject || '').replace(/^\s*(re|fw|fwd)\s*:\s*/i, '').trim().toLowerCase();
    if (!threads.has(subjKey)) threads.set(subjKey, []);
    threads.get(subjKey).push(e);
  }
  counts.comm_email = arr.length; rendered.push('comm_email');
  const emailChars = charsOf(arr, e => e.body);
  md.push(`## ${KEY_LABEL.comm_email} (${arr.length} message(s), ${threads.size} thread(s), ${fmtChars(emailChars)})`, '');
  const hChunks = [];
  let shown = 0;
  for (const [, msgs] of threads) {
    // --max gates only the markdown skim; the HTML always carries every thread.
    const inMd = shown < MAX;
    msgs.sort((a, b) => new Date(a.sentDateTime) - new Date(b.sentDateTime));
    const subj = msgs[0].subject || '(no subject)';
    if (inMd) md.push(`### ${subj}`);
    const rows = [];
    for (const e of msgs) {
      if (inMd) {
        md.push(`- **${localPart(e.from)} -> ${localPart(e.to)}**${e.cc ? ' (cc ' + localPart(e.cc) + ')' : ''} | ${shortDate(e.sentDateTime)}`);
        md.push(`  > ${snippet(e.body, 200)}`);
      }
      rows.push(`<div class="msg"><div class="meta"><b>${esc(localPart(e.from))}</b> &rarr; ${esc(localPart(e.to))}` +
        `${e.cc ? ' (cc ' + esc(localPart(e.cc)) + ')' : ''} &middot; ${esc(shortDate(e.sentDateTime))}</div>` +
        `<div class="body">${esc(htmlBody(e.body))}</div></div>`);
    }
    if (inMd) md.push('');
    hChunks.push(`<div class="card"><div class="subj">${esc(subj)}</div>${rows.join('')}</div>`);
    shown++;
  }
  if (threads.size > MAX) md.push(`_...and ${threads.size - MAX} more thread(s) (all shown in the HTML preview)._`, '');
  htmlSections.push(`<section><h2>${esc(KEY_LABEL.comm_email)} <span class="cnt">${arr.length} message(s) &middot; ${esc(fmtChars(emailChars))}</span></h2>${hChunks.join('')}</section>`);
}

// ---- Chats ------------------------------------------------------------------
function renderChats() {
  const arr = parseArr('comm_chat'); if (!arr) return;
  counts.comm_chat = arr.length; rendered.push('comm_chat');
  const chatChars = charsOf(arr, ch => (ch.messages || []).map(m => String(m.text == null ? '' : m.text)).join(''));
  md.push(`## ${KEY_LABEL.comm_chat} (${arr.length} chat(s), ${fmtChars(chatChars)})`, '');
  const hChunks = [];
  arr.forEach((ch, idx) => {
    // --max gates only the markdown skim; the HTML always carries every chat + message.
    const inMd = idx < MAX;
    const parts = (ch.participants || []).map(localPart).join(', ');
    if (inMd) {
      md.push(`### ${ch.chatTitle || '(group chat)'}`);
      if (parts) md.push(`_Participants: ${parts}_`, '');
    }
    const msgs = ch.messages || [];
    const mdCap = MAX === Infinity ? msgs.length : Math.max(MAX, 6);
    const hMsgs = [];
    msgs.forEach((m, mi) => {
      if (inMd && mi < mdCap) md.push(`- **${localPart(m.sender)}** | ${shortDate(m.sentDateTime)}: ${snippet(m.text, 160)}`);
      hMsgs.push(`<div class="bubble"><b>${esc(localPart(m.sender))}</b> <span class="t">${esc(shortDate(m.sentDateTime))}</span><div>${esc(htmlBody(m.text))}</div></div>`);
    });
    if (inMd) {
      if (msgs.length > mdCap) md.push(`  _...and ${msgs.length - mdCap} more message(s) (all shown in the HTML preview)._`);
      md.push('');
    }
    hChunks.push(`<div class="card"><div class="subj">${esc(ch.chatTitle || 'group chat')}</div>` +
      `<div class="parts">${esc(parts)}</div>${hMsgs.join('')}</div>`);
  });
  if (arr.length > MAX) md.push(`_...and ${arr.length - MAX} more chat(s) (all shown in the HTML preview)._`, '');
  htmlSections.push(`<section><h2>${esc(KEY_LABEL.comm_chat)} <span class="cnt">${arr.length} chat(s) &middot; ${esc(fmtChars(chatChars))}</span></h2>${hChunks.join('')}</section>`);
}

// ---- Channel posts ----------------------------------------------------------
function renderChannels() {
  const arr = parseArr('comm_channel'); if (!arr) return;
  counts.comm_channel = arr.length; rendered.push('comm_channel');
  const chanChars = charsOf(arr, p => p.message);
  md.push(`## ${KEY_LABEL.comm_channel} (${arr.length} post(s), ${fmtChars(chanChars)})`, '');
  const byChan = new Map();
  for (const p of arr) { const n = p.channelName || '(channel)'; if (!byChan.has(n)) byChan.set(n, []); byChan.get(n).push(p); }
  const hChunks = [];
  for (const [name, posts] of byChan) {
    // --max gates only the markdown skim; the HTML always carries every post.
    const mdCap = MAX === Infinity ? posts.length : Math.max(MAX, 5);
    md.push(`### #${name}`);
    posts.slice(0, mdCap).forEach(p => {
      md.push(`- **${localPart(p.sender)}** | ${shortDate(p.datetime)}: ${snippet(p.message, 160)}`);
    });
    if (posts.length > mdCap) md.push(`  _...and ${posts.length - mdCap} more post(s) (all shown in the HTML preview)._`);
    md.push('');
    const hMsgs = posts
      .map(p => `<div class="bubble"><b>${esc(localPart(p.sender))}</b> <span class="t">${esc(shortDate(p.datetime))}</span><div>${esc(htmlBody(p.message))}</div></div>`).join('');
    hChunks.push(`<div class="card"><div class="subj">#${esc(name)}</div>${hMsgs}</div>`);
  }
  htmlSections.push(`<section><h2>${esc(KEY_LABEL.comm_channel)} <span class="cnt">${arr.length} post(s) &middot; ${esc(fmtChars(chanChars))}</span></h2>${hChunks.join('')}</section>`);
}

// ---- Meetings ---------------------------------------------------------------
function renderMeetings() {
  const arr = parseArr('meetings'); if (!arr) return;
  counts.meetings = arr.length; rendered.push('meetings');
  const mtgChars = charsOf(arr, mt => String(mt.body == null ? '' : mt.body) + (mt.chatMessages || []).map(c => String(c.text == null ? '' : c.text)).join(''));
  md.push(`## ${KEY_LABEL.meetings} (${arr.length} meeting(s), ${fmtChars(mtgChars)})`, '');
  const hChunks = [];
  arr.forEach((mt, idx) => {
    // --max gates only the markdown skim; the HTML always carries every meeting.
    const inMd = idx < MAX;
    const att = (mt.attendees || []).map(localPart).join(', ');
    const cms = mt.chatMessages || [];
    const mdCap = MAX === Infinity ? cms.length : Math.max(MAX, 3);
    if (inMd) {
      md.push(`### ${mt.subject || '(meeting)'}`);
      md.push(`- Organizer: **${localPart(mt.organizer)}** | ${shortDate(mt.startDateTime)} - ${shortDate(mt.endDateTime)}`);
      if (att) md.push(`- Attendees: ${att}`);
      md.push(`  > ${snippet(mt.body, 240)}`);
      if (cms.length) {
        md.push(`- Meeting chat (${cms.length} msg):`);
        cms.slice(0, mdCap).forEach(c => md.push(`  - **${localPart(c.sender)}** ${c.time || ''}: ${snippet(c.text, 120)}`));
      }
      md.push('');
    }
    const hChat = cms
      .map(c => `<div class="bubble"><b>${esc(localPart(c.sender))}</b> <span class="t">${esc(c.time || '')}</span><div>${esc(htmlBody(c.text))}</div></div>`).join('');
    hChunks.push(`<div class="card"><div class="subj">${esc(mt.subject || 'meeting')}</div>` +
      `<div class="meta">Organizer <b>${esc(localPart(mt.organizer))}</b> &middot; ${esc(shortDate(mt.startDateTime))} - ${esc(shortDate(mt.endDateTime))}</div>` +
      `<div class="parts">${esc(att)}</div><div class="body">${esc(htmlBody(mt.body))}</div>${hChat}</div>`);
  });
  if (arr.length > MAX) md.push(`_...and ${arr.length - MAX} more meeting(s) (all shown in the HTML preview)._`, '');
  htmlSections.push(`<section><h2>${esc(KEY_LABEL.meetings)} <span class="cnt">${arr.length} meeting(s) &middot; ${esc(fmtChars(mtgChars))}</span></h2>${hChunks.join('')}</section>`);
}

// ---- Prose (Word / PPT / Excel / transcript / AoP) -------------------------
function renderProse(key) {
  const text = map[key];
  if (!String(text || '').trim()) { warnings.push(`${key}: empty - nothing to preview`); return; }
  const clean = stripHtml(text);
  counts[key] = clean.length; rendered.push(key);
  // The markdown skim caps prose only in --max mode; the HTML is always the full document.
  const cap = MAX === Infinity ? Infinity : Math.max(MAX * 80, 400);
  md.push(`## ${KEY_LABEL[key] || key} (${fmtChars(clean.length)})`, '');
  md.push('> ' + (clean.length > cap ? clean.slice(0, cap).trimEnd() + '... _(full document in the HTML preview)_' : clean), '');
  htmlSections.push(`<section><h2>${esc(KEY_LABEL[key] || key)} <span class="cnt">${esc(fmtChars(clean.length))}</span></h2>` +
    `<div class="card"><div class="body">${esc(fullText(text))}</div></div></section>`);
}

// ---- Drive rendering in a sensible order -----------------------------------
const wanted = onlyKeys.length ? onlyKeys : [...Object.keys(JSON_KEYS), ...PROSE_KEYS];
for (const key of wanted) {
  if (!(key in map)) continue;
  switch (key) {
    case 'comm_email': renderEmails(); break;
    case 'comm_chat': renderChats(); break;
    case 'comm_channel': renderChannels(); break;
    case 'meetings': renderMeetings(); break;
    default: if (PROSE_KEYS.includes(key)) renderProse(key); break;
  }
}

if (rendered.length === 0) {
  md.push('_No previewable content found in the map._', '');
}
if (warnings.length) {
  md.push('---', '', '**Notes:**');
  warnings.forEach(w => md.push(`- ${w}`));
  md.push('');
}

process.stdout.write(md.join('\n') + '\n');

// ---- Optional self-contained HTML preview ----------------------------------
let htmlWritten = null;
if (htmlOut) {
  const style = [
    ':root{--ink:#1b1a22;--muted:#6b6975;--line:#e9e7f0;--bg:#f6f5fb;--card:#fff;',
    '--accent:#7719aa;--accent-soft:#f3ecfb;--blue:#2563eb;--blue-soft:#eef3ff;',
    '--green:#107c10;--amber:#b76e00;--shadow:0 1px 3px rgba(24,16,40,.06),0 6px 20px rgba(24,16,40,.05)}',
    '*{box-sizing:border-box}',
    'body{font-family:"Segoe UI",system-ui,-apple-system,Arial,sans-serif;margin:0;color:var(--ink);',
    'background:linear-gradient(180deg,#f8f6fd 0%,var(--bg) 240px) no-repeat,var(--bg);line-height:1.55;-webkit-font-smoothing:antialiased}',
    '.wrap{max-width:920px;margin:0 auto;padding:0 20px 64px}',
    /* ---- header ---- */
    '.hero{background:linear-gradient(120deg,#7719aa 0%,#5b14b8 55%,#3f0d8c 100%);color:#fff;',
    'border-radius:0 0 22px 22px;padding:30px 28px 26px;box-shadow:var(--shadow);position:relative;overflow:hidden}',
    '.hero::after{content:"";position:absolute;right:-60px;top:-60px;width:220px;height:220px;border-radius:50%;background:rgba(255,255,255,.08)}',
    '.hero h1{margin:0;font-size:1.5rem;font-weight:800;letter-spacing:-.01em;display:flex;align-items:center;gap:10px}',
    '.hero .spark{font-size:1.35rem}',
    '.hero p{margin:10px 0 0;font-size:.92rem;color:#efe6fb;max-width:640px}',
    '.pill{display:inline-flex;align-items:center;gap:6px;margin-top:16px;background:rgba(255,255,255,.16);',
    'border:1px solid rgba(255,255,255,.25);color:#fff;font-size:.74rem;font-weight:600;padding:5px 12px;border-radius:99px}',
    /* ---- toc ---- */
    '.toc{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0 4px}',
    '.toc a{font-size:.76rem;font-weight:600;color:var(--accent);background:var(--accent-soft);border:1px solid #e7d8f4;',
    'padding:6px 12px;border-radius:99px;text-decoration:none;transition:background .15s,color .15s}',
    '.toc a:hover{background:var(--accent);color:#fff}',
    /* ---- sections ---- */
    'section{margin-top:30px;scroll-margin-top:16px}',
    'h2{font-size:1.05rem;font-weight:750;margin:0 0 12px;padding-bottom:9px;border-bottom:2px solid var(--line);',
    'display:flex;align-items:center;gap:9px}',
    'h2 .ic{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:8px;',
    'background:var(--accent-soft);color:var(--accent);font-size:.95rem;flex-shrink:0}',
    '.cnt{margin-left:auto;font-size:.72rem;color:var(--muted);font-weight:600;background:#fff;',
    'border:1px solid var(--line);padding:3px 10px;border-radius:99px}',
    /* ---- cards ---- */
    '.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0;box-shadow:var(--shadow)}',
    '.subj{font-weight:700;font-size:.95rem;margin-bottom:10px;color:var(--ink);display:flex;align-items:center;gap:8px}',
    '.subj::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent);flex-shrink:0}',
    '.meta,.parts{font-size:.78rem;color:var(--muted);margin:2px 0}',
    '.parts{margin-bottom:8px;padding-bottom:8px;border-bottom:1px dashed var(--line)}',
    '.body{font-size:.875rem;margin-top:5px;white-space:pre-wrap;color:#2a2833}',
    /* email rows */
    '.msg{border-top:1px solid #f1eef8;padding:9px 0}.msg:first-of-type{border-top:none}',
    '.msg .meta b{color:var(--accent)}',
    /* chat / channel bubbles */
    '.bubble{background:var(--accent-soft);border:1px solid #efe4fa;border-radius:10px 10px 10px 3px;',
    'padding:8px 11px;margin:7px 0;font-size:.85rem;color:#2a2833}',
    '.bubble b{color:var(--accent)}.bubble .t{color:var(--muted);font-size:.72rem;margin-left:6px;font-weight:500}',
    '.bubble>div{margin-top:3px;white-space:pre-wrap}',
    /* prose */
    'section.prose .body{font-size:.86rem;color:#33313c}',
    '.footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);font-size:.72rem;color:var(--muted);text-align:center}',
    '@media(max-width:600px){.wrap{padding:0 14px 48px}.hero{padding:24px 18px}}',
  ].join('');
  // Give each section an icon based on its heading label.
  const SEC_ICON = {
    'Emails': '\u2709\uFE0F', 'Teams group chat': '\uD83D\uDCAC', 'Teams channel posts': '\uD83D\uDCE2',
    'Meetings': '\uD83D\uDCC5', 'Word document': '\uD83D\uDCDD', 'PowerPoint deck': '\uD83D\uDCCA',
    'Excel workbook': '\uD83D\uDCC8', 'Meeting audio transcript': '\uD83D\uDD0A', 'Art-of-the-Possible runbook': '\u2728',
  };
  // Post-process the assembled sections: inject an icon span into each <h2> and a
  // stable id (so the table-of-contents can jump to it). Also tag prose sections.
  const toc = [];
  const decorated = htmlSections.map((sec, i) => {
    const m = /<h2>([^<]*?)\s*<span class="cnt"/.exec(sec);
    const label = m ? m[1].trim() : ('Section ' + (i + 1));
    const id = 'sec-' + label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const icon = SEC_ICON[label] || '\uD83D\uDCC4';
    const isProse = /Word document|PowerPoint deck|Excel workbook|audio transcript|runbook/.test(label);
    toc.push(`<a href="#${id}">${esc(icon)} ${esc(label)}</a>`);
    return sec
      .replace('<section>', `<section id="${id}"${isProse ? ' class="prose"' : ''}>`)
      .replace('<h2>', `<h2><span class="ic">${esc(icon)}</span>`);
  });
  const generatedOn = new Date().toLocaleString();
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1.0">' +
    '<title>DemoIQ content preview</title><style>' + style + '</style></head><body>' +
    '<div class="hero"><h1><span class="spark">\u2728</span> DemoIQ content preview</h1>' +
    '<p>A read-only preview of the authored demo content. Nothing here is seeded yet - ' +
    'what you see is exactly what will be written to the tenant when you confirm the run. ' +
    'Every email, chat message, post, and document below is shown <b>in full</b> - nothing is truncated.' +
    '</p>' +
    '<span class="pill">\uD83D\uDD12 Read-only &middot; no tenant changes</span></div>' +
    '<div class="wrap">' +
    (toc.length > 1 ? '<nav class="toc">' + toc.join('') + '</nav>' : '') +
    decorated.join('') +
    '<div class="footer">Generated by DemoIQ on ' + esc(generatedOn) + ' &middot; preview only</div>' +
    '</div></body></html>';
  try {
    const outAbs = path.resolve(htmlOut);
    // Own the output location: --html-out usually targets <run folder>\DemoIQ-Output,
    // which the SEEDER creates at runtime - at preview time (pre-seed) it does not
    // exist yet, and a missing parent once caused a failed preview write.
    fs.mkdirSync(path.dirname(outAbs), { recursive: true });
    fs.writeFileSync(outAbs, html, 'utf8'); htmlWritten = outAbs;
  }
  catch (e) { warnings.push('html-out write failed: ' + e.message); }
}

console.log(JSON.stringify({ ok: true, rendered, counts, htmlOut: htmlWritten, warnings }));
process.exit(0);
