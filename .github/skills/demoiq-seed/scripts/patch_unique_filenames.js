#!/usr/bin/env node
/*
 * One-time transform: make DemoIQ's embedded PowerShell file-upload logic
 * collision-resilient. Adds a Resolve-UniqueFileName helper (into the SF_WORD
 * JS-string constant) and calls it before each Word/Excel/PPT upload so that if
 * a file with the target name already exists in the owner's OneDrive, a NEW
 * timestamped file is created (continuing the story) instead of overwriting or
 * failing with HTTP 423 "resourceLocked" on the existing/locked file.
 *
 * Edits the giant single-line JS-string constants SF_WORD / SF_PPT in
 * DemoIQ.html. Asserts each anchor matches EXACTLY once; aborts (writes nothing)
 * otherwise. Pass --apply to write; default is dry-run (reports match counts).
 *
 * Usage: node patch_unique_filenames.js <DemoIQ.html> [--apply]
 */
'use strict';
const fs = require('fs');

// POSIX pipes are asynchronous in Node: output written right before process.exit()
// is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the pipe blocking.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}
const file = process.argv[2];
const apply = process.argv.includes('--apply');
if (!file) { console.error('usage: node patch_unique_filenames.js <DemoIQ.html> [--apply]'); process.exit(2); }
let html = fs.readFileSync(file, 'utf8');

// The helper, as it must appear inside the SF_WORD JS double-quoted string:
// newlines escaped as \n, double-quotes as \". Single quotes are literal.
const helper =
  '\\nfunction Resolve-UniqueFileName {' +
  '\\n  param([string]$Token,[string]$OwnerId,[string]$FileName)' +
  '\\n  try {' +
  '\\n    $null = Invoke-RestMethod -Method GET -Uri \\"https://graph.microsoft.com/v1.0/users/$OwnerId/drive/root:/$FileName\\" -Headers @{Authorization=\\"Bearer $Token\\"} -ErrorAction Stop' +
  '\\n    $dot = $FileName.LastIndexOf(\'.\')' +
  '\\n    $suffix = \'_\' + (Get-Date -Format \'yyyyMMdd_HHmm\')' +
  '\\n    if ($dot -gt 0) { $new = $FileName.Substring(0,$dot) + $suffix + $FileName.Substring($dot) } else { $new = $FileName + $suffix }' +
  '\\n    Write-Host \\" [FILES] Existing \'$FileName\' found - creating new file \'$new\' (continuing the story).\\" -ForegroundColor DarkCyan' +
  '\\n    return $new' +
  '\\n  } catch { return $FileName }' +
  '\\n}';

const edits = [
  {
    name: 'helper into SF_WORD',
    find: 'const SF_WORD="\\nfunction Acquire-FileTokens {',
    repl: 'const SF_WORD="' + helper + '\\nfunction Acquire-FileTokens {',
  },
  {
    name: 'Word upload rename',
    find: 'wordprocessingml.document\\"\\n  try{\\n    $up=Invoke-RestMethod -Method PUT',
    repl: 'wordprocessingml.document\\"\\n  try{\\n    $FileName = Resolve-UniqueFileName -Token $tok -OwnerId $me.id -FileName $FileName\\n    $up=Invoke-RestMethod -Method PUT',
  },
  {
    name: 'Excel upload rename',
    find: '$ownerId = $owner.id\\n    $uri = \\"https://graph.microsoft.com/v1.0/users/$ownerId/drive/root:/${FileName}:/content\\"',
    repl: '$ownerId = $owner.id\\n    $FileName = Resolve-UniqueFileName -Token $tok -OwnerId $ownerId -FileName $FileName\\n    $uri = \\"https://graph.microsoft.com/v1.0/users/$ownerId/drive/root:/${FileName}:/content\\"',
  },
  {
    name: 'PPT upload rename',
    find: 'presentationml.presentation\\"\\n  try{\\n    $up=Invoke-RestMethod -Method PUT',
    repl: 'presentationml.presentation\\"\\n  try{\\n    $FileName = Resolve-UniqueFileName -Token $tok -OwnerId $me.id -FileName $FileName\\n    $up=Invoke-RestMethod -Method PUT',
  },
];

let ok = true;
for (const e of edits) {
  const count = html.split(e.find).length - 1;
  console.log(`${count === 1 ? 'OK ' : 'XX '} [${e.name}] matches=${count}`);
  if (count !== 1) ok = false;
}
if (!ok) { console.error('ABORT: one or more anchors did not match exactly once. No changes written.'); process.exit(3); }

if (!apply) { console.log('DRY-RUN ok. Re-run with --apply to write.'); process.exit(0); }

for (const e of edits) html = html.replace(e.find, e.repl);
fs.writeFileSync(file, html, 'utf8');
console.log('APPLIED. ' + edits.length + ' edits written to ' + file);
