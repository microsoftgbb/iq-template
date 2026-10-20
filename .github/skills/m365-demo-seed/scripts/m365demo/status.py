"""Where this deployment's demo seeding stands, and the next step.

Reads local files and the runner's receipts only; it never calls the tenant. A seeded demo is
not proof that Work IQ answers from it (Microsoft 365 indexing takes time).
"""

from .common import CLI
from . import demo, receipts, runs, users, workiq, workspace


def _result(base, state, summary, next_steps):
    return {**base, "state": state, "summary": summary, "next": next_steps}


def report(receipts_file=None, demo_name=None):
    base = {"home": str(workspace.home())}
    receipt = receipts.load(receipts_file)
    if not receipt:
        return _result(base, "not-set-up", "Microsoft 365 demo seeding is not set up for this deployment.",
                       [f"{receipts.SETUP} (an administrator adds --grant-m365-demo-consent, or grants consent "
                        "in the portal)"])
    tenant = receipt["tenant_id"]
    base["setup"] = {"tenantId": tenant, "clientId": receipt["client_id"], "consent": receipt["consent"],
                     "checkedAt": receipt["checked_at"]}
    if receipt["consent"] not in ("granted", "unverified"):
        return _result(base, "consent-pending", "The demo app waits for administrator consent.",
                       [f"An administrator runs: {receipts.SETUP} --grant-m365-demo-consent"])
    active = workspace.read_lock()
    if active:
        base["activeRun"] = active
        return _result(base, "running", f"A {active.get('kind', 'demo')} run is active ({active.get('runId')}).",
                       [f"{CLI} monitor --run {active.get('runId')} --wait 300"])
    pending = runs.open_tenant_changes()
    if pending:
        base["runsToRestore"] = [run.id for run in pending]
        return _result(base, "needs-restore", f"The run {pending[0].id} ended without restoring the tenant settings it "
                       "relaxed (MFA, Conditional Access or Security Defaults). Restore them first.",
                       [f"{CLI} restore --run {pending[0].id} --tenant {tenant}"])
    base["demos"] = demo.names()
    name = demo_name or demo.latest()
    if not name:
        return _result(base, "needs-demo", "No demo has been authored yet.",
                       [f"{CLI} demo new NAME --admin ADMIN_UPN --person UPN --person UPN ... (then adapt the story; "
                        "see references/content.md)"])
    doc = demo.load(name)
    people = demo.people(doc)
    base["demo"] = name
    errors, _ = demo.validate(doc)
    if errors:
        base["errors"] = errors[:20]
        return _result(base, "needs-demo", f"The demo {name!r} has {len(errors)} problem(s) to fix.",
                       [f"{CLI} demo validate --demo {name}"])
    stored = users.status([person["upn"] for person in people])
    base["users"] = {"total": len(people), "missing": stored["missing"], "ownerOnly": stored["ownerOnly"]}
    if stored["missing"]:
        return _result(base, "needs-passwords",
                       f"{len(stored['missing'])} of {len(people)} demo users have no stored password.",
                       [f"Run this yourself in a terminal you control: {CLI} users set --demo {name}"])
    seeds = [run for run in runs.all_runs() if run.info().get("kind") == "seed" and run.info().get("demo") == name
             and run.info().get("tenantId") == tenant]
    if seeds:
        last = runs.snapshot(seeds[0])
        result = last.get("result") or {}
        outcome = result.get("outcome")
        base["lastRun"] = {"runId": last["runId"], "state": last["state"], "outcome": outcome,
                           "created": last.get("created"), "guide": result.get("guide")}
        resume = f"{CLI} resume --run {last['runId']} --tenant {tenant}"
        cleanups = seeds[0].info().get("cleanups") or []
        if cleanups and not last.get("created"):
            base["lastRun"]["cleanedBy"] = cleanups[-1]["runId"]
            return _result(base, "ready-to-seed", f"The content {last['runId']} seeded was cleaned up by "
                           f"{cleanups[-1]['runId']}; the demo can be seeded again.",
                           [f"{CLI} users check --demo {name}", f"{CLI} demo preview --demo {name}"])
        if cleanups:
            return _result(base, "cleanup-incomplete", f"The cleanup {cleanups[-1]['runId']} left {last['created']} "
                           f"item(s) of {last['runId']} in the tenant.", [f"{CLI} cleanup --run {last['runId']}"])
        if outcome in ("seeded", "seeded-with-failures"):
            summary = (f"The demo {name!r} was seeded by {last['runId']}"
                       + (" with some items not created" if outcome == "seeded-with-failures" else "")
                       + ". Seeded is not ready: Microsoft 365 indexes new content over minutes to hours.")
            steps = [f"Open the presenter guide yourself: {result.get('guide')}"]
            if outcome == "seeded-with-failures":
                steps.append(resume)
            check = workiq.latest(seeds[0])
            if check:
                base["workiq"] = {k: check.get(k) for k in ("verdict", "prompts", "withSeededCitations", "askedAs",
                                                             "askedAsIsDemoUser", "checkedAt")}
            if check and check.get("verdict") == "found-seeded-content" and check.get("askedAsIsDemoUser"):
                return _result(base, "workiq-verified", f"Work IQ cited content seeded by {last['runId']} for "
                               f"{check['withSeededCitations']} of {check['prompts']} prompt(s), asked as "
                               f"{check['askedAs']} at {check['checkedAt']}. This covers only the prompts asked.",
                               steps)
            steps.append(f"{CLI} workiq prompts --run {last['runId']}")
            return _result(base, "seeded", summary, steps)
        ended = last["state"] + (f" ({result['reason']})" if result.get("reason") else "")
        if last.get("created"):
            return _result(base, "partly-seeded", f"The last seed run {last['runId']} ended {ended} after creating "
                           f"{last['created']} item(s). Resume it to finish; seeding again would create a second "
                           "copy.", [resume])
        if last["state"] in ("failed", "interrupted", "cancelled"):
            return _result(base, "ready-to-seed", f"The last seed run {last['runId']} ended {ended} before creating "
                           "anything; resume it or seed again.", [resume, f"{CLI} demo preview --demo {name}"])
    return _result(base, "ready-to-seed", f"The demo {name!r} and its {len(people)} demo users' passwords are in place.",
                   [f"{CLI} users check --demo {name}", f"{CLI} demo preview --demo {name}"])
