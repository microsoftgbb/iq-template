"""Seed and restore runs: one detached worker process per run, with files the CLI reads.

    runs/<run id>/
      run.json            what the run is for (demo, tenant, app, administrator, people); no secrets
      demo.json           the approved demo, copied when the run started (seed runs)
      status.json         the worker's live state, step and progress
      log.txt             what happened, line by line (no passwords, tokens or sign-in codes)
      manifest.json       every created item (resume ledger and cleanup source)
      tenant_state.json   the temporary tenant changes and their restore
      result.json         the outcome; guide.html, the presenter guide
      sign-in-code.txt    the administrator's one-time code, only while the sign-in waits
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time
import traceback

from .common import CLI, Blocked, Stop, Usage, Waiting, utc_now
from . import auth, cleanup, content, demo, pace, pages, proc, receipts, tenant, users, workspace
from .graph import Graph, odata_string, query

RUN_ID = re.compile(r"^\d{8}-\d{6}-(seed|restore|cleanup)-[a-z0-9][a-z0-9-]{0,44}$")
ACTIVE = ("starting", "signing-in-admin", "waiting-for-sign-in", "resolving-users", "preparing-tenant",
          "signing-in-users", "seeding", "cleaning", "restoring")
SIGN_IN_WAITS = [0, 15, 30, 45, 60, 90, 120]
DONE = frozenset({"seeded", "seeded-with-failures", "cleaned", "cleaned-with-failures", "restored",
                  "nothing-to-restore"})
SCRIPT = Path(__file__).resolve().parents[1] / "m365demo.py"


class Run:
    def __init__(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.match(run_id):
            raise Usage("bad-run", f"Not a run id: {run_id!r} (see `history`).")
        self.id = run_id
        self.dir = workspace.runs_dir() / run_id

    def path(self, name):
        return self.dir / name

    @property
    def code_file(self):
        return self.path("sign-in-code.txt")

    def exists(self):
        return self.path("run.json").is_file()

    def info(self):
        data = workspace.read_json(self.path("run.json"))
        if not data:
            raise Usage("no-run", f"There is no run {self.id}.")
        return data

    def demo(self):
        return workspace.read_json(self.path("demo.json"))

    def status_data(self):
        return workspace.read_json(self.path("status.json"), default={}) or {}

    def status(self, state, **fields):
        data = self.status_data()
        data.update(fields, state=state, runId=self.id, pid=os.getpid(), updatedAt=utc_now())
        if state != "waiting-for-sign-in":
            for key in ("codeFile", "verificationUri", "expiresAt"):
                data.pop(key, None)
        workspace.write_json(self.path("status.json"), data)

    def log(self, message):
        line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
        with open(self.path("log.txt"), "a", encoding="utf-8") as handle:
            handle.write(line)

    def warn(self, message):
        self.log(f"WARNING: {message}")
        data = self.status_data()
        data.setdefault("warnings", []).append(message)
        workspace.write_json(self.path("status.json"), data)

    def cancel_requested(self):
        return self.path("cancel").exists()

    def pause(self, seconds):
        pace.sleep(seconds)
        if self.cancel_requested():
            raise content.Cancelled("cancelled", "The run was cancelled.")

    def result(self):
        return workspace.read_json(self.path("result.json"))


def new_run(kind, name):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{kind}-{name}"[:60].rstrip("-")
    run_id, number = base, 2
    while (workspace.runs_dir() / run_id).exists():
        run_id, number = f"{base}-{number}", number + 1
    run = Run(run_id)
    workspace.private_dir(run.dir)
    workspace.write_private(run.path("log.txt"), "")
    return run


def all_runs():
    root = workspace.runs_dir()
    if not root.is_dir():
        return []
    return [Run(entry.name) for entry in sorted(root.iterdir(), reverse=True)
            if entry.is_dir() and RUN_ID.match(entry.name) and (entry / "run.json").is_file()]


def final_state(outcome):
    """A finished run's state from its outcome (when its tenant needs no restore)."""
    if outcome in DONE:
        return "finished"
    return "cancelled" if outcome == "cancelled" else "failed"


def created_items(run):
    """How many of this run's items exist in the tenant, at least partly."""
    return content.Manifest(run.path("manifest.json")).created()


def cleaned_up(run):
    """True once a cleanup has touched this seed run: resuming it would re-create what was deleted."""
    if run.info().get("cleanups"):
        return True
    items = (content.Manifest(run.path("manifest.json")).data.get("items") or {}).values()
    return any(entry.get("deleted") or entry.get("deleteError") for entry in items)


def record_cleanup(target, run_id, *, create=False, **fields):
    """Add or update a cleanup run's record on the seed run it cleans."""
    info = target.info()
    records = info.setdefault("cleanups", [])
    record = next((entry for entry in records if entry.get("runId") == run_id), None)
    if record is None:
        if not create:
            return
        record = {"runId": run_id}
        records.append(record)
    record.update(fields)
    workspace.write_json(target.path("run.json"), info)


def open_tenant_changes():
    """Runs whose tenant changes are not restored and whose worker is gone."""
    found = []
    for run in all_runs():
        state = tenant.State(run.path("tenant_state.json"))
        if state.needs_restore and not proc.alive(run.status_data().get("pid")):
            found.append(run)
    return found


# --- the foreground commands ------------------------------------------------------------------

def _spawn(run, kind):
    workspace.acquire_lock(run.id, kind)
    try:
        run.status("starting")
        if not run.path("worker.out").exists():
            workspace.write_private(run.path("worker.out"), b"")
        pid = proc.spawn_detached([SCRIPT, "_worker", run.id], run.path("worker.out"))
    except BaseException:
        workspace.release_lock(run.id)
        raise
    workspace.set_lock_pid(run.id, pid)
    data = run.status_data()
    data["pid"] = pid
    workspace.write_json(run.path("status.json"), data)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        state = run.status_data().get("state")
        if state not in ("starting", "signing-in-admin") or not proc.alive(pid):
            break
        time.sleep(0.2)
    return snapshot(run)


def _check_tenant(receipt, tenant_id):
    if not tenant_id or tenant_id.strip().lower() != receipt["tenant_id"]:
        raise Usage("wrong-tenant", f"--tenant must be this deployment's tenant {receipt['tenant_id']}, confirmed by "
                    "the user as a non-production demo tenant.", tenantId=receipt["tenant_id"])


def _check_quiet(resuming=None):
    active = workspace.read_lock()
    if active:
        raise Blocked("another-run", f"Another run is active ({active.get('runId')}).", activeRun=active.get("runId"),
                      next=[f"{CLI} monitor --run {active.get('runId')}"])
    pending = [run for run in open_tenant_changes() if not (
        resuming and run.id == resuming and tenant.State(run.path("tenant_state.json")).resumable)]
    if pending:
        raise Blocked("needs-restore", f"The run {pending[0].id} ended without restoring the tenant settings it changed; "
                      "restore them before another run.", next=[f"{CLI} restore --run {pending[0].id} --tenant TENANT"])


def start_seed(name, receipts_file, tenant_id, approve, again=False):
    receipt = receipts.require(receipts_file)
    auth.require_consent(receipt)
    _check_tenant(receipt, tenant_id)
    demo.load_valid(name)
    raw = demo.file(name).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (approve or "").strip().lower() != digest:
        raise Usage("not-approved", "--approve must be the sha256 the user approved in this demo's preview; the demo "
                    "changed since then, or the hash is wrong. Preview it again.", sha256=digest)
    doc = json.loads(raw.decode("utf-8-sig"))
    errors, _ = demo.validate(doc)
    if errors:
        raise Usage("invalid-demo", f"The demo {name!r} has {len(errors)} problem(s).", errors=errors[:50])
    people = demo.people(doc)
    missing = users.status([p["upn"] for p in people])["missing"]
    if missing:
        raise Waiting("needs-passwords", f"{len(missing)} demo user(s) have no stored password.", missing=missing,
                      next=[f"Run this yourself in a terminal you control: {CLI} users set --demo {name}"])
    _check_quiet()
    previous = [run for run in all_runs() if run.info().get("kind") == "seed" and run.info().get("demo") == name
                and run.info().get("tenantId") == receipt["tenant_id"] and created_items(run)]
    if previous and not again:
        finish = (f"{CLI} cleanup --run {previous[0].id}" if cleaned_up(previous[0])
                  else f"{CLI} resume --run {previous[0].id} --tenant {receipt['tenant_id']}")
        raise Usage("already-seeded", f"The demo {name!r} already has content in this tenant from {previous[0].id}; "
                    "seeding it again creates a second copy. Finish that run (resume it, or finish its cleanup), or "
                    "add --again to seed a second copy anyway.", previousRun=previous[0].id, next=[finish])
    run = new_run("seed", name)
    try:
        workspace.write_private(run.path("demo.json"), raw)
        workspace.write_json(run.path("run.json"), {
            "runId": run.id, "kind": "seed", "demo": name, "demoSha256": digest, "title": doc.get("title"),
            "tenantId": receipt["tenant_id"], "clientId": receipt["client_id"], "adminUpn": demo.admin_upn(doc),
            "people": [{key: p.get(key) for key in ("key", "upn", "name", "role")} for p in people],
            "receipt": receipt, "createdAt": utc_now()})
        return _spawn(run, "seed")
    except Stop as stop:
        if stop.reason == "another-run":
            shutil.rmtree(run.dir, ignore_errors=True)
        raise


def resume(run_id, receipts_file, tenant_id):
    run = Run(run_id)
    info = run.info()
    receipt = receipts.require(receipts_file)
    _check_tenant(receipt, tenant_id)
    if info.get("kind") != "seed":
        raise Usage("not-a-seed", f"{run_id} is not a seed run.")
    if info.get("tenantId") != receipt["tenant_id"] or info.get("clientId") != receipt["client_id"]:
        raise Blocked("other-deployment", f"{run_id} belongs to another tenant or demo app.")
    if cleaned_up(run):
        raise Usage("cleaned-up", f"{run_id} has been cleaned up (fully or partly); resuming it would re-create content "
                    "no cleanup plan would find. Finish its cleanup, then seed the demo again.",
                    next=[f"{CLI} cleanup --run {run_id}"])
    if (run.result() or {}).get("outcome") == "seeded":
        raise Usage("complete", f"{run_id} already seeded everything.")
    _check_quiet(resuming=run_id)
    earlier = run.result()
    if earlier:
        info.setdefault("attempts", []).append(earlier)
    info.update(receipt=receipt, resumedAt=utc_now())
    workspace.write_json(run.path("run.json"), info)
    workspace.remove(run.path("result.json"))
    workspace.remove(run.path("cancel"))
    workspace.write_json(run.path("status.json"), {})
    return _spawn(run, "seed")


def start_restore(run_id, receipts_file, tenant_id):
    target = Run(run_id)
    info = target.info()
    receipt = receipts.require(receipts_file)
    _check_tenant(receipt, tenant_id)
    if info.get("tenantId") != receipt["tenant_id"]:
        raise Blocked("other-deployment", f"{run_id} changed another tenant.")
    if not tenant.State(target.path("tenant_state.json")).needs_restore:
        return {"runId": run_id, "state": "nothing-to-restore", "summary": f"{run_id} left nothing to restore."}
    active = workspace.read_lock()
    if active:
        raise Blocked("another-run", f"Another run is active ({active.get('runId')}).", activeRun=active.get("runId"))
    run = new_run("restore", info.get("demo") or "tenant")
    try:
        workspace.write_json(run.path("run.json"), {
            "runId": run.id, "kind": "restore", "target": run_id, "demo": info.get("demo"),
            "tenantId": receipt["tenant_id"], "clientId": receipt["client_id"], "adminUpn": info.get("adminUpn"),
            "receipt": receipt, "createdAt": utc_now()})
        return _spawn(run, "restore")
    except Stop as stop:
        if stop.reason == "another-run":
            shutil.rmtree(run.dir, ignore_errors=True)
        raise


def _seed_run(run_id):
    run = Run(run_id)
    if run.info().get("kind") != "seed":
        raise Usage("not-a-seed", f"{run_id} is not a seed run; clean up the seed run whose content you want removed.")
    return run


def _others(target, tenant_id):
    """Other seed runs in this tenant with content still there (their files may be shared with the target's)."""
    return [(run.id, content.Manifest(run.path("manifest.json")).data) for run in all_runs()
            if run.id != target.id and run.info().get("kind") == "seed" and run.info().get("tenantId") == tenant_id
            and created_items(run)]


def cleanup_rows(target):
    info = target.info()
    shared = cleanup.shared_with(_others(target, info.get("tenantId")))
    return cleanup.plan(content.Manifest(target.path("manifest.json")).data, shared)


def cleanup_plan(run_id):
    """What cleaning up a seed run would delete (local files only) and the hash that approves it."""
    target = _seed_run(run_id)
    info = target.info()
    rows = cleanup_rows(target)
    kinds = {}
    for row in rows:
        kinds.setdefault(row["kind"], {"delete": 0, "keep": 0})["keep" if row["action"] == "keep" else "delete"] += 1
    report = {"runId": run_id, "demo": info.get("demo"), "tenantId": info.get("tenantId"), "items": rows,
              "counts": kinds, "sha256": cleanup.digest(run_id, info.get("tenantId"), rows)}
    if not rows:
        report.update(summary=f"{run_id} has nothing left in the tenant.", next=[])
        return report
    report["summary"] = (f"Cleaning up {run_id} deletes {sum(k['delete'] for k in kinds.values())} item(s)"
                         + (f" and keeps {sum(k['keep'] for k in kinds.values())} shared with another run"
                            if any(k["keep"] for k in kinds.values()) else "") + ".")
    report["next"] = [f"After the user approves this plan: {CLI} cleanup --run {run_id} --tenant {info.get('tenantId')} "
                      f"--approve {report['sha256']}"]
    return report


def start_cleanup(run_id, receipts_file, tenant_id, approve):
    target = _seed_run(run_id)
    info = target.info()
    receipt = receipts.require(receipts_file)
    auth.require_consent(receipt)
    _check_tenant(receipt, tenant_id)
    if info.get("tenantId") != receipt["tenant_id"]:
        raise Blocked("other-deployment", f"{run_id} seeded another tenant.")
    rows = cleanup_rows(target)
    digest = cleanup.digest(run_id, info.get("tenantId"), rows)
    if not rows:
        return {"runId": run_id, "state": "nothing-to-clean", "summary": f"{run_id} has nothing left in the tenant."}
    if (approve or "").strip().lower() != digest:
        raise Usage("not-approved", "--approve must be the sha256 of the cleanup plan the user approved; the plan "
                    "changed since then, or the hash is wrong. Plan it again.", sha256=digest)
    people = demo.people(target.demo() or {})
    missing = users.status([p["upn"] for p in people])["missing"]
    if missing:
        raise Waiting("needs-passwords", f"{len(missing)} demo user(s) have no stored password; their items cannot be "
                      "deleted without signing in as them.", missing=missing,
                      next=[f"Run this yourself in a terminal you control: {CLI} users set"
                            + "".join(f" --upn {upn}" for upn in missing)])
    _check_quiet()
    run = new_run("cleanup", info.get("demo") or "demo")
    try:
        workspace.write_json(run.path("run.json"), {
            "runId": run.id, "kind": "cleanup", "target": run_id, "demo": info.get("demo"),
            "tenantId": receipt["tenant_id"], "clientId": receipt["client_id"], "adminUpn": info.get("adminUpn"),
            "planSha256": digest, "receipt": receipt, "createdAt": utc_now()})
        return _spawn(run, "cleanup")
    except Stop as stop:
        if stop.reason == "another-run":
            shutil.rmtree(run.dir, ignore_errors=True)
        raise


def cancel(run_id):
    run = Run(run_id)
    run.info()
    state = snapshot(run)["state"]
    if state not in ACTIVE:
        return {"runId": run_id, "state": state, "summary": "The run is not active."}
    workspace.write_private(run.path("cancel"), utc_now())
    return {"runId": run_id, "state": state, "summary": "Cancellation requested: the run stops after its current item "
            "and restores the tenant.", "next": [f"{CLI} monitor --run {run_id} --wait 300"]}


def snapshot(run):
    info, status = run.info(), run.status_data()
    kind, tenant_id = info.get("kind"), info.get("tenantId")
    state = status.get("state") or "starting"
    if state in ACTIVE and not proc.alive(status.get("pid")):
        state = "interrupted"
    state_run = Run(info["target"]) if kind == "restore" else run
    tenant_state = tenant.State(state_run.path("tenant_state.json"))
    result = run.result()
    outcome = (result or {}).get("outcome")
    if state == "needs-restore" and not tenant_state.needs_restore:
        state = final_state(outcome)  # a separate restore run has restored what this run changed
    report = {"runId": run.id, "kind": kind, "demo": info.get("demo"), "state": state,
              "step": status.get("step"), "detail": status.get("detail"), "progress": status.get("progress"),
              "updatedAt": status.get("updatedAt"), "log": str(run.path("log.txt")),
              "warnings": status.get("warnings") or []}
    if kind in ("restore", "cleanup"):
        report["target"] = info.get("target")
    if kind == "seed":
        report["created"] = created_items(run)
    if tenant_state.data.get("phase") != "new":
        report["tenant"] = tenant_state.summary()
    if result:
        report["result"] = {key: result.get(key) for key in ("outcome", "reason", "message", "counts", "failures",
                                                              "signInFailures", "problems", "guide", "kept", "swept",
                                                              "remaining") if key in result}
    steps = []
    if state == "waiting-for-sign-in":
        report.update(codeFile=status.get("codeFile"), verificationUri=status.get("verificationUri"),
                      account=info.get("adminUpn"), expiresAt=status.get("expiresAt"))
        steps = [f"Open {status.get('codeFile')} yourself to read the one-time code, then sign in as "
                 f"{info.get('adminUpn')} at {status.get('verificationUri')}.",
                 f"{CLI} monitor --run {run.id} --wait 600"]
    elif state in ACTIVE:
        steps = [f"{CLI} monitor --run {run.id} --wait 300"]
    else:
        if state == "interrupted":
            report["workerOutput"] = str(run.path("worker.out"))
        if tenant_state.needs_restore:
            steps.append(f"{CLI} restore --run {state_run.id} --tenant {tenant_id}")
        unfinished = state in ("interrupted", "failed", "cancelled") or outcome == "seeded-with-failures"
        if kind == "seed" and unfinished and (not tenant_state.needs_restore or tenant_state.resumable) \
                and not cleaned_up(run):
            steps.append(f"{CLI} resume --run {run.id} --tenant {tenant_id}")
        if kind == "seed" and result and result.get("guide") and not tenant_state.needs_restore:
            steps.append(f"Open the presenter guide yourself: {result['guide']}")
        cleanup_left = state in ("interrupted", "failed", "cancelled") or outcome == "cleaned-with-failures"
        if kind == "cleanup" and cleanup_left and not tenant_state.needs_restore:
            steps.append(f"{CLI} cleanup --run {info.get('target')}")
    report["next"] = steps
    return report


def monitor(run_id, wait=0):
    """The run's state; with wait, block until it changes, finishes or needs a person (at most 10 minutes)."""
    run = Run(run_id)
    first = snapshot(run)
    deadline = time.monotonic() + max(0, min(int(wait or 0), 600))
    current = first
    while time.monotonic() < deadline and current["state"] in ACTIVE:
        if current["state"] != first["state"] or current.get("step") != first.get("step"):
            break
        time.sleep(1)
        current = snapshot(run)
    return current


def history(limit=20):
    rows = []
    for run in all_runs()[:limit]:
        info, result = run.info(), run.result() or {}
        snap = snapshot(run)
        rows.append({"runId": run.id, "kind": info.get("kind"), "demo": info.get("demo"), "state": snap["state"],
                     "outcome": result.get("outcome"), "startedAt": info.get("createdAt"),
                     "finishedAt": result.get("finishedAt"), "tenant": (snap.get("tenant") or {}).get("phase"),
                     "guide": result.get("guide")})
    return rows


# --- the worker -------------------------------------------------------------------------------

def sign_in_admin(run, receipt, account):
    run.status("signing-in-admin")
    device = auth.start_device_code(receipt, run.code_file, account=account)
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(device.get("expires_in") or 900))
    run.status("waiting-for-sign-in", codeFile=str(run.code_file), verificationUri=device["verification_uri"],
               expiresAt=expires.isoformat(timespec="seconds"))
    run.log(f"Waiting for {account} to sign in with the one-time code (written to {run.code_file.name}).")
    tokens = auth.poll_device_code(receipt, device, run.code_file, sleep=run.pause)
    session = auth.admin_session(receipt, tokens, expected_upn=account, me=lambda s: Graph(s).me())
    run.log(f"Signed in as {session.account}.")
    for warning in session.warnings:
        run.warn(warning)
    return session


def resolve_people(graph, people):
    for person in people.values():
        path = "users?" + query(filter=f"userPrincipalName eq {odata_string(person['upn'])}",
                                select="id,userPrincipalName,displayName,accountEnabled")
        found = graph.get(path).get("value") or []
        if not found:
            raise Blocked("user-not-found", f"The demo user {person['upn']} does not exist in this tenant.")
        if found[0].get("accountEnabled") is False:
            raise Blocked("user-disabled", f"The demo user {person['upn']} is disabled.")
        person["id"] = found[0]["id"]


def sign_in_people(run, receipt, people, passwords):
    """Sign the demo users in, waiting while the relaxed MFA and Conditional Access settings take effect."""
    pending, verdicts = list(people.values()), {}
    for _, last in pace.waits(SIGN_IN_WAITS):
        if run.cancel_requested():
            raise content.Cancelled("cancelled", "The run was cancelled.")
        waiting = []
        for person in pending:
            session, verdict = auth.password_sign_in(receipt, person["upn"], passwords[person["upn"]])
            if session:
                person["session"] = session
                verdicts.pop(person["key"], None)
            elif verdict["status"] == "ok-pending-mfa" and not last:
                waiting.append(person)
            else:
                verdicts[person["key"]] = verdict
        pending = waiting
        if not pending:
            break
        run.status("signing-in-users", detail=f"waiting for {len(pending)} demo user(s): MFA or Conditional Access "
                   "changes are still propagating")
    return {people[key]["upn"]: {"status": v["status"], "hint": v["hint"]} for key, v in verdicts.items()}


def passwords_for(people):
    stored = users.load()["users"]
    passwords = {person["upn"]: (stored.get(person["upn"]) or {}).get("password") for person in people.values()}
    missing = [upn for upn, value in passwords.items() if not value]
    if missing:
        raise Waiting("needs-passwords", f"{len(missing)} demo user(s) have no stored password.", missing=missing)
    return passwords


def progress_writer(run, total):
    progress = {"done": 0, "total": total}

    def advance(number):
        progress["done"] = min(progress["total"], progress["done"] + number)
        try:
            data = run.status_data()
            data["progress"] = dict(progress)
            workspace.write_json(run.path("status.json"), data)
        except (OSError, Stop):
            pass  # progress is informational; the next update catches up

    return progress, advance


def seed_run(run, info):
    if cleaned_up(run):
        raise Blocked("cleaned-up", f"{run.id} has been cleaned up; seed the demo again instead.")
    doc, receipt = run.demo(), info["receipt"]
    people = {person["key"]: dict(person) for person in demo.people(doc)}
    passwords = passwords_for(people)
    admin = sign_in_admin(run, receipt, info["adminUpn"])
    graph = Graph(admin)
    run.status("resolving-users")
    resolve_people(graph, people)
    manifest = content.Manifest(run.path("manifest.json"), run.id, info["demo"])
    state = tenant.State(run.path("tenant_state.json"), run.id)
    progress, advance = progress_writer(run, content.units(doc))

    run.status("preparing-tenant")
    seeder = None
    with tenant.prepared(graph, [{"upn": p["upn"], "id": p["id"]} for p in people.values()], state, run.log):
        run.status("signing-in-users")
        sign_in_failures = sign_in_people(run, receipt, people, passwords)
        passwords = None
        if len(sign_in_failures) == len(people):
            raise Blocked("no-demo-users", "No demo user could sign in.", signInFailures=sign_in_failures)
        for upn, verdict in sign_in_failures.items():
            run.warn(f"{upn} could not sign in ({verdict['status']}): their content is skipped. {verdict['hint']}")
        seeder = content.Seeder(doc, people, admin, manifest, run_id=run.id, log=run.log, advance=advance,
                                cancelled=run.cancel_requested)
        for step, action in (("team", seeder.prepare_team), ("files", seeder.seed_files), ("mail", seeder.seed_mail),
                             ("chats", seeder.seed_chats), ("meetings", seeder.seed_meetings),
                             ("channel", seeder.seed_channel)):
            run.status("seeding", step=step, progress=dict(progress))
            action()
        run.status("restoring")
    for person in people.values():
        person.pop("session", None)
    result = {"outcome": "seeded-with-failures" if seeder.failures else "seeded", "failures": seeder.failures,
              "signInFailures": sign_in_failures, "counts": manifest.counts()}
    guide = run.path("guide.html")
    workspace.write_private(guide, pages.guide({**info, "finishedAt": utc_now()}, doc, manifest.data, result))
    result["guide"] = str(guide)
    return result


def cleanup_run(run, info):
    target = Run(info["target"])
    doc, receipt = target.demo() or {}, info["receipt"]
    people = {person["key"]: dict(person) for person in demo.people(doc)}
    passwords = passwords_for(people)
    admin = sign_in_admin(run, receipt, info["adminUpn"])
    graph = Graph(admin)
    run.status("resolving-users")
    resolve_people(graph, people)
    manifest = content.Manifest(target.path("manifest.json"))
    shared = cleanup.shared_with(_others(target, info["tenantId"]))
    rows = cleanup.plan(manifest.data, shared)
    if cleanup.digest(target.id, target.info().get("tenantId"), rows) != info["planSha256"]:
        raise Blocked("plan-changed", "The cleanup plan changed since the user approved it; plan and approve it again.",
                      next=[f"{CLI} cleanup --run {target.id}"])
    state = tenant.State(run.path("tenant_state.json"), run.id)
    progress, advance = progress_writer(run, len(rows))
    by_upn = {person["upn"]: person for person in people.values()}

    def graph_for(upn):
        person = by_upn.get(upn)
        if not person or not person.get("session"):
            raise Stop("no-session", f"{upn} could not sign in, so their items were not deleted.")
        return Graph(person["session"])

    run.status("preparing-tenant")
    with tenant.prepared(graph, [{"upn": p["upn"], "id": p["id"]} for p in people.values()], state, run.log):
        run.status("signing-in-users")
        sign_in_failures = sign_in_people(run, receipt, people, passwords)
        passwords = None
        for upn, verdict in sign_in_failures.items():
            run.warn(f"{upn} could not sign in ({verdict['status']}): their items are not deleted. {verdict['hint']}")
        cleaner = cleanup.Cleaner(manifest, by_upn, admin, shared=shared, log=run.log, advance=advance,
                                  cancelled=run.cancel_requested, graph_for=graph_for)
        run.status("cleaning", progress=dict(progress))
        # Recorded before the first deletion: from here on, resuming the seed run would re-create content.
        record_cleanup(target, run.id, create=True, outcome="running", startedAt=utc_now())
        cleaner.run()
        run.status("restoring")
    for person in people.values():
        person.pop("session", None)
    remaining = manifest.created()
    return {"outcome": "cleaned-with-failures" if cleaner.failures or remaining else "cleaned",
            "failures": cleaner.failures, "kept": cleaner.kept, "signInFailures": sign_in_failures,
            "counts": cleaner.counts, "swept": cleaner.swept, "remaining": remaining}


def restore_run(run, info):
    target = Run(info["target"])
    state = tenant.State(target.path("tenant_state.json"))
    if not state.needs_restore:
        return {"outcome": "nothing-to-restore"}
    admin = sign_in_admin(run, info["receipt"], info["adminUpn"])
    run.status("restoring")
    problems = tenant.restore(Graph(admin), state, run.log)
    return {"outcome": "restore-failed" if problems else "restored", "problems": problems}


def _terminate(*_):
    raise KeyboardInterrupt


def work(run_id):
    """The detached worker: runs one seed or restore run to its end, always releasing the lock."""
    run = Run(run_id)
    info = run.info()
    signal.signal(signal.SIGTERM, _terminate)
    result = {}
    try:
        lock = workspace.read_lock()
        if not lock or lock.get("runId") != run_id:
            raise Blocked("lock-lost", "This run does not hold the run lock.")
        workspace.set_lock_pid(run_id, os.getpid())
        result = {"seed": seed_run, "cleanup": cleanup_run, "restore": restore_run}[info["kind"]](run, info)
    except (content.Cancelled, KeyboardInterrupt):
        result = {"outcome": "cancelled", "message": "The run was cancelled."}
    except Stop as stop:
        result = {"outcome": "failed", **{k: v for k, v in stop.report().items() if k != "ok"}}
    except Exception as error:  # noqa: BLE001 - recorded in the log and result
        run.log("INTERNAL ERROR:\n" + traceback.format_exc())
        result = {"outcome": "failed", "reason": "internal-error", "message": f"{type(error).__name__}: {error}"}
    finally:
        workspace.remove(run.code_file, scrub=True)
        result["finishedAt"] = utc_now()
        try:
            target = Run(info["target"]) if info.get("kind") == "restore" else run
            open_changes = tenant.State(target.path("tenant_state.json")).needs_restore
        except Stop:
            open_changes = True
        final = "needs-restore" if open_changes else final_state(result.get("outcome"))
        # Each step on its own: none of them may keep the lock from being released.
        _quietly(lambda: workspace.write_json(run.path("result.json"), result))
        if info.get("kind") == "cleanup":
            def note_cleanup_end():
                cleaned = Run(info["target"])
                record_cleanup(cleaned, run_id, outcome=result.get("outcome"), remaining=created_items(cleaned),
                               finishedAt=result["finishedAt"])
            _quietly(note_cleanup_end)
        _quietly(lambda: run.log(f"Run ended: {result.get('outcome')} ({final})."))
        _quietly(lambda: run.status(final))
        _quietly(lambda: workspace.release_lock(run_id))
    return 0


def _quietly(action):
    try:
        action()
    except Exception:  # noqa: BLE001 - reported in worker.out; the run's end must still be recorded
        traceback.print_exc(file=sys.stderr)


def worker_main(run_id):
    try:
        return work(run_id)
    except Exception:  # noqa: BLE001 - the worker has no other place to report
        traceback.print_exc(file=sys.stderr)
        return 2
