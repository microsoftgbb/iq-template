"""Remove what this skill wrote to this computer - its workspace - never tenant content or the demo app.

Depths:
  runs  the run folders (logs, manifests, presenter guides, sign-in residue); keeps demos and passwords;
  all   the run folders, the demos and the stored demo-user passwords, then the workspace folder itself.

Nothing is removed without --confirm CLEANUP-<DEPTH>; without it the command only lists what it would
remove. A run folder is always kept while its tenant settings still need a restore (it is how to
restore them), and kept while the content it seeded is still in the tenant (its manifest is how to
clean that up) unless --include-uncleaned. The demo app belongs to the deployment's Terraform state.
"""

import os
from pathlib import Path
import shutil
import sys

from .common import CLI, Blocked, Usage, Waiting
from . import runs, tenant, workspace

DEPTHS = ("runs", "all")
KNOWN = {"runs", "demos", "demo-users.json", "run.lock"}


def token(depth):
    return f"CLEANUP-{depth.upper()}"


def _size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def inventory(depth="runs", include_uncleaned=False):
    if depth not in DEPTHS:
        raise Usage("bad-depth", f"--depth must be one of {', '.join(DEPTHS)}.")
    home = workspace.home()
    report = {"home": str(home), "depth": depth, "confirmToken": token(depth), "remove": [], "kept": [], "foreign": []}
    if not home.is_dir():
        report["summary"] = "There is nothing to clean up on this computer."
        return report
    active = workspace.read_lock()
    if active:
        raise Blocked("another-run", f"A run is active ({active.get('runId')}); clean up after it ends.",
                      activeRun=active.get("runId"))
    root = workspace.runs_dir()
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if not folder.is_dir() or not runs.RUN_ID.match(folder.name):
            report["foreign"].append(str(folder))
            continue
        run = runs.Run(folder.name)
        entry = {"path": str(folder), "runId": run.id, "bytes": _size(folder)}
        if tenant.State(run.path("tenant_state.json")).needs_restore:
            report["kept"].append({**entry, "reason": "its tenant settings are not restored yet: restore them first"})
        elif run.path("run.json").is_file() and run.info().get("kind") == "seed" and runs.created_items(run) \
                and not include_uncleaned:
            report["kept"].append({**entry, "reason": "the content it seeded is still in the tenant: clean it up first, "
                                                      "or add --include-uncleaned to give up that cleanup"})
        else:
            report["remove"].append({**entry, "what": "run"})
    if depth == "all":
        for name, what in (("demos", "demos"), ("demo-users.json", "stored demo-user passwords"),
                           ("run.lock", "stale run lock")):
            path = home / name
            if path.exists():
                report["remove"].append({"path": str(path), "what": what, "bytes": _size(path)})
    for entry in sorted(home.iterdir()):
        if entry.name not in KNOWN and not entry.name.startswith("."):
            report["foreign"].append(str(entry))
    count = len(report["remove"])
    report["summary"] = (f"{count} item(s) to remove ({sum(e['bytes'] for e in report['remove'])} bytes); "
                         f"{len(report['kept'])} run folder(s) kept." if count else "Nothing to remove at this depth.")
    if count:
        report["next"] = [f"After the user confirms: {CLI} local-cleanup --depth {depth}"
                          + (" --include-uncleaned" if include_uncleaned else "") + f" --confirm {token(depth)}"]
    return report


def _remove(path, problems):
    def failed(function, target, error):
        problems.append({"path": str(target), "locked": isinstance(error, PermissionError), "error": str(error)})

    path = Path(path)
    if path.is_dir():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=failed)
        else:
            shutil.rmtree(path, onerror=lambda function, target, info: failed(function, target, info[1]))
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            failed(os.unlink, path, error)


def execute(depth, confirm, include_uncleaned=False):
    if confirm != token(depth):
        raise Usage("bad-confirm", f"--confirm must be {token(depth)} (from the inventory) after the user agreed.")
    report = inventory(depth, include_uncleaned)
    problems, removed = [], []
    for entry in report["remove"]:
        before = len(problems)
        _remove(entry["path"], problems)
        if len(problems) == before:
            removed.append(entry["path"])
    home = workspace.home()
    if depth == "all" and home.is_dir() and not report["foreign"] and not report["kept"] and not problems:
        runs_root = workspace.runs_dir()
        if runs_root.is_dir() and not any(runs_root.iterdir()):
            runs_root.rmdir()
        leftovers = [item for item in home.iterdir()]
        if all(item.name.startswith(".") and item.is_file() for item in leftovers):
            for item in leftovers:
                _remove(item, problems)
            if not problems:
                home.rmdir()
                removed.append(str(home))
    result = {"home": report["home"], "depth": depth, "removed": removed, "kept": report["kept"],
              "foreign": report["foreign"], "locked": [p["path"] for p in problems if p["locked"]],
              "errors": [p for p in problems if not p["locked"]]}
    if problems:
        raise Waiting("partial", f"{len(removed)} item(s) removed; {len(problems)} file(s) could not be removed. Close "
                      "any open seeded files or guides (Windows keeps open files) and run the same command again.",
                      **{k: v for k, v in result.items() if k != "home"})
    return {**result, "summary": f"Removed {len(removed)} item(s) from this computer."}
