"""Demo users' passwords and their sign-in check.

The user types the passwords in their own terminal (`users set`, masked input). They are stored
in demo-users.json, owner-only, and read only by this tool's own processes. Every command reports
UPNs and outcomes, never a password or a token.
"""

import getpass
import os
import sys

from .common import CLI, Blocked, Usage, Waiting, utc_now
from . import auth, demo, receipts, workspace
from .graph import Graph

FAMILIES = ("exchange", "sharepoint", "teams")
ATTEMPTS = 3
BLOCKING = frozenset({"app-not-found", "app-not-public-client"})


def load():
    data = workspace.read_json(workspace.users_file())
    if data is None:
        return {"version": 1, "users": {}}
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        raise Blocked("unreadable-file", f"{workspace.users_file()} is not a demo users file; clear it with `users clear`.")
    return data


def save(data):
    workspace.ensure_home()
    if data["users"]:
        workspace.write_json(workspace.users_file(), data)
    else:
        workspace.users_file().unlink(missing_ok=True)


def password(upn):
    entry = load()["users"].get(upn.lower())
    return entry.get("password") if isinstance(entry, dict) else None


def owner_only():
    """True, or fixes the file's mode when it is readable by others (macOS and Linux)."""
    path = workspace.users_file()
    if os.name == "nt" or not path.exists():
        return True
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)
        return False
    return True


def targets(upns=None, demo_name=None):
    """The UPNs a command acts on: --upn values, else a demo's people, else every stored user."""
    if upns:
        chosen = []
        for value in upns:
            value = value.strip().lower()
            if not demo.UPN.match(value):
                raise Usage("bad-upn", f"Not a user principal name: {value!r}.")
            if value not in chosen:
                chosen.append(value)
        return chosen
    name = demo_name or demo.latest()
    if name:
        found = [person["upn"] for person in demo.people(demo.load(name))]
        if found:
            return found
        if demo_name:
            raise Usage("no-people", f"The demo {demo_name!r} lists no demo users yet.")
    return sorted(load()["users"])


def _ask(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.strip()


def _secret(prompt):
    return getpass.getpass(prompt, stream=sys.stderr)


def _read_twice(label, secret):
    for _ in range(ATTEMPTS):
        first = secret(f"{label}: ")
        if not first:
            sys.stderr.write("The password cannot be empty.\n")
            continue
        if secret("Repeat it: ") == first:
            return first
        sys.stderr.write("The two entries differ; try again.\n")
    raise Usage("password-not-set", f"No password was set for {label.removeprefix('Password for ')}.")


def set_passwords(upns, *, command, missing_only=False, interactive=None, ask=_ask, secret=_secret):
    """Prompt the person at this terminal for the demo users' passwords and store them owner-only."""
    if not upns:
        raise Usage("no-users", "Name the demo users: --demo NAME, or --upn UPN for each.")
    if not (sys.stdin.isatty() if interactive is None else interactive):
        raise Waiting("needs-your-terminal", "Demo users' passwords are typed by you, in your own terminal, never in chat.",
                      next=[f"Run this yourself in a terminal you control: {command}"])
    data = load()
    if missing_only:
        upns = [upn for upn in upns if upn not in data["users"]]
    if not upns:
        return {"stored": [], "file": str(workspace.users_file()), "message": "Every demo user already has a password."}
    sys.stderr.write(f"Passwords for {len(upns)} demo user(s): {', '.join(upns)}.\n"
                     f"They are stored owner-only in {workspace.users_file()} and never shown again.\n")
    try:
        shared = len(upns) > 1 and ask(f"Do all {len(upns)} share one password? [Y/n] ").lower() in ("", "y", "yes")
        if shared:
            value = _read_twice(f"Password for all {len(upns)} demo users", secret)
            values = dict.fromkeys(upns, value)
        else:
            values = {upn: _read_twice(f"Password for {upn}", secret) for upn in upns}
    except (EOFError, KeyboardInterrupt):
        raise Usage("cancelled", "Cancelled; no password was changed.") from None
    now = utc_now()
    for upn, value in values.items():
        data["users"][upn] = {"password": value, "setAt": now}
    save(data)
    values = None
    return {"stored": upns, "file": str(workspace.users_file()),
            "next": [f"Check the demo users' sign-ins: {CLI} users check"]}


def status(upns):
    data = load()
    rows = [{"upn": upn, "password": "stored" if upn in data["users"] else "missing",
             "setAt": (data["users"].get(upn) or {}).get("setAt")} for upn in upns]
    return {"file": str(workspace.users_file()), "ownerOnly": owner_only(), "users": rows,
            "missing": [row["upn"] for row in rows if row["password"] == "missing"]}


def clear(upns=None):
    """Forget the named users' passwords; None forgets all of them."""
    data = load()
    removed = sorted(data["users"]) if upns is None else [upn.lower() for upn in upns if upn.lower() in data["users"]]
    for upn in removed:
        data["users"].pop(upn, None)
    save(data)
    return {"removed": removed, "remaining": len(data["users"])}


def family(plans, name):
    if name == "teams":
        found = [plan for plan in plans if str(plan.get("servicePlanName", "")).upper().startswith("TEAMS")]
    elif name == "exchange":
        found = [plan for plan in plans if str(plan.get("servicePlanName", "")).upper().startswith("EXCHANGE_S_")]
    else:
        found = [plan for plan in plans if str(plan.get("servicePlanName", "")).upper().startswith("SHAREPOINT")
                 and not str(plan.get("servicePlanName", "")).upper().startswith("SHAREPOINTWAC")]
    states = [str(plan.get("provisioningStatus") or "") for plan in found]
    if not found:
        return "missing"
    if "Success" in states:
        return "ok"
    if any(state.startswith("Pending") for state in states):
        return "pending"
    return "disabled"


def licenses(session):
    """The user's Exchange, SharePoint and Teams plans, read with their own token (User.Read)."""
    try:
        details = list(Graph(session).pages("me/licenseDetails?$select=servicePlans,skuPartNumber"))
    except Blocked as error:
        return {"status": "unchecked", "reason": "check-failed", "detail": str(error)}
    plans = [plan for detail in details for plan in detail.get("servicePlans") or []]
    report = {name: family(plans, name) for name in FAMILIES}
    missing = [name for name in FAMILIES if report[name] in ("missing", "disabled")]
    pending = [name for name in FAMILIES if report[name] == "pending"]
    report.update(status="issues" if missing else "pending" if pending else "ok", missing=missing, pending=pending)
    if missing:
        report["detail"] = (f"No provisioned {', '.join(missing)} plan: content for those workloads cannot be created "
                            "as this user. Assign a license with the plan enabled (for example Microsoft 365 E3 or E5).")
    elif pending:
        report["detail"] = f"{', '.join(pending)} still provisioning; new tenants usually finish within hours."
    return report


def check(upns, receipt):
    """Sign each demo user in (public client, password) and read their licenses; never output secrets."""
    data = load()
    rows = []
    for upn in upns:
        stored = (data["users"].get(upn) or {}).get("password")
        if not stored:
            rows.append({"upn": upn, "status": "no-password", "passwordValid": False, "reason": "no-password",
                         "hint": f"No password stored for {upn}: run `users set` in your own terminal.",
                         "licenses": {"status": "unchecked", "reason": "no-token"}})
            continue
        session, verdict = auth.password_sign_in(receipt, upn, stored)
        stored = None
        row = {"upn": upn, **verdict}
        if session:
            lacking = sorted(scope for scope in receipt["delegated_scopes"] if session.scopes
                             and scope.lower() not in auth.SIGN_IN_ONLY and scope.lower() not in session.scopes)
            if lacking:
                row.update(status="consent-needed", reason="scopes-missing",
                           hint=f"The demo app's consent lacks {', '.join(lacking)}. {auth.CONSENT_HINT}")
            row["licenses"] = licenses(session)
        else:
            row["licenses"] = {"status": "unchecked", "reason": "no-token"}
        rows.append(row)
    data = None
    passed = sum(row["status"] in auth.PASSED for row in rows)
    grades = [row["licenses"]["status"] for row in rows]
    summary = {"tenantId": receipt["tenant_id"], "checked": len(rows), "passed": passed, "failed": len(rows) - passed,
               "licenses": {grade: grades.count(grade) for grade in ("ok", "issues", "pending", "unchecked")},
               "users": rows}
    summary["allReady"] = summary["failed"] == 0 and not summary["licenses"]["issues"] and not summary["licenses"]["pending"]
    hints = [row["hint"] for row in rows if row["status"] not in auth.PASSED]
    hints += [f"{row['upn']}: {row['licenses']['detail']}" for row in rows if row["licenses"].get("detail")
              and row["licenses"]["status"] in ("issues", "pending")]
    if any(row["status"] in BLOCKING for row in rows):
        raise Blocked("demo-app", "Demo users cannot sign in through the demo app.", next=hints, **summary)
    if summary["failed"]:
        raise Waiting("users-not-ready", f"{summary['failed']} of {len(rows)} demo users cannot sign in yet.",
                      next=hints, **summary)
    if not summary["allReady"]:
        raise Waiting("licenses-not-ready", "Some demo users' licenses are missing or still provisioning.",
                      next=hints, **summary)
    return summary


def require_receipt(receipts_file=None):
    receipt = receipts.require(receipts_file)
    auth.require_consent(receipt)
    return receipt
