#!/usr/bin/env python3
"""Deterministic deployment and teardown of the DEV platform, agents and app.

`up` runs the documented phases in order with native tools only: Terraform,
Azure CLI, ACR Tasks and Fabric/Search/Graph/Foundry REST. Its first phase
completes every CLI sign-in the run needs, back to back, before anything is
created. Every phase checks live state first and acts only on what is missing,
so a rerun resumes instead of duplicating work. Plans and creations need
approval.

The signed-in user is assumed to administer every target: the runner grants
tenant-wide consent and registers required subscription features as that user,
and names a manual grant only when an API refuses for lack of permission.
Device-code entry and each user's consent to the agents' tools stay with
people.

`down` signs in the same way, then applies a reviewed full Terraform destroy
plan, which deletes the app and, with the Foundry project, the agents, and
purges the deleted Foundry account. Retained Fabric data and Entra
registrations are reported for manual cleanup. Local configuration and state
stay.

Exit codes: 0 done, 1 usage error, 2 blocked (investigate), 3 waiting for a
person (a sign-in, a missing permission's grant) or a long-running cloud
operation, 4 approval required. Device codes go only to the terminal or stderr;
tokens stay in memory; nothing secret is printed or written.
"""

import argparse
import base64
import collections
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
from typing import NamedTuple
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID, uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux and macOS provide fcntl.
    fcntl = None

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]
sys.path.insert(0, str(ROOT / ".github" / "skills" / "runtime-integration" / "scripts"))
sys.path.insert(0, str(SCRIPTS))
import configure_knowledge as knowledge  # noqa: E402
import foundry_agent as foundry  # noqa: E402
import preflight_runtime as runtime  # noqa: E402

PHASES = ("signin", "config", "preflight", "identity", "consent", "platform",
          "bootstrap", "knowledge", "verify", "agent", "app")
OK, USAGE, BLOCKED, WAITING, APPROVAL = 0, 1, 2, 3, 4
PLAN_PHASES = ("identity", "platform", "knowledge", "app", "destroy")
# Never granted by --yes: deletions, and replacing live Fabric definitions.
DESTRUCTIVE_APPROVALS = ("destroy", "purge-foundry", "item-update", "shared-action-group")
# Approved by the sha256 prefix of exactly what they change.
DIGEST_APPROVALS = PLAN_PHASES + ("item-update",)
APPROVAL_KEYS = DIGEST_APPROVALS + ("knowledge-registration", "agent-publish", "app-build", "purge-foundry",
                                    "shared-action-group")
# A transient failure (a connection reset, EOF or timeout, HTTP 429 or 5xx) of a read is retried: at most this many
# attempts in all, after Retry-After or an exponential backoff from RETRY_BACKOFF seconds.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2
RETRY_STATUSES = (429, 500, 502, 503, 504)
# What a dropped or stalled connection raises; RemoteDisconnected is a ConnectionResetError.
TRANSIENT_TRANSPORT = (ConnectionError, TimeoutError, EOFError, ssl.SSLEOFError, IncompleteRead)
# How Azure CLI reports a lookup that failed in transit or that a busy service turned away.
TRANSIENT_CLI = re.compile(
    r"Connection (?:reset|aborted|broken|refused)|RemoteDisconnected|Remote end closed|EOF occurred|UNEXPECTED_EOF"
    r"|\btimed out\b|Read ?Timeout|Connect ?Timeout|Max retries exceeded|Temporary failure in name resolution"
    r"|\b(?:429|500|502|503|504)\b|Too ?Many ?Requests|Service ?Unavailable|Bad ?Gateway|Gateway ?Time-?out"
    r"|Internal ?Server ?Error", re.I)

FABRIC_API = "https://api.fabric.microsoft.com"
SEARCH_RESOURCE = "https://search.azure.com"
GRAPH_API = "https://graph.microsoft.com"
ARM_API = "https://management.azure.com"
GITHUB_API = "https://api.github.com"
POWER_BI_APP_ID = "00000009-0000-0000-c000-000000000000"
# The resource behind https://ai.azure.com tokens; the app's sign-in registration declares its user_impersonation.
FOUNDRY_API_APP_ID = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
APP_SCOPES = ("user_impersonation",)
APP_CONSENT = "Microsoft Foundry's user_impersonation, the app's https://ai.azure.com sign-in scope"
# Work IQ's published application: with work_iq_enabled, the work-iq connection asks it for WorkIQAgent.Ask as each
# user.
WORK_IQ_APP_ID = "fdcc1f02-fc51-4226-8753-f668596af7f7"
# The Work IQ MCP server the with-IQ agent's mail tool calls through the work-iq OAuth connection.
WORK_IQ_MCP_ENDPOINT = "https://workiq.svc.cloud.microsoft/mcp"
IDENTITY_TARGET = "module.fabric_user_auth"
IDENTITY_ADDRESSES = frozenset({
    f"{IDENTITY_TARGET}.random_uuid.access_scope",
    f"{IDENTITY_TARGET}.azuread_application.fabric",
    f"{IDENTITY_TARGET}.azuread_service_principal.fabric",
    f"{IDENTITY_TARGET}.azuread_application_identifier_uri.fabric",
    f'{IDENTITY_TARGET}.azuread_application_pre_authorized.clients["azure_cli"]',
    f'{IDENTITY_TARGET}.azuread_application_pre_authorized.clients["power_bi_client"]',
    f'{IDENTITY_TARGET}.azuread_application_pre_authorized.clients["power_bi_service"]',
})
PLATFORM_ADDRESSES = frozenset({
    "azurerm_resource_group.main", "azurerm_fabric_capacity.main", "fabric_workspace.dev",
    "fabric_workspace.test", "fabric_connection.github[0]", "fabric_deployment_pipeline.main",
    "azapi_resource.foundry", "azapi_resource.chat_model", "azapi_resource.embedding_model",
    "azapi_resource.agent_model", "azapi_resource.search", "azapi_resource.web_iq",
})
VARIABLES = ("project_name", "location", "fabric_location", "search_location", "search_sku", "fabric_capacity_sku",
             "fabric_git_enabled", "github_owner", "github_repository_name", "github_branch_name", "github_directory_name",
             "knowledge_lakehouse_id", "knowledge_ontologies", "chat_model", "embedding_model", "agent_model",
             "app_image_tag", "web_iq_terms_accepted", "work_iq_enabled")
WEB_IQ_API_VERSION = "2026-06-01-preview"
WEB_IQ_SEARCH = "https://api.microsoft.ai/v3/search/web"
WEB_IQ_PORTAL = "https://webiq.microsoft.ai/profiles"
WEB_IQ_QUESTION = "Microsoft Web IQ documentation"
SEARCH_OFFERINGS = f"{ARM_API}/providers/Microsoft.Search/fetchOfferings?api-version=2026-09-01-preview"
SEARCH_SERVICE_PATH = re.compile(r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Search/searchServices/[^/]+",
                                 re.IGNORECASE)
# Plan variable that defers the Search identity's grants for an apply creating serverless Search.
SEARCH_DEFERRED = {"search_identity_ready": "false"}


class NotebookRun(NamedTuple):
    """A bootstrap notebook (an item key in Scenario.items), its parameters and whether a completed run suffices."""
    item: str
    parameters: dict
    skip_completed: bool


class Scenario(NamedTuple):
    """What the runner deploys and proves: the workspace/ items, their plan and the readiness checks.

    A new domain changes this descriptor together with its workspace/ items, plan parameters and knowledge
    registration (fabric-workspace skill); items no longer described here are reported and left in DEV, never
    deleted.
    """
    items: tuple
    plan: str
    registration: str
    registration_description: str
    tables: tuple
    expression: str
    runs: tuple
    # Asked of the ontology's check-only knowledge base, which holds the live ontology source.
    ontology_question: str
    # Asked of the agents' knowledge base, retrieving from the indexed OneLake folder source document_source names.
    document_question: str
    document_source: str
    # Asked once of the public-context knowledge base: public terms only, never a site, asset or other private detail.
    public_context_question: str
    expected_facts: tuple
    app_question: str


SCENARIO = Scenario(
    items=(("lakehouse", "Lakehouse", "Scenario"), ("seed", "Notebook", "SeedScenario"),
           ("ontology", "Ontology", "ScenarioOntology"), ("bind", "Notebook", "BindScenario")),
    plan="Bootstrap",
    registration="scenario",
    registration_description="Current sites, assets, their status, and which site each asset belongs to.",
    tables=("demo_sites", "demo_assets"),
    expression="ScenarioLakehouse",
    runs=(NotebookRun("seed", {"lakehouse_name": "Scenario", "allow_other_workspaces": False}, True),
          NotebookRun("bind", {"lakehouse_name": "Scenario", "ontology_name": "ScenarioOntology"}, False)),
    # The value is quoted: unquoted, Fabric sometimes read a bare value as an empty one and found nothing.
    ontology_question=("Using the live ScenarioOntology, which Asset rows have the Status value \"Maintenance\", and "
                       "at which site is each asset located? Return asset names and site names."),
    document_question=("According to the indexed starter document, what does Git store, what does SeedScenario "
                       "materialize, and which source should be used for current entity values? Answer from the "
                       "OneLake document and cite it."),
    document_source="documents",
    public_context_question=("What does manufacturer and industry guidance recommend for the routine preventive "
                             "maintenance of industrial centrifugal pumps?"),
    # Seeded rows: the live ontology source must return them in its own source data, not only the starter document.
    expected_facts=("Pump B", "North Works"),
    app_question='Ask "Which assets are in maintenance, and at which site?" with IQ and expect Pump B at North Works.',
)
# Item types whose reviewed definitions --update-definitions may write over a live item; data items never.
UPDATABLE_TYPES = ("Notebook", "Ontology", "DeploymentPlan")
# The runner selects the deployment subscription itself after an Azure CLI sign-in.
SIGN_IN_ENV = {"AZURE_CORE_LOGIN_EXPERIENCE_V2": "off"}
# An API's refusal for lack of permission, as ARM, Graph, Fabric, Foundry and the native CLIs report it.
DENIED = re.compile(
    r"\b(?:HTTP|status ?code|status)[ =:]*(?:401|403)\b|\bunexpected status (?:401|403)\b"
    r"|\b(?:Unauthorized|Forbidden|AuthorizationFailed|LinkedAuthorizationFailed|Authorization_RequestDenied"
    r"|InsufficientScopes|InsufficientPrivileges|InsufficientPermissions|PrincipalTypeNotSupported|AccessDenied)\b"
    r"|insufficient (?:privileges|permissions)|not authori[sz]ed|missing prv\w* privilege"
    r"|(?:does not|doesn't|do not|don't) have (?:the |a |any )?(?:required |valid )?"
    r"(?:permissions?|privileges?|access|authorization|licen[cs]es?)\b", re.I)
ARM_ACTION = re.compile(r"perform action '([^']+)'", re.I)


def scenario_name(key):
    return next(name for item_key, _, name in SCENARIO.items if item_key == key)


def run_order():
    names = [scenario_name(step.item) for step in SCENARIO.runs]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)


PAT_STEPS = (
    "Add a Codespaces secret FABRIC_GITHUB_TOKEN for this repository (fine-grained, Contents read/write) "
    "and restart the Codespace, or export TF_VAR_github_token in your own shell with `read -rs`.",
    "Never paste the PAT into chat, files or command arguments.",
)
APP_NEXT = (
    "Open the app URL above in a browser and sign in. At first sign-in, consent to the app's access to Microsoft "
    "Foundry for you, unless an administrator granted it for everyone (terraform output app: api_permissions_url).",
    "Ask with IQ. When a tool first needs your permission, the app shows a consent card that links to Microsoft's "
    "consent page: accept, then ask again. Fabric IQ, and Work IQ when work_iq_enabled, each ask once per user; "
    "Foundry IQ and Web IQ use the project's identity and never ask.",
    SCENARIO.app_question,
    "Select Ask both to compare the agent with IQ and the agent without IQ side by side.",
    "This run proves the route for your account only: sign in as a second user to see that each user reaches only "
    "the data they may read.",
    "Nothing to commit: the runner wrote only the ignored infra/terraform.tfvars. Change an agent in agents/ or the app "
    "in app/, commit, and rerun deploy.py up.",
)


class Stop(Exception):
    """A deliberate stop: blocked, waiting for a person, or needing approval."""

    code = BLOCKED
    result = "blocked"

    def __init__(self, message, *next_steps, approval=None, detail=""):
        super().__init__(message)
        self.message = message
        self.next_steps = [step for step in next_steps if step]
        self.approval = approval
        # Native output, used only to recognize a permission refusal; never reported.
        self.detail = detail or ""


class Blocked(Stop):
    pass


class Waiting(Stop):
    code = WAITING
    result = "waiting"


class MissingPermission(Waiting):
    """An API refused for lack of permission: name the exact manual grant, then wait for it."""

    def __init__(self, message, *next_steps, grants):
        super().__init__(message, *grants, *next_steps)
        self.grants = list(grants)


class NeedsApproval(Stop):
    code = APPROVAL
    result = "approval"


class TransportError(Blocked):
    """A request that failed in transit; `transient` when another attempt can succeed (reset, EOF, timeout)."""

    transient = False


class Response:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = {str(key).lower(): value for key, value in (headers or {}).items()}
        self.body = body or b""

    def json(self, default=None):
        if not self.body:
            return default
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            return default


class ServiceError(Blocked):
    def __init__(self, label, response):
        details = response.json(default={})
        details = details if isinstance(details, dict) else {}
        error = details.get("error") if isinstance(details.get("error"), dict) else details
        self.status = response.status
        self.error_code = str(error.get("errorCode") or error.get("code") or "")
        text = str(error.get("message") or "").strip()[:400]
        super().__init__(f"{label} failed with HTTP {response.status} {self.error_code}".rstrip()
                         + (f": {text}" if text else "."))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Http:
    """JSON over HTTPS without following redirects or logging headers.

    A read (GET or HEAD) that fails transiently, in transit or with HTTP 429 or 5xx, is sent again: at most
    `attempts` (RETRY_ATTEMPTS) times in all, after the response's Retry-After (capped) or an exponential backoff.
    Any other method is sent once by default, because a second write could repeat what the service already did, and
    no other 4xx status is ever retried. Callers that keep their own retry policy pass attempts=1.
    """

    def __init__(self, sleep=time.sleep):
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._sleep = sleep

    def request(self, method, url, headers=None, body=None, timeout=120, attempts=None):
        if attempts is None:
            attempts = RETRY_ATTEMPTS if method in ("GET", "HEAD") else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._send(method, url, headers, body, timeout)
            except TransportError as error:
                if not error.transient or attempt == attempts:
                    raise
                self._sleep(backoff(attempt))
                continue
            if response.status not in RETRY_STATUSES or attempt == attempts:
                return response
            self._sleep(retry_after(response, backoff(attempt)))

    def _send(self, method, url, headers, body, timeout):
        headers = dict(headers or {})
        headers.setdefault("Accept", "application/json")
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return Response(response.status, dict(response.headers), response.read())
        except urllib.error.HTTPError as error:
            try:
                return Response(error.code, dict(error.headers or {}), error.read())
            finally:
                error.close()
        except (urllib.error.URLError, OSError, IncompleteRead) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            host = urllib.parse.urlparse(url).netloc
            failure = TransportError(f"{method} to {host} failed ({type(reason).__name__}).")
            failure.transient = isinstance(reason, TRANSIENT_TRANSPORT)
            raise failure from None


class Result:
    def __init__(self, code, stdout="", stderr=""):
        self.code, self.stdout, self.stderr = code, stdout, stderr


class Shell:
    """Runs native commands. Streamed output goes to stderr and keeps a tail for errors."""

    def which(self, name):
        return shutil.which(name)

    def run(self, args, *, cwd=None, env=None, input_text=None, stream=False, interactive=False):
        args = [self.which(args[0]) or args[0], *args[1:]]
        try:
            if interactive:
                return Result(subprocess.run(args, cwd=cwd, env=env, check=False).returncode)
            if stream:
                process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, errors="replace")
                lines = collections.deque(maxlen=60)
                for line in process.stdout:
                    sys.stderr.write(line)
                    lines.append(line)
                return Result(process.wait(), "".join(lines))
            options = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
            completed = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                                       errors="replace", check=False, **options)
            return Result(completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError:
            raise Blocked(f"{args[0]} is not installed or not on PATH.") from None


class State:
    """Non-secret receipts in ignored .artifacts/deploy. Live checks stay authoritative."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "state.json"
        self.data = {"schema_version": 1}
        self._lock = None

    def load(self):
        if self.path.is_symlink():
            raise Blocked(f"Refusing the symlinked receipt file {self.path}.")
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                data = None
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise Blocked(f"Unrecognized {self.path}; review it before continuing.")
            self.data = data
        return self

    def section(self, name):
        return self.data.setdefault(name, {})

    def save(self):
        self._directory()
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory,
                                             prefix=".state.", suffix=".tmp", delete=False)
        try:
            with handle:
                json.dump(self.data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(handle.name, 0o600)
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    def lock(self):
        self._directory()
        if fcntl is None:
            return
        descriptor = os.open(self.directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise Blocked("Another deploy.py run is active for this checkout; let it finish first.") from None
        self._lock = descriptor

    def _directory(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)


def ask(text):
    sys.stderr.write(text)
    sys.stderr.flush()
    return sys.stdin.readline()


class Context:
    def __init__(self, args, *, root=ROOT, shell=None, http=None, sleep=time.sleep,
                 clock=time.monotonic, prompt=ask, interactive=None, environ=None):
        self.args = args
        self.root = Path(root)
        self.infra = self.root / "infra"
        self.shell = shell or Shell()
        self.http = http or Http(sleep=sleep)
        self.sleep = sleep
        self.clock = clock
        self.prompt = prompt
        if interactive is None:
            interactive = sys.stdin.isatty() and sys.stderr.isatty() and not args.non_interactive
        self.interactive = interactive
        self.environ = os.environ if environ is None else environ
        self.state = State(self.root / ".artifacts" / "deploy")
        self.phases = PHASES
        self.phase = "deploy"
        self.deadline = None
        self.tokens = {}
        self.cache = {}
        self.initialized = False

    def log(self, message):
        print(f"[{self.phase}] {message}", file=sys.stderr, flush=True)

    def start(self, phase):
        self.phase = phase
        self.deadline = self.clock() + 60 * self.args.timeout_minutes

    def remaining(self):
        return float("inf") if self.deadline is None else self.deadline - self.clock()

    def forget(self):
        self.cache.clear()

    def target(self, key):
        value = self.state.section("target").get(key)
        if not value:
            raise Blocked("Run the preflight phase first; the deployment target is not recorded.")
        return value

    @property
    def rerun(self):
        """The command that resumes this run, for messages about operations that are still running."""
        return f"deploy.py {getattr(self.args, 'command', None) or 'up'}"


def guid(value, label):
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError):
        raise Blocked(f"{label} must be a GUID.") from None
    if parsed.int == 0:
        raise Blocked(f"{label} cannot be the zero GUID.")
    return str(parsed)


def same_guid(left, right):
    try:
        return UUID(str(left)) == UUID(str(right))
    except (TypeError, ValueError):
        return False


def https_origin(value, label):
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise Blocked(f"{label} must be an https URL without query or fragment.")
    return f"https://{parsed.netloc.lower()}"


ONE_TIME_CODE = re.compile(r"(?m)^([ \t]*)[A-Z0-9]{4}-[A-Z0-9]{4}[ \t]*$|(enter the code )[A-Z0-9]{8,12}\b")


def tail(text, lines=15):
    text = "\n".join((text or "").strip().splitlines()[-lines:])
    # One-time device codes belong in the user's own terminal or log, never in reports or chat.
    return ONE_TIME_CODE.sub(lambda match: (match.group(1) or match.group(2) or "") + "<one-time code redacted>", text)


def quoted(command):
    return " ".join(shlex.quote(str(part)) for part in command)


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose(ctx, name, value):
    """Remember target choices so a resumed run cannot silently switch targets."""
    choices = ctx.state.section("choices")
    recorded = choices.get(name)
    if value in (None, False, ""):
        return recorded
    if recorded not in (None, value):
        raise Blocked(f"{name.replace('_', '-')} {value} conflicts with this deployment's recorded {recorded}.",
                      "Keep one target per deployment; review .artifacts/deploy/state.json before changing it.")
    if recorded != value:
        choices[name] = value
        ctx.state.save()
    return value


def run(ctx, command, *, stream=False, interactive=False, env=None, input_text=None):
    return ctx.shell.run([str(part) for part in command], cwd=str(ctx.root), env=env,
                         input_text=input_text, stream=stream, interactive=interactive)


def checked(ctx, command, label=None, *, quiet=False, retry=False, **options):
    result = read_cli(ctx, command) if retry else run(ctx, command, **options)
    if result.code:
        detail = "" if quiet else tail(result.stderr or result.stdout)
        raise Blocked(f"{label or quoted(command[:3])} failed (exit {result.code})." + (f"\n{detail}" if detail else ""))
    return result


def backoff(attempt):
    """Seconds to wait after a transiently failed attempt (1-based): exponential from RETRY_BACKOFF."""
    return RETRY_BACKOFF * 2 ** (attempt - 1)


def read_cli(ctx, command):
    """Run a read-only lookup, retrying only a transient failure: never a refusal, a 4xx or a missing object."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        result = run(ctx, command)
        text = f"{result.stderr}\n{result.stdout}"
        if result.code == 0 or attempt == RETRY_ATTEMPTS or DENIED.search(text) or not TRANSIENT_CLI.search(text):
            return result
        ctx.log(f"{quoted(command[:4])} failed transiently (attempt {attempt} of {RETRY_ATTEMPTS}); retrying.")
        ctx.sleep(backoff(attempt))


def az_json(ctx, arguments, label, *, quiet=False):
    """A read-only Azure CLI lookup as JSON; a transient failure is retried."""
    result = checked(ctx, ["az", *arguments, "--only-show-errors", "--output", "json"], label, quiet=quiet, retry=True)
    try:
        return json.loads(result.stdout or "null")
    except ValueError:
        raise Blocked(f"{label} did not return JSON.") from None


def access_token(ctx, *, resource=None, scope=None, tenant=None):
    """Return a delegated token from Azure CLI, kept in memory only."""
    key = (resource, scope, tenant)
    cached = ctx.tokens.get(key)
    if cached and cached[1] - 300 > time.time():
        return cached[0]
    command = ["az", "account", "get-access-token", "--only-show-errors", "--output", "json"]
    for flag, value in (("--resource", resource), ("--scope", scope), ("--tenant", tenant)):
        if value:
            command += [flag, value]
    result = run(ctx, command)
    if result.code:
        return None
    try:
        payload = json.loads(result.stdout)
        token = payload["accessToken"]
        expires = float(payload.get("expires_on") or time.time() + 600)
    except (ValueError, KeyError, TypeError):
        return None
    ctx.tokens[key] = (token, expires)
    return token


def claims(token):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}


def sign_in(ctx, command, reason, after=()):
    """Run one person's device-code sign-in now (see complete_sign_ins)."""
    complete_sign_ins(ctx, [(reason, command, tuple(after))])


def complete_sign_ins(ctx, pending):
    """Run device-code sign-ins back to back before anything changes.

    Each code prints to the terminal or, without one, to stderr (the caller's private log), never to a
    report. In a terminal, one confirmation covers them all; declining stops with the commands instead.
    """
    names = "; ".join(label for label, *_ in pending)
    steps = [step for _, command, after in pending
             for step in (f"Run: {quoted(command)}", *(f"Then: {quoted(follow)}" for follow in after))]
    rerun = f"Then rerun deploy.py {ctx.args.command}."
    if ctx.interactive and ctx.prompt(f"Sign in now, before anything changes: {names}.\n"
                                      "Each sign-in prints a device code here. Continue? [Y/n] "
                                      ).strip().lower() not in ("", "y", "yes"):
        raise Waiting(f"Sign-in required before anything changes: {names}.", *steps, rerun)
    for number, (label, command, after) in enumerate(pending, 1):
        ctx.log(f"Sign-in {number} of {len(pending)}: {label}. Open the device-code page below and enter its code "
                "yourself; the code never reaches the report.")
        env = dict(ctx.environ, **SIGN_IN_ENV) if command[0] == "az" else None
        result = run(ctx, command, stream=not ctx.interactive, interactive=ctx.interactive, env=env)
        if result.code:
            raise Waiting(f"Sign-in did not complete: {label} (exit {result.code}).", f"Run: {quoted(command)}",
                          *(f"Then: {quoted(follow)}" for follow in after), rerun)
        for follow in after:
            checked(ctx, follow, quiet=True)
    ctx.tokens.clear()


def approve(ctx, key, description, pending=None):
    """Approve one action or one exact saved plan (by hash) before it changes anything."""
    granted = ctx.args.approve.get(key)
    if ctx.args.yes and key not in DESTRUCTIVE_APPROVALS:
        ctx.log(f"Approved by --yes: {description}.")
        return
    if pending is None and granted is True:
        ctx.log(f"Approved by --approve {key}: {description}.")
        return
    if pending is not None and granted:
        if pending["sha256"].startswith(granted):
            ctx.log(f"Approved by --approve {key}={granted}: {description}.")
            return
        target = "saved plan" if "path" in pending else "pending change"
        raise NeedsApproval(f"--approve {key}={granted} does not match the {target} (sha256 {pending['sha256'][:12]}).",
                            f"Review the current {target}, then approve its hash.",
                            approval=approval_record(key, description, pending))
    if ctx.interactive:
        if ctx.prompt(f"{description}? [y/N] ").strip().lower() in ("y", "yes"):
            return
        raise NeedsApproval(f"Not approved: {description}. Nothing was changed.")
    flag = f"{key}={pending['sha256'][:12]}" if pending else key
    review = None
    if pending:
        review = pending.get("review") or f"Review it: terraform -chdir=infra show {pending['path']}"
    raise NeedsApproval(f"Approval required: {description}.", review,
                        f"Then rerun with --approve {flag}, or run interactively.",
                        approval=approval_record(key, description, pending))


def approval_record(key, description, pending):
    record = {"key": key, "description": description}
    if pending:
        record.update(sha256=pending["sha256"], summary=pending["summary"])
        record.update({name: pending[field] for name, field in (("plan", "path"), ("review", "review")) if field in pending})
    return record


def terraform_env(ctx, token_required, web_iq_required=False):
    env = dict(ctx.environ)
    for key in ("TF_LOG", "TF_LOG_CORE", "TF_LOG_PROVIDER", "TF_LOG_PATH"):
        if env.get(key):
            raise Blocked(f"Unset {key}: Terraform debug logs can contain secrets.")
    # Ambient overrides could silently target, retarget state or change inputs behind a saved plan.
    overrides = sorted(key for key, value in env.items() if value and (
        key.startswith("TF_CLI_ARGS") or key in ("TF_WORKSPACE", "TF_DATA_DIR")
        or (key.startswith("TF_VAR_") and key != "TF_VAR_github_token")))
    if overrides:
        raise Blocked(f"Unset {', '.join(overrides)}: the runner uses exactly infra/terraform.tfvars, "
                      "the default workspace and its own plan arguments.")
    env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0"})
    token = env.get("TF_VAR_github_token") or env.get("FABRIC_GITHUB_TOKEN")
    if token:
        env["TF_VAR_github_token"] = token
    elif token_required:
        raise Waiting("The Fabric GitHub PAT is not available on this host.", *PAT_STEPS)
    # Terraform gets the key exactly when a plan manages the public-context source: an ephemeral variable set at plan
    # time must be set again at apply, and one that wasn't must not be, so its presence can't depend on the shell.
    key = (env.pop("WEB_IQ_API_KEY", None) or "").strip()
    if web_iq_required:
        if not key:
            raise Waiting("Knowledge plans need the Web IQ key: the public-context knowledge source stores it write-only.",
                          "Supply that resource's key only through WEB_IQ_API_KEY on the deployment host, then rerun.",
                          "Never paste keys into chat, Terraform inputs, files or the browser app; see docs/getting-started.md#web-iq.")
        if any(ord(character) < 33 or ord(character) > 126 for character in key):
            raise Blocked("WEB_IQ_API_KEY contains invalid HTTP header characters; supply a valid portal key.")
        env["TF_VAR_web_iq_api_key"] = key
    return env


def web_iq_key_required(ctx, destroy=False):
    """After knowledge setup every plan manages the Web IQ public-context source; Terraform needs its write-only
    key at plan and at apply, because ephemeral values are not saved in a plan. Teardown doesn't."""
    return not destroy and variables(ctx)["knowledge_lakehouse_id"] is not None


def terraform(ctx, *arguments, token=False, web_iq=False, stream=False, input_text=None):
    env = terraform_env(ctx, token, web_iq)
    if arguments[0] == "console" and not env.get("TF_VAR_github_token"):
        # Evaluate the Git opt-in before requiring its ephemeral credential, never for plan/apply.
        env["TF_VAR_github_token"] = "evaluation-only"
    return run(ctx, ["terraform", f"-chdir={ctx.infra}", *arguments], env=env,
               stream=stream, input_text=input_text)


def ensure_init(ctx):
    if ctx.initialized:
        return
    command = ["init", "-input=false"]
    backend = ctx.state.section("choices").get("backend_config")
    if backend:
        command.append(f"-backend-config={(ctx.root / backend).resolve()}")
    result = terraform(ctx, *command)
    if result.code:
        raise Blocked("terraform init failed; nothing was planned.\n" + tail(result.stderr or result.stdout))
    if run(ctx, ["git", "diff", "--quiet", "--", "infra/.terraform.lock.hcl"]).code == 1:
        ctx.log("terraform init added provider checksums to infra/.terraform.lock.hcl; review and commit them.")
    ctx.initialized = True


def state_addresses(ctx):
    if "addresses" not in ctx.cache:
        ensure_init(ctx)
        result = terraform(ctx, "state", "list")
        if result.code and "No state file was found" not in result.stdout + result.stderr:
            raise Blocked("terraform state list failed.\n" + tail(result.stderr))
        lines = () if result.code else result.stdout.splitlines()
        ctx.cache["addresses"] = {line.strip() for line in lines if line.strip()}
    return ctx.cache["addresses"]


def outputs(ctx):
    if "outputs" not in ctx.cache:
        ensure_init(ctx)
        result = terraform(ctx, "output", "-json")
        if result.code:
            raise Blocked("terraform output failed.\n" + tail(result.stderr))
        try:
            data = json.loads(result.stdout or "{}")
            ctx.cache["outputs"] = {name: entry.get("value") for name, entry in data.items()}
        except (ValueError, AttributeError):
            raise Blocked("terraform output did not return the expected JSON.") from None
    return ctx.cache["outputs"]


def required_outputs(ctx, *names):
    values = outputs(ctx)
    missing = [name for name in names if values.get(name) in (None, "", {}, [])]
    if missing:
        raise Blocked(f"Terraform outputs are missing: {', '.join(missing)}. Complete the earlier phases first.")
    return [values[name] for name in names]


def variables(ctx):
    """Evaluate the effective inputs, including defaults, with Terraform itself."""
    if "variables" not in ctx.cache:
        ensure_init(ctx)
        expression = "jsonencode({" + ", ".join(f"{name} = var.{name}" for name in VARIABLES) + "})\n"
        result = terraform(ctx, "console", input_text=expression)
        lock_notices = ("Acquiring state lock.", "Releasing state lock.")
        lines = [line for line in result.stdout.splitlines()
                 if line.strip() and not line.lstrip().startswith(lock_notices)]
        if result.code or not lines:
            raise Blocked("Terraform could not evaluate infra/terraform.tfvars.\n" + tail(result.stderr))
        try:
            ctx.cache["variables"] = json.loads(json.loads(lines[-1]))
        except (ValueError, TypeError):
            raise Blocked("Terraform console returned an unexpected value.") from None
    return ctx.cache["variables"]


def fingerprint(ctx):
    digest = hashlib.sha256()
    paths = sorted(ctx.infra.glob("*.tf")) + sorted(ctx.infra.glob("modules/**/*.tf"))
    paths += [ctx.infra / "terraform.tfvars", ctx.infra / ".terraform.lock.hcl"]
    backend = ctx.state.section("choices").get("backend_config")
    if backend:
        paths.append(ctx.root / backend)
    for path in paths:
        if path.is_file():
            digest.update(str(path.resolve().relative_to(ctx.root.resolve())).encode() + b"\0"
                          + path.read_bytes() + b"\0")
    return digest.hexdigest()


def summarize(plan):
    changes = []
    for change in plan.get("resource_changes") or []:
        actions = (change.get("change") or {}).get("actions") or []
        if actions in (["no-op"], ["read"]):
            continue
        action = "replace" if "delete" in actions and "create" in actions else "+".join(actions) or "unknown"
        changes.append({"address": change.get("address", "?"), "action": action})
    counts = collections.Counter(change["action"] for change in changes)
    return {"changes": changes, "counts": dict(sorted(counts.items()))}


def describe(summary):
    return ", ".join(f"{count} {action}" for action, count in summary["counts"].items()) or "no changes"


def discard_plan(ctx, phase):
    pending = ctx.state.section("pending_plans").pop(phase, None)
    if pending:
        Path(pending["path"]).unlink(missing_ok=True)
        ctx.state.save()


def converge(ctx, phase, target=None, check=None, variables=None, before_apply=None, note=None):
    """Plan, approve exactly that saved plan, apply it. Returns 'unchanged' or 'applied'."""
    destroy = phase == "destroy"
    if destroy and (target or variables):
        raise Blocked("Teardown requires an untargeted destroy plan without variable overrides.")
    plans = ctx.state.section("pending_plans")
    pending = plans.get(phase)
    current = fingerprint(ctx)
    if pending and (pending.get("fingerprint") != current or pending.get("target") != target
                    or pending.get("variables") != variables
                    or not Path(pending.get("path", "")).is_file()
                    or sha256_file(pending["path"]) != pending.get("sha256")):
        ctx.log("Discarding a saved plan whose configuration or file changed.")
        discard_plan(ctx, phase)
        pending = None
    if pending is None:
        directory = ctx.state.directory / "plans"
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        path = directory / f"{phase}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid4().hex[:8]}.tfplan"
        command = ["plan", "-input=false", "-detailed-exitcode", f"-out={path}"]
        if destroy:
            command.append("-destroy")
        if target:
            command.append(f"-target={target}")
        command += [f"-var={name}={value}" for name, value in sorted((variables or {}).items())]
        ctx.log("Creating a saved plan" + (f" targeted at {target} (expected only here)" if target else "")
                + "".join(f" with {name}={value}" for name, value in sorted((variables or {}).items())) + ".")
        result = terraform(ctx, *command, token=git_token_required(ctx), web_iq=web_iq_key_required(ctx, destroy),
                           stream=True)
        if result.code == 0:
            path.unlink(missing_ok=True)
            if before_apply:
                before_apply()
            return "unchanged"
        if result.code != 2:
            path.unlink(missing_ok=True)
            raise Blocked(f"terraform plan failed for {phase}; nothing was applied.\n{tail(result.stdout)}",
                          detail=result.stdout)
        shown = terraform(ctx, "show", "-json", str(path))
        try:
            summary = summarize(json.loads(shown.stdout)) if not shown.code else None
        except ValueError:
            summary = None
        if summary is None:
            path.unlink(missing_ok=True)
            raise Blocked("terraform show could not read the saved plan; nothing was applied.")
        pending = {"path": str(path), "sha256": sha256_file(path), "fingerprint": current, "target": target,
                   "variables": variables, "created_at": utc_now(), "summary": summary}
        plans[phase] = pending
        ctx.state.save()
    summary = pending["summary"]
    if destroy and any(change["action"] != "delete" for change in summary["changes"]):
        raise Blocked("The destroy plan contains non-delete changes; nothing was applied.")
    if check:
        check(summary)
    ctx.log(f"Saved plan {Path(pending['path']).name}: {describe(summary)} (sha256 {pending['sha256'][:12]}).")
    risky = [change for change in summary["changes"] if change["action"] not in ("create", "update")]
    for change in risky:
        ctx.log(f"  {change['action']}: {change['address']}")
    identity = [change["address"] for change in summary["changes"]
                if change["address"].startswith(IDENTITY_TARGET + ".")]
    if identity and phase not in ("identity", "destroy"):
        raise Blocked("This plan changes the Fabric user-auth registration: " + ", ".join(identity),
                      "Only the initial identity bootstrap changes it automatically. Review it with plain Terraform; "
                      "permission changes need administrator re-consent and a fresh sign-in.")
    if destroy and not ctx.args.allow_delete:
        raise NeedsApproval("Teardown deletes the environment and its data.",
                            f"Inspect: terraform -chdir=infra show {pending['path']}",
                            f"Then rerun down with --allow-delete --approve destroy={pending['sha256'][:12]}.",
                            approval=approval_record(phase, "Delete the environment and its data", pending))
    if risky and not destroy and not ctx.args.allow_replace:
        raise NeedsApproval(f"The {phase} plan deletes or replaces resources; review it before anything changes.",
                            f"Inspect: terraform -chdir=infra show {pending['path']}",
                            f"To apply exactly this plan, rerun with --allow-replace --approve {phase}={pending['sha256'][:12]}.",
                            approval=approval_record(phase, f"Apply the saved {phase} plan", pending))
    approve(ctx, phase, f"Apply the saved {phase} plan ({describe(summary)})" + (f"; {note}" if note else ""), pending)
    if before_apply:
        before_apply()
    result = terraform(ctx, "apply", "-input=false", pending["path"], token=git_token_required(ctx),
                       web_iq=web_iq_key_required(ctx, destroy), stream=True)
    discard_plan(ctx, phase)
    if not result.code:
        # Every other saved plan was made against the state this apply changed, so Terraform would refuse it.
        for other in list(plans):
            ctx.log(f"Discarding the saved {other} plan, which this apply made stale.")
            discard_plan(ctx, other)
    ctx.forget()
    if result.code:
        stale = " The saved plan was stale." if "stale" in result.stdout.lower() else ""
        fabric_refused = (re.search(r"with fabric_|api\.fabric\.microsoft\.com|InsufficientScopes", result.stdout)
                          and DENIED.search(result.stdout))
        hints = fabric_login_steps(ctx) if fabric_refused else ()
        raise Blocked(f"terraform apply failed during {phase}; state is preserved.{stale}",
                      "Investigate the error above, then rerun: a fresh plan is created from the current state.",
                      *(("Right after consent, Fabric can refuse until it propagates or you refresh the scoped sign-in:",)
                        + hints if hints else ()), detail=result.stdout)
    return "applied"


def retry_after(response, default=5):
    try:
        return min(max(int(response.headers.get("retry-after", default)), 1), 60)
    except (TypeError, ValueError):
        return default


def fabric_login_steps(ctx):
    auth = ctx.cache.get("outputs", {}).get("fabric_user_auth") or ctx.state.data.get("consent") or {}
    if auth.get("login_scope"):
        return (f"Run: az login --use-device-code --tenant {auth.get('tenant_id')} --scope {auth['login_scope']}",
                "Then select your subscription again and rerun deploy.py up.")
    return ("Sign in again with az login, then rerun deploy.py up.",)


def fabric(ctx, method, path, body=None):
    """One Fabric REST call: honor 429 and retry only idempotent reads on transient failures."""
    if not path.startswith("/v1/") or "://" in path:
        raise Blocked(f"Refusing an unexpected Fabric path: {path}")
    attempt = 0
    while True:
        attempt += 1
        token = access_token(ctx, resource=FABRIC_API)
        if not token:
            raise Waiting("Azure CLI could not get a Fabric token for the signed-in user.", *fabric_login_steps(ctx))
        try:
            response = ctx.http.request(method, FABRIC_API + path, headers={"Authorization": f"Bearer {token}"},
                                        body=body, attempts=1)
        except TransportError:
            if method == "GET" and attempt < 5 and ctx.remaining() > 30:
                ctx.sleep(10)
                continue
            raise
        if response.status == 429 and attempt < 10:
            ctx.sleep(retry_after(response, 10))
            continue
        if method == "GET" and response.status in (500, 502, 503, 504) and attempt < 5:
            ctx.sleep(10)
            continue
        return response


def fabric_json(ctx, method, path, body=None, label=None):
    response = fabric(ctx, method, path, body)
    if response.status not in (200, 201):
        raise ServiceError(label or f"Fabric {method} {path}", response)
    return response.json(default={})


def operation_id(response, label):
    try:
        return str(UUID(response.headers.get("x-ms-operation-id")))
    except (TypeError, ValueError):
        raise Blocked(f"{label} was accepted without a usable operation receipt; inspect Fabric before retrying.") from None


def wait_operation(ctx, operation, label, *, result, delay=5):
    """Poll the canonical Fabric operation endpoint; never resubmit the accepted request."""
    while True:
        if ctx.remaining() < delay:
            raise Waiting(f"{label} is still running (operation {operation}).",
                          f"Rerun {ctx.rerun} to keep observing it; nothing is resubmitted.")
        ctx.sleep(delay)
        response = fabric(ctx, "GET", f"/v1/operations/{operation}")
        if response.status != 200:
            raise ServiceError(f"Reading operation {operation}", response)
        body = response.json(default={})
        status = body.get("status")
        if status == "Succeeded":
            break
        if status not in ("NotStarted", "Running"):
            error = body.get("error") or {}
            raise Blocked(f"{label} {status or 'returned an unknown status'} (operation {operation}): "
                          f"{error.get('errorCode', '')} {str(error.get('message', ''))[:300]}".rstrip(": "))
        delay = retry_after(response, 5)
    if not result:
        return None
    response = fabric(ctx, "GET", f"/v1/operations/{operation}/result")
    if response.status != 200:
        raise ServiceError(f"Reading the result of operation {operation}", response)
    return response.json(default={})


def fabric_lro(ctx, method, path, body=None, *, result=True, label=None):
    label = label or f"Fabric {method} {path}"
    response = fabric(ctx, method, path, body)
    if response.status in (200, 201):
        return response.json(default={})
    if response.status != 202:
        raise ServiceError(label, response)
    return wait_operation(ctx, operation_id(response, label), label, result=result, delay=retry_after(response))


def git(ctx, *arguments):
    result = run(ctx, ["git", *arguments])
    if result.code:
        raise Blocked(f"git {' '.join(arguments[:2])} failed.\n{tail(result.stderr)}")
    return result.stdout.strip()


def origin_repository(ctx):
    try:
        return runtime.remote_repository(git(ctx, "remote", "get-url", "origin"))
    except runtime.PreflightError as error:
        raise Blocked(str(error)) from None


def published_commit(ctx, branch):
    line = git(ctx, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if not line:
        raise Blocked(f"Branch {branch} is not published on origin; push it before deploying.")
    return line.split()[0]


def reviewed_commit(ctx, branch):
    """Bootstrap only the clean checkout of the reviewed, published revision."""
    remote = published_commit(ctx, branch)
    head = git(ctx, "rev-parse", "HEAD")
    if head != remote:
        raise Blocked(f"Published origin/{branch} is at {remote[:12]}, but this checkout is at {head[:12]}.",
                      f"Check out and pull {branch} so the deployed commit is the one you reviewed.")
    if git(ctx, "status", "--porcelain", "--", "workspace"):
        raise Blocked("workspace/ has uncommitted changes; commit and push them before syncing Fabric.")
    return head


def workspace_items(ctx):
    items = {}
    logical_ids = set()
    for path in sorted((ctx.root / "workspace").glob("*/.platform")):
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
            raise Blocked("Workspace definitions cannot be symlinks.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name, kind, logical = data["metadata"]["displayName"], data["metadata"]["type"], data["config"]["logicalId"]
        except (OSError, ValueError, KeyError, TypeError):
            raise Blocked(f"{path.relative_to(ctx.root)} is not a valid Fabric .platform file.") from None
        logical = guid(logical, f"{path.parent.name} logicalId")
        if name in items or logical in logical_ids:
            raise Blocked(f"Duplicate workspace display name or logical ID in {path.parent.name}.")
        logical_ids.add(logical)
        items[name] = {"type": kind, "logical_id": logical, "directory": path.parent.name}
    for _, kind, name in SCENARIO.items + (("plan", "DeploymentPlan", SCENARIO.plan),):
        if (items.get(name) or {}).get("type") != kind:
            raise Blocked(f"workspace/ must define the scenario {kind} {name}; the runner deploys SCENARIO's items.")
    return items


def workspace_definition_parts(ctx, expected):
    """Import only tracked definition files from the reviewed workspace, never local artifacts."""
    folders = {item["directory"]: name for name, item in expected.items()}
    parts = {name: [] for name in expected}
    workspace = (ctx.root / "workspace").resolve()
    for filename in sorted(filter(None, git(ctx, "ls-files", "-z", "--", "workspace").split("\0"))):
        relative = Path(filename).relative_to("workspace")
        if len(relative.parts) < 2 or relative.parts[0] not in folders:
            continue
        path = ctx.root / filename
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(workspace):
            raise Blocked(f"Not a regular workspace definition file: {filename}.")
        parts[folders[relative.parts[0]]].append({
            "path": "/" + relative.as_posix(), "payloadType": "InlineBase64",
            "payload": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    for name, item in expected.items():
        platform = f"/{item['directory']}/.platform"
        if len(parts[name]) < 2 or platform not in {part["path"] for part in parts[name]}:
            raise Blocked(f"{name} needs tracked .platform and definition files.")
    return parts


def definition_digest(parts):
    """What the runner records for a deployed definition: the sha256 of its exact parts."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def git_token_required(ctx):
    return variables(ctx)["fabric_git_enabled"]


def phase_signin(ctx):
    """Complete every sign-in this run's phases need, back to back, before anything is created or changed."""
    github_sign_in(ctx)
    origin, _ = project_repository(ctx)  # Refuses the template and its forks before any other sign-in.
    # Three rounds of sign-ins, each checked afterwards.
    for attempt in range(4):
        pending, done = missing_sign_ins(ctx)
        if not pending:
            return "; ".join([f"GitHub CLI for {origin}", *done])
        if attempt < 3:
            complete_sign_ins(ctx, pending)
    directory = any(command[:2] == ["az", "login"] and "--scope" not in command for _, command, _ in pending)
    raise Waiting("Sign-in is still incomplete: " + "; ".join(label for label, *_ in pending) + ".",
                  *(f"Run: {quoted(command)}" for _, command, _ in pending),
                  "If the subscription belongs to another directory, rerun with --tenant <tenant-id>." if directory else "",
                  "Use the deploying user's account in the deployment tenant for every CLI, "
                  f"then rerun deploy.py {ctx.args.command}.")


def github_sign_in(ctx):
    """GitHub CLI reads the repository before anything else; its browser flow needs a terminal."""
    status = ["gh", "auth", "status", "--hostname", "github.com"]
    if not run(ctx, status).code:
        return
    login = ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"]
    if ctx.interactive:
        complete_sign_ins(ctx, [("GitHub CLI", login, ())])
        if not run(ctx, status).code:
            return
    raise Waiting("Sign in to GitHub CLI first; the runner reads this repository with it.",
                  f"Run: {quoted(login)}", f"Then rerun deploy.py {ctx.args.command}.")


def missing_sign_ins(ctx):
    """Return (missing device-code sign-ins as (label, command, follow-ups), completed sign-ins)."""
    pending, done = [], []
    tenant, subscription, login = azure_sign_in(ctx)
    reselect = (["az", "account", "set", "--subscription", subscription],) if subscription else ()
    if login:
        access = f" with access to subscription {subscription}" if subscription else ""
        pending.append((f"Azure CLI as the deploying user{access}", login, reselect))
        return pending, done
    audiences = token_audiences(ctx)
    failing = [(label, request) for label, request in audiences
               if not access_token(ctx, tenant=tenant, **request)]
    relogin = ["az", "login", "--use-device-code", "--tenant", tenant]
    if failing and failing[0] == audiences[0]:
        # Without an Azure Resource Manager token, the whole Azure CLI sign-in has expired.
        pending.append(("Azure CLI (expired sign-in)", relogin, reselect))
    elif failing:
        pending += [(f"Azure CLI for {label}",
                     relogin + ["--scope", request.get("scope") or request["resource"] + "/.default"], reselect)
                    for label, request in failing]
    else:
        done.append(f"Azure CLI in tenant {tenant} (tokens for "
                    + ", ".join(label for label, _ in audiences) + ")")
    return pending, done


def azure_sign_in(ctx):
    """Return (tenant, subscription, the Azure CLI login command when the deploying user must sign in)."""
    choices = ctx.state.section("choices")
    subscription = ctx.args.subscription or choices.get("subscription")
    expected = ctx.args.tenant or choices.get("tenant")
    login = ["az", "login", "--use-device-code", *(["--tenant", expected] if expected else [])]
    result = run(ctx, ["az", "account", "show", "--only-show-errors", "--output", "json",
                       *(["--subscription", subscription] if subscription else [])])
    if result.code:
        # Not signed in, or this sign-in cannot see the chosen subscription.
        return expected, subscription, login
    try:
        account = json.loads(result.stdout)
    except ValueError:
        raise Blocked("Azure CLI did not return its account as JSON.") from None
    if (account.get("user") or {}).get("type") != "user":
        raise Blocked("Azure CLI is signed in as a service principal; the bundled notebooks need the deploying user.",
                      f"Run: {quoted(login)}", f"Then rerun deploy.py {ctx.args.command}.")
    tenant = guid(account.get("tenantId"), "Azure CLI tenant")
    if expected and not same_guid(tenant, expected):
        return expected, subscription, login
    return tenant, subscription, None


def token_audiences(ctx):
    """The Azure CLI token audiences that this run's phases and native tools request."""
    later = set(ctx.phases) - {"signin", "config"}
    audiences = [("Azure Resource Manager", {"resource": ARM_API + "/"})]
    if later:
        audiences.append(("Microsoft Graph", {"resource": GRAPH_API}))
    if later - {"preflight"}:
        audiences.append(("Fabric", {"resource": FABRIC_API}))
        login_scope = (ctx.state.data.get("consent") or {}).get("login_scope")
        # Teardown deletes the registration behind this scope, so a resumed `down` must not require it.
        if login_scope and "destroy" not in ctx.phases:
            audiences.append(("the Fabric deployment scope", {"scope": login_scope}))
    if "verify" in ctx.phases:
        audiences.append(("Azure AI Search", {"resource": SEARCH_RESOURCE}))
    if "agent" in ctx.phases:
        audiences.append(("Microsoft Foundry", {"scope": FOUNDRY_SCOPE}))
    return audiences


def phase_config(ctx):
    origin, metadata = project_repository(ctx)
    if not (ctx.infra / "terraform.tfvars").exists():
        create_configuration(ctx, origin, metadata)
    extra = sorted(path.name for path in ctx.infra.iterdir() if path.name == "terraform.tfvars.json"
                   or path.name.endswith((".auto.tfvars", ".auto.tfvars.json")))
    if extra:
        raise Blocked(f"Auto-loaded Terraform inputs exist ({', '.join(extra)}); the runner uses only infra/terraform.tfvars.")
    backend = backend_mode(ctx)
    values = variables(ctx)
    configured = f"{values['github_owner']}/{values['github_repository_name']}"
    if configured.lower() != origin.lower():
        raise Blocked(f"infra/terraform.tfvars targets {configured}, but this checkout's origin is {origin}.")
    published_commit(ctx, values["github_branch_name"])
    return (f"{values['project_name']} in {values['location']} from {configured}@{values['github_branch_name']}"
            f"{values['github_directory_name']}; {backend}")


def project_repository(ctx):
    origin = origin_repository(ctx)
    result = run(ctx, ["gh", "repo", "view", origin, "--json", "nameWithOwner,isTemplate,defaultBranchRef"])
    if result.code:
        raise Blocked(f"GitHub CLI could not read {origin}; sign in with `gh auth login`.")
    try:
        metadata = json.loads(result.stdout)
    except ValueError:
        raise Blocked("GitHub CLI did not return repository metadata.") from None
    if str(metadata.get("nameWithOwner", "")).lower() != origin.lower():
        raise Blocked("GitHub CLI resolved a different repository than origin.")
    # Forks inherit isTemplate, so this also refuses forks of the template.
    if metadata.get("isTemplate") is not False:
        raise Blocked("This is the reusable template, not a derived project.",
                      "Create your own repository with Use this template and deploy from it.")
    if ctx.infra.is_symlink():
        raise Blocked("Refusing a symlinked infra directory.")
    return origin, metadata


def create_configuration(ctx, origin, metadata):
    args = ctx.args
    missing = [flag for flag, value in (("--project-name", args.project_name), ("--location", args.location),
                                        ("--capacity-admin", args.capacity_admin)) if not value]
    if missing:
        raise Waiting("infra/terraform.tfvars does not exist yet.",
                      f"Rerun with {' '.join(missing)} (and optionally --github-branch), or use /initialize-project.")
    branch = args.github_branch or (metadata.get("defaultBranchRef") or {}).get("name")
    if not branch:
        raise Blocked("Could not resolve the repository's published default branch; pass --github-branch.")
    command = [sys.executable, ctx.root / ".github/skills/initialize-project/scripts/initialize_project.py",
               "--project-name", args.project_name, "--location", args.location,
               "--github-repository", origin, "--github-branch", branch]
    for admin in args.capacity_admin:
        command += ["--capacity-admin", admin]
    result = run(ctx, command)
    if result.code:
        raise Blocked((result.stderr or result.stdout).strip())
    ctx.log(f"Created infra/terraform.tfvars for {args.project_name} in {args.location} ({origin}@{branch}).")


def backend_mode(ctx):
    declared = any(re.search(r'(?m)^\s*backend\s+"[A-Za-z0-9_]+"', path.read_text(encoding="utf-8"))
                   for path in ctx.infra.glob("*.tf"))
    config = choose(ctx, "backend_config", ctx.args.backend_config)
    if config and not declared:
        raise Blocked("--backend-config needs a backend block in infra/*.tf.")
    if config and not (ctx.root / config).is_file():
        raise Blocked(f"Backend configuration {config} does not exist.")
    if declared:
        return "remote backend" + (f" ({config})" if config else "")
    if not choose(ctx, "local_state", True if ctx.args.local_state else None):
        raise Waiting("No remote Terraform backend is declared in infra/.",
                      "Declare your secured remote backend (required for team or CI use), or",
                      "rerun with --local-state to accept single-user local state that you back up securely.")
    return "local state (accepted with --local-state)"


def phase_preflight(ctx):
    for tool in ("terraform", "az", "git", "gh"):
        if not ctx.shell.which(tool):
            raise Blocked(f"{tool} is not installed; use the repository's devcontainer or Codespace.")
    version = json.loads(checked(ctx, ["terraform", "version", "-json"], "terraform version").stdout)["terraform_version"]
    if not (1, 11) <= tuple(int(part) for part in version.split(".")[:2]) < (2, 0):
        raise Blocked(f"Terraform {version} is outside the supported >= 1.11, < 2.0 range.")
    account = signed_in_account(ctx)
    if (account.get("user") or {}).get("type") != "user":
        raise Blocked("Sign in to Azure CLI as a delegated user; a service principal cannot run the bundled notebooks.")
    if ctx.args.subscription:
        resolved = az_json(ctx, ["account", "show", "--subscription", ctx.args.subscription],
                           "Resolving --subscription", quiet=True)
        choose(ctx, "subscription", resolved["id"])
    subscription = ctx.state.section("choices").get("subscription")
    if not subscription:
        raise Waiting(f"Choose the deployment subscription explicitly (signed in to {account.get('name')}, {account.get('id')}).",
                      "Rerun with --subscription <id-or-name>.")
    if account.get("id") != subscription:
        checked(ctx, ["az", "account", "set", "--subscription", subscription], "Selecting the subscription", quiet=True)
        account = signed_in_account(ctx)
    tenant = choose(ctx, "tenant", guid(account.get("tenantId"), "Azure CLI tenant"))
    user = claims(access_token(ctx, resource=ARM_API + "/")).get("oid")
    if not user:
        raise Blocked("Could not identify the signed-in Azure user.")
    target = ctx.state.section("target")
    recorded = (target.get("tenant_id"), target.get("subscription_id"), target.get("user_object_id"))
    if any(recorded) and recorded != (tenant, subscription, user):
        raise Blocked("The signed-in user, tenant or subscription differs from the one that started this deployment.",
                      "Keep the same Terraform caller: switching it changes app ownership and role assignments.")
    target.update(tenant_id=tenant, subscription_id=subscription, user_object_id=user)
    ctx.state.save()
    try:
        runtime.check_environment(subscription, tenant)
    except runtime.PreflightError as error:
        raise Blocked(str(error)) from None
    notes = [f"user {user} in tenant {tenant}, subscription {subscription}"]
    values = variables(ctx)
    notes.append(check_web_iq(ctx, subscription, values))
    if values["fabric_git_enabled"]:
        check_pat(ctx, values)
    az_json(ctx, ["ad", "sp", "show", "--id", POWER_BI_APP_ID, "--query", "{id:id}"],
            "Reading the tenant's Microsoft Power BI Service principal", quiet=True)
    foundry_api(ctx)
    if values["work_iq_enabled"]:
        work_iq_api(ctx)
    addresses = state_addresses(ctx)
    if "azurerm_fabric_capacity.main" not in addresses:
        notes.append(check_fabric_quota(ctx, subscription, values))
    else:
        notes.append(check_capacity_resize(ctx, subscription, values))
    missing = [key for key, address in MODEL_DEPLOYMENTS.items() if address not in addresses]
    if missing:
        notes.append(check_model_quota(ctx, values, missing))
    # Checked on every run: a changed tier or region replaces Search, which state cannot reveal here.
    notes.append(check_search_tier(ctx, subscription, values))
    ctx.log("Not checked up front, because administrator access is assumed: Fabric tenant settings ("
            + ("Git/GitHub sync, " if values["fabric_git_enabled"] else "") + "ontology and "
            "deployment-plan previews, OneLake external access)"
            + ("" if values["search_sku"] == "serverless" else ", dedicated Search new-service capacity")
            + (", and for Work IQ a Microsoft 365 mailbox per user, a Copilot Credits spending policy that covers the "
               "Work IQ API (a Microsoft 365 Copilot licence does not replace it), administrator consent to "
               "WorkIQAgent.Ask and offline_access, and each user's one-time consent" if values["work_iq_enabled"] else "")
            + ". A refusal names the grant to request.")
    return "; ".join(notes)


def service_principal_exists(ctx, app_id):
    """Whether the tenant has the service principal of a published application; a transient failure is retried."""
    result = read_cli(ctx, ["az", "ad", "sp", "show", "--id", app_id, "--query", "{id:id}",
                            "--only-show-errors", "--output", "json"])
    if not result.code:
        return True
    text = f"{result.stderr}\n{result.stdout}"
    if re.search(r"does not exist|not found|NotFound", text, re.I):
        return False
    raise Blocked(f"Could not read the tenant's service principal for {app_id} (exit {result.code}).\n{tail(text)}")


def foundry_api(ctx):
    """The app's sign-in registration requests Microsoft Foundry's user_impersonation; Terraform reads its principal."""
    if not service_principal_exists(ctx, FOUNDRY_API_APP_ID):
        raise Blocked("This tenant has no service principal for Microsoft Foundry's API (Azure Machine Learning "
                      f"Services, {FOUNDRY_API_APP_ID}), which the app's sign-in requests access to.",
                      f"An administrator runs: az ad sp create --id {FOUNDRY_API_APP_ID}",
                      "Then rerun deploy.py up.")


def work_iq_api(ctx):
    """With work_iq_enabled, the Work IQ registration declares WorkIQAgent.Ask; Terraform reads Work IQ's principal."""
    if not service_principal_exists(ctx, WORK_IQ_APP_ID):
        raise Blocked(f"This tenant has no service principal for Work IQ ({WORK_IQ_APP_ID}), which the agent's mail "
                      "tool calls: the tenant is not enabled for the Work IQ API.",
                      f"A Global Administrator runs once: az ad sp create --id {WORK_IQ_APP_ID}",
                      "Then rerun deploy.py up, or set work_iq_enabled = false.")


def check_web_iq(ctx, subscription, values):
    if not values["web_iq_terms_accepted"]:
        raise Waiting("Web IQ is required, but its terms and paid usage have not been accepted.",
                      "Review https://www.microsoft.com/en-us/webiq/webiq-legal and docs/getting-started.md#web-iq.",
                      "Only after accepting, set web_iq_terms_accepted = true in infra/terraform.tfvars.")
    provider = az_json(ctx, ["provider", "show", "--namespace", "Microsoft.WebIQ", "--subscription", subscription],
                       "Reading the Web IQ resource provider")
    types = (provider or {}).get("resourceTypes") or []
    accounts = next((item for item in types if item.get("resourceType") == "accounts"), {})
    if WEB_IQ_API_VERSION not in (accounts.get("apiVersions") or []):
        raise Blocked("This subscription does not advertise the pinned Web IQ accounts API.",
                      "Ask your Microsoft representative to enable Web IQ for a paid subscription; Azure credits do not work.",
                      "Provider registration alone does not prove subscription eligibility.")
    return ("Web IQ global G3 required; Terraform registers Microsoft.WebIQ; paid-subscription eligibility "
            "is proven only by account creation; portal AppID linking remains manual")


def signed_in_account(ctx):
    result = run(ctx, ["az", "account", "show", "--only-show-errors", "--output", "json"])
    if result.code:
        tenant = ctx.args.tenant or ctx.state.section("choices").get("tenant")
        sign_in(ctx, ["az", "login", "--use-device-code", *(["--tenant", tenant] if tenant else [])],
                "Azure CLI as the deploying user")
        result = checked(ctx, ["az", "account", "show", "--only-show-errors", "--output", "json"], quiet=True)
    account = json.loads(result.stdout)
    if ctx.args.tenant and not same_guid(account.get("tenantId"), ctx.args.tenant):
        raise Blocked(f"Azure CLI is signed in to tenant {account.get('tenantId')}, not --tenant {ctx.args.tenant}.")
    return account


def check_pat(ctx, values):
    token = ctx.environ.get("TF_VAR_github_token") or ctx.environ.get("FABRIC_GITHUB_TOKEN")
    if not token:
        raise Waiting("The Fabric GitHub PAT is not available on this host.", *PAT_STEPS)
    owner, repository = values["github_owner"], values["github_repository_name"]
    directory = urllib.parse.quote(values["github_directory_name"])
    url = (f"{GITHUB_API}/repos/{owner}/{repository}/contents{directory}"
           f"?ref={urllib.parse.quote(values['github_branch_name'], safe='')}")
    response = ctx.http.request("GET", url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    if response.status != 200:
        raise Blocked(f"The Fabric GitHub PAT cannot read {owner}/{repository} on {values['github_branch_name']} "
                      f"(HTTP {response.status}).", "Use a fine-grained PAT for this repository with Contents read/write.")


def check_fabric_quota(ctx, subscription, values, current_sku=None):
    location = values["fabric_location"] or values["location"]
    usages = az_json(ctx, ["rest", "--method", "get", "--url",
                           f"{ARM_API}/subscriptions/{subscription}/providers/Microsoft.Fabric/locations/"
                           f"{location}/usages?api-version=2023-11-01"], "Reading Fabric CU quota")
    try:
        quota = runtime.fabric_quota_check(usages, location, values["fabric_capacity_sku"], current_sku=current_sku)
    except runtime.PreflightError as error:
        raise Blocked(str(error)) from None
    if current_sku:
        return (f"Fabric {location}: {quota['available_cus']} CU available for {current_sku} -> {quota['sku']} "
                f"(+{quota['required_cus']} CU)")
    return f"Fabric {location}: {quota['available_cus']} CU available for {quota['sku']}"


def check_capacity_resize(ctx, subscription, values):
    """A larger SKU on the existing capacity needs regional quota for the added CUs; a new region, for all of them."""
    resource = outputs(ctx).get("fabric_capacity_resource_id")
    if not (isinstance(resource, str)
            and resource.lower().startswith(f"/subscriptions/{subscription}/resourcegroups/")
            and "/providers/microsoft.fabric/capacities/" in resource.lower()):
        raise Blocked("Terraform state has a Fabric capacity without a resource ID in the selected subscription.",
                      "Inspect `terraform -chdir=infra output fabric_capacity_resource_id`.")
    capacity = az_json(ctx, ["rest", "--method", "get", "--url", f"{ARM_API}{resource}?api-version=2023-11-01"],
                       "Reading the Fabric capacity") or {}
    current, target = (capacity.get("sku") or {}).get("name"), values["fabric_capacity_sku"]
    if not all(isinstance(sku, str) and re.fullmatch(r"F[1-9][0-9]*", sku) for sku in (current, target)):
        raise Blocked(f"Unexpected Fabric SKU: live {current!r}, configured {target!r}.")
    location = values["fabric_location"] or values["location"]
    if re.sub(r"\s+", "", str(capacity.get("location", ""))).lower() != location.lower():
        return check_fabric_quota(ctx, subscription, values)
    if int(target[1:]) <= int(current[1:]):
        return f"Fabric capacity {current}" + ("" if target == current else f" -> {target}") + ": no added quota needed"
    return check_fabric_quota(ctx, subscription, values, current_sku=current)


MODEL_DEPLOYMENTS = {"chat_model": "azapi_resource.chat_model", "embedding_model": "azapi_resource.embedding_model",
                     "agent_model": "azapi_resource.agent_model"}


def check_model_quota(ctx, values, missing):
    """Check the quota of the model deployments still to create, summed per quota: chat and agent may share one."""
    location = values["location"]
    usages = az_json(ctx, ["cognitiveservices", "usage", "list", "--location", location], "Reading model quota") or []
    needed = {}
    for key in missing:
        model = values[key]
        entry = needed.setdefault(f"OpenAI.{model['sku']}.{model['name']}", {"capacity": 0, "keys": []})
        entry["capacity"] += model["capacity"]
        entry["keys"].append(key)
    notes = []
    for name, entry in needed.items():
        matches = [usage for usage in usages if (usage.get("name") or {}).get("value") == name]
        if not matches:
            ctx.log(f"Could not read {name} quota in {location}; confirm it before approving the platform plan.")
            notes.append(f"{name} quota unverified")
            continue
        available = (matches[0].get("limit") or 0) - (matches[0].get("currentValue") or 0)
        if available < entry["capacity"]:
            raise Blocked(f"{name} has {available:g} units free in {location}; {' + '.join(entry['keys'])} "
                          f"need{'s' if len(entry['keys']) == 1 else ''} {entry['capacity']:g}.",
                          "Choose another region or capacity explicitly; nothing was provisioned.")
        notes.append(f"{name} {available:g} free")
    return ", ".join(notes)


def check_search_tier(ctx, subscription, values):
    location, sku = values["search_location"] or values["location"], values["search_sku"]
    result = run(ctx, ["az", "rest", "--method", "post", "--url", SEARCH_OFFERINGS, "--only-show-errors", "--output", "json"])
    try:
        regions = {region.get("regionName"): {offer.get("name") for offer in region.get("skus") or []}
                   for region in json.loads(result.stdout or "{}").get("regions") or []}
    except (ValueError, AttributeError):
        regions = {}
    if result.code or not regions:
        ctx.log(f"Could not read Search offerings; confirm {sku} is offered in {location} before approving the platform plan.")
        note = f"Search {sku} in {location} unverified"
    elif sku not in regions.get(location, ()):
        offered = sorted(region for region, skus in regions.items() if sku in skus)
        raise Blocked(f"Search {sku} is not offered in {location}.",
                      f"Regions offering {sku}: {', '.join(offered) or 'none'}.",
                      "After reviewing cross-region data handling, set search_location to one of them, or choose "
                      "another search_sku explicitly; nothing was provisioned.")
    else:
        note = f"Search {sku} offered in {location}"
    return note + ("; " + serverless_feature(ctx, subscription) if sku == "serverless" else "")


def serverless_feature(ctx, subscription):
    """Register the serverless tier's auto-approved subscription preview feature as the assumed subscription owner."""
    url = (f"{ARM_API}/subscriptions/{subscription}/providers/Microsoft.Features/providers/Microsoft.Search"
           "/features/Serverless?api-version=2021-07-01")

    def state():
        feature = az_json(ctx, ["rest", "--method", "get", "--url", url], "Reading the Microsoft.Search/Serverless feature",
                          quiet=True) or {}
        return (feature.get("properties") or {}).get("state") or "NotRegistered"

    scope = ["--subscription", subscription, "--only-show-errors", "--output", "none"]
    register = ["az", "feature", "register", "--namespace", "Microsoft.Search", "--name", "Serverless", *scope]
    reregister = ["az", "provider", "register", "--namespace", "Microsoft.Search", "--wait", *scope]
    owner = (f"A subscription Owner or Contributor runs: {quoted(register)}", f"Then: {quoted(reregister)}",
             'Or set search_sku = "basic" (about 20 minutes to provision).')
    receipt = ctx.state.section("preflight")
    current = state()
    if current == "Registered" and (receipt.get("search_serverless") == subscription or ctx.args.manual_grants):
        return "Microsoft.Search/Serverless registered"
    if current != "Registered" and ctx.args.manual_grants:
        raise Waiting(f"The serverless Search tier needs the subscription's auto-approved Microsoft.Search/Serverless "
                      f"preview feature (currently {current}); --manual-grants leaves its registration to an owner.",
                      *owner)
    if current not in ("Registered", "Pending"):
        ctx.log("Registering the subscription's auto-approved Microsoft.Search/Serverless preview feature that the "
                "serverless Search tier needs.")
        try:
            checked(ctx, register, "Registering Microsoft.Search/Serverless")
        except Blocked as error:
            raise Blocked(error.message, *owner) from None
    for _ in range(18):
        current = state()
        if current == "Registered":
            break
        ctx.sleep(10)
    else:
        raise Waiting("Microsoft.Search/Serverless registration is still pending.", "Rerun deploy.py up in a few minutes.")
    # Re-registering the provider propagates the feature; the receipt keeps later runs from repeating it.
    try:
        checked(ctx, reregister, "Re-registering Microsoft.Search")
    except Blocked as error:
        raise Blocked(error.message, *owner[1:]) from None
    receipt["search_serverless"] = subscription
    ctx.state.save()
    return "Microsoft.Search/Serverless registered and Microsoft.Search re-registered"


def phase_identity(ctx):
    addresses = state_addresses(ctx)
    complete = IDENTITY_ADDRESSES <= addresses
    if addresses & PLATFORM_ADDRESSES:
        if not complete:
            raise Blocked("Platform resources exist, but the Fabric user-auth registration is incomplete in state.",
                          "The targeted bootstrap is only for a fresh deployment. Review a full plan with plain "
                          "Terraform and renew administrator consent.")
        return "registration exists; later plans may not change it"
    if complete:
        return "registration exists in state"

    def only_identity(summary):
        outside = [change["address"] for change in summary["changes"]
                   if not change["address"].startswith(IDENTITY_TARGET + ".")]
        if outside:
            raise Blocked("The targeted identity plan changes resources outside the module: " + ", ".join(outside))
        risky = [change["address"] for change in summary["changes"] if change["action"] not in ("create", "update")]
        if risky:
            raise Blocked("The identity bootstrap plan would delete or replace: " + ", ".join(risky))

    converge(ctx, "identity", target=IDENTITY_TARGET, check=only_identity,
             note=None if ctx.args.manual_grants else
             "the consent phase then grants its declared delegated Fabric scopes tenant-wide as you")
    if not IDENTITY_ADDRESSES <= state_addresses(ctx):
        raise Blocked("The identity apply finished without every registration resource in state.")
    return "registration prepared; consent is next (not a Fabric-ready state)"


def consent_state(ctx, client, resource_app_id, scopes):
    """Read a registration's tenant-wide grant to one resource as (state, target): granted, missing, partial or
    unverified, where `client` is the registration's service principal."""
    resource = az_json(ctx, ["ad", "sp", "show", "--id", resource_app_id, "--query", "{id:id}"],
                       "Reading the resource's service principal", quiet=True)
    target = {"clientId": guid(client, "service_principal_object_id"),
              "resourceId": guid((resource or {}).get("id"), "resource service principal id")}
    token = access_token(ctx, resource=GRAPH_API)
    if not token:
        return "unverified", target
    response = ctx.http.request("GET", f"{GRAPH_API}/v1.0/servicePrincipals/{target['clientId']}/oauth2PermissionGrants",
                                headers={"Authorization": f"Bearer {token}"})
    if response.status in (401, 403):
        return "unverified", target
    if response.status != 200:
        raise ServiceError("Reading the registration's consent grants", response)
    grants = [grant for grant in response.json(default={}).get("value") or []
              if grant.get("consentType") == "AllPrincipals" and same_guid(grant.get("resourceId"), target["resourceId"])]
    if not grants:
        return "missing", target
    missing = sorted(set(scopes) - set(" ".join(grant.get("scope") or "" for grant in grants).split()))
    return ("partial", dict(target, grant=grants[0], missing=missing)) if missing else ("granted", target)


def grant_consent(ctx, registration, scopes, described, state, target):
    """Grant exactly the declared scopes tenant-wide as the signed-in user, who is assumed to be an administrator."""
    manual = (f"An Entra administrator (Cloud Application Administrator or Application Administrator) opens "
              f"{registration.get('api_permissions_url')} and selects Grant admin consent for {described}.")
    rerun = f"Then rerun deploy.py {ctx.args.command}; the runner verifies the grant before continuing."
    if ctx.args.manual_grants:
        raise Waiting(f"Tenant-wide consent for {registration.get('client_id')} is {state}; --manual-grants leaves it "
                      "to an administrator.", manual, rerun)
    token = access_token(ctx, resource=GRAPH_API) or ""
    headers = {"Authorization": f"Bearer {token}"}
    if state == "missing":
        ctx.log(f"Granting tenant-wide consent for exactly {described} as you.")
        response = ctx.http.request("POST", f"{GRAPH_API}/v1.0/oauth2PermissionGrants", headers=headers, body={
            "clientId": target["clientId"], "consentType": "AllPrincipals", "resourceId": target["resourceId"],
            "scope": " ".join(scopes)})
    else:
        grant = target["grant"]
        identifier = urllib.parse.quote(str(grant.get("id") or ""), safe="")
        if not identifier:
            raise Blocked("The existing tenant-wide grant has no ID to complete.", manual)
        ctx.log("Adding the missing declared scopes to the tenant-wide grant as you: " + ", ".join(target["missing"]))
        # Graph replaces the scope list, so keep every scope already granted.
        scope = " ".join(sorted(set((grant.get("scope") or "").split()) | set(scopes)))
        response = ctx.http.request("PATCH", f"{GRAPH_API}/v1.0/oauth2PermissionGrants/{identifier}",
                                    headers=headers, body={"scope": scope})
    if response.status in (401, 403):
        raise MissingPermission(ServiceError("Granting tenant-wide consent", response).message
                                + " This sign-in cannot grant tenant-wide consent.", rerun, grants=[manual])
    # 409: an identical grant appeared meanwhile; the caller reads it back either way.
    if response.status not in (200, 201, 204, 409):
        raise ServiceError("Granting tenant-wide consent", response)


def ensure_consent(ctx, registration, resource_app_id, described, optional=False):
    """Grant, as the assumed administrator, or verify tenant-wide consent for exactly a registration's declared scopes.

    Returns (state, granted_now). An optional grant, which each user can give at first sign-in instead, never stops
    the run when it is left to an administrator (--manual-grants) or this sign-in cannot read it; the caller names
    the administrator's link instead.
    """
    scopes = sorted(registration.get("delegated_scopes") or [])
    client = registration.get("service_principal_object_id")
    state, target = consent_state(ctx, client, resource_app_id, scopes)
    if optional and (state == "unverified" or (state != "granted" and ctx.args.manual_grants)):
        return state, False
    granted_now = state in ("missing", "partial")
    if granted_now:
        grant_consent(ctx, registration, scopes, described, state, target)
        for attempt in range(6):
            state, target = consent_state(ctx, client, resource_app_id, scopes)
            if state == "granted" or attempt == 5:
                break
            ctx.sleep(10)
        if state != "granted":
            raise Waiting("Consent was granted but is not visible yet.", f"Rerun deploy.py {ctx.args.command} in a minute.")
    if state == "unverified":
        if not ctx.args.consent_confirmed:
            raise MissingPermission(
                "This sign-in cannot read the registration's consent grants, so it can neither grant nor verify them.",
                "Then rerun with --consent-confirmed.",
                grants=[f"An Entra administrator grants or confirms tenant-wide consent at "
                        f"{registration.get('api_permissions_url')} for {described}."])
        ctx.log("Consent confirmed with --consent-confirmed; this sign-in cannot read grants to verify it.")
    return state, granted_now


def app_registration(ctx, required):
    """The app's sign-in registration from the app output, or None before the platform apply creates it."""
    app = outputs(ctx).get("app") or {}
    if not app.get("service_principal_object_id"):
        if required:
            raise Blocked("The app output names no sign-in registration (service_principal_object_id); complete the "
                          "platform phase first.")
        return None
    if not same_guid(app.get("tenant_id"), ctx.target("tenant_id")):
        raise Blocked("The app registration's tenant differs from the signed-in deployment tenant.")
    return {"client_id": app.get("client_id"), "service_principal_object_id": app["service_principal_object_id"],
            "api_permissions_url": app.get("api_permissions_url"), "delegated_scopes": list(APP_SCOPES)}


def ensure_app_consent(ctx, required=False):
    """Grant the app registration's Microsoft Foundry sign-in scope tenant-wide, so no user meets Entra's consent
    screen. Optional: without it, each user consents at first sign-in where the tenant allows it.

    Returns a note for the phase's report, or None before the platform apply creates the registration.
    """
    registration = app_registration(ctx, required)
    if registration is None:
        return None
    state, granted_now = ensure_consent(ctx, registration, FOUNDRY_API_APP_ID, APP_CONSENT, optional=True)
    if state == "granted":
        return "the app's Microsoft Foundry sign-in scope is granted tenant-wide" + (" by you just now" if granted_now else "")
    ctx.log(f"The app's Microsoft Foundry sign-in scope is {state} tenant-wide: each user consents at first sign-in, "
            f"unless an Entra administrator grants {APP_CONSENT} for everyone at {registration['api_permissions_url']}.")
    return f"the app's sign-in scope is {state} tenant-wide, so each user consents at first sign-in"


def phase_consent(ctx):
    auth = required_outputs(ctx, "fabric_user_auth")[0]
    tenant = guid(auth.get("tenant_id"), "fabric_user_auth tenant")
    if not same_guid(tenant, ctx.target("tenant_id")):
        raise Blocked("The registration's tenant differs from the signed-in deployment tenant.")
    # Non-secret: lets later runs check the scoped token during the up-front sign-in.
    receipt = ctx.state.section("consent")
    receipt.update(tenant_id=tenant, client_id=auth.get("client_id"), login_scope=auth.get("login_scope"),
                   api_permissions_url=auth.get("api_permissions_url"))
    ctx.state.save()
    state, granted_now = ensure_consent(ctx, auth, POWER_BI_APP_ID,
                                        f"the {len(auth.get('delegated_scopes') or [])} delegated Fabric scopes")
    if granted_now:
        receipt["granted_at"] = utc_now()
        ctx.state.save()
    subscription = ctx.target("subscription_id")
    login = ["az", "login", "--use-device-code", "--tenant", tenant, "--scope", auth["login_scope"]]
    reselect = ["az", "account", "set", "--subscription", subscription]
    if not access_token(ctx, scope=auth["login_scope"], tenant=tenant):
        sign_in(ctx, login, "Azure CLI for the Fabric deployment scope", after=(reselect,))
    paths = ["/v1/workspaces", "/v1/capacities"]
    if git_token_required(ctx):
        paths.append("/v1/connections")
    # A fresh grant can take minutes to reach Fabric: observe it within the phase instead of stopping.
    attempts = 10 if granted_now else 1
    for path in paths:
        for attempt in range(1, attempts + 1):
            response = fabric(ctx, "GET", path)
            if response.status not in (401, 403) or attempt == attempts or ctx.remaining() < 60:
                break
            ctx.log(f"Fabric refused {path} (HTTP {response.status}) right after consent; retrying while it propagates.")
            ctx.tokens.clear()
            ctx.sleep(30)
        if response.status in (401, 403):
            raise Waiting(f"Fabric refused {path} for the signed-in user (HTTP {response.status}).",
                          f"Run: {quoted(login)}", f"Then: {quoted(reselect)}",
                          "Allow a few minutes for consent to propagate, then rerun deploy.py up.")
        if response.status != 200:
            raise ServiceError(f"Reading {path}", response)
    receipt["verified_at"] = utc_now()
    ctx.state.save()
    how = "by you just now" if granted_now else ("confirmed by an administrator" if state == "unverified" else "already")
    # The app's registration exists once the platform plan is applied; until then the app phase grants it.
    app = ensure_app_consent(ctx)
    return (f"consent granted ({how}); delegated Fabric reads verified (writes are proven by the platform apply)"
            + (f"; {app}" if app else ""))


def phase_platform(ctx):
    if variables(ctx)["knowledge_lakehouse_id"] is not None:
        return "covered by the knowledge phase's full plan"
    result = converge(ctx, "platform")
    workspace, name, *_ = required_outputs(ctx, "dev_workspace_id", "dev_workspace_name", "web_iq")
    if git_token_required(ctx):
        required_outputs(ctx, "github_connect_request")
    principal = ensure_search_identity(ctx, required=True)
    ctx.log(f"Web IQ account provisioned; connect its resource_id output at {WEB_IQ_PORTAL}. "
            "AppID onboarding, authenticated search and agent/app integration are not yet verified; see docs/getting-started.md#web-iq.")
    return (f"{result}; DEV workspace {name} ({workspace}); Search identity {principal}; "
            "workspace bootstrap and knowledge setup pending")


def arm(ctx, method, url, body=None):
    """One Azure Resource Manager call: honor throttling and retry only reads on transient failures."""
    attempt = 0
    while True:
        attempt += 1
        token = access_token(ctx, resource=ARM_API + "/")
        if not token:
            raise Waiting("Azure CLI could not get an Azure Resource Manager token.",
                          "Sign in again with az login, then rerun deploy.py up.")
        try:
            response = ctx.http.request(method, url, headers={"Authorization": f"Bearer {token}"}, body=body,
                                        attempts=1)
        except TransportError:
            if method == "GET" and attempt < 5 and ctx.remaining() > 30:
                ctx.sleep(10)
                continue
            raise
        if response.status in (409, 429) and attempt < 10:
            ctx.sleep(retry_after(response, 10))
            continue
        if method == "GET" and response.status in (500, 502, 503, 504) and attempt < 5:
            ctx.sleep(10)
            continue
        return response


def search_principal(service):
    identity = service.get("identity") or {}
    if "systemassigned" in str(identity.get("type") or "").replace(" ", "").lower():
        return identity.get("principalId")
    return None


def ensure_search_identity(ctx, required=False):
    """Enable the Search system identity, which Terraform cannot set on the serverless preview tier.

    Serverless provisioning fails when an identity is requested, and the resource provider answers
    HTTP 500 to the identity update while applying it, so the result is verified, not the status.
    """
    url = outputs(ctx).get("search_management_url")
    if not url:
        if required:
            raise Blocked("The search_management_url output is missing after the apply.")
        return None
    parsed = urllib.parse.urlparse(url)
    if (f"{parsed.scheme}://{parsed.netloc}" != ARM_API or not SEARCH_SERVICE_PATH.fullmatch(parsed.path)
            or "api-version" not in urllib.parse.parse_qs(parsed.query)):
        raise Blocked("The search_management_url output is not a Search service URL on Azure Resource Manager.")

    def observe(identity):
        """Wait, on its own budget, for a healthy service (and its identity); never accept a failed one."""
        for attempt in range(37):
            if attempt:
                ctx.sleep(5)
            response = arm(ctx, "GET", url)
            if response.status != 200:
                raise ServiceError("Reading the Search service", response)
            service = response.json(default={})
            properties = service.get("properties") or {}
            state = f"{properties.get('provisioningState')}/{properties.get('status')}"
            if properties.get("provisioningState") == "Failed":
                raise Blocked(f"The Search service is {state}.",
                              "A serverless service requested with an identity ends in Failed; inspect it before "
                              "replacing it.")
            principal = search_principal(service)
            if state == "Succeeded/running" and (principal or not identity):
                return principal
        raise Blocked(f"The Search service did not become ready{' with a system-assigned identity' if identity else ''} "
                      f"({state}).", f"Inspect: az rest --method get --url '{url}'",
                      "Rerun deploy.py up once it is healthy; do not replace the service blindly.")

    principal = observe(identity=False)
    if principal:
        return principal
    ctx.log("Enabling the Search service's system-assigned identity (Terraform cannot on the serverless preview tier).")
    response = arm(ctx, "PATCH", url, {"identity": {"type": "SystemAssigned"}})
    if response.status == 500:
        ctx.log("Search answered HTTP 500, a known serverless preview defect; verifying the identity instead.")
    elif response.status not in (200, 201, 202):
        raise ServiceError("Enabling the Search system-assigned identity", response)
    principal = observe(identity=True)
    ctx.state.section("platform")["search_identity"] = {"principal_id": principal, "enabled_at": utc_now()}
    ctx.state.save()
    return principal


class SearchCreation(Exception):
    """A full plan creates or replaces serverless Search, whose identity exists only after that apply."""


def refuse_search_creation(summary):
    if any(change["address"] == "azapi_resource.search" and change["action"] in ("create", "replace")
           for change in summary["changes"]):
        raise SearchCreation()


def converge_knowledge(ctx):
    """Apply the full knowledge plan, in two reviewed plans when it creates or replaces serverless Search."""
    ensure_search_identity(ctx)
    staged = (ctx.state.section("pending_plans").get("knowledge") or {}).get("variables") == SEARCH_DEFERRED
    if not staged:
        check = refuse_search_creation if variables(ctx)["search_sku"] == "serverless" else None
        try:
            return converge(ctx, "knowledge", check=check)
        except SearchCreation:
            discard_plan(ctx, "knowledge")
    ctx.log("This plan creates or replaces the serverless Search service, which gets its identity only after "
            "that apply: applying it first with the identity grants deferred, then the grants.")
    converge(ctx, "knowledge", variables=SEARCH_DEFERRED)
    ensure_search_identity(ctx, required=True)
    return converge(ctx, "knowledge")


def same_git_target(actual, expected):
    def normalized(key, value):
        value = str(value or "")
        if key == "directoryName":
            return "/" + value.strip("/")
        return value.lower() if key in ("gitProviderType", "ownerName", "repositoryName") else value

    keys = ("gitProviderType", "ownerName", "repositoryName", "branchName", "directoryName")
    different = [key for key in keys if normalized(key, actual.get(key)) != normalized(key, expected.get(key))]
    if different:
        raise Blocked("DEV is connected to a different Git target (" + ", ".join(different) + ").",
                      "The runner never disconnects or repoints a workspace; review it in Fabric.")


def ensure_git_credentials(ctx, git_path, connection):
    current = fabric_json(ctx, "GET", f"{git_path}/myGitCredentials", label="Reading your Git credentials")
    source = current.get("source")
    if source == "ConfiguredConnection" and same_guid(current.get("connectionId"), connection):
        return
    if source in (None, "None"):
        ctx.log("Setting your Git credentials to the Terraform-managed GitHub connection.")
        fabric_json(ctx, "PATCH", f"{git_path}/myGitCredentials",
                    {"source": "ConfiguredConnection", "connectionId": connection}, label="Updating your Git credentials")
        return
    raise Blocked(f"Your DEV Git credentials use {source}, not the Terraform-managed connection {connection}.",
                  "Review them in Fabric; the runner does not replace existing credentials.")


def incoming_items(changes, expected):
    """Allow only additions of items defined in the reviewed workspace/ folder."""
    known = {item["logical_id"]: name for name, item in expected.items()}
    names = set()
    for change in changes:
        metadata = change.get("itemMetadata") or {}
        name = metadata.get("displayName") or "an item"
        if change.get("conflictType") not in (None, "None"):
            raise Blocked(f"{name} has a Git conflict; resolve it in Fabric. The runner never chooses a side.")
        if change.get("remoteChange") != "Added" or change.get("workspaceChange") not in (None, "None"):
            raise Blocked(f"{name}: incoming {change.get('remoteChange')} with workspace change "
                          f"{change.get('workspaceChange')}. Only additions of reviewed items sync automatically.",
                          "Review this change in Fabric source control.")
        try:
            logical = str(UUID((metadata.get("itemIdentifier") or {}).get("logicalId")))
        except (TypeError, ValueError):
            logical = None
        if logical not in known:
            raise Blocked(f"{name} is not defined by a .platform file in the reviewed workspace/ folder.")
        names.add(known[logical])
    return names


def accepted(ctx, receipts, key, label, response, *, result, **details):
    """Persist an accepted Fabric mutation before polling, so a rerun observes it instead of resubmitting."""
    if response.status in (200, 201):
        value = response.json(default={})
        receipts[key] = {"status": "Succeeded", "at": utc_now(), **details,
                         **({"result": value} if result and isinstance(value, dict) else {})}
        ctx.state.save()
        return value
    if response.status != 202:
        raise ServiceError(label, response)
    try:
        operation = operation_id(response, label)
    except Blocked:
        receipts[key] = {"status": "Unknown", "at": utc_now(), **details}
        ctx.state.save()
        raise
    receipts[key] = {"operation": operation, "status": "Running", "submitted_at": utc_now(), **details}
    ctx.state.save()
    return observe(ctx, receipts, key, label, result=result, delay=retry_after(response, 5))


def observe(ctx, receipts, key, label, *, result, delay=1):
    record = receipts[key]
    try:
        value = wait_operation(ctx, record["operation"], label, result=result, delay=delay)
    except Waiting:
        raise
    except Blocked:
        record["status"] = "Failed"
        ctx.state.save()
        raise
    record["status"] = "Succeeded"
    if result and isinstance(value, dict):
        # Keep the small, non-secret result so a crash before validation cannot lose it.
        record["result"] = value
    ctx.state.save()
    return value


def previous(ctx, receipts, key, label, *, result=False):
    """Resume a running operation from an earlier run, or refuse to resubmit an unresolved one."""
    record = receipts.get(key) or {}
    status = record.get("status")
    if status == "Running":
        ctx.log(f"Observing {label} operation {record['operation']} from the previous run.")
        return True, observe(ctx, receipts, key, label, result=result)
    if status in ("Failed", "Unknown"):
        if not ctx.args.retry_failed:
            what = f"operation {record.get('operation')} failed" if status == "Failed" else "was accepted without a receipt"
            raise Blocked(f"The previous {label} {what}.",
                          "Inspect DEV in Fabric and fix the cause, then rerun with --retry-failed to continue from live state.")
        ctx.log(f"Continuing from live state after the unresolved {label} (--retry-failed).")
        receipts.pop(key)
        ctx.state.save()
    return False, None


def initialize_connection(ctx, receipts, git_path, state, branch):
    """Initialize once, and validate its required action before any sync, even across reruns."""
    if (receipts.get("initialize") or {}).get("validated"):
        return
    resumed, result = previous(ctx, receipts, "initialize", "Initialize Connection", result=True)
    record = receipts.get("initialize") or {}
    if not resumed:
        if record.get("status") == "Succeeded" and "result" in record:
            result = record["result"]
        elif state == "Connected":
            reviewed_commit(ctx, branch)
            ctx.log("Initializing the Git connection with PreferRemote.")
            response = fabric(ctx, "POST", f"{git_path}/initializeConnection", {"initializationStrategy": "PreferRemote"})
            result = accepted(ctx, receipts, "initialize", "Initialize Connection", response, result=True)
        else:
            return
    action = (result or {}).get("requiredAction")
    if action not in ("UpdateFromGit", "None"):
        raise Blocked(f"Initialize Connection requires {action!r}; the runner never commits workspace content to Git.",
                      "Review the DEV workspace and the repository manually.")
    commit = reviewed_commit(ctx, branch)
    if result.get("remoteCommitHash") not in (None, commit):
        raise Blocked("Fabric initialized against a different remote commit than the reviewed checkout.")
    receipts["initialize"]["validated"] = True
    ctx.state.save()


def synchronize(ctx, workspace, status, incoming, expected, receipts, branch):
    commit = reviewed_commit(ctx, branch)
    if status.get("remoteCommitHash") != commit:
        raise Blocked(f"Fabric would sync remote commit {str(status.get('remoteCommitHash'))[:12]}, "
                      f"not the reviewed {commit[:12]}.", "Pull the published branch, review it and rerun.")
    names = incoming_items(incoming, expected)
    ctx.log(f"Synchronizing {len(names)} reviewed item(s) at {commit[:12]}: {', '.join(sorted(names))}.")
    body = {"workspaceHead": status.get("workspaceHead"), "remoteCommitHash": commit,
            "options": {"allowOverrideItems": True}}
    planned = {**body, "options": {**body["options"], "deploymentPlan": {
        "logicalId": expected[SCENARIO.plan]["logical_id"], "referenceType": "ByLogicalId"}}}
    path = f"/v1/workspaces/{workspace}/git/updateFromGit"
    response = fabric(ctx, "POST", path + "?beta=true", planned)
    with_plan = True
    if response.status not in (200, 202):
        error = ServiceError(f"Update From Git with the {SCENARIO.plan} plan", response)
        if error.error_code != "FeatureNotAvailable":
            raise error
        if ctx.args.bootstrap_fallback != "notebooks":
            raise Blocked(error.message, "This tenant rejects attaching a deployment plan to Git sync.",
                          "Rerun with --bootstrap-fallback notebooks to sync the same reviewed commit without the plan, "
                          f"then run {run_order()} as you, in that order.")
        ctx.log("Plan attachment is unavailable; syncing without it (--bootstrap-fallback notebooks).")
        response = fabric(ctx, "POST", path, body)
        with_plan = False
    accepted(ctx, receipts, "update", "Update From Git", response, result=False, commit=commit, with_plan=with_plan)


def list_workspace_items(ctx, workspace):
    items = []
    seen = set()
    continuation = None
    while True:
        query = f"?continuationToken={urllib.parse.quote(continuation, safe='')}" if continuation else ""
        page = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/items{query}", label="Listing DEV items")
        if not isinstance(page.get("value"), list):
            raise Blocked("Fabric did not return an item list; refusing to assume DEV is empty.")
        for item in page["value"]:
            if not isinstance(item, dict) or not same_guid(item.get("workspaceId"), workspace):
                raise Blocked("Fabric returned an item outside the DEV workspace.")
            items.append(item)
        continuation = page.get("continuationToken")
        if not continuation:
            break
        if continuation in seen:
            raise Blocked("Fabric repeated an item-list continuation token.")
        seen.add(continuation)
    return items


def resolve_items(ctx, workspace):
    found = collections.defaultdict(list)
    for item in list_workspace_items(ctx, workspace):
        found[(item.get("type"), item.get("displayName"))].append(item)
    ids = {}
    for key, kind, name in SCENARIO.items:
        matches = found.get((kind, name), [])
        if len(matches) != 1:
            raise Blocked(f"Expected exactly one {kind} named {name} in DEV, found {len(matches)}.")
        if not same_guid(matches[0].get("workspaceId"), workspace):
            raise Blocked(f"Fabric returned {name} outside the DEV workspace.")
        ids[key] = guid(matches[0].get("id"), f"{name} id")
    return ids


def native_item_ids(ctx, workspace, expected):
    items = list_workspace_items(ctx, workspace)
    ids = {}
    for name, definition in expected.items():
        matches = [item for item in items if same_guid(item.get("logicalId"), definition["logical_id"])
                   or (item.get("type"), item.get("displayName")) == (definition["type"], name)]
        if len(matches) > 1:
            raise Blocked(f"DEV has conflicting items for {name}; nothing will be overwritten.")
        if not matches:
            continue
        item = matches[0]
        if (not same_guid(item.get("logicalId"), definition["logical_id"])
                or (item.get("type"), item.get("displayName")) != (definition["type"], name)):
            raise Blocked(f"DEV {name} does not match the reviewed type, name and logical ID.")
        ids[name] = guid(item.get("id"), f"{name} id")
    return ids


def validate_import(ctx, native, expected):
    record = native["import"]
    if record.get("validated"):
        return
    try:
        details = (record.get("result") or {}).get("importItemDefinitionsDetails")
        if not isinstance(details, list):
            raise Blocked("Bulk Import did not return per-item results.")
        ids = {}
        for detail in details:
            if not isinstance(detail, dict):
                raise Blocked("Bulk Import returned an invalid per-item result.")
            name = detail.get("itemDisplayName")
            definition = expected.get(name)
            if (name not in record["items"] or name in ids or not definition
                    or detail.get("itemType") != definition["type"]
                    or not same_guid(detail.get("itemLogicalId"), definition["logical_id"])):
                raise Blocked("Bulk Import returned an unexpected or duplicate item identity.")
            if detail.get("operationStatus") != "Succeeded":
                raise Blocked(f"Bulk Import {name}: {detail.get('operationStatus')!r}; inspect its deployment result.")
            if detail.get("operationType") != "Create":
                raise Blocked(f"Bulk Import unexpectedly updated {name}; inspect concurrent workspace changes.")
            ids[name] = guid(detail.get("itemId"), f"{name} imported id")
        if set(ids) != set(record["items"]):
            raise Blocked("Bulk Import omitted results for requested items.")
    except Blocked:
        record["status"] = "Failed"
        ctx.state.save()
        raise
    native.setdefault("item_ids", {}).update(ids)
    record["validated"] = True
    ctx.state.save()


def bootstrap_native(ctx, workspace, expected, receipts):
    """Import missing reviewed items; replace changed ones only through an approved --update-definitions.

    Returns the names of items whose definitions this run replaced.
    """
    commit = reviewed_commit(ctx, variables(ctx)["github_branch_name"])
    parts = workspace_definition_parts(ctx, expected)
    digests = {name: definition_digest(value) for name, value in parts.items()}
    native = receipts.setdefault("native", {})
    if native.get("workspace_id", workspace) != workspace:
        raise Blocked("Native bootstrap receipts belong to a different DEV workspace.")
    previous(ctx, native, "import", "Bulk Import", result=True)
    if (native.get("import") or {}).get("status") == "Succeeded":
        validate_import(ctx, native, expected)
    record_import_commits(ctx, native)
    ids = native_item_ids(ctx, workspace, expected)
    imported = native.get("item_ids", {})
    for name, item in imported.items():
        if name in expected and ids.get(name) != item:
            raise Blocked(f"Previously imported {name} is missing or changed identity; inspect DEV before retrying.")
    retired = {name: item for name, item in imported.items() if name not in expected}
    if retired != native.get("retired", {}):
        native["retired"] = retired
        ctx.state.save()
    if retired:
        ctx.log(f"No longer defined in workspace/ and left unchanged in DEV: {', '.join(sorted(retired))}. "
                "Removing them is a separate, approved cleanup.")
    updated = update_definitions(ctx, workspace, expected, parts, digests, ids, native, commit)
    recorded = native.setdefault("definitions", {})
    missing = sorted(set(expected) - set(ids))
    if not missing:
        return updated
    body = {"definitionParts": [part for name in missing for part in parts[name]],
            "options": {"allowPairingByName": False, "deploymentPlan": {
                "logicalId": expected[SCENARIO.plan]["logical_id"], "referenceType": "ByLogicalId"}}}
    native["workspace_id"] = workspace
    recorded.update({name: digests[name] for name in missing})
    # A lost response must not cause a duplicate import on the next run.
    native["import"] = {"status": "Unknown", "commit": commit, "items": missing, "submitted_at": utc_now()}
    ctx.state.save()
    ctx.log(f"Importing {len(missing)} reviewed item(s) at {commit[:12]} through Fabric APIs (no Git PAT).")
    path = f"/v1/workspaces/{workspace}/items/bulkImportDefinitions"
    response = fabric(ctx, "POST", path + "?beta=true", body)
    with_plan = True
    if response.status not in (200, 202):
        error = ServiceError(f"Bulk Import with the {SCENARIO.plan} plan", response)
        if error.error_code != "FeatureNotAvailable":
            native["import"]["status"] = "Failed"
            ctx.state.save()
            raise error
        if ctx.args.bootstrap_fallback != "notebooks":
            native.pop("import")
            ctx.state.save()
            raise Blocked(error.message, "This tenant rejects attaching a deployment plan to native import.",
                          "Rerun with --bootstrap-fallback notebooks to import the same definitions without the plan, "
                          f"then run {run_order()} as you, in that order.")
        ctx.log("Plan attachment is unavailable; importing without it (--bootstrap-fallback notebooks).")
        body["options"].pop("deploymentPlan")
        response = fabric(ctx, "POST", path, body)
        with_plan = False
    accepted(ctx, native, "import", "Bulk Import", response, result=True,
             commit=commit, items=missing, with_plan=with_plan)
    validate_import(ctx, native, expected)
    native.setdefault("commits", {}).update({name: commit for name in missing})
    ctx.state.save()
    ids = native_item_ids(ctx, workspace, expected)
    if any(ids.get(name) != item for name, item in native["item_ids"].items() if name in expected):
        raise Waiting("Bulk Import succeeded, but the imported item identities are not visible in DEV yet.",
                      "Inspect DEV and rerun; the accepted import will not be resubmitted.")
    return updated


def record_import_commits(ctx, native):
    """Remember the reviewed commit behind each imported definition, so update reviews can diff from it."""
    record = native.get("import") or {}
    if record.get("validated"):
        commits = native.setdefault("commits", {})
        added = {name: record["commit"] for name in record.get("items", []) if name not in commits}
        if added:
            commits.update(added)
            ctx.state.save()


def finish_update(ctx, native, name):
    record = native["updates"][name]
    native.setdefault("definitions", {})[name] = record["digest"]
    native.setdefault("commits", {})[name] = record["commit"]
    # An updated bootstrap notebook runs again, so its new logic takes effect.
    if name in {scenario_name(step.item) for step in SCENARIO.runs}:
        rerun = native.setdefault("rerun", [])
        if name not in rerun:
            rerun.append(name)
    ctx.state.save()


def update_definitions(ctx, workspace, expected, parts, digests, ids, native, commit):
    """Replace changed definitions of deployed items, approved by the digest of exactly those bytes."""
    recorded = native.setdefault("definitions", {})
    updates = native.setdefault("updates", {})
    done = []
    for name in sorted(updates):
        previous(ctx, updates, name, f"Update of {name}")
        record = updates.get(name) or {}
        if record.get("status") == "Succeeded" and recorded.get(name) != record.get("digest"):
            finish_update(ctx, native, name)
            done.append(name)
    changed = [name for name in sorted(ids) if recorded.get(name) != digests[name]]
    if not changed:
        return done
    commits = native.get("commits", {})
    review = "Review the change: " + "; ".join(
        f"git diff {commits[name][:12]} HEAD -- workspace/{expected[name]['directory']}" if name in commits
        else f"git log -p -- workspace/{expected[name]['directory']}" for name in changed)
    unrecorded = [name for name in changed if name not in recorded]
    fixed = [f"{expected[name]['type']} {name}" for name in changed if expected[name]["type"] not in UPDATABLE_TYPES]
    if not ctx.args.update_definitions:
        steps = ["Native bootstrap imports additions only and never overwrites live definitions implicitly.", review]
        if not unrecorded and not fixed:
            steps.append("To replace them with the reviewed definitions, rerun with --update-definitions "
                         "and approve exactly that change.")
        verb = "exists" if len(changed) == 1 else "exist"
        raise Blocked(f"{', '.join(changed)} already {verb} with changed or unrecorded definitions.", *steps)
    if fixed:
        raise Blocked(f"The runner never replaces the definition of {', '.join(fixed)}: it can hold data or settings.",
                      "Review the change in Fabric and apply or revert it there.")
    if unrecorded:
        raise Blocked(f"{', '.join(unrecorded)} exist in DEV without a definition this runner recorded; it replaces "
                      "only definitions it deployed.", "Inspect the item in Fabric.")
    items = {name: {"id": ids[name], "type": expected[name]["type"], "logical_id": expected[name]["logical_id"],
                    "digest": digests[name]} for name in changed}
    approval = hashlib.sha256(json.dumps({"workspace": workspace, "items": items}, sort_keys=True).encode()).hexdigest()
    summary = {"counts": {"update": len(changed)},
               "changes": [{"action": "update", "address": f"{items[name]['type']} {name}"} for name in changed]}
    approve(ctx, "item-update", f"Replace the live DEV definitions of {', '.join(changed)}",
            {"sha256": approval, "summary": summary, "review": review})
    for name in changed:
        # Item-relative paths; .platform applies only with updateMetadata=true.
        body = {"definition": {"parts": [{**part, "path": part["path"].split("/", 2)[2]} for part in parts[name]]}}
        details = {"item_id": ids[name], "digest": digests[name], "commit": commit, "approval": approval[:12]}
        updates[name] = {"status": "Unknown", "submitted_at": utc_now(), **details}
        ctx.state.save()
        ctx.log(f"Replacing {items[name]['type']} {name} with its reviewed definition at {commit[:12]}.")
        response = fabric(ctx, "POST", f"/v1/workspaces/{workspace}/items/{ids[name]}/updateDefinition"
                                       "?updateMetadata=true", body)
        if response.status not in (200, 201, 202):
            updates[name]["status"] = "Failed"
            ctx.state.save()
        accepted(ctx, updates, name, f"Update of {name}", response, result=False, **details)
        finish_update(ctx, native, name)
        done.append(name)
    return done

ANNOTATION = re.compile(r"(?m)^[ \t]*annotation[ \t]+(ONT_[A-Za-z]+)[ \t]*=[ \t]*(.*?)[ \t]*\r?$")


SQL_SOURCE = re.compile(r'Sql\.Database\(\s*"([^"\r\n]+)"\s*,\s*"([^"\r\n]+)"\s*\)')
# How Fabric stores that expression: ALM tokens it resolves from the pins of the named lakehouse.
ALM_DATABASE = re.compile(r"ONT_ALM_SQL_DATABASE-Lakehouse:([0-9a-fA-F-]{36}):([0-9a-fA-F-]{36})")


def binding_problems(parts, workspace, lakehouse, sql_endpoint):
    """Check the scenario's target-local binding, not Git-normalized placeholders.

    Every partition must carry the ONT_* pins for the DEV lakehouse: graph materialization identifies the source by
    them. Fabric keeps them only when the shared expression reads the lakehouse's SQL analytics endpoint (`sql_endpoint`
    is its host and ID), stored as ALM tokens that name the DEV workspace and lakehouse, and strips them from an
    AzureStorage.DataLake (OneLake) expression. A OneLake binding, exported zero or logical IDs and missing pins all
    need the binder to run.
    """
    problems = []
    for table in SCENARIO.tables:
        text = parts.get(f"tables/{table}.tmdl")
        if text is None:
            problems.append(f"tables/{table}.tmdl is missing")
            continue
        if not re.search(r"(?m)^[ \t]+partition[ \t]", text):
            problems.append(f"{table} has no partition yet")
            continue
        for field, value in (("mode", "directLake"), ("expressionSource", SCENARIO.expression)):
            found = [entry.strip().strip("'") for entry in re.findall(rf"(?m)^[ \t]+{field}:[ \t]+([^\r\n]+)", text)]
            if found != [value]:
                problems.append(f"{table} partition {field} is not {value}")
        pins = dict(ANNOTATION.findall(text))
        if not same_guid(pins.get("ONT_WorkspaceId"), workspace):
            problems.append(f"{table} is not pinned to the DEV workspace")
        if not same_guid(pins.get("ONT_ItemId"), lakehouse):
            problems.append(f"{table} is not pinned to the DEV lakehouse")
        if pins.get("ONT_ItemKind") != "Lakehouse":
            problems.append(f"{table} source kind is not Lakehouse")
    host, database = sql_endpoint

    def reads_dev(server, name):
        if server == "ONT_ALM_SQL_ENDPOINT":
            tokens = ALM_DATABASE.fullmatch(name)
            return bool(tokens) and same_guid(tokens[1], workspace) and same_guid(tokens[2], lakehouse)
        return server.lower() == host.lower() and same_guid(name, database)

    if not any(reads_dev(server, name) for server, name in SQL_SOURCE.findall(parts.get("expressions.tmdl") or "")):
        problems.append("expressions.tmdl does not read the DEV lakehouse SQL endpoint")
    return problems


def live_binding(ctx, workspace, ids, expected):
    ontology_name, lakehouse_name = scenario_name("ontology"), scenario_name("lakehouse")
    ontology = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/ontologies/{ids['ontology']}", label="Reading the ontology")
    if ontology.get("logicalId") and not same_guid(ontology["logicalId"], expected[ontology_name]["logical_id"]):
        raise Blocked(f"{ontology_name} in DEV does not have the reviewed logical ID.")
    if (ontology.get("properties") or {}).get("generation") not in (None, 2):
        raise Blocked(f"{ontology_name} must be a new-experience (generation 2) ontology.")
    lakehouse = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/lakehouses/{ids['lakehouse']}", label="Reading the lakehouse")
    if (lakehouse.get("properties") or {}).get("defaultSchema") != "dbo":
        raise Blocked(f"{lakehouse_name} must be a schema-enabled lakehouse with default schema dbo.")
    endpoint = (lakehouse.get("properties") or {}).get("sqlEndpointProperties") or {}
    sql_endpoint = (str(endpoint.get("connectionString") or ""), str(endpoint.get("id") or ""))
    definition = fabric_lro(ctx, "POST", f"/v1/workspaces/{workspace}/ontologies/{ids['ontology']}/getDefinition",
                            label="Reading the ontology definition")
    parts = {}
    for part in (definition.get("definition") or {}).get("parts") or []:
        if part.get("payloadType") == "InlineBase64":
            try:
                parts[part["path"]] = base64.b64decode(part["payload"], validate=True).decode("utf-8")
            except (KeyError, ValueError, UnicodeDecodeError):
                raise Blocked(f"Could not decode ontology part {part.get('path')}.") from None
    return binding_problems(parts, workspace, ids["lakehouse"], sql_endpoint)


def job_id(response, workspace, item, name):
    # Only the job ID is used; credentials are never sent to the Location host, which can be regional.
    parsed = urllib.parse.urlparse(response.headers.get("location", ""))
    segments = [segment.lower() for segment in parsed.path.split("/") if segment]
    if parsed.scheme != "https" or workspace not in segments or item not in segments:
        raise Blocked(f"{name} was accepted without a recognizable job receipt; inspect Fabric before retrying.")
    return guid(segments[-1], f"{name} job id")


def notebook_parameters(values):
    """Typed RunNotebook parameters; they override the notebook's parameters cell for this run."""
    typed = {}
    for name, value in values.items():
        kinds = ((bool, "bool"), (int, "int"), (float, "float"), (str, "string"))
        kind = next((label for kind, label in kinds if isinstance(value, kind)), None)
        if kind is None:
            raise Blocked(f"Notebook parameter {name} must be a string, integer, number or Boolean.")
        typed[name] = {"value": value, "type": kind}
    return typed


FINISHED = ("Completed", "Failed", "Cancelled", "Deduped")


def failure_reason(instance):
    reason = instance.get("failureReason")
    return str((reason or {}).get("message", "") if isinstance(reason, dict) else reason or "")[:300]


def run_notebook(ctx, workspace, item, name, receipts, *, skip_completed, parameters=None):
    """Run one notebook as the signed-in user, never duplicating an active run: an active run is observed, and a
    recorded failure needs --retry-failed."""
    jobs = receipts.setdefault("jobs", {})
    instances = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/items/{item}/jobs/instances",
                            label=f"Listing {name} runs").get("value") or []
    active = [instance for instance in instances if instance.get("status") in ("NotStarted", "InProgress")]
    recorded = jobs.get(name) or {}
    if active:
        job = guid(active[0].get("id"), f"{name} job id")
        ctx.log(f"Observing the active {name} run {job} instead of submitting another.")
    else:
        if recorded.get("status") in ("Failed", "Cancelled", "Deduped") and not ctx.args.retry_failed:
            raise Blocked(f"The previous {name} run {recorded.get('id')} {recorded['status']}: {recorded.get('failure', '')}".rstrip(": "),
                          "Inspect it in Fabric, fix the cause, then rerun with --retry-failed.")
        if skip_completed and any(instance.get("status") == "Completed" for instance in instances):
            ctx.log(f"{name} already completed in DEV; it is not rerun.")
            return
        ctx.log(f"Running {name} as you; Spark startup can take several minutes.")
        body = {"executionData": {"parameters": notebook_parameters(parameters)}} if parameters else None
        response = fabric(ctx, "POST", f"/v1/workspaces/{workspace}/notebooks/{item}/jobs/execute/instances?jobType=RunNotebook",
                          body)
        if response.status != 202:
            raise ServiceError(f"Starting {name}", response)
        job = job_id(response, workspace, item, name)
        jobs[name] = {"id": job, "status": "Submitted", "submitted_at": utc_now(), "parameters": parameters}
        ctx.state.save()
    delay = 15
    while True:
        response = fabric(ctx, "GET", f"/v1/workspaces/{workspace}/items/{item}/jobs/instances/{job}")
        if response.status != 200:
            raise ServiceError(f"Reading {name} run {job}", response)
        body = response.json(default={})
        status = body.get("status")
        if status in FINISHED:
            break
        if status not in ("NotStarted", "InProgress"):
            raise Blocked(f"{name} run {job} reported an unknown status {status!r}.")
        if ctx.remaining() < delay:
            raise Waiting(f"{name} run {job} is still {status}.",
                          f"Rerun {ctx.rerun} to keep observing it; nothing is resubmitted.")
        ctx.sleep(delay)
    reason = failure_reason(body)
    # An observed run that this runner did not submit has unknown parameters.
    known = same_guid(job, (jobs.get(name) or {}).get("id"))
    jobs[name] = {"id": job, "status": status, "failure": reason, "finished_at": utc_now(),
                  "parameters": parameters if known else None}
    ctx.state.save()
    if status != "Completed":
        raise Blocked(f"{name} run {job} {status}: {reason}".rstrip(": "),
                      "Inspect the run in Fabric; after fixing the cause, rerun with --retry-failed.")


def bootstrap_git(ctx, workspace, expected, receipts):
    request = required_outputs(ctx, "github_connect_request")[0]
    branch = request["gitProviderDetails"]["branchName"]
    git_path = f"/v1/workspaces/{workspace}/git"
    connection = fabric_json(ctx, "GET", f"{git_path}/connection", label="Reading the DEV Git connection")
    state = connection.get("gitConnectionState")
    if state == "NotConnected":
        reviewed_commit(ctx, branch)
        ctx.log("Connecting DEV to the configured repository, branch and directory.")
        fabric_json(ctx, "POST", f"{git_path}/connect", request, label="Connecting DEV to Git")
        connection = fabric_json(ctx, "GET", f"{git_path}/connection", label="Reading the DEV Git connection")
        state = connection.get("gitConnectionState")
    same_git_target(connection.get("gitProviderDetails") or {}, request["gitProviderDetails"])
    ensure_git_credentials(ctx, git_path, request["myGitCredentials"]["connectionId"])
    if state not in ("Connected", "ConnectedAndInitialized"):
        raise Blocked(f"Unexpected DEV Git connection state {state!r}.")
    initialize_connection(ctx, receipts, git_path, state, branch)
    previous(ctx, receipts, "update", "Update From Git")
    status = fabric_lro(ctx, "GET", f"{git_path}/status", label="Reading Git status")
    incoming = [change for change in status.get("changes") or [] if change.get("remoteChange") not in (None, "None")]
    if incoming:
        synchronize(ctx, workspace, status, incoming, expected, receipts, branch)


def phase_bootstrap(ctx):
    workspace = guid(required_outputs(ctx, "dev_workspace_id")[0], "dev_workspace_id")
    expected = workspace_items(ctx)
    receipts = ctx.state.section("bootstrap")
    git_enabled = variables(ctx)["fabric_git_enabled"]
    updated = []
    if git_enabled:
        if ctx.args.update_definitions:
            raise Blocked("--update-definitions replaces natively imported items only.",
                          "With Fabric Git synchronization, review and apply changed items through Fabric source control.")
        bootstrap_git(ctx, workspace, expected, receipts)
    else:
        updated = bootstrap_native(ctx, workspace, expected, receipts)
    native = receipts.get("native") or {}
    ids = resolve_items(ctx, workspace)
    problems = live_binding(ctx, workspace, ids, expected)
    rerun = list(native.get("rerun", ()))
    if problems or rerun:
        if problems:
            ctx.log("Binding not verified yet (" + "; ".join(problems) + "); running the bootstrap notebooks.")
        for step in SCENARIO.runs:
            name = scenario_name(step.item)
            if not (problems or name in rerun):
                continue
            run_notebook(ctx, workspace, ids[step.item], name, receipts, parameters=step.parameters,
                         skip_completed=step.skip_completed and name not in rerun)
            if name in rerun:
                native["rerun"].remove(name)
                ctx.state.save()
        problems = live_binding(ctx, workspace, ids, expected)
        if problems:
            raise Blocked("The bootstrap notebooks completed, but the binding is not verified: " + "; ".join(problems))
    receipts.update(lakehouse_id=ids["lakehouse"], ontology_id=ids["ontology"], verified_at=utc_now())
    ctx.state.save()
    ctx.log("Review the generated DEV binding before promotion."
            + (" Commit it through Fabric source control." if git_enabled else ""))
    notes = [f"seeded and bound in DEV: lakehouse {ids['lakehouse']}, ontology {ids['ontology']}"]
    if updated:
        notes.append("updated " + ", ".join(updated))
    if native.get("retired"):
        notes.append("left retired items in DEV: " + ", ".join(sorted(native["retired"])))
    return "; ".join(notes)


def phase_knowledge(ctx):
    workspace = guid(required_outputs(ctx, "dev_workspace_id")[0], "dev_workspace_id")
    ids = resolve_items(ctx, workspace)
    values = variables(ctx)
    registered = (values.get("knowledge_ontologies") or {}).get(SCENARIO.registration) or {}
    if not (same_guid(values.get("knowledge_lakehouse_id"), ids["lakehouse"])
            and same_guid(registered.get("ontology_id"), ids["ontology"])):
        command = [sys.executable, SCRIPTS / "configure_knowledge.py", "--lakehouse-id", ids["lakehouse"],
                   "--ontology-id", ids["ontology"], "--registration", SCENARIO.registration,
                   "--description", SCENARIO.registration_description]
        result = run(ctx, command)
        if result.code == knowledge.REPLACEMENT_REQUIRED:
            # An earlier scenario's registration: the reviewed knowledge plan then replaces its sources.
            approve(ctx, "knowledge-registration", result.stdout.strip().rstrip("."))
            result = run(ctx, command + ["--replace"])
        if result.code:
            raise Blocked((result.stderr or result.stdout).strip(),
                          "The runner fills the starter's null/empty knowledge settings, or replaces a registration it "
                          "wrote after approval; review custom registrations manually.")
        ctx.log("Set the verified physical DEV lakehouse and ontology GUIDs in infra/terraform.tfvars.")
        ctx.forget()
    result = converge_knowledge(ctx)
    values = outputs(ctx)
    source = (values.get("ontology_knowledge_sources") or {}).get(SCENARIO.registration) or {}
    sources = folder_sources(values)
    problems = [f"{name} is missing" for name in ("knowledge_base_name", "ontology_check_knowledge_base_name",
                                                  "knowledge_base_mcp_endpoint", "knowledge_base_connection_id")
                if not values.get(name)]
    if not sources.get(SCENARIO.document_source):
        problems.append(f"knowledge_source_names has no {SCENARIO.document_source} source")
    if values.get("knowledge_setup_pending") is not False:
        problems.append("knowledge_setup_pending is not false")
    if not (same_guid(source.get("ontology_id"), ids["ontology"]) and same_guid(source.get("workspace_id"), workspace)):
        problems.append("the scenario ontology source does not point at DEV")
    if problems:
        raise Blocked("Knowledge registration is incomplete: " + "; ".join(problems))
    report = (f"{result}; knowledge base {values['knowledge_base_name']} with OneLake folder sources "
              f"({', '.join(sorted(sources))}); live ontology source in {values['ontology_check_knowledge_base_name']}")
    change = work_iq_change(ctx, values)
    if change:
        ctx.log(change)
    return report + (f"; {change}" if change else "")


def work_iq_change(ctx, values):
    """A reminder when the applied Work IQ setting differs from the one the agents were last published with.

    The apply adds or removes the work-iq connection at once, but the with-IQ agent gains or loses its mail tool only
    when the agent phase publishes its changed definition.
    """
    current = work_iq_enabled(values)
    published = {receipt.get("work_iq_enabled") for receipt in (ctx.state.data.get("agents") or {}).values()
                 if isinstance(receipt, dict) and "work_iq_enabled" in receipt}
    if not published or published == {current}:
        return None
    return (f"work_iq_enabled is now {str(current).lower()}, but the agents were published with it "
            f"{str(not current).lower()}: until the agent phase publishes the changed with-IQ agent, it "
            + ("lacks the Work IQ mail tool" if current else "still names the removed work-iq connection")
            + ". Run deploy.py up through the agent phase.")


def folder_sources(values):
    """The indexed OneLake folder sources by knowledge_folders key, whether or not the knowledge base attaches them."""
    sources = values.get("knowledge_source_names")
    if not isinstance(sources, dict):
        return {}
    return {key: name for key, name in sources.items() if isinstance(name, str) and name}


def search_api_version(values):
    """The Search data-plane API version Terraform manages the knowledge objects with."""
    query = urllib.parse.urlparse(str(values.get("knowledge_base_mcp_endpoint") or "")).query
    version = dict(urllib.parse.parse_qsl(query)).get("api-version")
    if not version:
        raise Blocked("The knowledge_base_mcp_endpoint output has no api-version; run the knowledge phase first.")
    return version


def knowledge_url(endpoint, collection, name, action, version):
    """A Search data-plane URL on the deployed service: an object, or an action on it such as retrieve."""
    path = f"{endpoint}/{collection}/{urllib.parse.quote(name, safe='')}" + (f"/{action}" if action else "")
    return f"{path}?api-version={urllib.parse.quote(version, safe='')}"


def search_call(ctx, method, url, body=None, *, user_header=False):
    token = access_token(ctx, resource=SEARCH_RESOURCE)
    if not token:
        raise Waiting("Azure CLI could not get a Search token for the signed-in user.", "Sign in again, then rerun.")
    headers = {"Authorization": f"Bearer {token}"}
    if user_header:
        # The same delegated user token authenticates Search and the live Fabric source.
        headers["x-ms-query-source-authorization"] = token
    try:
        # Its callers poll or retry within their own budgets, and a public-context retrieval is sent once.
        return ctx.http.request(method, url, headers=headers, body=body, timeout=150, attempts=1)
    except TransportError:
        return Response(599)


def ingestion_state(body):
    """Return ready once a sync completed cleanly; the indexedOneLake retrieval proves the content.

    The service's per-sync average is an integer that floors to 0 after later no-op syncs, so it
    cannot show that a document was ever indexed.
    """
    last = body.get("lastSynchronizationState") or {}
    if not last:
        return "pending", "no synchronization has completed yet"
    errors = last.get("errors") or []
    failed = last.get("itemsUpdatesFailed") or 0
    if errors or failed:
        messages = [str((error or {}).get("errorMessage") or (error or {}).get("message") or error)[:200]
                    if isinstance(error, dict) else str(error)[:200] for error in errors[:3]]
        return "failed", "; ".join(messages) or f"{failed} item(s) failed"
    return "ready", {"items_processed_last": last.get("itemsUpdatesProcessed") or 0,
                     "synchronizations": (body.get("statistics") or {}).get("totalSynchronization") or 0}


def wait_ingestion(ctx, url, source="the knowledge source"):
    failed_syncs = {}
    while True:
        response = search_call(ctx, "GET", url)
        if response.status == 200:
            body = response.json(default={})
            verdict, detail = ingestion_state(body)
            if verdict == "ready":
                return detail
            if verdict == "failed":
                # One failed sync can be RBAC propagation; the same failure on two syncs needs a person.
                failed_syncs[(body.get("lastSynchronizationState") or {}).get("endTime")] = detail
                if len(failed_syncs) >= 2:
                    raise Blocked(f"Document ingestion of {source} failed on consecutive synchronizations: {detail}",
                                  "Fix the cause (Search identity access to DEV, file content), then rerun.")
                ctx.log(f"The last synchronization of {source} reported errors ({detail}); waiting for the next one.")
            else:
                ctx.log(f"Waiting for document ingestion of {source}: {detail}.")
        elif response.status in (401, 403, 404, 429, 500, 502, 503, 599):
            ctx.log(f"Ingestion status is not readable yet (HTTP {response.status}); permissions can take minutes to propagate.")
        else:
            raise ServiceError("Reading ingestion status", response)
        if ctx.remaining() < 30:
            failure = f" Its last synchronization reported errors: {list(failed_syncs.values())[-1]}." if failed_syncs else ""
            raise Waiting(f"Document ingestion of {source} has not completed within the observation window.{failure}",
                          "Each source indexes when it is created and then on its indexing schedule; rerun deploy.py "
                          "up to keep observing it.")
        ctx.sleep(30)


def ontology_evidence(body, source, facts):
    name = source["knowledge_source_name"]
    problems = []
    activity = [entry for entry in body.get("activity") or [] if entry.get("type") == "fabricOntology"
                and entry.get("knowledgeSourceName") in (None, name)]
    if not activity:
        problems.append(f"no fabricOntology activity for {name}")
    elif any(entry.get("error") for entry in activity):
        problems.append("the fabricOntology activity reported an error")
    references = [entry for entry in body.get("references") or [] if entry.get("type") == "fabricOntology"
                  and same_guid(entry.get("workspaceId"), source["workspace_id"])
                  and same_guid(entry.get("ontologyId"), source["ontology_id"])]
    if not references:
        problems.append("no fabricOntology reference for the DEV workspace and ontology")
    raw = "\n".join(str((entry.get("sourceData") or {}).get("fabricRawData") or "") for entry in references)
    missing = [fact for fact in facts if fact.casefold() not in raw.casefold()]
    if references and missing:
        problems.append("live source data lacks " + ", ".join(missing))
    return problems, {"activity": len(activity), "references": len(references), "facts": list(facts)}


def document_evidence(body, name):
    problems = []
    activity = [entry for entry in body.get("activity") or [] if entry.get("type") == "indexedOneLake"
                and entry.get("knowledgeSourceName") in (None, name)]
    references = [entry for entry in body.get("references") or [] if entry.get("type") == "indexedOneLake"]
    if not activity:
        problems.append(f"no indexedOneLake activity for {name}")
    elif any(entry.get("error") for entry in activity):
        problems.append("the indexedOneLake activity reported an error")
    if not references:
        problems.append("no indexedOneLake reference")
    return problems, {"activity": len(activity), "references": len(references)}


def public_context_evidence(body, name):
    problems = []
    activity = [entry for entry in body.get("activity") or [] if entry.get("type") == "mcpServer"
                and entry.get("knowledgeSourceName") in (None, name)]
    references = [entry for entry in body.get("references") or [] if entry.get("type") == "mcpServer"]
    if not activity:
        problems.append(f"no mcpServer activity for {name}")
    elif any(entry.get("error") for entry in activity):
        problems.append("the Web IQ tool call reported an error")
    if not references:
        problems.append("no cited Web IQ reference")
    return problems, {"activity": len(activity), "references": len(references)}


def save_evidence(ctx, name, body):
    directory = ctx.state.directory / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{name}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
    return path.relative_to(ctx.root)


def prove_retrieval(ctx, url, question, parameters, evaluate, attempts=3, intents=False):
    """Read-only retrieval with bounded retries; assert structured evidence, never answer text.

    A knowledge base at minimal reasoning effort refuses messages: it takes the question as a search intent."""
    query = ({"intents": [{"type": "semantic", "search": question}]} if intents
             else {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]})
    body = {**query, "knowledgeSourceParams": [parameters]}
    problems = []
    last = None
    for attempt in range(1, attempts + 1):
        response = search_call(ctx, "POST", url, body, user_header=True)
        if response.status == 200:
            last = response.json(default={})
            problems, evidence = evaluate(last)
            if not problems:
                evidence["response"] = str(save_evidence(ctx, parameters["kind"], last))
                return evidence
        else:
            error = ServiceError(f"Retrieval from {parameters['knowledgeSourceName']}", response)
            if response.status not in (403, 408, 429, 500, 502, 503, 504, 599):
                raise error
            problems = [error.message]
        ctx.log(f"Retrieval attempt {attempt} did not prove {parameters['kind']}: {'; '.join(problems)}.")
        if attempt == attempts or ctx.remaining() < 60:
            break
        ctx.sleep(20)
    saved = f"Inspect the last response: {save_evidence(ctx, parameters['kind'] + '-failed', last)}" if last else None
    raise Blocked(f"{parameters['kind']} retrieval is not verified: {'; '.join(problems)}", saved,
                  "Check the knowledge source, your Fabric and Search data access, and RBAC propagation.",
                  "Never accept a document-only answer as live ontology evidence.")


def verify_web_iq(ctx):
    account, = required_outputs(ctx, "web_iq")
    resource_id = account.get("resource_id") if isinstance(account, dict) else None
    subscription = re.escape(ctx.target("subscription_id"))
    if not isinstance(resource_id, str) or not re.fullmatch(
            rf"/subscriptions/{subscription}/resourceGroups/[^/?#]+/providers/Microsoft\.WebIQ/accounts/[^/?#]+",
            resource_id, re.IGNORECASE):
        raise Blocked("The Web IQ output is not an account in the selected subscription.")
    receipt = ctx.state.section("web_iq")
    receipt.clear()
    receipt.update(resource_id=resource_id, checked_at=utc_now(), status="provisioning_unverified",
                   agent_app_integration="unverified")
    ctx.state.save()
    url = f"{ARM_API}{resource_id}?api-version={WEB_IQ_API_VERSION}"
    response = arm(ctx, "GET", url)
    if response.status != 200:
        raise ServiceError("Reading the Web IQ account", response)
    body = response.json(default={})
    if not isinstance(body, dict) or str(body.get("id", "")).lower() != resource_id.lower():
        raise Blocked("Web IQ returned a different or malformed account.")
    properties = body.get("properties")
    if not isinstance(properties, dict):
        raise Blocked("Web IQ returned malformed account properties.")
    state = properties.get("provisioningState")
    if state != "Succeeded":
        if state in ("Failed", "Canceled"):
            raise Blocked(f"Web IQ account provisioning failed: {state}.")
        raise Waiting(f"Web IQ account is not provisioned yet: {state}.", "Rerun after provisioning completes.")
    receipt["status"] = "onboarding_pending"
    ctx.state.save()
    key = ctx.environ.get("WEB_IQ_API_KEY", "").strip()
    if not key:
        raise Waiting("Web IQ account is provisioned, but authenticated search is unverified.",
                      f"Sign in to {WEB_IQ_PORTAL}, connect the deployed Web IQ resource and create its AppID/API key.",
                      "Supply that resource's key only through WEB_IQ_API_KEY on the deployment host, then rerun.",
                      "Never paste keys into chat, Terraform inputs, files or the browser app; see docs/getting-started.md#web-iq.")
    if any(ord(character) < 33 or ord(character) > 126 for character in key):
        raise Blocked("WEB_IQ_API_KEY contains invalid HTTP header characters; supply a valid portal key.")
    receipt["status"] = "search_unverified"
    ctx.state.save()
    response = ctx.http.request("POST", WEB_IQ_SEARCH, headers={"x-apikey": key}, body={
        "query": WEB_IQ_QUESTION, "maxResults": 3, "contentFormat": "passage",
        "maxLength": 1000, "safeSearch": "strict", "language": "en", "region": "US",
    })
    if response.status != 200:
        # Do not echo data-plane error bodies: an upstream service may reflect credentials.
        raise Blocked(f"Web IQ search failed with HTTP {response.status}; no successful search evidence was recorded.",
                      "Check portal resource/AppID linking, the key's web permission and rate limits. "
                      "The runner does not retry a billable search automatically.")
    body = response.json(default={})
    results = body.get("webResults") if isinstance(body, dict) else None
    if not isinstance(results, list) or not results:
        raise Blocked("Web IQ returned no web results; authenticated grounding is not verified.")
    dated = 0
    for result in results:
        if not isinstance(result, dict):
            raise Blocked("Web IQ returned a malformed web result.")
        source = urllib.parse.urlparse(str(result.get("url") or ""))
        if (source.scheme not in ("http", "https") or not source.hostname or source.username or source.password
                or not isinstance(result.get("title"), str) or not result["title"].strip()
                or not isinstance(result.get("content"), str) or not result["content"].strip()):
            raise Blocked("Web IQ returned a result without a usable citation or grounding passage.")
        dated += bool(result.get("crawledAt") or result.get("lastUpdatedAt"))
    receipt.update(status="search_verified", verified_at=utc_now(), result_count=len(results),
                   dated_result_count=dated, query=WEB_IQ_QUESTION)
    ctx.state.save()
    return (f"Web IQ public-only search verified ({len(results)} cited passages, {dated} with freshness metadata); "
            "portal credential/resource association is operator-confirmed; scenario freshness/conflicts and "
            "agent/app integration remain unverified")


def phase_verify(ctx):
    values = outputs(ctx)
    endpoint = https_origin(values.get("search_endpoint"), "search_endpoint")
    base, check_base = values.get("knowledge_base_name"), values.get("ontology_check_knowledge_base_name")
    sources = folder_sources(values)
    documents = sources.get(SCENARIO.document_source)
    source = (values.get("ontology_knowledge_sources") or {}).get(SCENARIO.registration)
    if not (base and check_base and documents and source):
        raise Blocked("Knowledge registration outputs are missing; run the knowledge phase first.")
    version = search_api_version(values)
    # Every folder source, attached to the agents' knowledge base or not, is indexed before readiness.
    ingestion = {key: wait_ingestion(ctx, knowledge_url(endpoint, "knowledgesources", name, "status", version), name)
                 for key, name in sorted(sources.items())}
    facts = tuple(ctx.args.expect or SCENARIO.expected_facts)
    # The live ontology source sits in its own check-only knowledge base, never in the agents' knowledge base.
    ontology = prove_retrieval(ctx, knowledge_url(endpoint, "knowledgebases", check_base, "retrieve", version),
                               ctx.args.ontology_question, {
        "kind": "fabricOntology", "knowledgeSourceName": source["knowledge_source_name"],
        "includeReferences": True, "includeReferenceSourceData": True},
        lambda body: ontology_evidence(body, source, facts))
    # The agents' knowledge base runs at minimal effort, so the question goes in as a search intent.
    document = prove_retrieval(ctx, knowledge_url(endpoint, "knowledgebases", base, "retrieve", version),
                               ctx.args.document_question, {
        "kind": "indexedOneLake", "knowledgeSourceName": documents,
        "includeReferences": True, "includeReferenceSourceData": True},
        lambda body: document_evidence(body, documents), intents=True)
    web_iq = verify_web_iq(ctx)
    public, web_source = required_outputs(ctx, "public_context_knowledge_base_name", "public_context_knowledge_source_name")
    # One attempt: every retrieval is a billable Web IQ search, which the runner never retries automatically.
    public_context = prove_retrieval(ctx, knowledge_url(endpoint, "knowledgebases", public, "retrieve", version),
                                     SCENARIO.public_context_question, {
        "kind": "mcpServer", "knowledgeSourceName": web_source,
        "includeReferences": True, "includeReferenceSourceData": True},
        lambda body: public_context_evidence(body, web_source), attempts=1)
    work_iq = verify_work_iq(ctx)
    ctx.state.section("verify").update(dev_ready_at=utc_now(), ingestion=ingestion, ontology=ontology, documents=document,
                                       public_context=public_context, work_iq=work_iq)
    ctx.state.save()
    if work_iq["enabled"]:
        mail = (f"Work IQ's {work_iq['connection']} connection is consented for WorkIQAgent.Ask; each user consents to "
                "it once, on their first Work IQ question; Work IQ also needs a Microsoft 365 mailbox per user, "
                "administrator consent to offline_access and a Copilot Credits spending policy that covers the Work IQ "
                "API, which a Microsoft 365 Copilot licence does not replace, and only a user's first Work IQ answer "
                "proves the route")
    else:
        mail = "Work IQ is off (work_iq_enabled = false), so the with-IQ agent has no mail tool"
    return (f"DEV ready: ingestion succeeded for {len(ingestion)} OneLake folder source(s); live ontology retrieval "
            f"through {check_base} and document retrieval through {base} verified with your identity; "
            f"{web_iq}; Web IQ public context retrieved through Foundry IQ "
            f"({public_context['references']} cited references); {mail}")


def verify_work_iq(ctx):
    """The with-IQ agent's Work IQ route, only with work_iq_enabled and without a billable call: its registration's
    tenant, its connection and the tenant-wide grant of WorkIQAgent.Ask.

    Each user's own consent, on their first Work IQ question in the app, and that user's answer prove the route, which
    also needs the user's mailbox and a Copilot Credits spending policy that covers the Work IQ API.
    """
    registration, = required_outputs(ctx, "work_iq")
    if registration.get("enabled") is not True:
        return {"enabled": False}
    if not same_guid(registration.get("tenant_id"), ctx.target("tenant_id")):
        raise Blocked("The Work IQ registration's tenant differs from the signed-in deployment tenant.")
    if not (registration.get("connection_name") and registration.get("oauth_redirect_url")
            and registration.get("mcp_endpoint") == WORK_IQ_MCP_ENDPOINT):
        raise Blocked("The work_iq output has no Work IQ connection with a registered redirect URL.",
                      "Rerun deploy.py up so Terraform creates the work-iq connection.")
    state, _ = ensure_consent(ctx, registration, WORK_IQ_APP_ID, "WorkIQAgent.Ask")
    return {"enabled": True, "connection": registration["connection_name"], "consent": state}


# Foundry Agent Service's data plane, as the deployer: the agents are project objects outside Terraform state.
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
PROJECT_ENDPOINT = re.compile(
    r"https://[a-z0-9][a-z0-9-]{0,62}\.services\.ai\.azure\.com/api/projects/[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# The image's build context and inputs: its tag is their content digest at HEAD, so only an app change builds a new
# image and plans a new revision.
APP_CONTEXT = "app"
APP_BUILD_PATHS = (APP_CONTEXT,)
APP_HEALTH_ATTEMPTS = 30
# The reviewed agent definitions: a published version always corresponds to committed files.
AGENT_PATHS = ("agents",)


def foundry_endpoint(values):
    endpoint = str(values.get("foundry_project_endpoint") or "")
    if not PROJECT_ENDPOINT.fullmatch(endpoint):
        raise Blocked("The foundry_project_endpoint output is not a Foundry project endpoint.")
    return endpoint


def foundry_call(ctx, method, url, body=None):
    """One Foundry Agent Service call with the deployer's token; retries only throttling and transient reads."""
    attempt = 0
    while True:
        attempt += 1
        token = access_token(ctx, scope=FOUNDRY_SCOPE)
        if not token:
            raise Waiting("Azure CLI could not get a Foundry token for you.",
                          "Sign in again with az login as the deploying user, then rerun deploy.py up.")
        try:
            response = ctx.http.request(method, url, headers={"Authorization": f"Bearer {token}"}, body=body,
                                        attempts=1)
        except TransportError:
            if method == "GET" and attempt < 5 and ctx.remaining() > 30:
                ctx.sleep(10)
                continue
            raise
        retry = response.status == 429 or (method == "GET" and response.status in (500, 502, 503, 504))
        if retry and attempt < 5 and ctx.remaining() > 30:
            ctx.sleep(retry_after(response, 5))
            continue
        return response


def foundry_failure(label, response):
    error = ServiceError(label, response)
    if response.status in (401, 403):
        return Blocked(error.message, "The deployer needs Foundry User on the Foundry account, which the platform plan "
                       "grants; a new role assignment can take a few minutes to apply. Then rerun deploy.py up.")
    return error


def work_iq_enabled(values):
    """Whether the work_iq output turns the agent's optional Work IQ mail tool on."""
    return (values.get("work_iq") or {}).get("enabled") is True


def agent_values(values):
    """The placeholder values every agent definition is rendered with, all from Terraform outputs.

    Work IQ's mail tool needs its own only when work_iq_enabled; otherwise the agent phase omits that tool.
    """
    settings = values.get("agent_settings") or {}
    tools = values.get("foundry_tool_connections") or {}
    fabric = (values.get("fabric_iq_connections") or {}).get(SCENARIO.registration) or {}
    knowledge, public, mail = ((tools.get(key) or {}) for key in ("knowledge_base", "public_context", "work_iq_mail"))
    entries = {
        "agent_model_deployment": settings.get("model_deployment"),
        "knowledge_base_mcp_endpoint": knowledge.get("server_url"), "knowledge_base_connection": knowledge.get("name"),
        "public_context_mcp_endpoint": public.get("server_url"), "public_context_connection": public.get("name"),
        "fabric_iq_mcp_endpoint": fabric.get("server_url"), "fabric_iq_connection": fabric.get("name"),
    }
    if work_iq_enabled(values):
        entries.update(work_iq_mail_mcp_endpoint=mail.get("server_url"), work_iq_mail_connection=mail.get("name"))
    missing = sorted(name for name, value in entries.items() if not value)
    if missing:
        raise Blocked("Agent settings are missing from Terraform outputs: " + ", ".join(missing) + ".",
                      "Complete the knowledge phase first: the agents' tools need its knowledge bases and connections.")
    entries["agent_reasoning_effort"] = settings.get("reasoning_effort")
    return entries


def foundry_agents(ctx):
    """Each variant's Foundry agent: its name and display name from Terraform, and its rendered reviewed definition."""
    values = outputs(ctx)
    declared = values.get("foundry_agents")
    if not isinstance(declared, dict) or set(declared) != set(foundry.VARIANTS):
        raise Blocked(f"The foundry_agents output must name exactly the {' and '.join(foundry.VARIANTS)} agents.")
    endpoint = foundry_endpoint(values)
    shared = agent_values(values)
    work_iq = work_iq_enabled(values)
    agents = []
    for key, source in foundry.VARIANTS.items():
        entry = declared[key] if isinstance(declared[key], dict) else {}
        if entry.get("source") != source or not entry.get("name") or not entry.get("display_name"):
            raise Blocked(f"The foundry_agents output's {key} entry must name the agent, its display name and {source}.")
        try:
            # Without Work IQ, a variant leaves out the optional tools it has (the without-IQ variant has none).
            present = {name for name, _ in foundry.load(ctx.root / source)["tools"]}
            omit = frozenset() if work_iq else foundry.OPTIONAL_TOOLS & present
            body = foundry.render(ctx.root / source, {**shared, "agent_display_name": entry["display_name"]}, key,
                                  omit=omit)
            url = foundry.agent_url(endpoint, entry["name"])
        except (foundry.DefinitionError, OSError) as error:
            raise Blocked(str(error)) from None
        agents.append({"key": key, "name": entry["name"], "display_name": entry["display_name"], "source": source,
                       "body": body, "url": url, "receipts": ctx.state.section("agents").setdefault(key, {})})
    return endpoint, agents, work_iq


def live_agent(ctx, agent):
    response = foundry_call(ctx, "GET", agent["url"])
    if response.status == 404:
        return None
    if response.status != 200:
        raise foundry_failure(f"Reading agent {agent['name']}", response)
    live = response.json(default=None)
    if not isinstance(live, dict) or live.get("name") != agent["name"]:
        raise Blocked(f"Foundry returned an unexpected agent for {agent['name']}.")
    return live


def require_committed(ctx, paths, inputs, reason):
    """Refuse uncommitted or untracked changes under `paths`, whatever the user's Git status settings."""
    status = run(ctx, ["git", "status", "--porcelain", "--untracked-files=all", "--", *paths])
    if status.code:
        raise Blocked(f"git status could not read {inputs}.")
    if status.stdout.strip():
        raise Blocked(f"{inputs[0].upper()}{inputs[1:]} have uncommitted changes, and {reason}.",
                      "Commit or discard them (git status -- " + " ".join(paths) + "), then rerun deploy.py up.")


def phase_agent(ctx):
    require_committed(ctx, AGENT_PATHS, "the agent definitions", "a published version must match committed files")
    endpoint, agents, work_iq = foundry_agents(ctx)
    for agent in agents:
        agent["status"] = foundry.status(live_agent(ctx, agent), agent["body"])
    pending = [agent for agent in agents if agent["status"] != "current"]
    if pending:
        approve(ctx, "agent-publish", "; ".join(
            f"{'Create' if agent['status'] == 'missing' else 'Publish a new version of'} Foundry agent "
            f"{agent['name']} ({agent['display_name']}) from {agent['source']} "
            f"(sha256 {agent['body']['metadata'][foundry.DIGEST_KEY][:12]})" for agent in pending))
    for agent in pending:
        created = agent["status"] == "missing"
        digest = agent["body"]["metadata"][foundry.DIGEST_KEY]
        # Recorded before invoking: a publish that may have happened is observed on rerun, never repeated blindly.
        agent["receipts"]["published"] = {"digest": digest, "at": utc_now(), "status": "started"}
        ctx.state.save()
        if created:
            response = foundry_call(ctx, "POST", foundry.agent_url(endpoint), {"name": agent["name"], **agent["body"]})
        else:
            response = foundry_call(ctx, "POST", foundry.agent_url(endpoint, agent["name"], versions=True), agent["body"])
        if response.status not in (200, 201):
            agent["receipts"]["published"]["status"] = "failed"
            ctx.state.save()
            raise foundry_failure(f"{'Creating' if created else 'Publishing a new version of'} agent {agent['name']}",
                                  response)
        published = response.json(default={}) or {}
        version = published.get("version") or ((published.get("versions") or {}).get("latest") or {}).get("version")
        agent["receipts"]["published"].update(status="done", version=version)
        ctx.state.save()
    details = []
    for agent in agents:
        live = live_agent(ctx, agent)
        if foundry.status(live, agent["body"]) != "current":
            raise Blocked(f"Foundry agent {agent['name']} does not run its reviewed definition.",
                          "Inspect it in the Foundry portal, then rerun deploy.py up to publish it again.")
        version = (live.get("versions") or {}).get("latest", {}).get("version")
        # work_iq_enabled is recorded so a later change of it reminds the operator to publish again.
        agent["receipts"].update(name=agent["name"], version=version, digest=agent["body"]["metadata"][foundry.DIGEST_KEY],
                                 work_iq_enabled=work_iq, ready_at=utc_now())
        ctx.state.save()
        tools = [tool["server_label"] for tool in agent["body"]["definition"]["tools"]]
        details.append(f"{agent['name']} v{version} ({', '.join(tools) or 'no tools'})")
    settings = outputs(ctx).get("agent_settings") or {}
    return (f"agents run their reviewed definitions on {settings.get('model')} (the {settings.get('model_deployment')} "
            f"deployment, reasoning effort {settings.get('reasoning_effort') or 'unset'}): " + "; ".join(details)
            + ("" if work_iq else "; Work IQ is off, so the with-IQ agent has no mail tool"))


def app_tag(ctx):
    """The content digest of the image's inputs at HEAD, after refusing uncommitted or untracked changes to them."""
    require_committed(ctx, APP_BUILD_PATHS, "the app's build inputs", "the image is built only from committed files")
    listing = checked(ctx, ["git", "ls-tree", "-r", "HEAD", "--", *APP_BUILD_PATHS], "Listing the app's build inputs",
                      quiet=True)
    if not listing.stdout.strip():
        raise Blocked("HEAD holds none of the app's build inputs.")
    return hashlib.sha256(listing.stdout.encode()).hexdigest()[:12]


def image_built(ctx, registry, repository, tag):
    result = read_cli(ctx, ["az", "acr", "repository", "show-tags", "--name", registry, "--repository", repository,
                            "--only-show-errors", "--output", "json"])
    if result.code:
        # The repository exists only after the first build.
        if re.search(r"not found|not known|does not exist|NAME_UNKNOWN", result.stderr + result.stdout, re.I):
            return False
        raise Blocked(f"Could not list the images in registry {registry}.\n{tail(result.stderr or result.stdout)}")
    try:
        return tag in (json.loads(result.stdout or "[]") or [])
    except ValueError:
        raise Blocked("az acr repository show-tags did not return JSON.") from None


APP_IMAGE_TAG_LINE = re.compile(r'(?m)^app_image_tag[ \t]*=[ \t]*(null|"[0-9a-f]{12}")[ \t]*(?:#[^\r\n]*)?\r?$')


def app_image_tag_line(ctx):
    """The one top-level app_image_tag line of infra/terraform.tfvars, and the file's bytes."""
    path = ctx.infra / "terraform.tfvars"
    if path.is_symlink():
        raise Blocked("Refusing to update a symlinked infra/terraform.tfvars.")
    original = path.read_bytes()
    text = original.decode("utf-8")
    match = APP_IMAGE_TAG_LINE.search(text)
    if len(re.findall(r"(?m)^app_image_tag[ \t]*=", text)) != 1 or not match:
        raise Blocked("infra/terraform.tfvars needs exactly one top-level app_image_tag line, null or a 12-character tag.",
                      "Add `app_image_tag = null` as in infra/terraform.tfvars.example, then rerun deploy.py up.")
    return match, original


def set_app_image_tag(ctx, tag):
    """Write the built tag into infra/terraform.tfvars, replacing only its one app_image_tag value."""
    path = ctx.infra / "terraform.tfvars"
    match, original = app_image_tag_line(ctx)
    text = original.decode("utf-8")
    value = json.dumps(tag)
    if match.group(1) == value:
        return False
    handle = tempfile.NamedTemporaryFile("wb", dir=ctx.infra, prefix="terraform.tfvars.", suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write((text[:match.start(1)] + value + text[match.end(1):]).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(handle.name, 0o600)
        if path.read_bytes() != original:
            raise Blocked("infra/terraform.tfvars changed while the runner prepared it; review it, then rerun.")
        os.replace(handle.name, path)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    return True


def wait_healthy(ctx, origin):
    for attempt in range(APP_HEALTH_ATTEMPTS):
        if attempt:
            ctx.sleep(10)
        try:
            # A starting revision answers 502-504 for a while: this loop polls it on its own budget.
            response = ctx.http.request("GET", f"{origin}/healthz", timeout=15, attempts=1)
        except TransportError:
            continue
        if response.status == 200:
            return
    raise Waiting(f"{origin} does not answer its health check yet.",
                  "Container Apps can take a few minutes to start a new revision; inspect its revisions and logs in the "
                  "Azure portal if it doesn't. Then rerun deploy.py up.")


def phase_app(ctx):
    registry, = required_outputs(ctx, "container_registry")
    name, repository = registry.get("name"), registry.get("repository")
    if not (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9]{5,50}", name) and isinstance(repository, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,127}", repository)):
        raise Blocked("The container_registry output is incomplete.")
    # The app registration exists once the platform plan is applied: grant its sign-in scope before users arrive.
    consent = ensure_app_consent(ctx, required=True)
    tag = app_tag(ctx)
    app_image_tag_line(ctx)
    receipts = ctx.state.section("app")
    if not image_built(ctx, name, repository, tag):
        approve(ctx, "app-build", f"Build image {repository}:{tag} from this checkout's committed {APP_CONTEXT}/ in "
                                  f"registry {name} with ACR Tasks")
        receipts["build"] = {"tag": tag, "started_at": utc_now()}
        ctx.state.save()
        # For a local context, az acr build reads --file from the working directory, the repository root, and uploads
        # it beside the context, which its .dockerignore filters.
        result = run(ctx, ["az", "acr", "build", "--registry", name, "--image", f"{repository}:{tag}",
                           "--file", f"{APP_CONTEXT}/Dockerfile", "--platform", "linux/amd64", "--only-show-errors",
                           APP_CONTEXT], stream=True)
        if result.code:
            raise Blocked(f"az acr build failed (exit {result.code}).\n{tail(result.stdout)}",
                          "Fix the build, commit, then rerun deploy.py up. A registry's rate limit on a base image "
                          "(toomanyrequests) passes: rerun later.",
                          "Where ACR Tasks is unavailable, build and "
                          f"push {repository}:{tag} with Docker (az acr login --name {name}): the runner then finds it.",
                          detail=result.stdout)
        if not image_built(ctx, name, repository, tag):
            raise Blocked(f"ACR Tasks finished, but {repository}:{tag} is not in registry {name}.")
        receipts["build"]["completed_at"] = utc_now()
        ctx.state.save()

    def record_tag():
        # Only after the plan's approval: an earlier phase's full plan must not deploy an unapproved image.
        if set_app_image_tag(ctx, tag):
            ctx.log(f'Set app_image_tag = "{tag}" in infra/terraform.tfvars.')
            ctx.forget()

    result = converge(ctx, "app", variables={"app_image_tag": tag}, before_apply=record_tag)
    app = outputs(ctx).get("app") or {}
    if app.get("image_tag") != tag or not app.get("url"):
        raise Blocked("The app output does not run the built image yet; review the app plan.")
    origin = https_origin(app["url"], "app.url")
    wait_healthy(ctx, origin)
    config = ctx.http.request("GET", f"{origin}/api/config", timeout=15)
    if config.status != 200 or not same_guid((config.json(default={}) or {}).get("clientId"), app.get("client_id")):
        raise Blocked(f"{origin} does not serve this deployment's sign-in registration.",
                      "Inspect the container app's environment variables against terraform output app_settings.")
    receipts.update(url=origin, image_tag=tag, verified_at=utc_now())
    ctx.state.save()
    return (f"{result}; {origin} runs {repository}:{tag} and answers its health check with this deployment's sign-in "
            f"registration; {consent}. Open it and sign in: not proof of consent to the agents' tools or of an answer "
            "(next steps)")


def teardown_snapshot(ctx):
    target = ctx.state.section("target")
    if not all(target.get(key) for key in ("subscription_id", "tenant_id", "user_object_id")):
        raise Blocked("Teardown requires the original deployment receipts and Terraform state.")
    origin, _ = project_repository(ctx)
    if not (ctx.infra / "terraform.tfvars").is_file():
        raise Blocked("Restore the original infra/terraform.tfvars before teardown.")
    extra = [path.name for path in ctx.infra.iterdir() if path.name == "terraform.tfvars.json"
             or path.name.endswith((".auto.tfvars", ".auto.tfvars.json"))]
    if extra:
        raise Blocked("Remove auto-loaded Terraform inputs before teardown: " + ", ".join(sorted(extra)))
    backend_mode(ctx)
    account = signed_in_account(ctx)
    subscription = target["subscription_id"]
    if ctx.args.subscription:
        selected = az_json(ctx, ["account", "show", "--subscription", ctx.args.subscription],
                           "Resolving --subscription", quiet=True)
        if not same_guid(selected.get("id"), subscription):
            raise Blocked("--subscription differs from the recorded deployment target.")
    user = claims(access_token(ctx, resource=ARM_API + "/")).get("oid")
    if (not same_guid(account.get("id"), subscription)
            or not same_guid(account.get("tenantId"), target["tenant_id"])
            or user != target["user_object_id"] or (account.get("user") or {}).get("type") != "user"):
        raise Blocked("Teardown requires the original Azure user, tenant and active subscription.",
                      "Sign in as the deploying user and select the recorded subscription.")
    try:
        runtime.check_environment(subscription, target["tenant_id"])
    except runtime.PreflightError as error:
        raise Blocked(str(error)) from None
    values = variables(ctx)
    if f"{values['github_owner']}/{values['github_repository_name']}".lower() != origin.lower():
        raise Blocked("infra/terraform.tfvars does not match this repository.")
    shown = terraform(ctx, "show", "-json")
    if shown.code:
        raise Blocked("Could not read the deployment's Terraform state.")
    try:
        state = json.loads(shown.stdout)
        state_values = state.get("values") or {}
        resources = {}

        def collect(module):
            for resource in module.get("resources") or []:
                if resource.get("mode") == "managed":
                    resources[resource["address"]] = resource.get("values") or {}
            for child in module.get("child_modules") or []:
                collect(child)

        collect(state_values.get("root_module") or {})
        recorded_outputs = {name: output.get("value") for name, output in (state_values.get("outputs") or {}).items()}
    except (ValueError, AttributeError, KeyError, TypeError):
        raise Blocked("Terraform returned an invalid state document.") from None
    resource_ids = {address: item["id"] for address, item in resources.items() if isinstance(item.get("id"), str)}
    for resource_id in resource_ids.values():
        match = re.match(r"/subscriptions/([^/]+)/", resource_id, re.I)
        if match and not same_guid(match[1], subscription):
            raise Blocked("Terraform state contains resources from another subscription.")
    receipt = ctx.state.section("destroy")
    snapshot = receipt.get("snapshot")
    if snapshot:
        if snapshot["repository"].lower() != origin.lower() or snapshot["project_name"] != values["project_name"]:
            raise Blocked("The project differs from the saved teardown inventory.")
        if any(snapshot["resource_ids"].get(address) != identity for address, identity in resource_ids.items()):
            raise Blocked("Terraform resource identities changed since teardown was prepared; review the inventory.")
        return snapshot
    foundry = resources.get("azapi_resource.foundry") or {}
    foundry_id = foundry.get("id")
    purge = None
    if foundry_id:
        match = re.fullmatch(r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/"
                             r"Microsoft.CognitiveServices/accounts/([^/]+)", foundry_id, re.I)
        if not match or not isinstance(foundry.get("location"), str) or not foundry["location"]:
            raise Blocked("The Foundry account has no valid purge target in Terraform state.")
        purge = {"name": match[3], "resource_group": match[2], "location": foundry["location"]}
    application = (recorded_outputs.get("app") or {}).get("client_id")
    snapshot = {
        "repository": origin, "project_name": values["project_name"], "resource_ids": resource_ids,
        "agents": sorted(entry.get("name") for entry in (recorded_outputs.get("foundry_agents") or {}).values()
                         if isinstance(entry, dict) and entry.get("name")),
        "app_client_id": application,
        "workspaces": {stage: resources[f"fabric_workspace.{stage}"]["id"] for stage in ("dev", "test")
                       if f"fabric_workspace.{stage}" in resources},
        "foundry": purge,
    }
    receipt["snapshot"] = snapshot
    ctx.state.save()
    return snapshot


def purge_foundry(ctx, snapshot):
    account = snapshot.get("foundry")
    if not account:
        return
    subscription = ctx.target("subscription_id")
    path = (f"/subscriptions/{subscription}/providers/Microsoft.CognitiveServices/locations/{account['location']}"
            f"/resourceGroups/{account['resource_group']}/deletedAccounts/{account['name']}")
    url = f"{ARM_API}{path}?api-version=2024-10-01"
    response = arm(ctx, "GET", url)
    if response.status == 404:
        return
    if response.status != 200:
        raise ServiceError("Reading the deleted Foundry account", response)
    if str((response.json(default={}) or {}).get("id", "")).lower() != path.lower():
        raise Blocked("The deleted Foundry account does not match the recorded purge target.")
    approve(ctx, "purge-foundry", f"Permanently purge Foundry account {account['name']} in {subscription}")
    receipt = ctx.state.section("destroy")
    if receipt.get("purge_started_at") and not ctx.args.retry_failed:
        raise Waiting("The Foundry purge is not yet confirmed.",
                      "Inspect the deleted account; use --retry-failed only if the purge must be resubmitted.")
    receipt["purge_started_at"] = utc_now()
    ctx.state.save()
    checked(ctx, ["az", "cognitiveservices", "account", "purge", "--subscription", subscription,
                  "--resource-group", account["resource_group"], "--location", account["location"],
                  "--name", account["name"], "--only-show-errors"], "Purging the Foundry account", quiet=True)
    while True:
        response = arm(ctx, "GET", url)
        if response.status == 404:
            return
        if response.status != 200:
            raise ServiceError("Confirming the Foundry purge", response)
        if ctx.remaining() < 10:
            raise Waiting("The Foundry account is still being purged; rerun deploy.py down to check it.")
        ctx.sleep(10)


def teardown_next(snapshot):
    steps = []
    if snapshot["workspaces"]:
        workspaces = ", ".join(f"{stage}: {identity}" for stage, identity in snapshot["workspaces"].items())
        steps.append(f"Fabric tenant admin: permanently delete retained workspaces in OneLake catalog > Govern > "
                     f"Workspaces ({workspaces}). Retention is not immediate data erasure.")
    steps.append("Microsoft Entra keeps the deleted app registrations (the Fabric user-auth one, the app's sign-in "
                 "registration and, with Work IQ, Work IQ's) under Deleted applications for 30 days; restore or "
                 "permanently delete them there. Tenant-wide and users' consents are removed with their service "
                 "principals.")
    if snapshot.get("agents"):
        steps.append("The Foundry agents (" + ", ".join(snapshot["agents"]) + ") and their stored runs were deleted with "
                     "the Foundry project, and the purge removed the account.")
    steps += [
        "Revoke the Web IQ AppID's keys and remove its portal profile at https://webiq.microsoft.ai/profiles.",
        "Keep Terraform state, tfvars and receipts until cleanup is confirmed. GitHub, backend storage, tenant settings, "
        "licenses, external source data and service-retained backups/audit records are not deleted.",
    ]
    return steps


SMART_DETECTION_GROUP = "Application Insights Smart Detection"


def shared_smart_detection(ctx, snapshot):
    """Alert rules outside this deployment's resource group that notify the Smart Detection action group inside it.

    Azure creates the subscription's Smart Detection action group beside its first Application Insights component and
    reuses it for every later component, wherever it is. When this deployment's resource group holds that group, the
    destroy plan's cleanup deletes it, and the other components' Failure Anomalies rules then notify nobody.
    """
    group = (snapshot.get("resource_ids") or {}).get("azurerm_resource_group.main")
    if not group or ctx.state.section("destroy").get("started_at"):
        return []
    action_group = f"{group}/providers/microsoft.insights/actionGroups/{SMART_DETECTION_GROUP}"
    response = arm(ctx, "GET", f"{ARM_API}{urllib.parse.quote(action_group)}?api-version=2023-01-01")
    if response.status == 404:
        return []
    if response.status != 200:
        raise ServiceError("Reading this deployment's Smart Detection action group", response)
    url = (f"{ARM_API}/subscriptions/{ctx.target('subscription_id')}/providers/Microsoft.AlertsManagement"
           "/smartDetectorAlertRules?api-version=2021-04-01")
    outside = set()
    for _ in range(100):
        response = arm(ctx, "GET", url)
        if response.status != 200:
            raise ServiceError("Listing the subscription's Smart Detection alert rules", response)
        page = response.json(default={}) or {}
        for rule in page.get("value") or []:
            rule_id = str(rule.get("id") or "")
            groups = ((rule.get("properties") or {}).get("actionGroups") or {}).get("groupIds") or []
            if (any(urllib.parse.unquote(str(item)).lower() == action_group.lower() for item in groups)
                    and not rule_id.lower().startswith(group.lower() + "/")):
                outside.add(rule_id)
        url = page.get("nextLink")
        if not url:
            return sorted(outside)
        # Follow only Azure Resource Manager's own paging links.
        if not url.startswith(ARM_API + "/"):
            raise Blocked("Azure Resource Manager returned an unexpected paging link; nothing was deleted.")
    raise Blocked("The subscription lists too many Smart Detection alert rules to check; nothing was deleted.")


def phase_destroy(ctx):
    snapshot = teardown_snapshot(ctx)
    outside = shared_smart_detection(ctx, snapshot)
    if outside:
        shown = ", ".join(outside[:5]) + (f" and {len(outside) - 5} more" if len(outside) > 5 else "")
        approve(ctx, "shared-action-group",
                f"Delete this resource group's {SMART_DETECTION_GROUP} action group, which {len(outside)} alert "
                f"rule(s) outside this deployment also notify ({shown}); their Smart Detection notifications stop. "
                "To keep them, give those rules another action group first, then rerun down")

    def started():
        # From here on, up refuses this deployment's receipts.
        ctx.state.section("destroy").setdefault("started_at", utc_now())
        ctx.state.save()

    # The agents are Foundry project objects and the app a Terraform resource: the destroy plan removes both.
    result = converge(ctx, "destroy", before_apply=started)
    remaining = {address for address in state_addresses(ctx) if not re.search(r"(^|\.)data\.", address)}
    if remaining:
        raise Blocked("Terraform still tracks resources after teardown: " + ", ".join(sorted(remaining)))
    purge_foundry(ctx, snapshot)
    receipt = ctx.state.section("destroy")
    receipt["managed_resources_deleted_at"] = utc_now()
    ctx.state.save()
    return f"{result}; managed resources deleted; retained Fabric data and Entra registrations need manual cleanup"


def permission_grants(ctx, phase, text):
    """Name the manual grant behind an API's refusal for lack of permission; empty for any other failure."""
    subscription = ctx.state.section("target").get("subscription_id") or "the deployment subscription"
    portal = (ctx.state.data.get("consent") or {}).get("api_permissions_url") or "its api_permissions_url"
    grants = []

    def grant(line):
        if line not in grants:
            grants.append(line)

    # Terraform reports one "Error:" block per failed resource; classify each block on its own.
    for block in re.split(r"(?m)^[│|\s]*Error: ", text):
        policy = "RequestDisallowedByPolicy" in block
        if not (DENIED.search(block) or policy):
            continue
        actions = [action.lower() for action in ARM_ACTION.findall(block)]
        if actions or re.search(r"\b(?:Linked)?AuthorizationFailed\b", block):
            rbac = [action for action in actions if "/roleassignments/" in action]
            providers = [action for action in actions if action.endswith("/register/action")]
            if rbac:
                grant(f"A subscription Owner assigns you Role Based Access Control Administrator or User Access "
                      f"Administrator (or Owner) on subscription {subscription}; Terraform creates resource-scoped "
                      "role assignments.")
            if providers:
                grant(f"A subscription Owner or Contributor registers the required resource providers and preview "
                      f"features in subscription {subscription}, or assigns you Contributor there.")
            if not actions or len(rbac) + len(providers) < len(actions):
                grant(f"A subscription Owner assigns you Contributor (or Owner) on subscription {subscription}.")
        if policy:
            grant("An Azure Policy administrator exempts this deployment from the denying assignment named in the "
                  "error, or names the compliant settings to use.")
        if phase in ("identity", "platform", "knowledge", "destroy") and re.search(
                r"Authorization_RequestDenied|insufficient privileges to complete the operation", block, re.I):
            grant("An Entra administrator assigns you Application Developer or Cloud Application Administrator, or "
                  "allows users to register applications, so you can create and manage the deployment app "
                  "registration and its service principal.")
        fabric = phase == "bootstrap" or re.search(r"with fabric_|api\.fabric\.microsoft\.com", block)
        if (fabric and not actions and phase in ("platform", "bootstrap", "knowledge", "destroy")
                and "FeatureNotAvailable" not in block):
            grant(f"An Entra administrator grants the deployment registration's delegated Fabric scopes at {portal}."
                  if "InsufficientScopes" in block else
                  "A Fabric administrator enables the tenant settings in getting started for you (create workspaces, "
                  "deployment pipelines, ontology and deployment-plan items) and lists you in fabric_capacity_admins. "
                  "Right after consent, a refusal can also be propagation: rerun in a few minutes.")
        if phase == "config" and not actions:
            grant("The owner of your Terraform backend grants you read, write and lock access to its state (for "
                  "azurerm: Storage Blob Data Contributor on the state container).")
    return grants


def as_missing_permission(ctx, phase, stop):
    """Report a phase's refusal for lack of permission as the manual grant it needs."""
    grants = permission_grants(ctx, phase, f"{stop.message}\n{stop.detail}")
    if not grants:
        return stop
    return MissingPermission(f"Missing permission: {stop.message}", *stop.next_steps,
                             f"After the grant, rerun deploy.py {ctx.args.command}; completed work is not repeated.",
                             grants=grants)


PHASE_FUNCTIONS = {
    "signin": phase_signin, "config": phase_config, "preflight": phase_preflight, "identity": phase_identity,
    "consent": phase_consent, "platform": phase_platform, "bootstrap": phase_bootstrap,
    "knowledge": phase_knowledge, "verify": phase_verify, "agent": phase_agent, "app": phase_app,
    "destroy": phase_destroy,
}


def run_phases(ctx, phases):
    ctx.state.lock()
    ctx.state.load()
    ctx.phases = phases
    if ctx.args.command == "up" and ctx.state.section("destroy").get("started_at"):
        raise Blocked("This deployment is being torn down; do not resume up with its old receipts.")
    report = {"result": "done", "phases": {}}
    began_run = ctx.clock()
    for phase in phases:
        ctx.start(phase)
        began = ctx.clock()
        try:
            detail = PHASE_FUNCTIONS[phase](ctx)
        except Exception as error:
            stop = error if isinstance(error, Stop) else Blocked(
                f"Unexpected {type(error).__name__} in {phase}: {str(error)[:300]}",
                "This is a runner bug or an unrecognized service response; nothing was retried.")
            if not isinstance(error, Stop):
                traceback.print_exc(file=sys.stderr)
            elif isinstance(error, Blocked):
                stop = as_missing_permission(ctx, phase, error)
            seconds = round(ctx.clock() - began)
            report["phases"][phase] = {"status": stop.result, "detail": stop.message, "seconds": seconds}
            report.update(result=stop.result, phase=phase, message=stop.message, next=stop.next_steps,
                          seconds=round(ctx.clock() - began_run))
            if stop.approval:
                report["approval"] = stop.approval
            if isinstance(stop, MissingPermission):
                report["grants"] = stop.grants
            record_phase(ctx, phase, stop.result, stop.message, seconds)
            return stop.code, report
        seconds = round(ctx.clock() - began)
        ctx.log(f"{detail} ({seconds}s)")
        report["phases"][phase] = {"status": "done", "detail": detail, "seconds": seconds}
        record_phase(ctx, phase, "done", detail, seconds)
    report["seconds"] = round(ctx.clock() - began_run)
    if phases[-1] == "app":
        report["next"] = list(APP_NEXT)
    elif phases[-1] == "destroy":
        report["next"] = teardown_next(ctx.state.section("destroy")["snapshot"])
    return OK, report


def record_phase(ctx, phase, status, detail, seconds):
    ctx.state.section("phases")[phase] = {"status": status, "detail": detail, "seconds": seconds, "at": utc_now()}
    ctx.state.save()


def status_report(ctx):
    ctx.state.load()
    recorded = ctx.state.section("phases")
    report = {"result": "status", "phases": {}, "choices": ctx.state.section("choices")}
    for phase in PHASES + (("destroy",) if "destroy" in recorded else ()):
        entry = recorded.get(phase)
        report["phases"][phase] = ({"status": entry["status"], "detail": entry["detail"], "at": entry["at"],
                                    "seconds": entry.get("seconds")}
                                   if entry else {"status": "not run", "detail": ""})
    pending = ctx.state.section("pending_plans")
    if pending:
        report["pending_plans"] = {phase: {"sha256": plan["sha256"], "summary": describe(plan["summary"]),
                                           "plan": plan["path"]} for phase, plan in pending.items()}
    report["next"] = ["Receipts only; up/down rechecks live state before acting."]
    return OK, report


def print_report(report, as_json):
    if as_json:
        print(json.dumps(report, indent=2))
        return
    for phase, entry in report["phases"].items():
        seconds = f"{entry['seconds']}s" if entry.get("seconds") is not None else ""
        print(f"{phase:<10} {entry['status']:<9} {seconds:>6}  {entry['detail']}")
    if report.get("seconds") is not None:
        print(f"{'total':<10} {'':<9} {report['seconds']:>5}s")
    if report.get("message"):
        print(f"\n{report['result'].upper()}: {report['message']}")
    for step in report.get("next") or []:
        print(f"  - {step}")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(USAGE, f"{self.prog}: error: {message}\n")


def approvals(values, parser):
    granted = {}
    for value in values:
        key, separator, digest = value.partition("=")
        if key not in APPROVAL_KEYS:
            parser.error(f"--approve: unknown key {key!r}; use one of {', '.join(APPROVAL_KEYS)}")
        if key in DIGEST_APPROVALS:
            if not re.fullmatch(r"[0-9a-fA-F]{12,64}", digest):
                parser.error(f"--approve {key} needs the sha256 prefix it printed, for example {key}=1a2b3c4d5e6f")
            granted[key] = digest.lower()
        elif separator:
            parser.error(f"--approve {key} takes no value")
        else:
            granted[key] = True
    return granted


def parse_arguments(argv):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print the final report as JSON on stdout")
    options = argparse.ArgumentParser(add_help=False)
    group = options.add_argument_group("approvals and behavior")
    group.add_argument("--yes", action="store_true",
                       help="approve plans and creations in this run (never deletions or replacements)")
    group.add_argument("--approve", action="append", default=[], metavar="KEY[=SHA]",
                       help="approve a saved plan or item update by hash (identity|platform|knowledge|app|destroy|"
                            "item-update=SHA) or an action (knowledge-registration, agent-publish, app-build, "
                            "purge-foundry, shared-action-group)")
    group.add_argument("--retry-failed", action="store_true", help="resubmit a reviewed failed or unconfirmed operation")
    group.add_argument("--non-interactive", action="store_true",
                       help="never prompt: approvals stop with exit 4; device-code sign-ins still run and print their "
                            "codes to stderr only")
    group.add_argument("--timeout-minutes", type=int, default=30, help="observation window per phase (default 30)")
    group = options.add_argument_group("target (remembered in .artifacts/deploy/state.json)")
    group.add_argument("--subscription", help="Azure subscription ID or name (required on first run)")
    group.add_argument("--tenant", help="expected Entra tenant ID")
    group.add_argument("--local-state", action="store_true", help="accept single-user local Terraform state")
    group.add_argument("--backend-config", help="backend config file, relative to the repository root")
    deployment = argparse.ArgumentParser(add_help=False)
    deployment.add_argument("--allow-replace", action="store_true",
                            help="permit an approved up plan that deletes or replaces")
    group = deployment.add_argument_group("first-use configuration (when infra/terraform.tfvars is missing)")
    group.add_argument("--project-name")
    group.add_argument("--location")
    group.add_argument("--capacity-admin", action="append")
    group.add_argument("--github-branch")
    group = deployment.add_argument_group("explicit gate choices")
    group.add_argument("--manual-grants", action="store_true",
                       help="you are not an administrator or policy requires one: never grant tenant-wide consent or "
                            "register subscription preview features yourself; print them for an administrator")
    group.add_argument("--consent-confirmed", action="store_true",
                       help="an administrator confirmed consent and this sign-in cannot read grants to verify it")
    group.add_argument("--bootstrap-fallback", choices=("notebooks",),
                       help="if plan attachment is FeatureNotAvailable, import/sync without it and run Seed then Bind")
    group.add_argument("--update-definitions", action="store_true",
                       help="replace changed definitions of deployed notebooks, ontologies and deployment plans "
                            "after an exact item-update approval (native import only; never lakehouses)")
    group = deployment.add_argument_group("readiness evidence")
    group.add_argument("--ontology-question", default=SCENARIO.ontology_question)
    group.add_argument("--document-question", default=SCENARIO.document_question)
    group.add_argument("--expect", action="append", help="fact required in live ontology source data (repeatable)")
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("status", parents=[common], help="summarize receipts without cloud calls")
    up = commands.add_parser("up", parents=[common, options, deployment],
                             help="run every phase in order, re-verifying earlier ones, until done or a gate")
    up.add_argument("--through", choices=PHASES, default=PHASES[-1], help="stop after this phase")
    down = commands.add_parser("down", parents=[common, options],
                               help="delete this deployment, its app, agents and ingested data")
    down.add_argument("--allow-delete", action="store_true", help="permit approved environment and data deletion")
    args = parser.parse_args(argv)
    if args.command == "status":
        args.non_interactive, args.timeout_minutes, args.approve = True, 30, []
    if args.timeout_minutes < 1:
        parser.error("--timeout-minutes must be at least 1")
    args.approve = approvals(args.approve, parser)
    return args


def main(argv=None, **context):
    args = parse_arguments(argv)
    ctx = Context(args, **context)
    try:
        if args.command == "status":
            code, report = status_report(ctx)
        else:
            phases = ("signin", "destroy") if args.command == "down" else PHASES[:PHASES.index(args.through) + 1]
            code, report = run_phases(ctx, phases)
    except Stop as stop:
        code, report = stop.code, {"result": stop.result, "phases": {}, "message": stop.message, "next": stop.next_steps}
    print_report(report, args.json)
    return code


if __name__ == "__main__":
    sys.exit(main())
