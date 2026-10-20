"""Temporary tenant preparation for a run, and its exact restore.

Demo users sign in with their passwords, which Security Defaults, Conditional Access and
per-user MFA block. For each seed or cleanup run, as the signed-in administrator, prepare():
  1. turns Security Defaults off when it is on;
  2. creates a security group for the run with a random name, adds the demo users - never the
     administrator - to it, and excludes it from every enabled Conditional Access policy;
  3. turns per-user MFA off for demo users who have it.
Every change is recorded in the run's tenant_state.json *before* it is made, so an interrupted
run can always be restored. restore() undoes exactly the recorded changes: exclusions it did not
add and settings it did not change stay as they were found.
"""

import contextlib
import secrets

from .common import Blocked, Stop, utc_now
from . import pace, workspace
from .graph import GraphError, add_to_group, odata_string, query

GROUP_NAME = "m365-demo-seed MFA exclusion"
SECURITY_DEFAULTS = "policies/identitySecurityDefaultsEnforcementPolicy"
POLICIES = "identity/conditionalAccess/policies"
OPEN_PHASES = frozenset({"preparing", "prepared", "restoring", "restore-failed"})


class State:
    """tenant_state.json: what this run changed, written before each change."""

    def __init__(self, path, run_id=None):
        self.path = path
        self.data = workspace.read_json(path) or {"version": 1, "runId": run_id, "phase": "new"}

    def save(self):
        self.data["updatedAt"] = utc_now()
        workspace.write_json(self.path, self.data)

    def reset(self):
        """Start over after a completed restore (a resumed run prepares the tenant again)."""
        previous = {key: value for key, value in self.data.items() if key not in ("history",)}
        self.data = {"version": 1, "runId": self.data.get("runId"), "phase": "new",
                     "history": [*(self.data.get("history") or []), previous]}
        self.save()

    @property
    def resumable(self):
        """Prepared, or interrupted while preparing: preparing again continues from the records."""
        return self.phase in ("preparing", "prepared")

    @property
    def phase(self):
        return self.data.get("phase")

    @property
    def needs_restore(self):
        return self.phase in OPEN_PHASES

    def summary(self):
        policies = self.data.get("policies") or {}
        return {"phase": self.phase, "securityDefaultsTurnedOff": bool((self.data.get("securityDefaults") or {})
                                                                        .get("disabled")),
                "policiesExcluded": len(policies.get("touched") or []),
                "policiesAlreadyExcluding": len(policies.get("preExisting") or []),
                "policiesNotChanged": len(policies.get("skipped") or []),
                "perUserMfaTurnedOff": sum(bool(entry.get("changed")) for entry in self.data.get("mfa") or []),
                "problems": self.data.get("problems") or []}


def _excluded(policy):
    return list((((policy.get("conditions") or {}).get("users") or {}).get("excludeGroups")) or [])


def _find_group(graph, name):
    """The run's own exclusion group by its random name, waiting out directory replication."""
    path = "groups?" + query(filter=f"displayName eq {odata_string(name)}",
                             select="id,displayName,securityEnabled,mailEnabled")
    for _, last in pace.waits([0, 5, 10, 20]):
        found = [group for group in graph.pages(path) if group.get("securityEnabled") and not group.get("mailEnabled")]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise Blocked("exclusion-group", f"Several security groups are named '{name}'; none was used.")
    return None


def _create_group(graph, group, state, log):
    """Create the run's exclusion group, recording its unique name first so a crash can still find it."""
    if not group.get("displayName"):
        token = secrets.token_hex(4)
        group.update(displayName=f"{GROUP_NAME} {token}", mailNickname=f"m365-demo-seed-{token}", created=True)
        state.save()
    else:
        found = _find_group(graph, group["displayName"])
        if found:
            group["id"] = found["id"]
            state.save()
            return
    try:
        created = graph.post("groups", {
            "displayName": group["displayName"], "mailNickname": group["mailNickname"], "mailEnabled": False,
            "securityEnabled": True, "description": "Demo users excluded from MFA and Conditional Access while one "
            "m365-demo-seed run is active. The run deletes it when it restores the tenant."})
    except GraphError as error:
        if 400 <= error.status < 500:
            # Graph refused the request, so no group exists to clean up.
            state.data.pop("exclusionGroup", None)
            state.save()
        raise
    group["id"] = created["id"]
    state.save()
    log(f"Created the security group '{group['displayName']}' for this run.")


def _members(graph, group_id):
    """The group's member ids, waiting out replication of a group created moments ago."""
    for attempt, last in pace.waits([0, 5, 10, 20, 30]):
        try:
            return {member.get("id") for member in graph.pages(f"groups/{group_id}/members?$select=id")}
        except GraphError as error:
            if error.status != 404 or last:
                raise
    return set()


def prepare(graph, users, state, log):
    """Relax sign-in requirements for these demo users ([{upn, id}]) and record each change first."""
    if state.phase == "restored":
        state.reset()
    if state.phase not in ("new", "preparing", "prepared"):
        raise Blocked("needs-restore", "This run's earlier tenant changes are not fully restored; restore them first.")
    state.data["phase"] = "preparing"
    state.save()

    defaults = state.data.setdefault("securityDefaults", {})
    if "wasEnabled" not in defaults:
        defaults["wasEnabled"] = bool(graph.get(SECURITY_DEFAULTS).get("isEnabled"))
        state.save()
    if defaults["wasEnabled"] and not defaults.get("disabled"):
        defaults["disabled"] = True
        state.save()
        try:
            graph.patch(SECURITY_DEFAULTS, {"isEnabled": False})
        except GraphError as error:
            defaults["disabled"] = False
            state.save()
            raise Blocked("security-defaults", "Security Defaults is on and the administrator could not turn it off "
                          f"for the run ({error.status} {error.code}). It needs a Security Administrator or Global "
                          "Administrator.") from None
        log("Turned Security Defaults off for this run.")
        pace.sleep(10)

    group = state.data.setdefault("exclusionGroup", {})
    if not group.get("id"):
        _create_group(graph, group, state, log)
    group_id = group["id"]
    added = group.setdefault("membersAdded", [])
    present = _members(graph, group_id)
    outsiders = present - {user["id"] for user in users}
    if outsiders:
        raise Blocked("exclusion-group", f"The run's exclusion group '{group.get('displayName')}' has "
                      f"{len(outsiders)} member(s) that are not demo users, so it was not used.")
    for user in users:
        if user["id"] in present or user["id"] in added:
            continue
        added.append(user["id"])
        state.save()
        add_to_group(graph, group_id, user["id"])
    if added:
        log(f"Added {len(added)} demo user(s) to the exclusion group.")

    policies = state.data.setdefault("policies", {"touched": [], "preExisting": [], "skipped": []})
    handled = {entry["id"] for key in ("touched", "preExisting", "skipped") for entry in policies[key]}
    for policy in graph.pages(POLICIES):
        if policy.get("state") != "enabled" or policy.get("id") in handled:
            continue
        excluded = _excluded(policy)
        if group_id in excluded:
            policies["preExisting"].append({"id": policy["id"], "displayName": policy.get("displayName")})
            state.save()
            continue
        entry = {"id": policy["id"], "displayName": policy.get("displayName"), "restored": False}
        policies["touched"].append(entry)
        state.save()
        try:
            graph.patch(f"{POLICIES}/{policy['id']}", {"conditions": {"users": {"excludeGroups": excluded + [group_id]}}})
        except GraphError as error:
            policies["touched"].remove(entry)
            policies["skipped"].append({**entry, "error": str(error)})
            state.save()
            log(f"WARNING: could not exclude the demo users from the policy '{policy.get('displayName')}': {error}")
    if policies["touched"]:
        log(f"Excluded the demo users from {len(policies['touched'])} Conditional Access policy(ies).")

    mfa = state.data.setdefault("mfa", [])
    recorded = {entry["userId"] for entry in mfa}
    for user in users:
        if user["id"] in recorded:
            continue
        path = f"users/{user['id']}/authentication/requirements"
        try:
            original = graph.get(path, version="beta").get("perUserMfaState")
        except GraphError as error:
            mfa.append({"userId": user["id"], "upn": user["upn"], "original": None, "changed": False,
                        "error": str(error)})
            state.save()
            log(f"WARNING: could not read per-user MFA for {user['upn']}: {error}")
            continue
        entry = {"userId": user["id"], "upn": user["upn"], "original": original,
                 "changed": original in ("enabled", "enforced"), "restored": False}
        mfa.append(entry)
        state.save()
        if entry["changed"]:
            graph.patch(path, {"perUserMfaState": "disabled"}, version="beta")
            log(f"Turned per-user MFA off for {user['upn']} (was {original}).")
    state.data["phase"] = "prepared"
    state.save()


def restore(graph, state, log):
    """Undo exactly the recorded changes; returns the problems that need a person (empty when restored)."""
    if not state.needs_restore:
        return []
    state.data["phase"] = "restoring"
    state.save()
    problems = []

    for entry in state.data.get("mfa") or []:
        if entry.get("changed") and not entry.get("restored"):
            try:
                graph.patch(f"users/{entry['userId']}/authentication/requirements",
                            {"perUserMfaState": entry["original"]}, version="beta")
                entry["restored"] = True
                log(f"Restored per-user MFA for {entry['upn']} ({entry['original']}).")
            except Stop as error:
                problems.append(f"per-user MFA for {entry['upn']} is still off (was {entry['original']}): {error}")
            state.save()

    group = state.data.get("exclusionGroup") or {}
    if not group.get("id") and group.get("displayName"):
        # Interrupted while creating the group: find it by its unique name to delete it.
        try:
            found = _find_group(graph, group["displayName"])
        except Stop as error:
            found = None
            problems.append(f"the exclusion group '{group['displayName']}' could not be checked: {error}")
        if found:
            group["id"] = found["id"]
        else:
            group["notFound"] = True
        state.save()
    group_id = group.get("id")
    policies = state.data.get("policies") or {}
    for entry in policies.get("touched") or []:
        if entry.get("restored"):
            continue
        try:
            policy = graph.get(f"{POLICIES}/{entry['id']}")
            excluded = _excluded(policy)
            if group_id in excluded:
                graph.patch(f"{POLICIES}/{entry['id']}",
                            {"conditions": {"users": {"excludeGroups": [g for g in excluded if g != group_id]}}})
            entry["restored"] = True
        except GraphError as error:
            if error.status == 404:
                entry["restored"] = True
            else:
                problems.append(f"the policy '{entry.get('displayName')}' still excludes the demo users: {error}")
        except Stop as error:
            problems.append(f"the policy '{entry.get('displayName')}' still excludes the demo users: {error}")
        state.save()
    if any(entry.get("restored") for entry in policies.get("touched") or []):
        log("Removed the demo users' exclusion from the Conditional Access policies this run changed.")

    if group_id:
        removed = group.setdefault("membersRemoved", [])
        for user_id in group.get("membersAdded") or []:
            if user_id in removed:
                continue
            try:
                graph.delete(f"groups/{group_id}/members/{user_id}/$ref")
                removed.append(user_id)
            except Stop as error:
                problems.append(f"a demo user is still in the exclusion group: {error}")
            state.save()
        settled = (all(entry.get("restored") for entry in policies.get("touched") or [])
                   and set(group.get("membersAdded") or []) <= set(removed))
        if group.get("created") and not group.get("deleted") and settled:
            try:
                referenced = any(group_id in _excluded(policy) for policy in graph.pages(POLICIES))
                if referenced:
                    group["kept"] = "another policy still excludes it"
                else:
                    graph.delete(f"groups/{group_id}")
                    group["deleted"] = True
                    log(f"Deleted the security group '{GROUP_NAME}'.")
            except Stop as error:
                problems.append(f"the exclusion group could not be deleted: {error}")
            state.save()

    defaults = state.data.get("securityDefaults") or {}
    if defaults.get("disabled") and not defaults.get("restored"):
        try:
            graph.patch(SECURITY_DEFAULTS, {"isEnabled": True})
            defaults["restored"] = True
            log("Turned Security Defaults back on.")
        except Stop as error:
            problems.append(f"Security Defaults is still off (it was on): {error}")
        state.save()

    state.data["problems"] = problems
    state.data["phase"] = "restore-failed" if problems else "restored"
    state.save()
    return problems


@contextlib.contextmanager
def prepared(graph, users, state, log):
    """Prepare the tenant for the block, and always restore it afterwards."""
    try:
        prepare(graph, users, state, log)
        yield state
    finally:
        try:
            problems = restore(graph, state, log)
        except Exception as error:  # noqa: BLE001 - recorded; the run reports needs-restore
            problems = [f"restore stopped: {error}"]
            state.data.update(phase="restore-failed", problems=problems)
            state.save()
        for problem in problems:
            log(f"RESTORE PROBLEM: {problem}")
