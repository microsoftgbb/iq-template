#!/usr/bin/env python3
"""Deterministic deployment and teardown of the DEV platform, agent and app.

`up` runs the documented phases in order with native tools only: Terraform,
Azure CLI (including ACR builds) and Fabric/Search/Graph/Foundry/ARM REST. Its
first phase completes every CLI sign-in the run needs, back to back, before
anything is created.
Every phase checks live state first and acts only on what is missing, so a
rerun resumes instead of duplicating work. Plans and creations need approval.

The signed-in user is assumed to administer every target: the runner grants
tenant-wide consent and registers required subscription features as that user,
and names a manual grant only when an API refuses for lack of permission.
Device-code entry and browser sign-in stay with people.

`down` signs in the same way, deletes the Container App, hosted agent and
toolbox, applies a reviewed full
Terraform destroy plan, and purges the deleted Foundry account. Retained Fabric
workspaces and the Web IQ portal profile are reported for manual cleanup. Local
configuration and state stay.

Exit codes: 0 done, 1 usage error, 2 blocked (investigate), 3 waiting for a
person (a sign-in, a missing permission's grant) or a long-running cloud
operation, 4 approval required. Device codes go only to the terminal or stderr;
tokens stay in memory; nothing secret is printed or written.
"""

import argparse
import base64
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
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
import preflight_runtime as runtime  # noqa: E402
import hosted_agent as hosted  # noqa: E402

PHASES = ("signin", "config", "preflight", "identity", "consent", "platform",
          "bootstrap", "knowledge", "verify", "agent", "app")
OK, USAGE, BLOCKED, WAITING, APPROVAL = 0, 1, 2, 3, 4
PLAN_PHASES = ("identity", "platform", "knowledge", "destroy")
DESTRUCTIVE_APPROVALS = ("destroy", "runtime-delete", "purge-foundry")
APPROVAL_KEYS = PLAN_PHASES + ("agent-deploy", "app-deploy", "runtime-delete", "purge-foundry")

FABRIC_API = "https://api.fabric.microsoft.com"
SEARCH_RESOURCE = "https://search.azure.com"
GRAPH_API = "https://graph.microsoft.com"
ARM_API = "https://management.azure.com"
GITHUB_API = "https://api.github.com"
FOUNDRY_RESOURCE = "https://ai.azure.com"
CONTAINER_APPS_API = "2025-07-01"
# The shipped agent and app: build contexts, image repositories and the toolbox labels the checks expect.
AGENT_SOURCE, AGENT_REPOSITORY = "agents/scenario-assistant", "scenario-assistant"
APP_SOURCE, APP_REPOSITORY = "app", "web"
ONTOLOGY_TOOL, KNOWLEDGE_TOOL, STARTER_DOCUMENT = "scenario_ontology", "knowledge_base", "starter.txt"
POWER_BI_APP_ID = "00000009-0000-0000-c000-000000000000"
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
    "azapi_resource.search", "azapi_resource.web_iq",
})
VARIABLES = ("project_name", "location", "fabric_location", "search_location", "search_sku", "fabric_capacity_sku",
             "fabric_git_enabled", "github_owner", "github_repository_name", "github_branch_name", "github_directory_name",
             "knowledge_lakehouse_id", "knowledge_ontologies", "chat_model", "embedding_model",
             "web_iq_terms_accepted")
WEB_IQ_API_VERSION = "2026-06-01-preview"
WEB_IQ_SEARCH = "https://api.microsoft.ai/v3/search/web"
WEB_IQ_PORTAL = "https://webiq.microsoft.ai/profiles"
WEB_IQ_QUESTION = "Microsoft Web IQ documentation"
SEARCH_OFFERINGS = f"{ARM_API}/providers/Microsoft.Search/fetchOfferings?api-version=2026-09-01-preview"
SEARCH_SERVICE_PATH = re.compile(r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Search/searchServices/[^/]+",
                                 re.IGNORECASE)
# Plan variable that defers the Search identity's grants for an apply creating serverless Search.
SEARCH_DEFERRED = {"search_identity_ready": "false"}
# The template's sample items; a coordinated rename (fabric-workspace skill) must update these too.
SAMPLE_ITEMS = (("lakehouse", "Lakehouse", "Scenario"), ("seed", "Notebook", "SeedScenario"),
                ("ontology", "Ontology", "ScenarioOntology"), ("bind", "Notebook", "BindScenario"))
SAMPLE_PLAN = "Bootstrap"
SAMPLE_REGISTRATION = "scenario"
SAMPLE_TABLES = ("demo_sites", "demo_assets")
ONTOLOGY_QUESTION = ("Using the live ScenarioOntology, which assets currently have Status Maintenance, "
                     "and at which site is each asset located? Return asset names and site names.")
DOCUMENT_QUESTION = ("According to the indexed starter document, what does Git store, what does "
                     "SeedScenario materialize, and which source should be used for current entity "
                     "values? Answer from the OneLake document and cite it.")
EXPECTED_FACTS = ("Pump B", "North Works")
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
PAT_STEPS = (
    "Add a Codespaces secret FABRIC_GITHUB_TOKEN for this repository (fine-grained, Contents read/write) "
    "and restart the Codespace, or export TF_VAR_github_token in your own shell with `read -rs`.",
    "Never paste the PAT into chat, files or command arguments.",
)
APP_NEXT = (
    "Open the web app URL above in a browser and sign in with your work account. The runner verified the app "
    "and agent as you; every other user needs Foundry Agent Consumer (or Foundry User) on the project or agent, "
    "and their own Fabric access.",
    'Select Check connection, then ask "Which assets are in Maintenance and at which site?" and expect Pump B at '
    "North Works from the live ontology.",
    "Review the generated DEV ontology binding before promotion; commit it through Fabric source control when Git "
    "synchronization is enabled.",
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
    pass


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
    """JSON over HTTPS without following redirects or logging headers."""

    def __init__(self):
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method, url, headers=None, body=None, timeout=120):
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
            return Response(error.code, dict(error.headers or {}), error.read())
        except (urllib.error.URLError, OSError) as error:
            host = urllib.parse.urlparse(url).netloc
            raise TransportError(f"{method} to {host} failed ({type(error).__name__}).") from None


class Result:
    def __init__(self, code, stdout="", stderr=""):
        self.code, self.stdout, self.stderr = code, stdout, stderr


class Shell:
    """Runs native commands. Streamed output goes to stderr and keeps a tail for errors."""

    def which(self, name):
        return shutil.which(name)

    def run(self, args, *, cwd=None, env=None, input_text=None, stream=False, interactive=False):
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
        self.http = http or Http()
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


def checked(ctx, command, label=None, *, quiet=False, **options):
    result = run(ctx, command, **options)
    if result.code:
        detail = "" if quiet else tail(result.stderr or result.stdout)
        raise Blocked(f"{label or quoted(command[:3])} failed (exit {result.code})." + (f"\n{detail}" if detail else ""))
    return result


def az_json(ctx, arguments, label, *, quiet=False):
    result = checked(ctx, ["az", *arguments, "--only-show-errors", "--output", "json"], label, quiet=quiet)
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
        raise NeedsApproval(f"--approve {key}={granted} does not match the saved plan (sha256 {pending['sha256'][:12]}).",
                            "Review the current saved plan, then approve its hash.",
                            approval=approval_record(key, description, pending))
    if ctx.interactive:
        if ctx.prompt(f"{description}? [y/N] ").strip().lower() in ("y", "yes"):
            return
        raise NeedsApproval(f"Not approved: {description}. Nothing was changed.")
    flag = f"{key}={pending['sha256'][:12]}" if pending else key
    review = f"Review it: terraform -chdir=infra show {pending['path']}" if pending else None
    raise NeedsApproval(f"Approval required: {description}.", review,
                        f"Then rerun with --approve {flag}, or run interactively.",
                        approval=approval_record(key, description, pending))


def approval_record(key, description, pending):
    record = {"key": key, "description": description}
    if pending:
        record.update(sha256=pending["sha256"], plan=pending["path"], summary=pending["summary"])
    return record


def terraform_env(ctx, token_required):
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
    return env


def terraform(ctx, *arguments, token=False, stream=False, input_text=None):
    env = terraform_env(ctx, token)
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
        lines = [line for line in result.stdout.splitlines() if line.strip()]
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
        result = terraform(ctx, *command, token=git_token_required(ctx), stream=True)
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
    result = terraform(ctx, "apply", "-input=false", pending["path"], token=git_token_required(ctx), stream=True)
    discard_plan(ctx, phase)
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
            response = ctx.http.request(method, FABRIC_API + path, headers={"Authorization": f"Bearer {token}"}, body=body)
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
                          "Rerun deploy.py up to keep observing it; nothing is resubmitted.")
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
    for _, kind, name in SAMPLE_ITEMS + (("plan", "DeploymentPlan", SAMPLE_PLAN),):
        if (items.get(name) or {}).get("type") != kind:
            raise Blocked(f"workspace/ must define the sample {kind} {name}; the runner deploys the template shell.")
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


def git_token_required(ctx):
    return variables(ctx)["fabric_git_enabled"]


def phase_signin(ctx):
    """Complete every sign-in this run's phases need, back to back, before anything is created or changed."""
    github_sign_in(ctx)
    origin, _ = project_repository(ctx)  # Refuses the template and its forks before any other sign-in.
    needs = sign_in_needs(ctx)
    # Three rounds of sign-ins, each checked afterwards.
    for attempt in range(4):
        pending, done = missing_sign_ins(ctx, needs)
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


def sign_in_needs(ctx):
    """Whether this run's phases call the Foundry data plane (the hosted agent, its toolbox or the app check)."""
    if "destroy" in ctx.phases:
        data = ctx.state.data
        deleted = (data.get("destroy") or {}).get("runtime_deleted_at")
        published = bool(data.get("agent") or data.get("app")) or bool({"agent", "app"} & set(data.get("phases") or {}))
        return {"foundry": published and not deleted}
    return {"foundry": bool({"agent", "app"} & set(ctx.phases))}


def missing_sign_ins(ctx, needs):
    """Return (missing device-code sign-ins as (label, command, follow-ups), completed sign-ins)."""
    pending, done = [], []
    tenant, subscription, login = azure_sign_in(ctx)
    reselect = (["az", "account", "set", "--subscription", subscription],) if subscription else ()
    if login:
        access = f" with access to subscription {subscription}" if subscription else ""
        pending.append((f"Azure CLI as the deploying user{access}", login, reselect))
    else:
        audiences = token_audiences(ctx, needs)
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


def token_audiences(ctx, needs):
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
    if needs["foundry"]:
        audiences.append(("Microsoft Foundry", {"resource": FOUNDRY_RESOURCE}))
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
    try:
        runtime.hosted_agent_region(values["location"])
    except runtime.PreflightError as error:
        raise Blocked(str(error), "Choose the region explicitly before anything is provisioned.") from None
    notes.append(f"hosted agents documented in {values['location']}")
    notes.append(check_web_iq(ctx, subscription, values))
    if values["fabric_git_enabled"]:
        check_pat(ctx, values)
    az_json(ctx, ["ad", "sp", "show", "--id", POWER_BI_APP_ID, "--query", "{id:id}"],
            "Reading the tenant's Microsoft Power BI Service principal", quiet=True)
    addresses = state_addresses(ctx)
    if "azurerm_fabric_capacity.main" not in addresses:
        notes.append(check_fabric_quota(ctx, subscription, values))
    if not {"azapi_resource.chat_model", "azapi_resource.embedding_model"} <= addresses:
        notes.append(check_model_quota(ctx, values))
    # Checked on every run: a changed tier or region replaces Search, which state cannot reveal here.
    notes.append(check_search_tier(ctx, subscription, values))
    ctx.log("Not checked up front, because administrator access is assumed: Fabric tenant settings ("
            + ("Git/GitHub sync, " if values["fabric_git_enabled"] else "") + "ontology and "
            "deployment-plan previews, OneLake external access)"
            + ("" if values["search_sku"] == "serverless" else " and dedicated Search new-service capacity")
            + ". A refusal names the grant to request.")
    return "; ".join(notes)


def check_web_iq(ctx, subscription, values):
    if not values["web_iq_terms_accepted"]:
        raise Waiting("Web IQ is required, but its terms and paid usage have not been accepted.",
                      "Review https://www.microsoft.com/en-us/webiq/webiq-legal and preparation.md.",
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


def check_fabric_quota(ctx, subscription, values):
    location = values["fabric_location"] or values["location"]
    usages = az_json(ctx, ["rest", "--method", "get", "--url",
                           f"{ARM_API}/subscriptions/{subscription}/providers/Microsoft.Fabric/locations/"
                           f"{location}/usages?api-version=2023-11-01"], "Reading Fabric CU quota")
    try:
        quota = runtime.fabric_quota_check(usages, location, values["fabric_capacity_sku"])
    except runtime.PreflightError as error:
        raise Blocked(str(error)) from None
    return f"Fabric {location}: {quota['available_cus']} CU available for {quota['sku']}"


def check_model_quota(ctx, values):
    location = values["location"]
    usages = az_json(ctx, ["cognitiveservices", "usage", "list", "--location", location], "Reading model quota") or []
    notes = []
    for key in ("chat_model", "embedding_model"):
        model = values[key]
        name = f"OpenAI.{model['sku']}.{model['name']}"
        matches = [usage for usage in usages if (usage.get("name") or {}).get("value") == name]
        if not matches:
            ctx.log(f"Could not read {name} quota in {location}; confirm it before approving the platform plan.")
            notes.append(f"{name} quota unverified")
            continue
        available = (matches[0].get("limit") or 0) - (matches[0].get("currentValue") or 0)
        if available < model["capacity"]:
            raise Blocked(f"{name} has {available:g} units free in {location}; {key} needs {model['capacity']}.",
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


def tenant_grant(ctx, client, resource, scopes):
    """Read a client's tenant-wide grant to one resource as (state, target): granted, missing, partial or unverified."""
    target = {"clientId": client, "resourceId": resource}
    token = access_token(ctx, resource=GRAPH_API)
    if not token:
        return "unverified", target
    response = ctx.http.request("GET", f"{GRAPH_API}/v1.0/servicePrincipals/{client}/oauth2PermissionGrants",
                                headers={"Authorization": f"Bearer {token}"})
    if response.status in (401, 403):
        return "unverified", target
    if response.status != 200:
        raise ServiceError("Reading the registration's consent grants", response)
    grants = [grant for grant in response.json(default={}).get("value") or []
              if grant.get("consentType") == "AllPrincipals" and same_guid(grant.get("resourceId"), resource)]
    if not grants:
        return "missing", target
    missing = sorted(set(scopes) - set(" ".join(grant.get("scope") or "" for grant in grants).split()))
    return ("partial", dict(target, grant=grants[0], missing=missing)) if missing else ("granted", target)


def consent_state(ctx, auth, scopes):
    """The Fabric deployment registration's tenant-wide grant to the Power BI Service."""
    resource = az_json(ctx, ["ad", "sp", "show", "--id", POWER_BI_APP_ID, "--query", "{id:id}"],
                       "Reading the Power BI Service principal", quiet=True)
    return tenant_grant(ctx, guid(auth.get("service_principal_object_id"), "service_principal_object_id"),
                        guid((resource or {}).get("id"), "Power BI Service principal id"), scopes)


def grant_consent(ctx, client_id, description, scopes, state, target, manual):
    """Grant exactly the declared scopes tenant-wide as the signed-in user, who is assumed to be an administrator."""
    rerun = f"Then rerun deploy.py {ctx.args.command}; the runner verifies the grant before continuing."
    if ctx.args.manual_grants:
        raise Waiting(f"Tenant-wide consent for {client_id} is {state}; --manual-grants leaves it to an "
                      "administrator.", manual, rerun)
    token = access_token(ctx, resource=GRAPH_API) or ""
    headers = {"Authorization": f"Bearer {token}"}
    if state == "missing":
        ctx.log(f"Granting tenant-wide consent for exactly the declared {description} as you.")
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


def ensure_consent(ctx, read, grant):
    """Grant a missing or partial tenant-wide grant, then wait briefly until Graph shows it. Returns (state, granted_now)."""
    state, target = read()
    if state not in ("missing", "partial"):
        return state, False
    grant(state, target)
    for attempt in range(6):
        state, target = read()
        if state == "granted" or attempt == 5:
            break
        ctx.sleep(10)
    if state != "granted":
        raise Waiting("Consent was granted but is not visible yet.", f"Rerun deploy.py {ctx.args.command} in a minute.")
    return state, True


def phase_consent(ctx):
    auth = required_outputs(ctx, "fabric_user_auth")[0]
    tenant = guid(auth.get("tenant_id"), "fabric_user_auth tenant")
    if not same_guid(tenant, ctx.target("tenant_id")):
        raise Blocked("The registration's tenant differs from the signed-in deployment tenant.")
    scopes = sorted(auth.get("delegated_scopes") or [])
    # Non-secret: lets later runs check the scoped token during the up-front sign-in.
    receipt = ctx.state.section("consent")
    receipt.update(tenant_id=tenant, client_id=auth.get("client_id"), login_scope=auth.get("login_scope"),
                   api_permissions_url=auth.get("api_permissions_url"))
    ctx.state.save()
    manual = (f"An Entra administrator (Cloud Application Administrator or Application Administrator) opens "
              f"{auth.get('api_permissions_url')} and selects Grant admin consent for the {len(scopes)} delegated "
              "Fabric scopes.")
    state, granted_now = ensure_consent(
        ctx, lambda: consent_state(ctx, auth, scopes),
        lambda current, target: grant_consent(ctx, auth.get("client_id"), f"{len(scopes)} delegated Fabric scopes",
                                               scopes, current, target, manual))
    if granted_now:
        receipt["granted_at"] = utc_now()
        ctx.state.save()
    if state == "unverified":
        if not ctx.args.consent_confirmed:
            raise MissingPermission(
                "This sign-in cannot read the registration's consent grants, so it can neither grant nor verify them.",
                "Then rerun with --consent-confirmed.",
                grants=[f"An Entra administrator grants or confirms tenant-wide consent at "
                        f"{auth.get('api_permissions_url')} for the {len(scopes)} delegated Fabric scopes."])
        ctx.log("Consent confirmed with --consent-confirmed; this sign-in cannot read grants to verify it.")
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
    return f"consent granted ({how}); delegated Fabric reads verified (writes are proven by the platform apply)"


def phase_platform(ctx):
    if variables(ctx)["knowledge_lakehouse_id"] is not None:
        return "covered by the knowledge phase's full plan"
    result = converge(ctx, "platform")
    workspace, name, *_ = required_outputs(ctx, "dev_workspace_id", "dev_workspace_name", "web_iq")
    if git_token_required(ctx):
        required_outputs(ctx, "github_connect_request")
    principal = ensure_search_identity(ctx, required=True)
    ctx.log(f"Web IQ account provisioned; connect its resource_id output at {WEB_IQ_PORTAL}. "
            "AppID onboarding, authenticated search and agent/app integration are not yet verified; see preparation.md.")
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
            response = ctx.http.request(method, url, headers={"Authorization": f"Bearer {token}"}, body=body)
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
        "logicalId": expected[SAMPLE_PLAN]["logical_id"], "referenceType": "ByLogicalId"}}}
    path = f"/v1/workspaces/{workspace}/git/updateFromGit"
    response = fabric(ctx, "POST", path + "?beta=true", planned)
    with_plan = True
    if response.status not in (200, 202):
        error = ServiceError("Update From Git with the Bootstrap plan", response)
        if error.error_code != "FeatureNotAvailable":
            raise error
        if ctx.args.bootstrap_fallback != "notebooks":
            raise Blocked(error.message, "This tenant rejects attaching a deployment plan to Git sync.",
                          "Rerun with --bootstrap-fallback notebooks to sync the same reviewed commit without the plan, "
                          "then run SeedScenario and BindScenario as you.")
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
    for key, kind, name in SAMPLE_ITEMS:
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
    commit = reviewed_commit(ctx, variables(ctx)["github_branch_name"])
    parts = workspace_definition_parts(ctx, expected)
    digests = {name: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
               for name, value in parts.items()}
    native = receipts.setdefault("native", {})
    if native.get("workspace_id", workspace) != workspace:
        raise Blocked("Native bootstrap receipts belong to a different DEV workspace.")
    previous(ctx, native, "import", "Bulk Import", result=True)
    if (native.get("import") or {}).get("status") == "Succeeded":
        validate_import(ctx, native, expected)
    ids = native_item_ids(ctx, workspace, expected)
    for name, item in native.get("item_ids", {}).items():
        if ids.get(name) != item:
            raise Blocked(f"Previously imported {name} is missing or changed identity; inspect DEV before retrying.")
    recorded = native.setdefault("definitions", {})
    for name in ids:
        if recorded.get(name) != digests[name]:
            raise Blocked(f"{name} already exists with changed or unrecorded definitions.",
                          "Native bootstrap imports additions only; review existing items without overwriting their data or bindings.")
    missing = sorted(set(expected) - set(ids))
    if not missing:
        return
    body = {"definitionParts": [part for name in missing for part in parts[name]],
            "options": {"allowPairingByName": False, "deploymentPlan": {
                "logicalId": expected[SAMPLE_PLAN]["logical_id"], "referenceType": "ByLogicalId"}}}
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
        error = ServiceError("Bulk Import with the Bootstrap plan", response)
        if error.error_code != "FeatureNotAvailable":
            native["import"]["status"] = "Failed"
            ctx.state.save()
            raise error
        if ctx.args.bootstrap_fallback != "notebooks":
            native.pop("import")
            ctx.state.save()
            raise Blocked(error.message, "This tenant rejects attaching a deployment plan to native import.",
                          "Rerun with --bootstrap-fallback notebooks to import the same definitions without the plan, "
                          "then run SeedScenario and BindScenario as you.")
        ctx.log("Plan attachment is unavailable; importing without it (--bootstrap-fallback notebooks).")
        body["options"].pop("deploymentPlan")
        response = fabric(ctx, "POST", path, body)
        with_plan = False
    accepted(ctx, native, "import", "Bulk Import", response, result=True,
             commit=commit, items=missing, with_plan=with_plan)
    validate_import(ctx, native, expected)
    ids = native_item_ids(ctx, workspace, expected)
    if any(ids.get(name) != item for name, item in native["item_ids"].items()):
        raise Waiting("Bulk Import succeeded, but the imported item identities are not visible in DEV yet.",
                      "Inspect DEV and rerun; the accepted import will not be resubmitted.")


PARTITION_SOURCE = {"mode": "directLake", "schemaName": "dbo", "expressionSource": "ScenarioLakehouse"}


def binding_problems(parts, workspace, lakehouse):
    """Check the sample's target-local partitions and OneLake source, not Git-normalized placeholders.

    getDefinition elides the ONT_* source pins that BindScenario writes, so they are not evidence either way.
    """
    problems = []
    for table in SAMPLE_TABLES:
        text = parts.get(f"tables/{table}.tmdl")
        if text is None:
            problems.append(f"tables/{table}.tmdl is missing")
            continue
        partitions = re.findall(r"(?m)^[ \t]+partition[ \t]+([^\r\n]+)", text)
        if not partitions:
            problems.append(f"{table} has no partition yet")
            continue
        fields = dict(PARTITION_SOURCE, entityName=table)
        if (len(partitions) != 1 or partitions[0].strip() not in (f"{table} = entity", f"'{table}' = entity")
                or any([value.strip().strip("'") for value in re.findall(rf"(?m)^[ \t]+{field}:[ \t]+([^\r\n]+)", text)]
                       != [wanted] for field, wanted in fields.items())):
            problems.append(f"{table} does not read dbo.{table} through ScenarioLakehouse")
    expression = parts.get("expressions.tmdl") or ""
    root = f"https://onelake.dfs.fabric.microsoft.com/{workspace}/{lakehouse}"
    if not re.match(r"\s*expression[ \t]+'?ScenarioLakehouse'?[ \t]*=", expression) or root not in expression.lower():
        problems.append("expressions.tmdl does not read the DEV lakehouse")
    return problems


def live_binding(ctx, workspace, ids, expected):
    ontology = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/ontologies/{ids['ontology']}", label="Reading the ontology")
    if ontology.get("logicalId") and not same_guid(ontology["logicalId"], expected["ScenarioOntology"]["logical_id"]):
        raise Blocked("ScenarioOntology in DEV is not the template's sample ontology.")
    if (ontology.get("properties") or {}).get("generation") not in (None, 2):
        raise Blocked("ScenarioOntology must be a new-experience (generation 2) ontology.")
    lakehouse = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/lakehouses/{ids['lakehouse']}", label="Reading the lakehouse")
    if (lakehouse.get("properties") or {}).get("defaultSchema") != "dbo":
        raise Blocked("Scenario must be a schema-enabled lakehouse with default schema dbo.")
    definition = fabric_lro(ctx, "POST", f"/v1/workspaces/{workspace}/ontologies/{ids['ontology']}/getDefinition",
                            label="Reading the ontology definition")
    parts = {}
    for part in (definition.get("definition") or {}).get("parts") or []:
        if part.get("payloadType") == "InlineBase64":
            try:
                parts[part["path"]] = base64.b64decode(part["payload"], validate=True).decode("utf-8")
            except (KeyError, ValueError, UnicodeDecodeError):
                raise Blocked(f"Could not decode ontology part {part.get('path')}.") from None
    return binding_problems(parts, workspace, ids["lakehouse"])


def job_id(response, workspace, item, name):
    # Only the job ID is used; credentials are never sent to the Location host, which can be regional.
    parsed = urllib.parse.urlparse(response.headers.get("location", ""))
    segments = [segment.lower() for segment in parsed.path.split("/") if segment]
    if parsed.scheme != "https" or workspace not in segments or item not in segments:
        raise Blocked(f"{name} was accepted without a recognizable job receipt; inspect Fabric before retrying.")
    return guid(segments[-1], f"{name} job id")


def run_notebook(ctx, workspace, item, name, receipts, *, skip_completed):
    """Run one notebook as the signed-in user, never duplicating an active run."""
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
        response = fabric(ctx, "POST", f"/v1/workspaces/{workspace}/notebooks/{item}/jobs/execute/instances?jobType=RunNotebook")
        if response.status != 202:
            raise ServiceError(f"Starting {name}", response)
        job = job_id(response, workspace, item, name)
        jobs[name] = {"id": job, "status": "Submitted", "submitted_at": utc_now()}
        ctx.state.save()
    delay = 15
    while True:
        response = fabric(ctx, "GET", f"/v1/workspaces/{workspace}/items/{item}/jobs/instances/{job}")
        if response.status != 200:
            raise ServiceError(f"Reading {name} run {job}", response)
        body = response.json(default={})
        status = body.get("status")
        if status in ("Completed", "Failed", "Cancelled", "Deduped"):
            break
        if status not in ("NotStarted", "InProgress"):
            raise Blocked(f"{name} run {job} reported an unknown status {status!r}.")
        if ctx.remaining() < delay:
            raise Waiting(f"{name} run {job} is still {status}.",
                          "Rerun deploy.py up to keep observing it; nothing is resubmitted.")
        ctx.sleep(delay)
    reason = body.get("failureReason")
    reason = str((reason or {}).get("message", "") if isinstance(reason, dict) else reason or "")[:300]
    jobs[name] = {"id": job, "status": status, "failure": reason, "finished_at": utc_now()}
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
    if git_enabled:
        bootstrap_git(ctx, workspace, expected, receipts)
    else:
        bootstrap_native(ctx, workspace, expected, receipts)
    ids = resolve_items(ctx, workspace)
    problems = live_binding(ctx, workspace, ids, expected)
    if problems:
        ctx.log("Binding not verified yet (" + "; ".join(problems) + "); running the bootstrap notebooks.")
        run_notebook(ctx, workspace, ids["seed"], "SeedScenario", receipts, skip_completed=True)
        run_notebook(ctx, workspace, ids["bind"], "BindScenario", receipts, skip_completed=False)
        problems = live_binding(ctx, workspace, ids, expected)
        if problems:
            raise Blocked("BindScenario completed, but the binding is not verified: " + "; ".join(problems))
    receipts.update(lakehouse_id=ids["lakehouse"], ontology_id=ids["ontology"], verified_at=utc_now())
    ctx.state.save()
    ctx.log("Review the generated DEV binding before promotion."
            + (" Commit it through Fabric source control." if git_enabled else ""))
    return f"seeded and bound in DEV: lakehouse {ids['lakehouse']}, ontology {ids['ontology']}"


def phase_knowledge(ctx):
    workspace = guid(required_outputs(ctx, "dev_workspace_id")[0], "dev_workspace_id")
    ids = resolve_items(ctx, workspace)
    values = variables(ctx)
    registered = (values.get("knowledge_ontologies") or {}).get(SAMPLE_REGISTRATION) or {}
    if not (same_guid(values.get("knowledge_lakehouse_id"), ids["lakehouse"])
            and same_guid(registered.get("ontology_id"), ids["ontology"])):
        result = run(ctx, [sys.executable, SCRIPTS / "configure_knowledge.py",
                           "--lakehouse-id", ids["lakehouse"], "--ontology-id", ids["ontology"]])
        if result.code:
            raise Blocked((result.stderr or result.stdout).strip(),
                          "The runner only fills the starter's null/empty knowledge settings; review custom registrations manually.")
        ctx.log("Set the verified physical DEV lakehouse and ontology GUIDs in infra/terraform.tfvars.")
        ctx.forget()
    result = converge_knowledge(ctx)
    values = outputs(ctx)
    source = (values.get("ontology_knowledge_sources") or {}).get(SAMPLE_REGISTRATION) or {}
    problems = [f"{name} is missing" for name in ("knowledge_base_name", "knowledge_source_name",
                                                  "knowledge_source_status_url", "knowledge_base_connection_id")
                if not values.get(name)]
    if values.get("knowledge_setup_pending") is not False:
        problems.append("knowledge_setup_pending is not false")
    if not (same_guid(source.get("ontology_id"), ids["ontology"]) and same_guid(source.get("workspace_id"), workspace)):
        problems.append("the scenario ontology source does not point at DEV")
    if problems:
        raise Blocked("Knowledge registration is incomplete: " + "; ".join(problems))
    return f"{result}; knowledge base {values['knowledge_base_name']} with document and live ontology sources"


def search_call(ctx, method, url, body=None, *, user_header=False):
    token = access_token(ctx, resource=SEARCH_RESOURCE)
    if not token:
        raise Waiting("Azure CLI could not get a Search token for the signed-in user.", "Sign in again, then rerun.")
    headers = {"Authorization": f"Bearer {token}"}
    if user_header:
        # The same delegated user token authenticates Search and the live Fabric source.
        headers["x-ms-query-source-authorization"] = token
    try:
        return ctx.http.request(method, url, headers=headers, body=body, timeout=150)
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


def wait_ingestion(ctx, url):
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
                    raise Blocked(f"Document ingestion failed on consecutive synchronizations: {detail}",
                                  "Fix the cause (Search identity access to DEV, file content), then rerun.")
                ctx.log(f"The last synchronization reported errors ({detail}); waiting for the next one.")
            else:
                ctx.log(f"Waiting for document ingestion: {detail}.")
        elif response.status in (401, 403, 404, 429, 500, 502, 503, 599):
            ctx.log(f"Ingestion status is not readable yet (HTTP {response.status}); permissions can take minutes to propagate.")
        else:
            raise ServiceError("Reading ingestion status", response)
        if ctx.remaining() < 30:
            raise Waiting("Document ingestion has not completed within the observation window.",
                          "It runs every five minutes; rerun deploy.py up to keep observing it.")
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


def save_evidence(ctx, name, body):
    directory = ctx.state.directory / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{name}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
    return path.relative_to(ctx.root)


def prove_retrieval(ctx, url, question, parameters, evaluate, attempts=3):
    """Read-only retrieval with bounded retries; assert structured evidence, never answer text."""
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
            "knowledgeSourceParams": [parameters]}
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
                      "Never paste keys into chat, Terraform inputs, files or the browser app; see preparation.md.")
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
    status_url, base, documents = (values.get("knowledge_source_status_url"), values.get("knowledge_base_name"),
                                   values.get("knowledge_source_name"))
    source = (values.get("ontology_knowledge_sources") or {}).get(SAMPLE_REGISTRATION)
    if not (status_url and base and documents and source):
        raise Blocked("Knowledge registration outputs are missing; run the knowledge phase first.")
    if not status_url.lower().startswith(endpoint + "/"):
        raise Blocked("The knowledge status URL does not belong to the deployed Search service.")
    version = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(status_url).query)).get("api-version")
    if not version:
        raise Blocked("The knowledge status URL has no api-version.")
    ingestion = wait_ingestion(ctx, status_url)
    url = (f"{endpoint}/knowledgebases/{urllib.parse.quote(base, safe='')}/retrieve"
           f"?api-version={urllib.parse.quote(version, safe='')}")
    facts = tuple(ctx.args.expect or EXPECTED_FACTS)
    ontology = prove_retrieval(ctx, url, ctx.args.ontology_question, {
        "kind": "fabricOntology", "knowledgeSourceName": source["knowledge_source_name"],
        "includeReferences": True, "includeReferenceSourceData": True},
        lambda body: ontology_evidence(body, source, facts))
    document = prove_retrieval(ctx, url, ctx.args.document_question, {
        "kind": "indexedOneLake", "knowledgeSourceName": documents,
        "includeReferences": True, "includeReferenceSourceData": True},
        lambda body: document_evidence(body, documents))
    web_iq = verify_web_iq(ctx)
    ctx.state.section("verify").update(dev_ready_at=utc_now(), ingestion=ingestion, ontology=ontology, documents=document)
    ctx.state.save()
    return ("DEV ready: ingestion succeeded; live ontology and document retrieval verified with your identity; "
            f"{web_iq}")


def project_url(value):
    """A Foundry project data-plane endpoint; refuses anything else so tokens only reach Foundry."""
    parsed = urllib.parse.urlparse(str(value or ""))
    if (parsed.scheme != "https" or not parsed.netloc.lower().endswith(".services.ai.azure.com")
            or not re.fullmatch(r"/api/projects/[A-Za-z0-9][A-Za-z0-9_.-]*", parsed.path)
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise Blocked("The Foundry project endpoint output is not a Foundry project data-plane URL.")
    return f"https://{parsed.netloc.lower()}{parsed.path}"


def foundry(ctx, method, url, body=None, *, timeout=120):
    """One Foundry data-plane call as the signed-in user; retries throttling, and transient failures of reads."""
    attempt = 0
    while True:
        attempt += 1
        token = access_token(ctx, resource=FOUNDRY_RESOURCE)
        if not token:
            raise Waiting("Azure CLI could not get a Microsoft Foundry token for the signed-in user.",
                          f"Sign in again with az login, then rerun deploy.py {ctx.args.command}.")
        try:
            response = ctx.http.request(method, url, headers={"Authorization": f"Bearer {token}"}, body=body,
                                        timeout=timeout)
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


def foundry_json(ctx, method, url, body=None, *, label, missing=False):
    response = foundry(ctx, method, url, body)
    if missing and response.status == 404:
        return None
    if response.status not in (200, 201):
        raise ServiceError(label, response)
    value = response.json(default=None)
    if not isinstance(value, dict):
        raise Blocked(f"{label} did not return a JSON object.")
    return value


def released_source(ctx, directory):
    """The image tag for a clean, committed and published source directory: its Git tree hash."""
    if git(ctx, "status", "--porcelain", "--untracked-files=all", "--", directory):
        raise Blocked(f"{directory}/ has uncommitted changes; the runner builds only committed, published source.",
                      "Commit and push them, then rerun deploy.py up.")
    branch = variables(ctx)["github_branch_name"]
    head = git(ctx, "rev-parse", "HEAD")
    if published_commit(ctx, branch) != head:
        raise Blocked(f"This checkout ({head[:12]}) is not the published origin/{branch}.",
                      f"Push or pull {branch} so the deployed images come from the reviewed commit.")
    return git(ctx, "rev-parse", f"HEAD:{directory}")[:16]


def image_present(ctx, registry, repository, tag):
    result = run(ctx, ["az", "acr", "repository", "show", "--name", registry, "--image", f"{repository}:{tag}",
                       "--only-show-errors", "--output", "none"])
    if not result.code:
        return True
    text = result.stderr or result.stdout
    if re.search(r"not found|MANIFEST_UNKNOWN|NAME_UNKNOWN|does not exist", text, re.I):
        return False
    raise Blocked(f"Could not read {repository}:{tag} in registry {registry}.\n{tail(text)}", detail=text)


def build_image(ctx, registry, repository, tag, directory):
    """Build one linux/amd64 image in the registry from the committed context; nothing is built locally."""
    checked(ctx, ["az", "acr", "build", "--registry", registry, "--image", f"{repository}:{tag}",
                  "--platform", "linux/amd64", "--file", f"{directory}/Dockerfile", directory, "--only-show-errors"],
            f"Building {repository}:{tag} in {registry}", stream=True)
    if not image_present(ctx, registry, repository, tag):
        raise Blocked(f"az acr build finished, but {repository}:{tag} is not in registry {registry}.")


def toolbox_state(ctx, url, tools):
    """(exists, default version, published tools match) for the project toolbox."""
    toolbox = foundry_json(ctx, "GET", f"{url}?api-version=v1", label="Reading the toolbox", missing=True)
    default = (toolbox or {}).get("default_version")
    if toolbox is None or default in (None, ""):
        return toolbox is not None, None, False
    version = foundry_json(ctx, "GET", f"{url}/versions/{urllib.parse.quote(str(default), safe='')}?api-version=v1",
                           label="Reading the toolbox's default version", missing=True)
    return True, default, bool(version) and hosted.tools_match(version.get("tools"), tools)


def wait_agent_version(ctx, url, version):
    """Observe a created version until it serves; never resubmit the accepted request."""
    while True:
        body = foundry_json(ctx, "GET", f"{url}/versions/{urllib.parse.quote(str(version), safe='')}?api-version=v1",
                            label=f"Reading hosted agent version {version}")
        status = body.get("status")
        if status == "active":
            return body
        if status in ("failed", "deleting", "deleted"):
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise Blocked(f"Hosted agent version {version} is {status}: {error.get('code') or ''} "
                          f"{str(error.get('message') or '')[:300]}".rstrip(": "),
                          "Inspect the version, its image and the registry pull access; after fixing the cause, "
                          "rerun with --retry-failed to create a new version.")
        if ctx.remaining() < 20:
            raise Waiting(f"Hosted agent version {version} is still {status or 'provisioning'}.",
                          "Rerun deploy.py up to keep observing it; nothing is resubmitted.")
        ctx.sleep(10)


def invoke_agent(ctx, url, text):
    """One Responses call as the signed-in user. A cold session can answer 424 or 5xx first, so retry briefly."""
    for attempt in range(1, 7):
        response = foundry(ctx, "POST", url, {"input": text, "stream": False}, timeout=240)
        if response.status == 200:
            body = response.json(default=None)
            if not isinstance(body, dict):
                raise Blocked("The hosted agent returned a response that is not a JSON object.")
            return body
        if response.status not in (408, 424, 500, 502, 503, 504) or attempt == 6 or ctx.remaining() < 60:
            raise ServiceError("Invoking the hosted agent", response)
        ctx.log(f"The hosted agent answered HTTP {response.status} (attempt {attempt}); a new session can take a "
                "moment to start.")
        ctx.sleep(15)


def prove_agent(ctx, url, question, evaluate, name, attempts=2):
    """Ask once more on a miss: the model can skip a tool; the structured evidence decides, never answer text."""
    for attempt in range(1, attempts + 1):
        body = invoke_agent(ctx, url, question)
        problems, evidence = evaluate(body)
        if not problems:
            evidence["response"] = str(save_evidence(ctx, name, body))
            return evidence
        ctx.log(f"Agent attempt {attempt} did not prove {name}: {'; '.join(problems)}.")
    raise Blocked(f"The hosted agent's {name} answer is not verified: {'; '.join(problems)}",
                  f"Inspect the last response: {save_evidence(ctx, name + '-failed', body)}",
                  "Check the toolbox connections, your Fabric access and the agent's logs in Application Insights.")


def phase_agent(ctx):
    agent, registry, deployments, kb_endpoint, kb_connection = required_outputs(
        ctx, "hosted_agent", "container_registry", "model_deployments", "knowledge_base_mcp_endpoint",
        "knowledge_base_connection_id")
    fabric = (outputs(ctx).get("fabric_iq_connections") or {}).get(SAMPLE_REGISTRATION) or {}
    if not (fabric.get("name") and fabric.get("server_url")):
        raise Blocked("The scenario's Fabric IQ connection is missing; complete the knowledge phase first.")
    project = project_url(agent.get("project_endpoint"))
    name, toolbox_name = agent["name"], agent["toolbox_name"]
    if agent.get("toolbox_endpoint") != f"{project}/toolboxes/{toolbox_name}/mcp?api-version=v1":
        raise Blocked("The hosted_agent toolbox endpoint output does not belong to the project.")
    tag = released_source(ctx, AGENT_SOURCE)
    image = f"{registry['login_server']}/{AGENT_REPOSITORY}:{tag}"
    try:
        definition = hosted.render(ctx.root / AGENT_SOURCE / "hosted-agent.json", {
            "chat_deployment": deployments.get("chat"),
            "toolbox_endpoint": agent["toolbox_endpoint"],
            "fabric_iq_server_url": fabric["server_url"],
            "fabric_iq_connection": fabric["name"],
            "knowledge_base_mcp_endpoint": kb_endpoint,
            "knowledge_base_connection": str(kb_connection).rstrip("/").rsplit("/", 1)[-1],
        })
    except hosted.DefinitionError as error:
        raise Blocked(str(error)) from None
    desired = hosted.agent_definition(definition["agent"], image)
    receipts = ctx.state.section("agent")
    toolbox_url = f"{project}/toolboxes/{urllib.parse.quote(toolbox_name, safe='')}"
    agent_url = f"{project}/agents/{urllib.parse.quote(name, safe='')}"

    built = image_present(ctx, registry["name"], AGENT_REPOSITORY, tag)
    toolbox_exists, default, toolbox_ready = toolbox_state(ctx, toolbox_url, definition["toolbox"]["tools"])
    current = foundry_json(ctx, "GET", f"{agent_url}?api-version=v1", label=f"Reading hosted agent {name}", missing=True)
    latest = hosted.latest_version(current)
    failed = (latest or {}).get("status") == "failed"
    agent_ready = latest is not None and hosted.definition_matches(latest.get("definition"), desired) and not (
        failed and ctx.args.retry_failed)
    actions = ([] if built else [f"build image {AGENT_REPOSITORY}:{tag} in {registry['name']}"]) + (
        [] if toolbox_ready else [f"publish a version of toolbox {toolbox_name}"]) + (
        [] if agent_ready else [f"create a version of hosted agent {name} running {AGENT_REPOSITORY}:{tag}"])
    if actions:
        approve(ctx, "agent-deploy", f"In Foundry project {project}: " + "; ".join(actions))
    if not built:
        build_image(ctx, registry["name"], AGENT_REPOSITORY, tag, AGENT_SOURCE)
    if not toolbox_ready:
        created = foundry_json(ctx, "POST", f"{toolbox_url}/versions?api-version=v1", definition["toolbox"],
                               label=f"Creating a version of toolbox {toolbox_name}")
        version = created.get("version")
        if version in (None, ""):
            raise Blocked(f"Toolbox {toolbox_name} accepted a version without returning its number; inspect it.")
        if toolbox_exists and str(version) != str(default):
            foundry_json(ctx, "PATCH", f"{toolbox_url}?api-version=v1", {"default_version": version},
                         label=f"Promoting toolbox {toolbox_name} version {version}")
        receipts["toolbox"] = {"name": toolbox_name, "version": version, "published_at": utc_now()}
        ctx.state.save()
    if agent_ready:
        version = latest["version"]
    else:
        if current is None:
            created = foundry_json(ctx, "POST", f"{project}/agents?api-version=v1", {"name": name, "definition": desired},
                                   label=f"Creating hosted agent {name}")
        else:
            created = foundry_json(ctx, "POST", f"{agent_url}/versions?api-version=v1", {"definition": desired},
                                   label=f"Creating a version of hosted agent {name}")
        version = created.get("version") or (hosted.latest_version(created) or {}).get("version")
        if version in (None, ""):
            raise Blocked(f"Hosted agent {name} accepted a version without returning its number; inspect it.")
        # Recorded before polling: an accepted version is observed on rerun, never created twice.
        receipts["version"] = {"version": version, "image": image, "created_at": utc_now()}
        ctx.state.save()
    active = wait_agent_version(ctx, agent_url, version)
    identity = (active.get("instance_identity") or {}).get("principal_id") or (
        (current or {}).get("instance_identity") or {}).get("principal_id")

    url = f"{agent_url}/endpoint/protocols/openai/responses?api-version=v1"
    if url != agent.get("responses_url"):
        raise Blocked("The hosted_agent responses URL output does not match the agent endpoint.")
    challenge = f"connection proof {uuid4()}"
    proof = invoke_agent(ctx, url, challenge)
    if not hosted.proof_matches(proof, challenge):
        raise Blocked("The hosted agent did not return this check's fresh challenge: "
                      + (hosted.failure(proof) or "a different reply"),
                      f"Inspect it: {save_evidence(ctx, 'agent-proof-failed', proof)}",
                      "The proof loads the toolbox but calls no model or tool; check the toolbox and agent logs.")
    facts = tuple(ctx.args.expect or EXPECTED_FACTS)
    ontology = prove_agent(ctx, url, ctx.args.ontology_question,
                           lambda body: hosted.ontology_evidence(body, ONTOLOGY_TOOL, facts), "agent-ontology")
    documents = prove_agent(ctx, url, ctx.args.document_question,
                            lambda body: hosted.document_evidence(body, KNOWLEDGE_TOOL, STARTER_DOCUMENT),
                            "agent-documents")
    receipts.update(name=name, version=dict(receipts.get("version") or {}, version=version, image=image),
                    identity_principal_id=identity, ready_at=utc_now(), ontology=ontology, documents=documents)
    ctx.state.save()
    return (f"hosted agent {name} version {version} ready as you: fresh challenge, live ontology facts through "
            f"{ONTOLOGY_TOOL} and {STARTER_DOCUMENT} through {KNOWLEDGE_TOOL}")


def container_app_id(web, subscription):
    environment = str(web.get("environment_id") or "")
    match = re.fullmatch(r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/Microsoft\.App/"
                         r"managedEnvironments/[^/]+", environment, re.I)
    if not match or not same_guid(match[1], subscription) or not re.fullmatch(r"[a-z][a-z0-9-]{0,30}[a-z0-9]",
                                                                               str(web.get("name") or "")):
        raise Blocked("The web_app output is not a Container Apps target in the deployment subscription.")
    return f"/subscriptions/{match[1]}/resourceGroups/{match[2]}/providers/Microsoft.App/containerApps/{web['name']}"


def container_app_body(location, web, registry, agent, image, project_name):
    settings = {"AGENT_RESPONSES_URL": agent["responses_url"], "AGENT_NAME": agent["name"],
                "ENTRA_CLIENT_ID": web["client_id"], "ENTRA_TENANT_ID": web["tenant_id"], "AGENT_SCOPE": web["scope"]}
    probe = {"httpGet": {"path": "/healthz", "port": 8080}, "periodSeconds": 10}
    return {
        "location": location,
        "tags": {"project": project_name, "managed_by": "deploy.py"},
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {web["identity_id"]: {}}},
        "properties": {
            "environmentId": web["environment_id"],
            "workloadProfileName": "Consumption",
            "configuration": {
                "activeRevisionsMode": "Single",
                "ingress": {"external": True, "targetPort": 8080, "transport": "auto", "allowInsecure": False,
                            "traffic": [{"latestRevision": True, "weight": 100}]},
                "registries": [{"server": registry["login_server"], "identity": web["identity_id"]}],
            },
            "template": {
                "containers": [{
                    "name": "web", "image": image, "resources": {"cpu": 0.5, "memory": "1Gi"},
                    "env": [{"name": key, "value": value} for key, value in settings.items()],
                    "probes": [dict(probe, type="Readiness"), dict(probe, type="Liveness")],
                }],
                "scale": {"minReplicas": 0, "maxReplicas": 2},
            },
        },
    }


def container_app_matches(current, desired):
    """True when the live app already runs this image with these settings, identity and ingress."""
    if not isinstance(current, dict):
        return False
    properties, wanted = current.get("properties") or {}, desired["properties"]
    containers = (properties.get("template") or {}).get("containers") or []
    container, target = (containers[0] if len(containers) == 1 else {}), wanted["template"]["containers"][0]
    env = {item.get("name"): item.get("value") for item in container.get("env") or [] if isinstance(item, dict)}
    ingress = (properties.get("configuration") or {}).get("ingress") or {}
    identities = {key.lower() for key in ((current.get("identity") or {}).get("userAssignedIdentities") or {})}
    return (container.get("image") == target["image"]
            and env == {item["name"]: item["value"] for item in target["env"]}
            and ingress.get("external") is True and ingress.get("targetPort") == 8080
            and {key.lower() for key in desired["identity"]["userAssignedIdentities"]} <= identities)


def wait_container_app(ctx, url):
    """Observe the app until its latest revision is ready; a failed revision stops with its own error."""
    logs = "Inspect the revision's system and console logs in Log Analytics, fix the cause, then rerun."
    while True:
        response = arm(ctx, "GET", url)
        if response.status != 200:
            raise ServiceError("Reading the Container App", response)
        properties = (response.json(default={}) or {}).get("properties") or {}
        state, latest = properties.get("provisioningState"), properties.get("latestRevisionName")
        if state == "Succeeded" and latest and properties.get("latestReadyRevisionName") == latest:
            return properties
        if state in ("Failed", "Canceled"):
            raise Blocked(f"The Container App is {state}; its latest revision did not become ready.", logs)
        if state == "Succeeded" and latest:
            base, _, query = url.partition("?")
            revision = arm(ctx, "GET", f"{base}/revisions/{urllib.parse.quote(latest, safe='')}?{query}")
            if revision.status == 200:
                details = (revision.json(default={}) or {}).get("properties") or {}
                if "failed" in f"{details.get('provisioningState')} {details.get('runningState')}".lower():
                    raise Blocked(f"Container App revision {latest} failed: "
                                  f"{str(details.get('provisioningError') or details.get('runningStateDetails') or '')[:300]}",
                                  logs)
        if ctx.remaining() < 20:
            raise Waiting(f"The Container App is still {state or 'provisioning'}.",
                          "Rerun deploy.py up to keep observing it; nothing is resubmitted.")
        ctx.sleep(10)


def check_web_app(ctx, base, web, agent):
    """Prove the deployed app: health, its sign-in settings and one proxied fresh challenge as you."""
    for attempt in range(1, 13):
        try:
            response = ctx.http.request("GET", f"{base}/healthz", timeout=60)
        except TransportError:
            response = Response(599)
        if response.status == 200:
            break
        if attempt == 12 or ctx.remaining() < 30:
            raise Blocked(f"The web app is not healthy at {base}/healthz (HTTP {response.status}).")
        ctx.sleep(10)
    try:
        config = ctx.http.request("GET", f"{base}/config.json", timeout=60).json(default={}) or {}
    except TransportError:
        config = {}
    expected = {"clientId": web["client_id"], "tenantId": web["tenant_id"], "scope": web["scope"],
                "agentName": agent["name"]}
    if config != expected:
        raise Blocked("The web app serves different sign-in settings than this deployment.")
    challenge = f"connection proof {uuid4()}"
    token = access_token(ctx, resource=FOUNDRY_RESOURCE)
    if not token:
        raise Waiting("Azure CLI could not get a Microsoft Foundry token for the signed-in user.",
                      "Sign in again with az login, then rerun deploy.py up.")
    for attempt in range(1, 4):
        try:
            response = ctx.http.request("POST", f"{base}/api/responses", timeout=240, body={"input": challenge},
                                        headers={"Authorization": f"Bearer {token}"})
        except TransportError:
            response = Response(599)
        if response.status == 200 or response.status not in (502, 503, 504, 599) or attempt == 3:
            break
        ctx.sleep(15)
    body = response.json(default={})
    if response.status != 200 or not hosted.proof_matches(body, challenge):
        raise Blocked(f"The web app did not relay this check's fresh challenge from the agent (HTTP {response.status}"
                      + (f", {hosted.failure(body)}" if hosted.failure(body) else "") + ").",
                      f"Inspect it: {save_evidence(ctx, 'app-proof-failed', body)}")


def phase_app(ctx):
    web, registry, agent = required_outputs(ctx, "web_app", "container_registry", "hosted_agent")
    subscription, tenant = ctx.target("subscription_id"), ctx.target("tenant_id")
    if not same_guid(web.get("tenant_id"), tenant):
        raise Blocked("The web app registration's tenant differs from the deployment tenant.")
    base = https_origin(web.get("url"), "web_app url")
    if not urllib.parse.urlparse(base).netloc.endswith(".azurecontainerapps.io"):
        raise Blocked("The web_app URL is not a Container Apps address.")
    app_id = container_app_id(web, subscription)
    url = f"{ARM_API}{app_id}?api-version={CONTAINER_APPS_API}"
    receipts = ctx.state.section("app")
    scopes = [web["scope"].rsplit("/", 1)[-1]]
    client, resource = guid(web.get("service_principal_object_id"), "web service principal"), guid(
        web.get("api_principal_object_id"), "Foundry API service principal")
    manual = (f"An Entra administrator opens {web.get('api_permissions_url')} and selects Grant admin consent for "
              "Azure Machine Learning Services user_impersonation (the https://ai.azure.com sign-in scope).")
    tag = released_source(ctx, APP_SOURCE)
    image = f"{registry['login_server']}/{APP_REPOSITORY}:{tag}"
    environment = arm(ctx, "GET", f"{ARM_API}{web['environment_id']}?api-version={CONTAINER_APPS_API}")
    if environment.status != 200:
        raise ServiceError("Reading the Container Apps environment", environment)
    location = (environment.json(default={}) or {}).get("location")
    desired = container_app_body(location, web, registry, agent, image, variables(ctx)["project_name"])

    state, _ = tenant_grant(ctx, client, resource, scopes)
    built = image_present(ctx, registry["name"], APP_REPOSITORY, tag)
    current = arm(ctx, "GET", url)
    if current.status not in (200, 404):
        raise ServiceError("Reading the Container App", current)
    deployed = current.status == 200 and container_app_matches(current.json(default={}), desired)
    actions = ([] if state in ("granted", "unverified") else
               [f"grant tenant-wide consent for the web sign-in ({web['client_id']}) to {web['scope']}"]) + (
        [] if built else [f"build image {APP_REPOSITORY}:{tag} in {registry['name']}"]) + (
        [] if deployed else [("update" if current.status == 200 else "create") + f" Container App {web['name']} at {base}"])
    if actions:
        approve(ctx, "app-deploy", "; ".join(actions))
    state, _ = ensure_consent(ctx, lambda: tenant_grant(ctx, client, resource, scopes),
                              lambda current_state, target: grant_consent(
                                  ctx, web["client_id"], "Foundry sign-in scope for the web app", scopes,
                                  current_state, target, manual))
    if state == "unverified":
        if not ctx.args.consent_confirmed:
            raise MissingPermission("This sign-in cannot read the web app's consent grants, so it can neither grant "
                                    "nor verify them.", "Then rerun with --consent-confirmed.", grants=[manual])
        ctx.log("Web sign-in consent confirmed with --consent-confirmed; this sign-in cannot read grants to verify it.")
    if not built:
        build_image(ctx, registry["name"], APP_REPOSITORY, tag, APP_SOURCE)
    if not deployed:
        # Recorded before submitting: an accepted update is observed on rerun.
        receipts["deploy"] = {"image": image, "submitted_at": utc_now()}
        ctx.state.save()
        response = arm(ctx, "PUT", url, desired)
        if response.status not in (200, 201, 202):
            raise ServiceError("Creating or updating the Container App", response)
    properties = wait_container_app(ctx, url)
    check_web_app(ctx, base, web, agent)
    receipts.update(name=web["name"], url=base, image=image, revision=properties.get("latestReadyRevisionName"),
                    verified_at=utc_now())
    ctx.state.save()
    return (f"web app {base} serves {APP_REPOSITORY}:{tag} and relayed the agent's fresh challenge as you; "
            "browser sign-in is next (next steps)")


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
    agent, web = recorded_outputs.get("hosted_agent") or {}, recorded_outputs.get("web_app") or {}
    published = (ctx.state.section("agent") or ctx.state.section("app")
                 or {"agent", "app"} & set(ctx.state.data.get("phases") or {}))
    if published and not (agent.get("name") and web.get("name")):
        raise Blocked("The hosted agent or web app outputs are missing; restore Terraform state before teardown.")
    snapshot = {
        "repository": origin, "project_name": values["project_name"], "resource_ids": resource_ids,
        "hosted_agent": {"project_endpoint": project_url(agent["project_endpoint"]), "name": agent["name"],
                         "toolbox_name": agent["toolbox_name"]} if agent.get("name") else None,
        "container_app": container_app_id(web, subscription) if web.get("name") else None,
        "workspaces": {stage: resources[f"fabric_workspace.{stage}"]["id"] for stage in ("dev", "test")
                       if f"fabric_workspace.{stage}" in resources},
        "foundry": purge,
    }
    receipt["snapshot"] = snapshot
    ctx.state.save()
    return snapshot


def runtime_targets(ctx, snapshot):
    """The runner-published releases that still exist, as (label, read URL, delete URL)."""
    targets = []
    if snapshot.get("container_app"):
        url = f"{ARM_API}{snapshot['container_app']}?api-version={CONTAINER_APPS_API}"
        targets.append((f"Container App {snapshot['container_app'].rsplit('/', 1)[-1]}", url, url))
    agent = snapshot.get("hosted_agent") or {}
    if agent:
        base = project_url(agent["project_endpoint"])
        name, toolbox = (urllib.parse.quote(agent[key], safe="") for key in ("name", "toolbox_name"))
        targets += [(f"hosted agent {agent['name']} and its sessions", f"{base}/agents/{name}?api-version=v1",
                     f"{base}/agents/{name}?api-version=v1&force=true"),
                    (f"toolbox {agent['toolbox_name']}", f"{base}/toolboxes/{toolbox}?api-version=v1",
                     f"{base}/toolboxes/{toolbox}?api-version=v1")]
    present = []
    for label, read, delete in targets:
        call = arm if read.startswith(ARM_API) else foundry
        response = call(ctx, "GET", read)
        if response.status == 200:
            present.append((label, read, delete))
        elif response.status != 404:
            raise ServiceError(f"Reading the {label}", response)
    return present


def delete_runtime(ctx, snapshot):
    """Delete the Container App, hosted agent and toolbox before Terraform removes the hosts they run in."""
    receipt = ctx.state.section("destroy")
    present = runtime_targets(ctx, snapshot)
    if not ctx.args.allow_delete:
        raise NeedsApproval("Pass --allow-delete to delete the environment and its data.")
    if present:
        approve(ctx, "runtime-delete", "Delete " + "; ".join(label for label, *_ in present))
    receipt.setdefault("started_at", utc_now())
    ctx.state.save()
    for label, read, delete in present:
        call = arm if read.startswith(ARM_API) else foundry
        receipt.setdefault("runtime_delete_started", {})[label] = utc_now()
        ctx.state.save()
        response = call(ctx, "DELETE", delete)
        if response.status not in (200, 202, 204, 404):
            raise ServiceError(f"Deleting the {label}", response)
        while True:
            response = call(ctx, "GET", read)
            if response.status == 404:
                break
            if response.status != 200:
                raise ServiceError(f"Confirming the deleted {label}", response)
            if ctx.remaining() < 15:
                raise Waiting(f"The {label} is still being deleted.", "Rerun deploy.py down to keep observing it.")
            ctx.sleep(10)
    receipt["runtime_deleted_at"] = utc_now()
    ctx.state.save()


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
    steps += [
        "Revoke the Web IQ AppID's keys and remove its portal profile at https://webiq.microsoft.ai/profiles.",
        "Hosted-agent traces stay in the deleted Application Insights workspace's retention; Foundry keeps no "
        "conversation after the project is purged.",
        "Keep Terraform state, tfvars and receipts until cleanup is confirmed. GitHub, backend storage, tenant settings, "
        "licenses, external source data and service-retained backups/audit records are not deleted.",
    ]
    return steps


def phase_destroy(ctx):
    snapshot = teardown_snapshot(ctx)
    result = converge(ctx, "destroy", before_apply=lambda: delete_runtime(ctx, snapshot))
    remaining = {address for address in state_addresses(ctx) if not re.search(r"(^|\.)data\.", address)}
    if remaining:
        raise Blocked("Terraform still tracks resources after teardown: " + ", ".join(sorted(remaining)))
    purge_foundry(ctx, snapshot)
    receipt = ctx.state.section("destroy")
    receipt["managed_resources_deleted_at"] = utc_now()
    ctx.state.save()
    return f"{result}; managed resources deleted; retained Fabric workspaces and the Web IQ profile need manual cleanup"


def permission_grants(ctx, phase, text):
    """Name the manual grant behind an API's refusal for lack of permission; empty for any other failure."""
    subscription = ctx.state.section("target").get("subscription_id") or "the deployment subscription"
    portal = (ctx.state.data.get("consent") or {}).get("api_permissions_url") or "its api_permissions_url"
    grants = []
    foundry_refused = phase in ("agent", "app", "destroy") and re.search(
        r"hosted agent|toolbox|Foundry|services\.ai\.azure\.com", text, re.I)

    def grant(line):
        if line not in grants:
            grants.append(line)

    # Terraform reports one "Error:" block per failed resource; classify each block on its own.
    for block in re.split(r"(?m)^[│|\s]*Error: ", text):
        policy = re.search(r"RequestDisallowedByPolicy|Data Loss Prevention|\bDLP\b", block, re.I)
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
        if "RequestDisallowedByPolicy" in block:
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
        if foundry_refused and not actions and re.search(r"\b(?:401|403)\b|Forbidden|Unauthorized", block):
            grant("A Foundry project owner assigns you Foundry User (or Foundry Project Manager) on the deployment's "
                  "Foundry project so you can publish toolboxes and agent versions and invoke the agent.")
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
        if key in PLAN_PHASES:
            if not re.fullmatch(r"[0-9a-fA-F]{12,64}", digest):
                parser.error(f"--approve {key} needs the saved plan's sha256 prefix, for example {key}=1a2b3c4d5e6f")
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
                       help="approve a saved plan by hash (identity|platform|knowledge|destroy=SHA) or an action "
                            "(agent-deploy, app-deploy, runtime-delete, purge-foundry)")
    group.add_argument("--retry-failed", action="store_true",
                       help="resubmit a reviewed failed or unconfirmed operation, or replace a failed agent version")
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
                       help="an administrator confirmed consent (Fabric deployment app and web sign-in) and this "
                            "sign-in cannot read grants to verify it")
    group.add_argument("--bootstrap-fallback", choices=("notebooks",),
                       help="if plan attachment is FeatureNotAvailable, import/sync without it and run Seed then Bind")
    group = deployment.add_argument_group("readiness evidence")
    group.add_argument("--ontology-question", default=ONTOLOGY_QUESTION)
    group.add_argument("--document-question", default=DOCUMENT_QUESTION)
    group.add_argument("--expect", action="append", help="fact required in live ontology source data (repeatable)")
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("status", parents=[common], help="summarize receipts without cloud calls")
    up = commands.add_parser("up", parents=[common, options, deployment],
                             help="run every phase in order, re-verifying earlier ones, until done or a gate")
    up.add_argument("--through", choices=PHASES, default=PHASES[-1], help="stop after this phase")
    down = commands.add_parser("down", parents=[common, options],
                               help="delete this deployment, its app/agent and ingested data")
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
