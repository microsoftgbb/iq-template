#!/usr/bin/env node
/*
 * detach.js - start a process fully detached from the caller (macOS/Linux).
 *
 * Usage:
 *   node detach.js [--log <file>] [--cwd <dir>] -- <command> [args...]
 *
 * The child runs in its own session and process group (Node calls setsid() for
 * `detached: true`), with stdin closed and stdout/stderr appended to --log (or
 * discarded). This process exits immediately, so the child is re-parented away
 * from the calling shell and survives the end of a Copilot tool call, a closed
 * terminal (SIGHUP) or a process-group kill.
 *
 * Prints ONE JSON line: { ok, pid } or { ok:false, error }.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

function fail(message) {
  process.stdout.write(JSON.stringify({ ok: false, error: message }) + '\n');
  process.exitCode = 2;
}

const argv = process.argv.slice(2);
const sep = argv.indexOf('--');
if (sep < 0 || sep === argv.length - 1) {
  fail('usage: node detach.js [--log <file>] [--cwd <dir>] -- <command> [args...]');
} else {
  const opts = {};
  for (let i = 0; i < sep; i++) {
    if (argv[i] === '--log') opts.log = argv[++i];
    else if (argv[i] === '--cwd') opts.cwd = argv[++i];
  }
  const [command, ...args] = argv.slice(sep + 1);
  let out = 'ignore';
  try {
    if (opts.log) {
      fs.mkdirSync(path.dirname(path.resolve(opts.log)), { recursive: true });
      out = fs.openSync(opts.log, 'a', 0o600);
    }
    const child = spawn(command, args, {
      cwd: opts.cwd || process.cwd(),
      detached: true,
      stdio: ['ignore', out, out],
      env: process.env,
    });
    child.once('error', (e) => fail('spawn failed: ' + e.message));
    child.once('spawn', () => {
      child.unref();
      process.stdout.write(JSON.stringify({ ok: true, pid: child.pid }) + '\n');
    });
  } catch (e) {
    fail(e.message);
  }
}
