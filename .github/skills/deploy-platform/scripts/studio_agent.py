#!/usr/bin/env python3
"""Render, connect, configure and check the scenario's Copilot Studio agent.

`connections`, `app-connection` and `configure` print their intended changes and
change nothing unless `--apply` is given. Commands use the caller's Azure CLI
sign-in and never print tokens. PAC CLI commands create, push and publish the
agent itself.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "agents" / "scenario-assistant"
DEFAULT_SEARCH_API_VERSION = "2026-08-01-preview"
POWERAPPS_API = "https://api.powerapps.com/providers/Microsoft.PowerApps/apis"
POWERAPPS_RESOURCE = "https://service.powerapps.com/"
FABRIC_IQ = "shared_fabriciqmcpserver"
FOUNDRY_IQ = "shared_azureaisearchfoundryiq"
# The app's own connector: the App Player calls the published agent through each user's connection.
COPILOT_STUDIO = "shared_microsoftcopilotstudio"
APP_CONNECTION = ("oauthDefault", {})
REFERENCE_SUFFIX = {
    FABRIC_IQ: "00000000000000000000000000000001",
    FOUNDRY_IQ: "00000000000000000000000000000002",
}
ACTIONS = {
    "FabricIQOntology": ("mcp_FabricIQOntology", FABRIC_IQ),
    "FoundryIQKnowledge": ("AzureAISearchKBRetrieveMcp", FOUNDRY_IQ),
}
AGENT_READERS = 1
GROUNDING_OFF = ("useModelKnowledge", "isFileAnalysisEnabled", "isSemanticSearchEnabled")
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


class StudioError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Bearer tokens must never follow a redirect to another URL."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def schema_name(value):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,7}_[A-Za-z0-9_]{1,80}", value or ""):
        raise StudioError("agent-schema must be '<publisher prefix>_<name>' using letters, digits and underscores.")
    return value


def guid(value, label):
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError):
        raise StudioError(f"{label} must be a GUID.") from None
    if parsed.int == 0:
        raise StudioError(f"{label} cannot be the zero GUID.")
    return str(parsed)


def https_url(value, label):
    parsed = urllib.parse.urlparse(value or "")
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise StudioError(f"{label} must be an https URL without query or fragment.")
    return f"https://{parsed.netloc}{parsed.path}".rstrip("/")


def reference_name(schema, connector):
    return f"{schema}.{connector}.{REFERENCE_SUFFIX[connector]}"


def render_text(text, values):
    unknown = sorted(set(PLACEHOLDER.findall(text)) - set(values))
    if unknown:
        raise StudioError(f"Unknown placeholders: {', '.join(unknown)}.")
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def render(source, workspace, values):
    if not (workspace / ".mcs").is_dir():
        raise StudioError("Render only into a PAC workspace created by 'pac copilot init' or 'clone'.")
    written = []
    for path in sorted(source.rglob("*")):
        if path.is_dir() or path.name == "README.md":
            continue
        relative = path.relative_to(source)
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_text(path.read_text(encoding="utf-8"), values), encoding="utf-8")
        written.append(str(relative))
    return written


def desired_configuration(text):
    configuration = json.loads(text or "{}")
    before = json.dumps(configuration, sort_keys=True)
    configuration.setdefault("settings", {})["GenerativeActionsEnabled"] = True
    ai_settings = configuration.setdefault("aISettings", {})
    for key in GROUNDING_OFF:
        ai_settings[key] = False
    return json.dumps(configuration), json.dumps(configuration, sort_keys=True) != before


def expected_connections(fabric_workspace_id, ontology_id, search_endpoint):
    return {
        FABRIC_IQ: ("certOauth", {"workspace_id": fabric_workspace_id, "ontology_id": ontology_id}),
        FOUNDRY_IQ: ("oauth", {"targetUri": search_endpoint}),
    }


def connection_values(connection):
    parameter_set = (connection.get("properties", {}).get("connectionParametersSet") or {})
    values = {key: (value or {}).get("value") for key, value in (parameter_set.get("values") or {}).items()}
    return parameter_set.get("name"), values


def connection_status(connection):
    statuses = connection.get("properties", {}).get("statuses") or []
    return "Connected" if statuses and all(status.get("status") == "Connected" for status in statuses) else "NotConnected"


def plan_connection(existing, parameter_set, expected):
    matching = []
    for connection in existing:
        name, values = connection_values(connection)
        normalized = {key: str(value).rstrip("/") for key, value in values.items()}
        if name == parameter_set and all(normalized.get(key) == value for key, value in expected.items()):
            matching.append(connection)
    connected = [connection for connection in matching if connection_status(connection) == "Connected"]
    if connected:
        return "reuse", connected[0]["name"], "Connected"
    if matching:
        return "sign-in", matching[0]["name"], "NotConnected"
    return "create", None, None


def evaluate(schema, bot, components, references, connection_statuses):
    blockers = []
    if bot.get("accesscontrolpolicy") != AGENT_READERS:
        blockers.append("Access control is not Agent readers.")
    configuration = json.loads(bot.get("configuration") or "{}")
    if (configuration.get("settings") or {}).get("GenerativeActionsEnabled") is not True:
        blockers.append("Generative orchestration is off.")
    ai_settings = configuration.get("aISettings") or {}
    for key in GROUNDING_OFF:
        if ai_settings.get(key) is not False:
            blockers.append(f"{key} must be off.")
    by_name = {component.get("schemaname"): component for component in components}
    for action, (operation, connector) in ACTIONS.items():
        data = (by_name.get(f"{schema}.action.{action}") or {}).get("data") or ""
        if not data:
            blockers.append(f"Tool {action} is missing.")
        elif "mode: Invoker" not in data or operation not in data or reference_name(schema, connector) not in data:
            blockers.append(f"Tool {action} must use end-user (Invoker) credentials and its connection reference.")
    if f"{schema}.topic.ConnectionProof" not in by_name:
        blockers.append("Connection proof topic is missing.")
    if "webBrowsing: false" not in ((by_name.get(f"{schema}.gpt.default") or {}).get("data") or ""):
        blockers.append("Web browsing must be off.")
    for connector in (FABRIC_IQ, FOUNDRY_IQ):
        bound = references.get(reference_name(schema, connector))
        if not bound:
            blockers.append(f"{connector} connection reference is missing or unbound.")
        elif connection_statuses.get(bound) != "Connected":
            blockers.append(f"{connector} connection is not Connected; the owner must sign in.")
    sync = json.loads(bot.get("synchronizationstatus") or "{}").get("currentSynchronizationState") or {}
    published = bot.get("publishedon") or ""
    if not published or sync.get("state") != "Synchronized":
        state = sync.get("state") or "unknown"
        blockers.append(f"The agent is not published and synchronized (state: {state}); recheck after publishing finishes.")
    elif any((component.get("modifiedon") or "") > published for component in components):
        blockers.append("The agent has changes that are not published.")
    return blockers


def token(resource, tenant):
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--tenant", tenant, "-o", "json"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode:
        raise StudioError("Azure CLI could not get a token for the chosen tenant; sign in with 'az login'.")
    return json.loads(result.stdout)["accessToken"]


def call(method, url, bearer, body=None):
    headers = {"Authorization": "Bearer " + bearer, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        if method == "PATCH":
            headers["If-Match"] = "*"
    request = urllib.request.Request(url, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
    try:
        with OPENER.open(request, timeout=120) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise StudioError(f"{method} {urllib.parse.urlparse(url).path} failed with HTTP {error.code}: {detail}") from None


def dataverse(args):
    base = https_url(args.dataverse_url, "dataverse-url")
    bearer = token(base, args.tenant_id)

    def query(entity, params):
        url = f"{base}/api/data/v9.2/{entity}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="$,()'")
        return call("GET", url, bearer).get("value", [])

    def patch(entity, body):
        call("PATCH", f"{base}/api/data/v9.2/{entity}", bearer, body)

    return query, patch


def find_bot(query, schema):
    bots = query("bots", {"$select": "botid,schemaname,accesscontrolpolicy,configuration,publishedon,synchronizationstatus",
                          "$filter": f"schemaname eq '{schema}'"})
    if len(bots) != 1:
        raise StudioError(f"Expected exactly one agent with schema name {schema}; create it with 'pac copilot init' first.")
    return bots[0]


def find_references(query, schema):
    rows = query("connectionreferences", {"$select": "connectionreferenceid,connectionreferencelogicalname,connectionid",
                                          "$filter": f"startswith(connectionreferencelogicalname,'{schema}.')"})
    return {row["connectionreferencelogicalname"]: row for row in rows}


def powerapps(args):
    environment = guid(args.environment_id, "environment-id")
    bearer = token(POWERAPPS_RESOURCE, args.tenant_id)
    scope = "$filter=" + urllib.parse.quote(f"environment eq '{environment}'")

    def connections(connector):
        return call("GET", f"{POWERAPPS_API}/{connector}/connections?api-version=2016-11-01&{scope}", bearer).get("value", [])

    def create(connector, display_name, parameter_set, values):
        name = uuid.uuid4().hex
        body = {"properties": {
            "environment": {"id": f"/providers/Microsoft.PowerApps/environments/{environment}", "name": environment},
            "displayName": display_name,
            "connectionParametersSet": {"name": parameter_set, "values": {key: {"value": value} for key, value in values.items()}},
        }}
        call("PUT", f"{POWERAPPS_API}/{connector}/connections/{name}?api-version=2016-11-01&{scope}", bearer, body)
        return name

    return environment, connections, create


def command_render(args):
    values = {
        "agent_schema": schema_name(args.agent_schema),
        "agent_display_name": args.display_name.replace('"', "'"),
        "knowledge_base_name": args.knowledge_base,
        "search_api_version": args.search_api_version,
    }
    written = render(Path(args.source), Path(args.workspace), values)
    return {"status": "rendered", "files": written, "next": "pac copilot push --project-dir <workspace>"}


def command_connections(args):
    expected = expected_connections(guid(args.fabric_workspace_id, "fabric-workspace-id"),
                                    guid(args.ontology_id, "ontology-id"),
                                    https_url(args.search_endpoint, "search-endpoint"))
    environment, connections, create = powerapps(args)
    report = {}
    for connector, (parameter_set, values) in expected.items():
        action, name, status = plan_connection(connections(connector), parameter_set, values)
        if action == "create" and args.apply:
            name = create(connector, f"{args.display_name} {connector}", parameter_set, values)
            action, status = "sign-in", "NotConnected"
        report[connector] = {"action": action, "connection": name, "status": status}
    pending = [connector for connector, entry in report.items() if entry["status"] != "Connected"]
    return {
        "status": "connected" if not pending else ("needs-sign-in" if args.apply else "plan"),
        "connections": report,
        "sign_in": f"https://make.powerapps.com/environments/{environment}/connections" if pending else None,
        "applied": bool(args.apply),
    }


def command_app_connection(args):
    environment, connections, create = powerapps(args)
    parameter_set, values = APP_CONNECTION
    action, name, status = plan_connection(connections(COPILOT_STUDIO), parameter_set, values)
    if action == "create" and args.apply:
        name = create(COPILOT_STUDIO, f"{args.display_name} {COPILOT_STUDIO}", parameter_set, values)
        action, status = "sign-in", "NotConnected"
    pending = status != "Connected"
    return {
        "status": "connected" if not pending else ("needs-sign-in" if args.apply else "plan"),
        "connector": COPILOT_STUDIO,
        "action": action,
        "connection": name,
        "connection_status": status,
        "sign_in": f"https://make.powerapps.com/environments/{environment}/connections" if pending else None,
        "applied": bool(args.apply),
    }


def command_configure(args):
    schema = schema_name(args.agent_schema)
    query, patch = dataverse(args)
    bot = find_bot(query, schema)
    configuration, configuration_changed = desired_configuration(bot.get("configuration"))
    changes = []
    if bot.get("accesscontrolpolicy") != AGENT_READERS or configuration_changed:
        changes.append(("bot", f"bots({bot['botid']})", {"accesscontrolpolicy": AGENT_READERS, "configuration": configuration}))
    references = find_references(query, schema)
    bindings = {FABRIC_IQ: guid_or_name(args.fabric_connection), FOUNDRY_IQ: guid_or_name(args.knowledge_connection)}
    for connector, connection in bindings.items():
        row = references.get(reference_name(schema, connector))
        if not row:
            raise StudioError(f"{reference_name(schema, connector)} is missing; push the rendered workspace first.")
        if row.get("connectionid") != connection:
            changes.append((connector, f"connectionreferences({row['connectionreferenceid']})", {"connectionid": connection}))
    if args.apply:
        for _, entity, body in changes:
            patch(entity, body)
    return {"status": "applied" if args.apply else "plan", "bot_id": bot["botid"],
            "changes": [label for label, _, _ in changes],
            "next": f"pac copilot publish --environment {args.dataverse_url} --bot {bot['botid']}"}


def guid_or_name(value):
    if not re.fullmatch(r"[0-9a-f]{32}", value or ""):
        raise StudioError("Connection names are 32 lowercase hexadecimal characters.")
    return value


def command_find(args):
    schema = schema_name(args.agent_schema)
    if args.environment_id:
        expected = guid(args.environment_id, "environment-id")
        base = https_url(args.dataverse_url, "dataverse-url")
        detail = call("GET", f"{base}/api/data/v9.2/RetrieveCurrentOrganization(AccessType=@p1)"
                             "?@p1=Microsoft.Dynamics.CRM.EndpointAccessType'Default'",
                      token(base, args.tenant_id)).get("Detail") or {}
        reported = detail.get("EnvironmentId") or ""
        if isinstance(reported, str) and reported.lower().startswith("default-"):
            raise StudioError(
                f"{base} is the tenant's Default environment (EnvironmentId '{reported}'). "
                "This deployment workflow requires a non-Default environment with a GUID ID. "
                "Use `dotnet pac env list` to choose an existing environment, then pass its "
                "ID and Org URL as --environment-id and --environment-url to deploy.py.")
        if not reported or guid(reported, "EnvironmentId") != expected:
            raise StudioError(f"{base} belongs to environment {reported}, not {expected}.")
        if detail.get("TenantId") and guid(detail["TenantId"], "TenantId") != guid(args.tenant_id, "tenant-id"):
            raise StudioError(f"{base} belongs to another tenant.")
    query, _ = dataverse(args)
    bots = query("bots", {"$select": "botid,schemaname", "$filter": f"schemaname eq '{schema}'"})
    if len(bots) > 1:
        raise StudioError(f"More than one agent has schema name {schema}; resolve the duplicate first.")
    return {"status": "found" if bots else "missing", "bot_id": bots[0]["botid"] if bots else None}


def command_check(args):
    schema = schema_name(args.agent_schema)
    query, _ = dataverse(args)
    bot = find_bot(query, schema)
    components = query("botcomponents", {"$select": "schemaname,data,modifiedon",
                                         "$filter": f"_parentbotid_value eq {bot['botid']}"})
    references = {name: row.get("connectionid") for name, row in find_references(query, schema).items()}
    _, connections, _ = powerapps(args)
    statuses = {}
    for connector in (FABRIC_IQ, FOUNDRY_IQ):
        for connection in connections(connector):
            statuses[connection["name"]] = connection_status(connection)
    blockers = evaluate(schema, bot, components, references, statuses)
    return {"status": "ready" if not blockers else "blocked", "bot_id": bot["botid"], "blockers": blockers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    render_parser = commands.add_parser("render", help="Render agents/scenario-assistant into a PAC workspace.")
    render_parser.add_argument("--workspace", required=True)
    render_parser.add_argument("--source", default=str(SOURCE))
    render_parser.add_argument("--agent-schema", required=True)
    render_parser.add_argument("--display-name", required=True)
    render_parser.add_argument("--knowledge-base", required=True)
    render_parser.add_argument("--search-api-version", default=DEFAULT_SEARCH_API_VERSION)

    for name, help_text in (("connections", "Find or create the caller's two IQ connections."),
                            ("app-connection", "Find or create the caller's Microsoft Copilot Studio connection "
                                               "that the app uses."),
                            ("configure", "Set access, grounding and connection bindings."),
                            ("check", "Read-only readiness check of the published agent."),
                            ("find", "Read-only lookup of the agent by schema name.")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--tenant-id", required=True)
        if name == "connections":
            sub.add_argument("--environment-id", required=True)
            sub.add_argument("--display-name", default="IQ scenario")
            sub.add_argument("--fabric-workspace-id", required=True)
            sub.add_argument("--ontology-id", required=True)
            sub.add_argument("--search-endpoint", required=True)
        elif name == "app-connection":
            sub.add_argument("--environment-id", required=True)
            sub.add_argument("--display-name", default="IQ scenario")
        else:
            sub.add_argument("--dataverse-url", required=True)
            sub.add_argument("--agent-schema", required=True)
        if name == "configure":
            sub.add_argument("--fabric-connection", required=True)
            sub.add_argument("--knowledge-connection", required=True)
        if name == "check":
            sub.add_argument("--environment-id", required=True)
        if name == "find":
            sub.add_argument("--environment-id", help="Also prove the Dataverse URL belongs to this environment.")
        if name in ("connections", "app-connection", "configure"):
            sub.add_argument("--apply", action="store_true", help="Make the planned changes.")

    args = parser.parse_args(argv)
    try:
        if args.command != "render":
            guid(args.tenant_id, "tenant-id")
        handler = {"render": command_render, "connections": command_connections,
                   "app-connection": command_app_connection, "configure": command_configure,
                   "check": command_check, "find": command_find}[args.command]
        report = handler(args)
    except StudioError as error:
        print(f"Studio agent step blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
