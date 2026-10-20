"""Entra sign-ins through this deployment's demo app: the administrator by device code, demo users
by password (a public client: no secret, no assertion).

Tokens live only in the memory of the process that signed in. The administrator's one-time code
goes to an owner-only file in the run folder for the user to open; it never reaches stdout, so
an agent relaying this tool's output never sees it.
"""

import base64
from datetime import datetime, timezone
import json
import re
import time

from .common import Blocked, Stop, Waiting
from . import receipts, transport, workspace

GRAPH_SCOPE = "https://graph.microsoft.com/.default offline_access openid"
SIGN_IN_ONLY = frozenset({"offline_access", "openid", "profile", "email"})
GLOBAL_ADMINISTRATOR = "62e90394-69f5-4237-9190-012177145e10"
REFRESH_EARLY = 300
RETRY_REFRESH = 60
CONSENT_HINT = f"An administrator runs: {receipts.SETUP} --grant-m365-demo-consent"

# AADSTS codes of a failed password sign-in: (codes, status, passwordValid, hint).
USER_SIGN_IN = (
    ({50126}, "bad-password", False,
     "Wrong password for {upn}: run `users set` for this user again in your own terminal, or reset it in the tenant."),
    ({50034}, "not-found", False, "{upn} does not exist in this tenant: correct the demo's people."),
    ({50057}, "disabled", False, "{upn} is disabled: re-enable the account in the tenant."),
    ({50053}, "locked", False, "{upn} is locked after too many sign-in attempts: wait or have an administrator "
     "unlock it, then check again."),
    ({50055}, "password-expired", False, "{upn}'s password has expired: reset it, then run `users set` for this user."),
    ({50072, 50074, 50076, 50079, 50158, 53003}, "ok-pending-mfa", True,
     "{upn}'s password is valid; MFA or Conditional Access blocks password sign-in right now. A seed run relaxes "
     "both for the demo users only and restores them afterwards."),
    ({65001}, "consent-needed", True,
     "{upn}'s password is valid, but the demo app has no administrator consent yet. " + CONSENT_HINT),
    ({700016}, "app-not-found", False, f"The demo app was not found in this tenant. Rerun: {receipts.SETUP}"),
    ({7000218}, "app-not-public-client", False,
     f"The demo app does not allow public-client sign-in. Rerun: {receipts.SETUP} (it restores that setting)."),
)
PASSED = frozenset({"ok", "ok-pending-mfa"})


def claims(token):
    """The payload of a JWT, or {} when the token is not a readable JWT."""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        value = json.loads(base64.urlsafe_b64decode((parts[1] + "=" * (-len(parts[1]) % 4)).encode("ascii")))
    except (ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def scope_names(value):
    """Lowercase scope names without a resource prefix: 'https://graph.microsoft.com/User.Read' -> 'user.read'."""
    items = value.split() if isinstance(value, str) else list(value or [])
    return {str(item).rsplit("/", 1)[-1].lower() for item in items if item}


def oauth_error(response):
    """(error, first description line, AADSTS codes) of a failed token-endpoint answer."""
    body = response.json(default={})
    body = body if isinstance(body, dict) else {}
    lines = str(body.get("error_description") or "").strip().splitlines()
    description = lines[0].strip() if lines else f"HTTP {response.status}"
    codes = [int(code) for code in body.get("error_codes") or [] if str(code).isdigit()]
    if not codes:
        codes = [int(code) for code in re.findall(r"AADSTS(\d+)", " ".join(lines))]
    return str(body.get("error") or ""), description, codes


def classify(codes, description, upn):
    for known, status, valid, hint in USER_SIGN_IN:
        if known & set(codes):
            return {"status": status, "passwordValid": valid, "reason": f"AADSTS{min(known & set(codes))}",
                    "hint": hint.format(upn=upn)}
    return {"status": "error", "passwordValid": False, "reason": "unknown", "hint": f"Sign-in failed for {upn}: {description}"}


def authority(tenant):
    return f"{transport.login_url()}/{tenant}/oauth2/v2.0"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class Session:
    """One account's Microsoft Graph tokens, renewed in memory for the length of a run."""

    def __init__(self, tenant, client, tokens, *, account=None, clock=time.time):
        self.tenant, self.client, self.account = tenant, client, account
        self.user_id = None
        self.warnings = []
        self._clock = clock
        self._refresh = None
        self._retry_at = 0
        self._take(tokens)

    def _take(self, tokens):
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise Blocked("no-token", "The sign-in returned no access token.")
        self._access = str(tokens["access_token"])
        self._refresh = tokens.get("refresh_token") or self._refresh
        self.expires_at = self._clock() + max(60, int(tokens.get("expires_in") or 3600))
        self.scopes = scope_names(tokens.get("scope")) or scope_names(claims(self._access).get("scp"))
        self.claims = claims(self._access)

    def token(self):
        now = self._clock()
        if now < self.expires_at - REFRESH_EARLY or (now < self._retry_at and now < self.expires_at):
            return self._access
        try:
            self.renew()
        except (Stop, transport.TransportError) as error:
            if now < self.expires_at:
                self._retry_at = now + RETRY_REFRESH
                return self._access
            raise Blocked("session-expired", f"The sign-in of {self.account} expired and could not be renewed: "
                          f"{getattr(error, 'message', error)}") from None
        return self._access

    def renew(self):
        if not self._refresh:
            raise Blocked("no-refresh-token", f"The sign-in of {self.account} cannot be renewed (no offline_access).")
        answer = transport.request("POST", f"{authority(self.tenant)}/token", form={
            "grant_type": "refresh_token", "client_id": self.client, "refresh_token": self._refresh,
            "scope": GRAPH_SCOPE})
        if not answer.ok:
            raise Blocked("refresh-failed", oauth_error(answer)[1])
        self._take(answer.json(default={}))
        self._retry_at = 0


def require_consent(receipt):
    if receipt.get("consent") not in ("granted", "unverified"):
        raise Waiting("consent-missing", "Administrator consent for the demo app is not confirmed.", next=[CONSENT_HINT])


def start_device_code(receipt, code_file, *, account=None):
    """Request a device code and write its instructions to an owner-only file; return the device answer."""
    require_consent(receipt)
    try:
        answer = transport.request("POST", f"{authority(receipt['tenant_id'])}/devicecode",
                                   form={"client_id": receipt["client_id"], "scope": GRAPH_SCOPE})
    except transport.TransportError as error:
        raise Blocked("network", f"Could not start the administrator sign-in: {error}") from None
    if not answer.ok:
        _, description, codes = oauth_error(answer)
        verdict = classify(codes, description, account or "the administrator")
        raise Blocked(verdict["status"] if verdict["status"] != "error" else "sign-in-failed", verdict["hint"])
    device = answer.json(default={})
    if not isinstance(device, dict) or not all(device.get(key) for key in ("device_code", "message", "verification_uri")):
        raise Blocked("sign-in-failed", "The device-code answer was incomplete.")
    who = f" as {account}" if account else " as a tenant administrator"
    workspace.write_private(code_file, f"Microsoft 365 demo seeding: sign in{who} (tenant {receipt['tenant_id']}).\n"
                            f"{device['message']}\n")
    return device


def poll_device_code(receipt, device, code_file, *, timeout=900, sleep=time.sleep, clock=time.time):
    """Wait for the person to finish the device-code sign-in; the code file is removed either way."""
    deadline = clock() + max(1, min(int(timeout), int(device.get("expires_in") or 900)))
    requested = device.get("interval")
    interval = max(0 if transport.testing() else 1, int(requested) if requested is not None else 5)
    form = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": receipt["client_id"],
            "device_code": device["device_code"]}
    try:
        while True:
            if clock() >= deadline:
                raise Waiting("sign-in-expired", "The administrator sign-in did not finish in time; start the run again.")
            sleep(interval)
            try:
                answer = transport.request("POST", f"{authority(receipt['tenant_id'])}/token", form=form)
            except transport.TransportError:
                continue
            if answer.ok:
                return answer.json(default={})
            error, description, codes = oauth_error(answer)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error in ("authorization_declined", "access_denied"):
                raise Waiting("sign-in-declined", "The administrator sign-in was declined.")
            if error in ("expired_token", "code_expired"):
                raise Waiting("sign-in-expired", "The one-time code expired; start the run again.")
            if 65001 in codes or error in ("consent_required", "interaction_required"):
                raise Waiting("consent-missing", f"The administrator sign-in needs consent: {description}", next=[CONSENT_HINT])
            raise Blocked("sign-in-failed", description)
    finally:
        workspace.remove(code_file, scrub=True)


def admin_session(receipt, tokens, *, expected_upn=None, me=None, clock=time.time):
    """Validate a fresh administrator sign-in: tenant, app, account and consented scopes.

    `me` reads /me with the new session (graph.me); it names the signed-in account from the
    tenant itself, so the check does not depend on the access token being readable.
    """
    tokens = tokens if isinstance(tokens, dict) else {}
    access, identity = claims(tokens.get("access_token")), claims(tokens.get("id_token"))
    tenant = str(access.get("tid") or identity.get("tid") or "").lower()
    if tenant != receipt["tenant_id"]:
        raise Blocked("wrong-tenant", f"The sign-in is not for the deployment tenant {receipt['tenant_id']}.")
    app = str(access.get("appid") or access.get("azp") or identity.get("aud") or "").lower()
    if app and app != receipt["client_id"]:
        raise Blocked("wrong-app", f"The sign-in is not for the demo app {receipt['client_id']}.")
    if not tokens.get("refresh_token"):
        raise Blocked("no-refresh-token", "The sign-in returned no refresh token (offline_access).")
    session = Session(receipt["tenant_id"], receipt["client_id"], tokens, clock=clock)
    profile = me(session) if me else {}
    upn = str(profile.get("userPrincipalName") or access.get("upn") or access.get("preferred_username") or "")
    if expected_upn and upn.lower() != expected_upn.lower():
        raise Waiting("wrong-account", f"Signed in as {upn or 'an unknown account'}, but the demo's administrator is "
                      f"{expected_upn}. Start the run again and sign in as {expected_upn}.")
    session.account, session.user_id = upn, profile.get("id") or access.get("oid")
    if session.scopes:
        missing = [scope for scope in receipt["delegated_scopes"]
                   if scope.lower() not in SIGN_IN_ONLY and scope.lower() not in session.scopes]
        if missing:
            raise Waiting("consent-missing", "The administrator's sign-in lacks " + ", ".join(missing) + ".",
                          next=[CONSENT_HINT])
    else:
        session.warnings.append("scopes-unverified: the sign-in did not report its scopes.")
    wids = access.get("wids")
    if isinstance(wids, list) and GLOBAL_ADMINISTRATOR not in wids:
        session.warnings.append("not-global-administrator: preparing the tenant (Conditional Access, per-user MFA, "
                                "Security Defaults) normally needs a Global Administrator.")
    if receipt.get("consent") == "unverified":
        session.warnings.append("consent-unverified: deploy.py could not read the tenant-wide grant.")
    return session


def password_sign_in(receipt, upn, password, *, clock=time.time):
    """(Session, verdict): a demo user's public-client password sign-in and its classification."""
    form = {"grant_type": "password", "client_id": receipt["client_id"], "username": upn, "password": password,
            "scope": GRAPH_SCOPE}
    try:
        answer = transport.request("POST", f"{authority(receipt['tenant_id'])}/token", form=form)
    except transport.TransportError as error:
        return None, {"status": "error", "passwordValid": False, "reason": "network", "hint": str(error)}
    if answer.ok:
        try:
            session = Session(receipt["tenant_id"], receipt["client_id"], answer.json(default={}), account=upn, clock=clock)
        except Stop as stop:
            return None, {"status": "error", "passwordValid": False, "reason": stop.reason, "hint": stop.message}
        return session, {"status": "ok", "passwordValid": True, "reason": None, "hint": f"{upn} signed in."}
    _, description, codes = oauth_error(answer)
    return None, classify(codes, description, upn)
