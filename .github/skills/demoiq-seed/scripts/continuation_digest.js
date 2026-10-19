#!/usr/bin/env node
/*
 * DemoIQ continuation digest - extract a clean "prior run" digest from a seeded
 * run's cleanup.config so the agent can author a NATURAL CONTINUATION (Mode 2
 * under Option B): reuse the exact email subjects / chat topics / channel names
 * (so the seeding layer threads new content into the existing conversations),
 * invent NEW creative file names that continue the narrative, and reference past
 * events with CORRECT relative dates (only say "last Tuesday" if that date was a
 * Tuesday).
 *
 * Usage:
 *   node continuation_digest.js --config <path to cleanup.config> [--scope last|all]
 *   node continuation_digest.js --dir <DemoIQ-Output dir> [--scope last|all]
 *   (optional) --today YYYY-MM-DD   override "today" (defaults to the system date)
 *
 * Prints a JSON digest:
 *   { ok, configPath, runsTotal, scope, today:{date,dayOfWeek},
 *     reuse:{ emailSubjects[], chatTopics[], channelNames[] },   // reuse VERBATIM to thread
 *     priorFiles:[{type,name}],                                  // invent NEW names that continue these
 *     meetings:[{subject,startDate,dayOfWeek,relative}],         // dated anchors for correct references
 *     runTimestamps:[...],
 *     notes:[...] }
 * On error: { ok:false, error }.
 */
'use strict';
const fs = require('fs');

// POSIX pipes are asynchronous in Node: output written right before process.exit()
// is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the pipe blocking.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}
const path = require('path');

function arg(name) { const i = process.argv.indexOf('--' + name); return i > -1 ? process.argv[i + 1] : null; }

let cfgPath = arg('config');
const dir = arg('dir');
if (!cfgPath && dir) cfgPath = path.join(dir, 'cleanup.config');
if (!cfgPath) { console.log(JSON.stringify({ ok: false, error: 'provide --config <cleanup.config> or --dir <DemoIQ-Output>' })); process.exit(2); }
if (!fs.existsSync(cfgPath)) { console.log(JSON.stringify({ ok: false, error: 'cleanup.config not found at ' + cfgPath })); process.exit(2); }

const scope = (arg('scope') || 'last').toLowerCase();
let cfg;
try { cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8')); }
catch (e) { console.log(JSON.stringify({ ok: false, error: 'cannot parse cleanup.config: ' + e.message })); process.exit(2); }

const runs = Array.isArray(cfg.runs) ? cfg.runs : [];
if (runs.length === 0) { console.log(JSON.stringify({ ok: false, error: 'no runs in cleanup.config' })); process.exit(0); }
const targetRuns = scope === 'all' ? runs : [runs[runs.length - 1]];

const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const todayArg = arg('today');
const today = todayArg ? new Date(todayArg + 'T00:00:00') : new Date();
today.setHours(0, 0, 0, 0);

// Parse cleanup.config date strings: "06/24/2026 10:00:00" (MM/DD/YYYY) or ISO.
function parseDate(s) {
  if (!s) return null;
  let m = String(s).match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m) return new Date(Number(m[3]), Number(m[1]) - 1, Number(m[2]));
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}
function ymd(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function relative(d) {
  const days = Math.round((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  const dow = DOW[d.getDay()];
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days === -1) return 'tomorrow';
  if (days > 1 && days <= 7) return 'last ' + dow + ' (' + days + ' days ago)';
  if (days > 7 && days <= 14) return 'two weeks ago, on ' + dow;
  if (days > 14) { const w = Math.round(days / 7); return w + (w === 1 ? ' week' : ' weeks') + ' ago, on ' + dow; }
  if (days < -1 && days >= -7) return 'this coming ' + dow + ' (in ' + (-days) + ' days)';
  if (days < -7) { const w = Math.round(-days / 7); return 'in ' + w + (w === 1 ? ' week' : ' weeks') + ', on ' + dow; }
  return dow;
}

const emailSubjects = new Set();
const chatTopics = new Set();
const channelNames = new Set();
const priorFiles = [];
const meetings = [];
const runTimestamps = [];

const normSubj = s => String(s || '').replace(/^(RE|FW|FWD|AW|WG):\s*/i, '').trim();

for (const r of targetRuns) {
  if (r.timestamp) runTimestamps.push(r.timestamp);
  (r.emails || []).forEach(e => { if (e.subject) emailSubjects.add(normSubj(e.subject)); });
  (r.chats || []).forEach(c => { if (c.topic) chatTopics.add(c.topic); });
  if (r.team && Array.isArray(r.team.channels)) r.team.channels.forEach(ch => { if (ch.name) channelNames.add(ch.name); });
  (r.channels || []).forEach(ch => { const n = ch.name || ch.channelName; if (n) channelNames.add(n); });
  (r.files || []).forEach(f => { if (f.name) priorFiles.push({ type: f.type || '', name: f.name }); });
  (r.meetings || []).forEach(m => {
    const d = parseDate(m.startDateTime);
    meetings.push({
      subject: m.subject || '',
      startDate: d ? ymd(d) : (m.startDateTime || ''),
      dayOfWeek: d ? DOW[d.getDay()] : '',
      relative: d ? relative(d) : ''
    });
  });
}

// de-dup files by name (keep first)
const seenF = new Set();
const filesOut = priorFiles.filter(f => (seenF.has(f.name) ? false : (seenF.add(f.name), true)));

const digest = {
  ok: true,
  configPath: path.resolve(cfgPath),
  runsTotal: runs.length,
  scope,
  today: { date: ymd(today), dayOfWeek: DOW[today.getDay()] },
  reuse: {
    emailSubjects: Array.from(emailSubjects),
    chatTopics: Array.from(chatTopics),
    // Drop any channel name that is a strict prefix of another (cleanup.config can
    // store a truncated team.channels[].name alongside the full channels[].name).
    channelNames: Array.from(channelNames).filter(n =>
      !Array.from(channelNames).some(o => o !== n && o.startsWith(n)))
  },
  priorFiles: filesOut,
  meetings,
  runTimestamps,
  notes: [
    'REUSE the email subjects / chat topics / channel names VERBATIM in the new content so the seeding layer threads it into the existing conversations (email subject -> conversationId, chat topic match, team/channel by name).',
    'INVENT NEW creative file names that continue the narrative from priorFiles (do NOT reuse the same names) and reference them naturally across the new emails/chats/channels/meetings.',
    'When referencing past events, use the meetings[].relative / dayOfWeek values (computed against today=' + ymd(today) + ') so relative phrases like "last Tuesday" are factually correct.',
    'cleanup.config does not store per-message email/chat send dates; meeting dates and the run timestamp are the reliable temporal anchors.'
  ]
};
console.log(JSON.stringify(digest, null, 2));
