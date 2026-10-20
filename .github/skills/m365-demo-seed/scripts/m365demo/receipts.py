"""This deployment's demo app, from the deploy-platform runner's receipts.

`deploy.py status --json` reports receipts only (no cloud calls); its `m365_demo` section names
the tenant, the delegated-only app and the administrator consent state. It is null until the
runner's optional `m365-demo` phase has run (deploy.py up --with-m365-demo).
"""

import json
from pathlib import Path
import re
import subprocess
import sys

from .common import PYTHON, Blocked

REPO_ROOT = Path(__file__).resolve().parents[5]
DEPLOY = REPO_ROOT / ".github" / "skills" / "deploy-platform" / "scripts" / "deploy.py"
GUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
SETUP = f"{PYTHON} .github/skills/deploy-platform/scripts/deploy.py up --with-m365-demo --through m365-demo"


def _status_text(receipts_file):
    if receipts_file:
        try:
            return Path(receipts_file).read_text(encoding="utf-8-sig")
        except OSError as error:
            raise Blocked("receipts-unreadable", f"Could not read {receipts_file}: {error.strerror}.") from None
    if not DEPLOY.is_file():
        raise Blocked("no-runner", f"The deploy-platform runner was not found at {DEPLOY}: run this skill from the "
                      "deployment's checkout.")
    try:
        result = subprocess.run([sys.executable, str(DEPLOY), "status", "--json"], capture_output=True,
                                text=True, timeout=120, cwd=REPO_ROOT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Blocked("receipts-unreadable", f"deploy.py status failed: {error}.") from None
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise Blocked("receipts-unreadable", f"deploy.py status --json failed: {detail[0]}")
    return result.stdout


def load(receipts_file=None):
    """The m365_demo receipt as {tenant_id, client_id, delegated_scopes, consent, checked_at}, or None."""
    text = _status_text(receipts_file)
    try:
        status = json.loads(text)
    except ValueError:
        raise Blocked("receipts-unreadable", "deploy.py status --json did not return JSON.") from None
    demo = status.get("m365_demo") if isinstance(status, dict) else None
    if not demo:
        return None
    tenant, client = str(demo.get("tenant_id") or ""), str(demo.get("client_id") or "")
    if not GUID.match(tenant) or not GUID.match(client):
        raise Blocked("receipts-invalid", "The m365_demo receipt has no valid tenant_id and client_id; rerun the "
                      f"runner: {SETUP}")
    return {"tenant_id": tenant.lower(), "client_id": client.lower(),
            "delegated_scopes": sorted(demo.get("delegated_scopes") or []),
            "consent": demo.get("consent") or "unknown", "checked_at": demo.get("checked_at")}


def require(receipts_file=None):
    receipt = load(receipts_file)
    if not receipt:
        raise Blocked("not-set-up", "Microsoft 365 demo seeding is not set up for this deployment yet.",
                      next=[SETUP])
    return receipt
