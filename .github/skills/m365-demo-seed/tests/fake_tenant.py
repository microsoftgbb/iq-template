"""A fake Entra token endpoint and Microsoft 365 (Graph) on 127.0.0.1 for the offline tests.

Tokens are unsigned JWTs (alg none) carrying the claims the tool reads. The tenant model is small
but behaves like the real thing where the tool depends on it: password sign-in is refused while
Security Defaults, per-user MFA or an enabled Conditional Access policy applies to the user; mail
is delivered to recipients' mailboxes; new groups, teams, chats and channel rosters can be told
to lag; an admin who creates a Microsoft 365 group becomes its owner. Requests are recorded with
any password masked, so tests can assert exactly what went over the wire.
"""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import re
import threading
import urllib.parse
import uuid

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
OTHER = "99999999-9999-4999-8999-999999999999"
ADMIN = "admin@contoso-demo.example"
GLOBAL_ADMINISTRATOR = "62e90394-69f5-4237-9190-012177145e10"
SCOPES = ["Calendars.ReadWrite", "Channel.ReadBasic.All", "ChannelMessage.Send", "Chat.Create", "Chat.ManageDeletion.All",
          "Chat.ReadWrite", "ChatMessage.Send", "Files.ReadWrite", "Group.ReadWrite.All", "Mail.ReadWrite", "Mail.Send",
          "Policy.Read.All", "Policy.ReadWrite.AuthenticationMethod", "Policy.ReadWrite.ConditionalAccess",
          "Team.ReadBasic.All", "User.Read", "User.Read.All", "offline_access", "openid"]
E5_PLANS = [{"servicePlanName": "EXCHANGE_S_ENTERPRISE", "provisioningStatus": "Success"},
            {"servicePlanName": "SHAREPOINTENTERPRISE", "provisioningStatus": "Success"},
            {"servicePlanName": "SHAREPOINTWAC", "provisioningStatus": "Success"},
            {"servicePlanName": "TEAMS1", "provisioningStatus": "Success"}]
FILTER = re.compile(r"^(\w+) eq '((?:[^']|'')*)'$")
BIND = re.compile(r"users\('([^']+)'\)")


def fake_jwt(claims):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


class Reply:
    def __init__(self, status=200, body=None, headers=None, raw=None, drop=False):
        """drop: close the connection without answering, as a timeout after the server processed the request."""
        self.status, self.body, self.headers, self.raw, self.drop = status, body, headers or {}, raw, drop


def oauth_failure(error, codes=(), description=None, status=400):
    lines = description or (f"AADSTS{codes[0]}: Simulated failure." if codes else f"{error}: simulated.")
    return Reply(status, {"error": error, "error_description": lines + "\r\nTrace ID: 0\r\nTimestamp: now",
                          "error_codes": list(codes)})


def graph_error(status, code, message):
    return Reply(status, {"error": {"code": code, "message": message}})


def lag(counter, key):
    """True (and one less to go) while a simulated delay for `key` lasts."""
    if counter.get(key, 0) > 0:
        counter[key] -= 1
        return True
    return False


class FakeTenant:
    def __init__(self):
        self.tenant, self.client = TENANT, CLIENT
        self.granted = list(SCOPES)
        self.users = {}
        self.by_id = {}
        self.device_script = []
        self.device_account = ADMIN
        self.device_interval = 0
        self.hold_sign_in = threading.Event()
        self.hold_sign_in.set()
        self.claims_override = {}
        self.omit_scope = False
        self.opaque = False
        self.expires_in = 3600
        self.refresh_fails = False
        self.requests = []
        self.access = {}
        self.refresh = {}
        self.ids = itertools.count(1)
        self.lock = threading.RLock()
        self.routes = []
        # Tenant security settings.
        self.security_defaults = False
        self.security_defaults_forbidden = False
        self.policies = {}
        self.policy_patch_forbidden = set()
        self.mfa = {}
        # Microsoft 365 content.
        self.groups = {}
        self.chats = {}
        self.mailboxes = {}
        self.events = {}
        self.calendars = {}
        self.drives = {}
        self.invites = []
        self.reply_without_internet_id = False
        # Simulated lag: counters of requests to refuse before the thing exists or is visible.
        self.delays = {"delivery": 0, "team": 0, "team_ready": 0, "roster": {}, "drive": {}, "chat": 0, "members": 0}
        self.meeting_chat_forbidden = False
        self.add_user(ADMIN, "Admin-Pass-1", wids=[GLOBAL_ADMINISTRATOR])
        routes = [
            ("POST", r"^/(?P<tenant>[^/]+)/oauth2/v2\.0/devicecode$", self.devicecode),
            ("POST", r"^/(?P<tenant>[^/]+)/oauth2/v2\.0/token$", self.token),
            ("GET", r"^/v1\.0/me$", self.me),
            ("GET", r"^/v1\.0/me/licenseDetails$", self.license_details),
            ("GET", r"^/v1\.0/users$", self.list_users),
            ("GET", r"^/v1\.0/policies/identitySecurityDefaultsEnforcementPolicy$", self.get_defaults),
            ("PATCH", r"^/v1\.0/policies/identitySecurityDefaultsEnforcementPolicy$", self.patch_defaults),
            ("GET", r"^/v1\.0/identity/conditionalAccess/policies$", self.list_policies),
            ("GET", r"^/v1\.0/identity/conditionalAccess/policies/(?P<pid>[^/]+)$", self.get_policy),
            ("PATCH", r"^/v1\.0/identity/conditionalAccess/policies/(?P<pid>[^/]+)$", self.patch_policy),
            ("GET", r"^/beta/users/(?P<uid>[^/]+)/authentication/requirements$", self.get_mfa),
            ("PATCH", r"^/beta/users/(?P<uid>[^/]+)/authentication/requirements$", self.patch_mfa),
            ("GET", r"^/v1\.0/groups$", self.list_groups),
            ("POST", r"^/v1\.0/groups$", self.create_group),
            ("DELETE", r"^/v1\.0/groups/(?P<gid>[^/]+)$", self.delete_group),
            ("GET", r"^/v1\.0/groups/(?P<gid>[^/]+)/(?P<rel>members|owners)$", self.list_relation),
            ("POST", r"^/v1\.0/groups/(?P<gid>[^/]+)/(?P<rel>members|owners)/\$ref$", self.add_relation),
            ("DELETE", r"^/v1\.0/groups/(?P<gid>[^/]+)/(?P<rel>members|owners)/(?P<uid>[^/]+)/\$ref$",
             self.remove_relation),
            ("PUT", r"^/v1\.0/groups/(?P<gid>[^/]+)/team$", self.create_team),
            ("GET", r"^/v1\.0/teams/(?P<gid>[^/]+)$", self.get_team),
            ("GET", r"^/v1\.0/teams/(?P<gid>[^/]+)/primaryChannel$", self.primary_channel),
            ("GET", r"^/v1\.0/teams/(?P<gid>[^/]+)/channels$", self.list_channels),
            ("POST", r"^/v1\.0/teams/(?P<gid>[^/]+)/channels$", self.create_channel),
            ("POST", r"^/v1\.0/teams/(?P<gid>[^/]+)/channels/(?P<cid>[^/]+)/messages$", self.channel_post),
            ("POST", r"^/v1\.0/teams/(?P<gid>[^/]+)/channels/(?P<cid>[^/]+)/messages/(?P<mid>[^/]+)/replies$",
             self.channel_reply),
            ("POST", r"^/v1\.0/chats$", self.create_chat),
            ("POST", r"^/v1\.0/chats/(?P<cid>[^/]+)/messages$", self.chat_post),
            ("POST", r"^/v1\.0/users/(?P<uid>[^/]+)/chats/(?P<cid>[^/]+)/messages/(?P<mid>[^/]+)/softDelete$",
             self.soft_delete),
            ("DELETE", r"^/v1\.0/chats/(?P<cid>[^/]+)$", self.delete_chat),
            ("POST", r"^/v1\.0/me/messages$", self.create_draft),
            ("GET", r"^/v1\.0/me/messages$", self.find_messages),
            ("GET", r"^/v1\.0/me/mailFolders/sentitems/messages$", self.find_sent),
            ("GET", r"^/v1\.0/me/messages/(?P<mid>[^/]+)$", self.get_message),
            ("PATCH", r"^/v1\.0/me/messages/(?P<mid>[^/]+)$", self.patch_message),
            ("POST", r"^/v1\.0/me/messages/(?P<mid>[^/]+)/send$", self.send_message),
            ("POST", r"^/v1\.0/me/messages/(?P<mid>[^/]+)/createReplyAll$", self.create_reply_all),
            ("POST", r"^/v1\.0/me/events$", self.create_event),
            ("GET", r"^/v1\.0/me/events$", self.list_events),
            ("DELETE", r"^/v1\.0/me/events/(?P<eid>[^/]+)$", self.delete_event),
            ("DELETE", r"^/v1\.0/me/messages/(?P<mid>[^/]+)$", self.delete_message),
            ("DELETE", r"^/v1\.0/me/drive/items/(?P<iid>[^/]+)$", self.delete_item),
            ("GET", r"^/v1\.0/me/drive$", self.get_drive),
            ("PUT", r"^/v1\.0/me/drive/root:/(?P<path>.+):/content$", self.upload),
            ("POST", r"^/v1\.0/me/drive/items/(?P<iid>[^/]+)/invite$", self.invite),
        ]
        for method, pattern, handler in routes:
            self.route(method, pattern, handler)

    # --- lifecycle -------------------------------------------------------------------------
    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                fake.dispatch(self)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.hold_sign_in.set()
        self.server.shutdown()
        self.server.server_close()

    def env(self):
        return {"M365_DEMO_LOGIN_URL": self.url, "M365_DEMO_GRAPH_URL": self.url}

    def receipt(self, **overrides):
        value = {"tenant_id": self.tenant, "client_id": self.client, "service_principal_object_id": OTHER,
                 "api_permissions_url": "https://portal.azure.com/", "delegated_scopes": sorted(SCOPES),
                 "consent": "granted", "checked_at": "2026-01-01T00:00:00+00:00"}
        value.update(overrides)
        return value

    def receipts_file(self, path, **overrides):
        status = {"result": "status", "phases": {}, "m365_demo": self.receipt(**overrides)}
        path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        return path

    # --- tenant model ----------------------------------------------------------------------
    def add_user(self, upn, password, *, error=None, plans=None, wids=None, name=None, enabled=True):
        user = {"upn": upn.lower(), "password": password, "error": error, "wids": wids or [], "enabled": enabled,
                "plans": E5_PLANS if plans is None else plans, "id": f"00000000-0000-4000-8000-{len(self.users) + 1:012d}",
                "displayName": name or upn.split("@")[0].title()}
        self.users[user["upn"]] = user
        self.by_id[user["id"]] = user
        return user

    def add_policy(self, name, state="enabled", exclude_groups=()):
        policy_id = str(uuid.uuid4())
        self.policies[policy_id] = {"id": policy_id, "displayName": name, "state": state, "conditions": {
            "users": {"includeUsers": ["All"], "excludeUsers": [], "excludeGroups": list(exclude_groups)},
            "applications": {"includeApplications": ["All"]}}}
        return policy_id

    def route(self, method, pattern, handler):
        self.routes.insert(0, (method, re.compile(pattern), handler))

    def mfa_blocks(self, user):
        if self.security_defaults or self.mfa.get(user["id"]) in ("enabled", "enforced"):
            return True
        for policy in self.policies.values():
            if policy["state"] != "enabled":
                continue
            excluded = policy["conditions"]["users"].get("excludeGroups") or []
            if not any(user["id"] in self.groups.get(g, {}).get("members", []) for g in excluded):
                return True
        return False

    def issue(self, upn):
        user = self.users[upn]
        serial = next(self.ids)
        claims = {"aud": "https://graph.microsoft.com", "tid": self.tenant, "appid": self.client, "upn": user["upn"],
                  "oid": user["id"], "scp": " ".join(s for s in self.granted if s not in ("offline_access", "openid")),
                  "wids": user["wids"], "serial": serial, **self.claims_override}
        access = f"opaque-access-{serial}" if self.opaque else fake_jwt(claims)
        refresh = f"rt-{serial}"
        self.access[access] = upn
        self.refresh[refresh] = upn
        body = {"token_type": "Bearer", "expires_in": self.expires_in, "access_token": access, "refresh_token": refresh,
                "id_token": fake_jwt({"aud": claims["appid"], "tid": claims["tid"], "preferred_username": user["upn"]})}
        if not self.omit_scope:
            body["scope"] = " ".join(f"https://graph.microsoft.com/{s}" if s not in ("offline_access", "openid") else s
                                     for s in self.granted)
        return Reply(200, body)

    def caller(self, request):
        header = request["headers"].get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        return self.access.get(token)

    def new_id(self, prefix="id"):
        return f"{prefix}-{next(self.ids)}"

    def page(self, items, request, path, size=None):
        if not size or len(items) <= size:
            return Reply(200, {"value": items})
        skip = int(request["query"].get("$skiptoken", "0"))
        body = {"value": items[skip:skip + size]}
        if skip + size < len(items):
            body["@odata.nextLink"] = f"{self.url}{path}?$skiptoken={skip + size}"
        return Reply(200, body)

    @staticmethod
    def filter_of(request):
        match = FILTER.match(request["query"].get("$filter", ""))
        return (match.group(1), match.group(2).replace("''", "'")) if match else (None, None)

    @staticmethod
    def bound_id(value):
        tail = str(value).rstrip("/").rsplit("/", 1)[-1]
        match = BIND.search(str(value))
        return match.group(1) if match else tail

    # --- sign-in endpoints -----------------------------------------------------------------
    def devicecode(self, request, tenant):
        if request["form"].get("client_id") != self.client:
            return oauth_failure("unauthorized_client", [700016])
        return Reply(200, {"device_code": "device-code-1", "user_code": "FAKE-CODE-42",
                           "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900,
                           "interval": self.device_interval,
                           "message": "To sign in, open https://microsoft.com/devicelogin and enter FAKE-CODE-42."})

    def token(self, request, tenant):
        form = request["form"]
        if form.get("client_id") != self.client:
            return oauth_failure("unauthorized_client", [700016])
        grant = form.get("grant_type")
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            if not self.hold_sign_in.is_set():
                return oauth_failure("authorization_pending")
            outcome = self.device_script.pop(0) if self.device_script else "ok"
            if outcome == "ok":
                return self.issue(self.device_account)
            errors = {"pending": "authorization_pending", "slow_down": "slow_down", "declined": "authorization_declined",
                      "expired": "expired_token", "consent": "invalid_grant"}
            return oauth_failure(errors[outcome], [65001] if outcome == "consent" else [])
        if grant == "password":
            user = self.users.get(str(form.get("username", "")).lower())
            if not user:
                return oauth_failure("invalid_grant", [50034])
            if user["error"]:
                return oauth_failure("invalid_grant", [user["error"]])
            if form.get("password") != user["password"]:
                return oauth_failure("invalid_grant", [50126])
            if self.mfa_blocks(user):
                return oauth_failure("invalid_grant", [50076])
            return self.issue(user["upn"])
        if grant == "refresh_token":
            upn = self.refresh.pop(form.get("refresh_token"), None)
            if not upn or self.refresh_fails:
                return oauth_failure("invalid_grant", [70043])
            return self.issue(upn)
        return oauth_failure("unsupported_grant_type")

    # --- directory and security ------------------------------------------------------------
    def me(self, request):
        user = self.users[request["caller"]]
        return Reply(200, {"id": user["id"], "userPrincipalName": user["upn"], "displayName": user["displayName"]})

    def license_details(self, request):
        return Reply(200, {"value": [{"skuPartNumber": "SPE_E5", "servicePlans": self.users[request["caller"]]["plans"]}]})

    def list_users(self, request):
        field, value = self.filter_of(request)
        user = self.users.get(value.lower()) if field == "userPrincipalName" else None
        return Reply(200, {"value": [{"id": user["id"], "userPrincipalName": user["upn"], "displayName": user["displayName"],
                                      "accountEnabled": user["enabled"]}] if user else []})

    def get_defaults(self, request):
        return Reply(200, {"id": "00000000-0000-0000-0000-000000000005", "isEnabled": self.security_defaults})

    def patch_defaults(self, request):
        if self.security_defaults_forbidden:
            return graph_error(403, "Authorization_RequestDenied", "Insufficient privileges.")
        self.security_defaults = bool(request["json"]["isEnabled"])
        return Reply(204)

    def list_policies(self, request):
        return self.page([json.loads(json.dumps(p)) for p in self.policies.values()], request,
                         "/v1.0/identity/conditionalAccess/policies", size=2)

    def get_policy(self, request, pid):
        policy = self.policies.get(pid)
        return Reply(200, json.loads(json.dumps(policy))) if policy else graph_error(404, "NotFound", "No policy.")

    def patch_policy(self, request, pid):
        if pid in self.policy_patch_forbidden:
            return graph_error(403, "Forbidden", "This policy cannot be changed.")
        policy = self.policies.get(pid)
        if not policy:
            return graph_error(404, "NotFound", "No policy.")
        users = ((request["json"] or {}).get("conditions") or {}).get("users") or {}
        policy["conditions"]["users"].update(users)
        return Reply(204)

    def get_mfa(self, request, uid):
        return Reply(200, {"perUserMfaState": self.mfa.get(uid, "disabled")})

    def patch_mfa(self, request, uid):
        self.mfa[uid] = request["json"]["perUserMfaState"]
        return Reply(204)

    # --- groups and teams ------------------------------------------------------------------
    def list_groups(self, request):
        field, value = self.filter_of(request)
        found = [g for g in self.groups.values() if field in ("displayName", "mailNickname") and g[field] == value]
        return Reply(200, {"value": [{k: g[k] for k in ("id", "displayName", "securityEnabled", "mailEnabled")}
                                     for g in found]})

    def create_group(self, request):
        body = request["json"]
        group_id = str(uuid.uuid4())
        caller = self.users[request["caller"]]
        unified = "Unified" in (body.get("groupTypes") or [])
        self.groups[group_id] = {"id": group_id, "displayName": body["displayName"], "mailNickname": body["mailNickname"],
                                 "mailEnabled": body.get("mailEnabled"), "securityEnabled": body.get("securityEnabled"),
                                 "groupTypes": body.get("groupTypes") or [],
                                 "owners": [caller["id"]] if unified and caller["wids"] else [], "members": [],
                                 "team": None, "deleted": False,
                                 "resourceBehaviorOptions": body.get("resourceBehaviorOptions") or []}
        return Reply(201, {"id": group_id, "displayName": body["displayName"]})

    def delete_group(self, request, gid):
        if gid not in self.groups:
            return graph_error(404, "Request_ResourceNotFound", "No group.")
        if any(gid in p["conditions"]["users"].get("excludeGroups", []) for p in self.policies.values()):
            return graph_error(400, "Request_BadRequest", "The group is used by a Conditional Access policy.")
        self.groups[gid]["deleted"] = True
        del self.groups[gid]
        return Reply(204)

    def list_relation(self, request, gid, rel):
        group = self.groups.get(gid)
        if not group or lag(self.delays, "members"):
            return graph_error(404, "Request_ResourceNotFound", "No group.")
        return Reply(200, {"value": [{"id": member} for member in group[rel]]})

    def add_relation(self, request, gid, rel):
        group = self.groups.get(gid)
        if not group:
            return graph_error(404, "Request_ResourceNotFound", "No group.")
        member = self.bound_id(request["json"]["@odata.id"])
        if member in group[rel]:
            return graph_error(400, "Request_BadRequest", "One or more added object references already exist for the "
                               f"following modified properties: '{rel}'.")
        group[rel].append(member)
        return Reply(204)

    def remove_relation(self, request, gid, rel, uid):
        group = self.groups.get(gid)
        if not group or uid not in group[rel]:
            return graph_error(404, "Request_ResourceNotFound", "Not a member.")
        group[rel].remove(uid)
        return Reply(204)

    def create_team(self, request, gid):
        group = self.groups.get(gid)
        if not group or lag(self.delays, "team"):
            return graph_error(404, "NotFound", "Failed to execute Templates backend request.")
        if not group["owners"]:
            return graph_error(400, "BadRequest", "The group has no owner.")
        if group["team"]:
            return graph_error(409, "Conflict", "The group already has a team.")
        general = f"19:general-{gid[:8]}@thread.tacv2"
        group["team"] = {"settings": request["json"], "primary": general,
                         "channels": {general: {"id": general, "displayName": "General", "messages": []}}}
        return Reply(201, {"id": gid})

    def team_of(self, gid):
        group = self.groups.get(gid)
        return group["team"] if group else None

    def get_team(self, request, gid):
        if not self.team_of(gid) or lag(self.delays, "team_ready"):
            return graph_error(404, "NotFound", "No team.")
        return Reply(200, {"id": gid})

    def primary_channel(self, request, gid):
        team = self.team_of(gid)
        return Reply(200, {"id": team["primary"], "displayName": "General"}) if team else graph_error(404, "NotFound", "")

    def list_channels(self, request, gid):
        team = self.team_of(gid)
        if not team:
            return graph_error(404, "NotFound", "No team.")
        return Reply(200, {"value": [{"id": c["id"], "displayName": c["displayName"]} for c in team["channels"].values()]})

    def create_channel(self, request, gid):
        team = self.team_of(gid)
        if not team:
            return graph_error(404, "NotFound", "No team.")
        channel_id = f"19:{uuid.uuid4().hex}@thread.tacv2"
        team["channels"][channel_id] = {"id": channel_id, "displayName": request["json"]["displayName"],
                                        "description": request["json"].get("description"), "messages": []}
        return Reply(201, {"id": channel_id, "displayName": request["json"]["displayName"]})

    def roster_check(self, request, gid):
        group = self.groups.get(gid)
        user = self.users[request["caller"]]
        if not group or not group["team"]:
            return graph_error(404, "NotFound", "No team.")
        if user["id"] not in group["members"] + group["owners"]:
            return graph_error(403, "Forbidden", "AclCheckFailed: not a member of the team.")
        if lag(self.delays["roster"], user["upn"]):
            return graph_error(403, "Forbidden", "AclCheckFailed: roster not propagated.")
        return None

    def channel_post(self, request, gid, cid):
        denied = self.roster_check(request, gid)
        if denied:
            return denied
        channel = self.team_of(gid)["channels"].get(cid)
        if not channel:
            return graph_error(404, "NotFound", "No channel.")
        message = {"id": self.new_id("msg"), "from": request["caller"], "subject": request["json"].get("subject"),
                   "body": request["json"]["body"], "replies": []}
        channel["messages"].append(message)
        return Reply(201, {"id": message["id"]})

    def channel_reply(self, request, gid, cid, mid):
        denied = self.roster_check(request, gid)
        if denied:
            return denied
        channel = self.team_of(gid)["channels"].get(cid)
        message = next((m for m in channel["messages"] if m["id"] == mid), None) if channel else None
        if not message:
            return graph_error(404, "NotFound", "No message.")
        reply = {"id": self.new_id("reply"), "from": request["caller"], "body": request["json"]["body"]}
        message["replies"].append(reply)
        return Reply(201, {"id": reply["id"]})

    # --- chats -----------------------------------------------------------------------------
    def create_chat(self, request):
        body = request["json"]
        members = sorted(self.bound_id(m["user@odata.bind"]) for m in body["members"])
        caller = self.users[request["caller"]]
        if caller["id"] not in members:
            return graph_error(403, "Forbidden", "The caller must be a member of the chat.")
        if body["chatType"] == "oneOnOne":
            if len(members) != 2:
                return graph_error(400, "BadRequest", "A one-on-one chat has two members.")
            for chat in self.chats.values():
                if chat["chatType"] == "oneOnOne" and chat["members"] == members:
                    return Reply(201, {"id": chat["id"], "chatType": "oneOnOne"})
        chat_id = f"19:{uuid.uuid4().hex}@thread.v2"
        self.chats[chat_id] = {"id": chat_id, "chatType": body["chatType"], "topic": body.get("topic"),
                               "members": members, "messages": [], "deleted": False}
        return Reply(201, {"id": chat_id, "chatType": body["chatType"], "topic": body.get("topic")})

    def chat_post(self, request, cid):
        chat = self.chats.get(cid)
        user = self.users[request["caller"]]
        if not chat or lag(self.delays, "chat"):
            return graph_error(404, "NotFound", "No chat.")
        if chat["chatType"] == "meeting" and self.meeting_chat_forbidden:
            return graph_error(403, "Forbidden", "The meeting chat is not available yet.")
        if user["id"] not in chat["members"]:
            return graph_error(403, "Forbidden", "Not a member of the chat.")
        message = {"id": self.new_id("chatmsg"), "from": user["upn"], "body": request["json"]["body"]}
        chat["messages"].append(message)
        return Reply(201, {"id": message["id"]})

    def delete_chat(self, request, cid):
        if not self.users[request["caller"]]["wids"]:
            return graph_error(403, "Forbidden", "Only administrators can delete chats.")
        if cid not in self.chats:
            return graph_error(404, "NotFound", "No chat.")
        del self.chats[cid]
        return Reply(204)

    def soft_delete(self, request, uid, cid, mid):
        user = self.users[request["caller"]]
        chat = self.chats.get(cid)
        if uid != user["id"]:
            return graph_error(403, "Forbidden", "Only the signed-in user's own messages.")
        message = next((m for m in (chat or {}).get("messages", []) if m["id"] == mid), None)
        if not message or message.get("deleted"):
            return graph_error(404, "NotFound", "No message.")
        if message["from"] != user["upn"]:
            return graph_error(403, "Forbidden", "Not your message.")
        message["deleted"] = True
        return Reply(204)

    # --- mail ------------------------------------------------------------------------------
    def mailbox(self, upn):
        return self.mailboxes.setdefault(upn, [])

    def message(self, upn, mid):
        return next((m for m in self.mailbox(upn) if m["id"] == mid), None)

    def create_draft(self, request):
        body = request["json"]
        draft = {"id": self.new_id("mail"), "internetMessageId": f"<{uuid.uuid4().hex}@fake.example>",
                 "conversationId": body.get("conversationId") or uuid.uuid4().hex, "subject": body.get("subject"),
                 "body": body.get("body"), "toRecipients": body.get("toRecipients") or [],
                 "ccRecipients": body.get("ccRecipients") or [], "from": request["caller"], "folder": "drafts",
                 "receivedDateTime": None}
        self.mailbox(request["caller"]).append(draft)
        return Reply(201, dict(draft))

    def patch_message(self, request, mid):
        message = self.message(request["caller"], mid)
        if not message:
            return graph_error(404, "ErrorItemNotFound", "No message.")
        message.update(request["json"])
        return Reply(200, dict(message))

    def send_message(self, request, mid):
        message = self.message(request["caller"], mid)
        if not message or message["folder"] != "drafts":
            return graph_error(404, "ErrorItemNotFound", "No draft.")
        message["folder"] = "sentitems"
        stamp = f"2026-01-01T00:00:{next(self.ids) % 60:02d}Z"
        message["receivedDateTime"] = stamp
        for recipient in message["toRecipients"] + message["ccRecipients"]:
            address = recipient["emailAddress"]["address"].lower()
            copy = {**message, "id": self.new_id("mail"), "folder": "inbox", "hidden": self.delays["delivery"]}
            self.mailbox(address).append(copy)
        return Reply(202)

    def deliver(self, upn, subject, kind="message", sender="no-reply@fake.example"):
        """A notification-style message in a user's inbox (invitations, cancellations, share notices)."""
        message = {"id": self.new_id("mail"), "subject": subject, "folder": "inbox", "from": sender, "kind": kind,
                   "conversationId": uuid.uuid4().hex, "internetMessageId": f"<{uuid.uuid4().hex}@fake.example>",
                   "body": {"contentType": "html", "content": ""}, "toRecipients": [], "ccRecipients": [],
                   "receivedDateTime": f"2026-01-02T00:00:{next(self.ids) % 60:02d}Z"}
        self.mailbox(upn).append(message)
        return message

    def delete_message(self, request, mid):
        box = self.mailbox(request["caller"])
        message = self.message(request["caller"], mid)
        if not message:
            return graph_error(404, "ErrorItemNotFound", "No message.")
        box.remove(message)
        return Reply(204)

    def find_messages(self, request):
        field, value = self.filter_of(request)
        if not field:
            listed = [m for m in self.mailbox(request["caller"]) if m["folder"] != "drafts"]
            listed.sort(key=lambda m: m.get("receivedDateTime") or "", reverse=True)
            return Reply(200, {"value": [{"id": m["id"], "subject": m["subject"],
                                          **({"@odata.type": "#microsoft.graph.eventMessage"}
                                             if m.get("kind") == "event" else {})} for m in listed]})
        found = []
        for message in self.mailbox(request["caller"]):
            if message["folder"] == "drafts" or message.get(field) != value:
                continue
            if message.get("hidden"):
                message["hidden"] -= 1
                continue
            found.append({"id": message["id"], "subject": message["subject"],
                          "receivedDateTime": message["receivedDateTime"]})
        return Reply(200, {"value": found})

    def get_message(self, request, mid):
        message = self.message(request["caller"], mid)
        if not message:
            return graph_error(404, "ErrorItemNotFound", "No message.")
        return Reply(200, {"id": message["id"], "internetMessageId": message.get("internetMessageId"),
                           "conversationId": message["conversationId"]})

    def find_sent(self, request):
        field, value = self.filter_of(request)
        found = [{"id": m["id"]} for m in self.mailbox(request["caller"])
                 if m["folder"] == "sentitems" and m.get(field) == value]
        return Reply(200, {"value": found})

    def processed_then(self, handler, reply):
        """A route that runs the real handler, then answers with `reply` (a timeout or gateway error)."""
        def route(request, **parts):
            handler(request, **parts)
            return reply
        return route

    def create_reply_all(self, request, mid):
        caller = request["caller"]
        original = self.message(caller, mid)
        if not original:
            return graph_error(404, "ErrorItemNotFound", "No message.")
        everyone = [original["from"]] + [r["emailAddress"]["address"].lower() for r in original["toRecipients"]]
        to = [{"emailAddress": {"address": a}} for a in dict.fromkeys(everyone) if a != caller]
        cc = [r for r in original["ccRecipients"] if r["emailAddress"]["address"].lower() != caller]
        subject = original["subject"] if original["subject"].startswith("RE: ") else "RE: " + original["subject"]
        quoted = (f'<html><body><div id="appendonsend"></div><hr><div>From: {original["from"]}</div>'
                  f'{original["body"]["content"]}</body></html>')
        draft = {"id": self.new_id("mail"), "conversationId": original["conversationId"], "subject": subject,
                 "body": {"contentType": "html", "content": quoted}, "toRecipients": to, "ccRecipients": cc,
                 "from": caller, "folder": "drafts", "receivedDateTime": None,
                 "internetMessageId": None if self.reply_without_internet_id else f"<{uuid.uuid4().hex}@fake.example>"}
        self.mailbox(caller).append(draft)
        visible = {k: v for k, v in draft.items() if v is not None or k != "internetMessageId"}
        return Reply(201, visible)

    # --- calendar ---------------------------------------------------------------------------
    def create_event(self, request):
        body = request["json"]
        organizer = self.users[request["caller"]]
        for event in self.events.values():
            if event["organizer"] == organizer["upn"] and event.get("transactionId") == body.get("transactionId"):
                return Reply(201, event["answer"])
        thread = f"19:meeting_{uuid.uuid4().hex[:16]}@thread.v2"
        attendees = [self.users[a["emailAddress"]["address"].lower()]["id"] for a in body.get("attendees") or []]
        self.chats[thread] = {"id": thread, "chatType": "meeting", "topic": body["subject"],
                              "members": sorted([organizer["id"], *attendees]), "messages": [], "deleted": False}
        event_id = self.new_id("event")
        uid = f"040000008200E00074C5B7101A82E008{uuid.uuid4().hex}"
        join = "https://teams.microsoft.com/l/meetup-join/" + urllib.parse.quote(thread, safe="") + "/0?context=%7b%7d"
        answer = {"id": event_id, "iCalUId": uid, "subject": body["subject"], "start": body["start"], "end": body["end"],
                  "isOnlineMeeting": True, "onlineMeeting": {"joinUrl": join}}
        self.events[event_id] = {"organizer": organizer["upn"], "transactionId": body.get("transactionId"),
                                 "request": body, "answer": answer, "thread": thread, "iCalUId": uid}
        self.calendars.setdefault(organizer["upn"], {})[event_id] = {"id": event_id, "iCalUId": uid,
                                                                     "subject": body["subject"]}
        for attendee in body.get("attendees") or []:
            upn = attendee["emailAddress"]["address"].lower()
            copy = self.new_id("event")
            self.calendars.setdefault(upn, {})[copy] = {"id": copy, "iCalUId": uid, "subject": body["subject"]}
            self.deliver(upn, body["subject"], kind="event", sender=organizer["upn"])
        return Reply(201, answer)

    def list_events(self, request):
        field, value = self.filter_of(request)
        found = [{"id": e["id"], "subject": e["subject"]} for e in self.calendars.get(request["caller"], {}).values()
                 if field == "iCalUId" and e["iCalUId"] == value]
        return Reply(200, {"value": found})

    def delete_event(self, request, eid):
        calendar = self.calendars.get(request["caller"], {})
        if eid not in calendar:
            return graph_error(404, "ErrorItemNotFound", "No event.")
        item = calendar.pop(eid)
        organized = self.events.pop(eid, None)
        if organized:
            for attendee in organized["request"].get("attendees") or []:
                upn = attendee["emailAddress"]["address"].lower()
                for copy in self.calendars.get(upn, {}).values():
                    if copy["iCalUId"] == item["iCalUId"]:
                        copy["subject"] = "Canceled: " + item["subject"]
                self.deliver(upn, "Canceled: " + item["subject"], kind="event", sender=request["caller"])
        return Reply(204)

    # --- OneDrive ---------------------------------------------------------------------------
    def get_drive(self, request):
        if lag(self.delays["drive"], request["caller"]):
            return graph_error(404, "ResourceNotFound", "User's mysite not found.")
        return Reply(200, {"id": f"drive-{request['caller']}"})

    def upload(self, request, path):
        caller = request["caller"]
        files = self.drives.setdefault(caller, {})
        name = urllib.parse.unquote(path)
        item = files.get(name) or {"id": self.new_id("item"), "name": name.rsplit("/", 1)[-1], "path": name}
        item.update(size=len(request["raw"]), data=request["raw"], contentType=request["headers"].get("content-type"),
                    webUrl=f"https://contoso-my.sharepoint.com/personal/{caller}/Documents/{urllib.parse.quote(name)}")
        files[name] = item
        return Reply(201, {"id": item["id"], "name": item["name"], "webUrl": item["webUrl"], "size": item["size"]})

    def delete_item(self, request, iid):
        files = self.drives.get(request["caller"], {})
        name = next((n for n, item in files.items() if item["id"] == iid), None)
        if not name:
            return graph_error(404, "itemNotFound", "No item.")
        del files[name]
        return Reply(204)

    def invite(self, request, iid):
        self.invites.append({"from": request["caller"], "item": iid, **request["json"]})
        item = next((i for i in self.drives.get(request["caller"], {}).values() if i["id"] == iid), None)
        if item and request["json"].get("sendInvitation"):
            sharer = self.users[request["caller"]]["displayName"]
            for recipient in request["json"]["recipients"]:
                self.deliver(recipient["email"].lower(), f'{sharer} shared "{item["name"]}" with you')
        return Reply(200, {"value": [{"id": self.new_id("perm")} for _ in request["json"]["recipients"]]})

    # --- plumbing --------------------------------------------------------------------------
    def dispatch(self, handler):
        parsed = urllib.parse.urlsplit(handler.path)
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        kind = handler.headers.get("Content-Type", "")
        form, body = {}, None
        if kind.startswith("application/x-www-form-urlencoded"):
            form = dict(urllib.parse.parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        elif kind.startswith("application/json") and raw:
            body = json.loads(raw.decode("utf-8"))
        path = urllib.parse.unquote(parsed.path)
        request = {"method": handler.command, "path": path, "query": dict(urllib.parse.parse_qsl(parsed.query)),
                   "headers": {k.lower(): v for k, v in handler.headers.items()}, "form": form, "json": body, "raw": raw}
        with self.lock:
            request["caller"] = self.caller(request)
            self.requests.append({**request, "form": {k: ("***" if k == "password" else v) for k, v in form.items()},
                                  "raw": b"" if form else raw})
            reply = graph_error(404, "NotFound", f"No fake route for {handler.command} {path}.")
            for method, pattern, endpoint in self.routes:
                match = pattern.match(path)
                if method == handler.command and match:
                    needs_caller = not path.endswith(("/devicecode", "/token")) and path.startswith(("/v1.0", "/beta"))
                    if needs_caller and not request["caller"]:
                        reply = graph_error(401, "InvalidAuthenticationToken", "Unauthorized.")
                    else:
                        reply = endpoint(request, **match.groupdict())
                    break
        if reply.drop:
            handler.close_connection = True
            return
        payload = reply.raw if reply.raw is not None else (b"" if reply.body is None else json.dumps(reply.body).encode())
        handler.send_response(reply.status)
        for key, value in reply.headers.items():
            handler.send_header(key, value)
        if reply.body is not None:
            handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def calls(self, method=None, pattern=None):
        return [r for r in self.requests if (not method or r["method"] == method)
                and (not pattern or re.search(pattern, r["path"]))]
