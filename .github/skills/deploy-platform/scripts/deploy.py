#!/usr/bin/env python3
"""Deterministic runner for the complete DEV deployment, Studio agent and app.

`up` runs the documented phases in order with native tools only: Terraform,
Azure CLI, Fabric/Search/Graph REST, PAC and `ms`. Every phase checks live state
first and acts only on what is missing, so a rerun resumes instead of
duplicating work. Plans and creations need approval. Administrator consent,
sign-ins and connector OAuth stay with people.

Exit codes: 0 done, 1 usage error, 2 blocked (investigate), 3 waiting for a
person or a long-running cloud operation, 4 approval required. Tokens stay in
memory; nothing secret is printed or written.
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
import studio_agent as studio  # noqa: E402

PHASES = ("config", "preflight", "identity", "consent", "platform",
          "bootstrap", "knowledge", "verify", "m365-demo", "agent", "app")
OK, USAGE, BLOCKED, WAITING, APPROVAL = 0, 1, 2, 3, 4
PLAN_PHASES = ("identity", "platform", "knowledge", "m365-demo")
APPROVAL_KEYS = PLAN_PHASES + ("agent-create", "agent-connections", "agent-publish", "app-register")

FABRIC_API = "https://api.fabric.microsoft.com"
SEARCH_RESOURCE = "https://search.azure.com"
GRAPH_API = "https://graph.microsoft.com"
ARM_API = "https://management.azure.com"
GITHUB_API = "https://api.github.com"
POWER_BI_APP_ID = "00000009-0000-0000-c000-000000000000"
MICROSOFT_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
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
    "fabric_workspace.test", "fabric_connection.github", "fabric_deployment_pipeline.main",
    "azapi_resource.foundry", "azapi_resource.chat_model", "azapi_resource.embedding_model",
    "azapi_resource.search",
})
VARIABLES = ("project_name", "location", "fabric_location", "search_location", "search_sku", "fabric_capacity_sku",
             "github_owner", "github_repository_name", "github_branch_name", "github_directory_name",
             "knowledge_lakehouse_id", "knowledge_ontologies", "chat_model", "embedding_model",
             "web_iq_enabled", "web_iq_terms_accepted", "m365_demo_enabled", "work_iq_enabled",
             "work_iq_terms_accepted")
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
# Optional Work IQ (preview) knowledge source: the Microsoft Work IQ application and its delegated permission.
WORK_IQ_APP_ID = "fdcc1f02-fc51-4226-8753-f668596af7f7"
WORK_IQ_ENABLE = f"az ad sp create --id {WORK_IQ_APP_ID}"
# A question about the deploying user's own work; the proof is the Work IQ activity, not the answer.
WORK_IQ_QUESTION = "What have I been working on recently?"
# Optional Microsoft 365 demo seeding (.github/skills/m365-demo-seed): its only Terraform resources.
M365_DEMO_ADDRESSES = frozenset({"azuread_application.m365_demo[0]", "azuread_service_principal.m365_demo[0]"})
PAT_STEPS = (
    "Add a Codespaces secret FABRIC_GITHUB_TOKEN for this repository (fine-grained, Contents read/write) "
    "and restart the Codespace, or export TF_VAR_github_token in your own shell with `read -rs`.",
    "Never paste the PAT into chat, files or command arguments.",
)
APP_NEXT = (
    "Before playback, open Power Apps Connections in the app's environment, select New connection -> "
    "Microsoft Copilot Studio, and sign in as the app user (or verify that user's existing connection is Connected).",
    "Run `ms app dev` and open the App Player URL it prints.",
    "Select Check connection. Separately, follow the agent's Studio user-connections link to authorize "
    "your own Fabric IQ and Foundry IQ connections when prompted, then check again for the fresh challenge. "
    "Each user must authorize their own connections; registration is not app readiness.",
    'Ask "Which assets are in Maintenance and at which site?" and expect Pump B at North Works.',
    "Review and commit app/src/config.ts and ms.config.json, and review/commit the generated DEV "
    "ontology binding through Fabric source control before any promotion.",
)


class Stop(Exception):
    """A deliberate stop: blocked, waiting for a person, or needing approval."""

    code = BLOCKED
    result = "blocked"

    def __init__(self, message, *next_steps, approval=None):
        super().__init__(message)
        self.message = message
        self.next_steps = [step for step in next_steps if step]
        self.approval = approval


class Blocked(Stop):
    pass


class Waiting(Stop):
    code = WAITING
    result = "waiting"


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

    def request(self, method, url, headers=None, body=None, timeout=120, raw=None):
        """Send `body` as JSON, or `raw` bytes as-is with the caller's Content-Type."""
        headers = dict(headers or {})
        headers.setdefault("Accept", "application/json")
        data = raw if raw is not None else None if body is None else json.dumps(body).encode("utf-8")
        if body is not None and raw is None:
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


def tree_digest(directory, exclude=(".mcs",)):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).rglob("*")):
        relative = path.relative_to(directory)
        if path.is_file() and relative.parts[0] not in exclude:
            digest.update(str(relative).encode() + b"\0" + path.read_bytes() + b"\0")
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
    """Run a person's sign-in interactively, or stop with the exact command to run."""
    if ctx.interactive and ctx.prompt(f"{reason}\nRun `{quoted(command)}` now? [Y/n] ").strip().lower() in ("", "y", "yes"):
        if run(ctx, command, interactive=True).code:
            raise Blocked(f"{command[0]} sign-in did not complete.")
        for follow in after:
            checked(ctx, follow, quiet=True)
        ctx.tokens.clear()
        return
    raise Waiting(reason, f"Run: {quoted(command)}", *(f"Then: {quoted(follow)}" for follow in after),
                  "Then rerun deploy.py up.")


def approve(ctx, key, description, pending=None):
    """Approve one action or one exact saved plan (by hash) before it changes anything."""
    granted = ctx.args.approve.get(key)
    if ctx.args.yes:
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
    else:
        # Variable evaluation only; plan and apply always receive the real ephemeral PAT.
        env["TF_VAR_github_token"] = "evaluation-only"
    return env


def terraform(ctx, *arguments, token=False, stream=False, input_text=None):
    return run(ctx, ["terraform", f"-chdir={ctx.infra}", *arguments], env=terraform_env(ctx, token),
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


def converge(ctx, phase, target=None, check=None, variables=None, before_apply=None):
    """Plan, approve exactly that saved plan, apply it. Returns 'unchanged' or 'applied'.

    `before_apply` runs once the plan is approved, just before it is applied.
    """
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
        if target:
            command.append(f"-target={target}")
        command += [f"-var={name}={value}" for name, value in sorted((variables or {}).items())]
        ctx.log("Creating a saved plan" + (f" targeted at {target} (expected only here)" if target else "")
                + "".join(f" with {name}={value}" for name, value in sorted((variables or {}).items())) + ".")
        result = terraform(ctx, *command, token=True, stream=True)
        if result.code == 0:
            path.unlink(missing_ok=True)
            return "unchanged"
        if result.code != 2:
            path.unlink(missing_ok=True)
            raise Blocked(f"terraform plan failed for {phase}; nothing was applied.\n{tail(result.stdout)}")
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
    if check:
        check(summary)
    ctx.log(f"Saved plan {Path(pending['path']).name}: {describe(summary)} (sha256 {pending['sha256'][:12]}).")
    risky = [change for change in summary["changes"] if change["action"] not in ("create", "update")]
    for change in risky:
        ctx.log(f"  {change['action']}: {change['address']}")
    identity = [change["address"] for change in summary["changes"]
                if change["address"].startswith(IDENTITY_TARGET + ".")]
    if identity and phase != "identity":
        raise Blocked("This plan changes the Fabric user-auth registration: " + ", ".join(identity),
                      "Only the initial identity bootstrap changes it automatically. Review it with plain Terraform; "
                      "permission changes need administrator re-consent and a fresh sign-in.")
    if risky and not ctx.args.allow_replace:
        raise NeedsApproval(f"The {phase} plan deletes or replaces resources; review it before anything changes.",
                            f"Inspect: terraform -chdir=infra show {pending['path']}",
                            f"To apply exactly this plan, rerun with --allow-replace --approve {phase}={pending['sha256'][:12]}.",
                            approval=approval_record(phase, f"Apply the saved {phase} plan", pending))
    approve(ctx, phase, f"Apply the saved {phase} plan ({describe(summary)})", pending)
    if before_apply:
        before_apply()
    result = terraform(ctx, "apply", "-input=false", pending["path"], token=True, stream=True)
    discard_plan(ctx, phase)
    ctx.forget()
    if result.code:
        stale = " The saved plan was stale." if "stale" in result.stdout.lower() else ""
        refused = re.search(r"\b(401|403|Unauthorized|Forbidden|InsufficientScopes|insufficient privileges)\b",
                            result.stdout, re.I)
        hints = fabric_login_steps(ctx) if refused else ()
        raise Blocked(f"terraform apply failed during {phase}; state is preserved.{stale}",
                      "Investigate the error above, then rerun: a fresh plan is created from the current state.",
                      *(("An authorization error can need consent propagation or the scoped sign-in:",) + hints
                        if hints else ()))
    return "applied"


def retry_after(response, default=5):
    try:
        return min(max(int(response.headers.get("retry-after", default)), 1), 60)
    except (TypeError, ValueError):
        return default


def fabric_login_steps(ctx):
    auth = ctx.cache.get("outputs", {}).get("fabric_user_auth") or {}
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
    """The checkout you reviewed must be exactly what Fabric will synchronize."""
    remote = published_commit(ctx, branch)
    head = git(ctx, "rev-parse", "HEAD")
    if head != remote:
        raise Blocked(f"Fabric syncs origin/{branch} at {remote[:12]}, but this checkout is at {head[:12]}.",
                      f"Check out and pull {branch} so the deployed commit is the one you reviewed.")
    if git(ctx, "status", "--porcelain", "--", "workspace"):
        raise Blocked("workspace/ has uncommitted changes; commit and push them before syncing Fabric.")
    return head


def workspace_items(ctx):
    items = {}
    for path in sorted((ctx.root / "workspace").glob("*/.platform")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name, kind, logical = data["metadata"]["displayName"], data["metadata"]["type"], data["config"]["logicalId"]
        except (OSError, ValueError, KeyError, TypeError):
            raise Blocked(f"{path.relative_to(ctx.root)} is not a valid Fabric .platform file.") from None
        items[name] = {"type": kind, "logical_id": guid(logical, f"{path.parent.name} logicalId")}
    for _, kind, name in SAMPLE_ITEMS + (("plan", "DeploymentPlan", SAMPLE_PLAN),):
        if (items.get(name) or {}).get("type") != kind:
            raise Blocked(f"workspace/ must define the sample {kind} {name}; the runner deploys the template shell.")
    return items


def phase_config(ctx):
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
    if metadata.get("isTemplate") is not False:
        raise Blocked("This is the reusable template, not a derived project.",
                      "Create your own repository with Use this template and deploy from it.")
    if ctx.infra.is_symlink():
        raise Blocked("Refusing a symlinked infra directory.")
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
    notes.append(check_work_iq(ctx, values))
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
    ctx.log("Not verified here: administrator-owned Fabric tenant settings (Git/GitHub sync, ontology and "
            "deployment-plan previews, OneLake external access)"
            + ("." if values["search_sku"] == "serverless" else " and dedicated Search new-service capacity."))
    return "; ".join(notes)


def check_web_iq(ctx, subscription, values):
    if not values["web_iq_enabled"]:
        return "Web IQ disabled"
    if not values["web_iq_terms_accepted"]:
        raise Waiting("Web IQ is enabled but its terms and paid usage have not been accepted.",
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
    return ("Web IQ global G3 opted in; Terraform registers Microsoft.WebIQ; paid-subscription eligibility "
            "is proven only by account creation; portal AppID linking remains manual")


def check_work_iq(ctx, values):
    if not values.get("work_iq_enabled"):
        return "Work IQ disabled"
    if not values.get("work_iq_terms_accepted"):
        raise Waiting("Work IQ is enabled but its preview terms have not been accepted.",
                      "Review the Work IQ section of docs/getting-started.md: Copilot Credits billing, data processed "
                      "outside the Azure compliance boundary and preview capabilities that can perform actions.",
                      "Only after accepting, set work_iq_terms_accepted = true in infra/terraform.tfvars.")
    found = az_json(ctx, ["ad", "sp", "list", "--filter", f"appId eq '{WORK_IQ_APP_ID}'", "--query", "[].id"],
                    "Reading the tenant's Work IQ service principal")
    if not found:
        raise Waiting("Work IQ is not enabled in this tenant.",
                      f"A Global Administrator enables it once: {WORK_IQ_ENABLE}",
                      "Then rerun deploy.py up.")
    ctx.log("Not verified here: the Copilot Studio usage-based billing plan for Work IQ and your assignment to it; "
            "verify proves them with a live Work IQ retrieval.")
    return "Work IQ opted in; tenant enabled"


def signed_in_account(ctx):
    result = run(ctx, ["az", "account", "show", "--only-show-errors", "--output", "json"])
    if result.code:
        tenant = ctx.args.tenant or ctx.state.section("choices").get("tenant")
        sign_in(ctx, ["az", "login", "--use-device-code", *(["--tenant", tenant] if tenant else [])],
                "Sign in to Azure CLI as the deploying user (device code; your MFA).")
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
    """The serverless tier is gated by an auto-approved subscription preview feature."""
    url = (f"{ARM_API}/subscriptions/{subscription}/providers/Microsoft.Features/providers/Microsoft.Search"
           "/features/Serverless?api-version=2021-07-01")

    def state():
        feature = az_json(ctx, ["rest", "--method", "get", "--url", url], "Reading the Microsoft.Search/Serverless feature",
                          quiet=True) or {}
        return (feature.get("properties") or {}).get("state") or "NotRegistered"

    current = state()
    scope = ["--subscription", subscription, "--only-show-errors", "--output", "none"]
    flag = ctx.args.register_search_serverless
    if current != "Registered":
        if not flag:
            raise Waiting(f"The serverless Search tier needs the subscription's auto-approved Microsoft.Search/Serverless "
                          f"preview feature (currently {current}).",
                          f"Run: az feature register --namespace Microsoft.Search --name Serverless --subscription {subscription}",
                          f"Then: az provider register --namespace Microsoft.Search --wait --subscription {subscription}",
                          "Or rerun with --register-search-serverless to let the runner do both, or set "
                          'search_sku = "basic" (about 20 minutes to provision).')
        if current != "Pending":
            checked(ctx, ["az", "feature", "register", "--namespace", "Microsoft.Search", "--name", "Serverless", *scope],
                    "Registering Microsoft.Search/Serverless")
        for _ in range(18):
            current = state()
            if current == "Registered":
                break
            ctx.sleep(10)
        else:
            raise Waiting("Microsoft.Search/Serverless registration is still pending.", "Rerun deploy.py up in a few minutes.")
    if not flag:
        return "Microsoft.Search/Serverless registered"
    # Re-registering the provider propagates the feature; repeat it on every flagged run so it can resume.
    checked(ctx, ["az", "provider", "register", "--namespace", "Microsoft.Search", "--wait", *scope],
            "Re-registering Microsoft.Search")
    return "Microsoft.Search/Serverless registered and Microsoft.Search re-registered (--register-search-serverless)"


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

    converge(ctx, "identity", target=IDENTITY_TARGET, check=only_identity)
    if not IDENTITY_ADDRESSES <= state_addresses(ctx):
        raise Blocked("The identity apply finished without every registration resource in state.")
    return "registration prepared; administrator consent is next (not a Fabric-ready state)"


def consent_state(ctx, auth, scopes):
    return delegated_grant_state(ctx, auth.get("service_principal_object_id"), POWER_BI_APP_ID, scopes,
                                 "Reading the Power BI Service principal")


def delegated_grant_state(ctx, service_principal, api_app_id, scopes, label):
    """Read a client's tenant-wide (AllPrincipals) delegated grant on one API: granted, missing,
    ("partial", missing scopes) or unverified when this sign-in cannot read grants."""
    resource = az_json(ctx, ["ad", "sp", "show", "--id", api_app_id, "--query", "{id:id}"], label, quiet=True)
    client = guid(service_principal, "service_principal_object_id")
    token = access_token(ctx, resource=GRAPH_API)
    if not token:
        return "unverified"
    response = ctx.http.request("GET", f"{GRAPH_API}/v1.0/servicePrincipals/{client}/oauth2PermissionGrants",
                                headers={"Authorization": f"Bearer {token}"})
    if response.status in (401, 403):
        return "unverified"
    if response.status != 200:
        raise ServiceError("Reading the registration's consent grants", response)
    grants = [grant for grant in response.json(default={}).get("value") or []
              if grant.get("consentType") == "AllPrincipals" and same_guid(grant.get("resourceId"), resource.get("id"))]
    if not grants:
        return "missing"
    missing = sorted(set(scopes) - set(" ".join(grant.get("scope") or "" for grant in grants).split()))
    return ("partial", missing) if missing else "granted"


def phase_consent(ctx):
    auth = required_outputs(ctx, "fabric_user_auth")[0]
    tenant = guid(auth.get("tenant_id"), "fabric_user_auth tenant")
    if not same_guid(tenant, ctx.target("tenant_id")):
        raise Blocked("The registration's tenant differs from the signed-in deployment tenant.")
    scopes = sorted(auth.get("delegated_scopes") or [])
    grant = consent_state(ctx, auth, scopes)
    if grant == "missing":
        if not ctx.args.grant_admin_consent:
            raise Waiting(f"Administrator consent is pending for {auth.get('client_id')}.",
                          f"An administrator opens {auth.get('api_permissions_url')} and selects Grant admin consent "
                          f"for the {len(scopes)} delegated Fabric scopes.",
                          "If you are that administrator, rerun with --grant-admin-consent.")
        ctx.log(f"Granting tenant-wide consent for exactly {len(scopes)} declared delegated scopes "
                "as the signed-in administrator (--grant-admin-consent).")
        result = run(ctx, ["az", "ad", "app", "permission", "grant", "--only-show-errors",
                           "--id", auth["client_id"], "--api", POWER_BI_APP_ID, "--scope", " ".join(scopes),
                           "--consent-type", "AllPrincipals", "--output", "none"])
        if result.code:
            raise Blocked("Granting admin consent failed; it needs an administrator role such as "
                          "Cloud Application Administrator.\n" + tail(result.stderr))
        grant = consent_state(ctx, auth, scopes)
        if grant != "granted":
            raise Waiting("Consent was granted but is not visible yet.", "Rerun deploy.py up in a minute.")
    if isinstance(grant, tuple):
        raise Blocked("The existing tenant-wide grant lacks: " + ", ".join(grant[1]),
                      f"An administrator reviews and completes it at {auth.get('api_permissions_url')}.")
    if grant == "unverified":
        if not ctx.args.consent_confirmed:
            raise Waiting("This sign-in cannot read the registration's consent grants, so consent is unverified.",
                          f"Ask the administrator to confirm consent at {auth.get('api_permissions_url')}.",
                          "Then rerun with --consent-confirmed.")
        ctx.log("Consent confirmed with --consent-confirmed; this sign-in cannot read grants to verify it.")
    subscription = ctx.target("subscription_id")
    login = ["az", "login", "--use-device-code", "--tenant", tenant, "--scope", auth["login_scope"]]
    reselect = ["az", "account", "set", "--subscription", subscription]
    if not access_token(ctx, scope=auth["login_scope"], tenant=tenant):
        sign_in(ctx, login, "Sign in with the generated deployment scope (device code; your MFA).", after=(reselect,))
    for path in ("/v1/workspaces", "/v1/capacities", "/v1/connections"):
        response = fabric(ctx, "GET", path)
        if response.status in (401, 403):
            raise Waiting(f"Fabric refused {path} for the signed-in user (HTTP {response.status}).",
                          f"Run: {quoted(login)}", f"Then: {quoted(reselect)}",
                          "Allow a few minutes for consent to propagate, then rerun deploy.py up.")
        if response.status != 200:
            raise ServiceError(f"Reading {path}", response)
    return "consent granted; delegated Fabric reads verified (writes are proven by the platform apply)"


def phase_platform(ctx):
    if variables(ctx)["knowledge_lakehouse_id"] is not None:
        return "covered by the knowledge phase's full plan"
    result = converge(ctx, "platform")
    workspace, name, _ = required_outputs(ctx, "dev_workspace_id", "dev_workspace_name", "github_connect_request")
    principal = ensure_search_identity(ctx, required=True)
    if outputs(ctx).get("web_iq"):
        ctx.log(f"Web IQ account provisioned; connect its resource_id output at {WEB_IQ_PORTAL}. "
                "AppID onboarding, authenticated search and agent/app integration are not yet verified; see preparation.md.")
    return (f"{result}; DEV workspace {name} ({workspace}); Search identity {principal}; "
            "Git and knowledge setup pending")


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


def resolve_items(ctx, workspace):
    found = collections.defaultdict(list)
    seen = set()
    continuation = None
    while True:
        query = f"?continuationToken={urllib.parse.quote(continuation, safe='')}" if continuation else ""
        page = fabric_json(ctx, "GET", f"/v1/workspaces/{workspace}/items{query}", label="Listing DEV items")
        for item in page.get("value") or []:
            found[(item.get("type"), item.get("displayName"))].append(item)
        continuation = page.get("continuationToken")
        if not continuation:
            break
        if continuation in seen:
            raise Blocked("Fabric repeated an item-list continuation token.")
        seen.add(continuation)
    ids = {}
    for key, kind, name in SAMPLE_ITEMS:
        matches = found.get((kind, name), [])
        if len(matches) != 1:
            raise Blocked(f"Expected exactly one {kind} named {name} in DEV, found {len(matches)}.")
        if not same_guid(matches[0].get("workspaceId"), workspace):
            raise Blocked(f"Fabric returned {name} outside the DEV workspace.")
        ids[key] = guid(matches[0].get("id"), f"{name} id")
    return ids


ANNOTATION = re.compile(r"(?m)^[ \t]*annotation[ \t]+(ONT_[A-Za-z]+)[ \t]*=[ \t]*(.*?)[ \t]*\r?$")


def binding_problems(parts, workspace, lakehouse):
    """Check the sample's target-local binding pins, not Git-normalized placeholders."""
    problems = []
    for table in SAMPLE_TABLES:
        text = parts.get(f"tables/{table}.tmdl")
        if text is None:
            problems.append(f"tables/{table}.tmdl is missing")
            continue
        if not re.search(r"(?m)^[ \t]+partition[ \t]", text):
            problems.append(f"{table} has no partition yet")
            continue
        pins = dict(ANNOTATION.findall(text))
        if not same_guid(pins.get("ONT_WorkspaceId"), workspace):
            problems.append(f"{table} is not pinned to the DEV workspace")
        if not same_guid(pins.get("ONT_ItemId"), lakehouse):
            problems.append(f"{table} is not pinned to the DEV lakehouse")
        if pins.get("ONT_ItemKind") != "Lakehouse":
            problems.append(f"{table} source kind is not Lakehouse")
    root = f"https://onelake.dfs.fabric.microsoft.com/{workspace}/{lakehouse}"
    if root not in (parts.get("expressions.tmdl") or "").lower():
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


def phase_bootstrap(ctx):
    workspace, request = required_outputs(ctx, "dev_workspace_id", "github_connect_request")
    workspace = guid(workspace, "dev_workspace_id")
    expected = workspace_items(ctx)
    receipts = ctx.state.section("bootstrap")
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
    ctx.log("Review and commit the generated DEV binding through Fabric source control before any promotion.")
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
    work_iq = f"; {work_iq_knowledge(ctx, values['work_iq'])}" if values.get("work_iq") else ""
    return (f"{result}; knowledge base {values['knowledge_base_name']} with document and live ontology sources"
            + work_iq)


def search_call(ctx, method, url, body=None, *, user_header=False, work_iq_token=None):
    token = access_token(ctx, resource=SEARCH_RESOURCE)
    if not token:
        raise Waiting("Azure CLI could not get a Search token for the signed-in user.", "Sign in again, then rerun.")
    headers = {"Authorization": f"Bearer {token}"}
    if user_header:
        # The same delegated user token authenticates Search and the live Fabric source.
        headers["x-ms-query-source-authorization"] = token
    if work_iq_token:
        # The user's token for the Work IQ app, which Search exchanges for a delegated Work IQ token.
        headers["x-ms-query-work-iq-source-authorization"] = work_iq_token
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
    account = outputs(ctx).get("web_iq")
    if account is None:
        return None
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
    work_iq = verify_work_iq(ctx, endpoint, version)
    ctx.state.section("verify").update(dev_ready_at=utc_now(), ingestion=ingestion, ontology=ontology, documents=document)
    ctx.state.save()
    return ("DEV ready: ingestion succeeded; live ontology and document retrieval verified with your identity"
            + (f"; {web_iq}" if web_iq else "") + (f"; {work_iq}" if work_iq else ""))


WORK_IQ_CHECKS = (
    "Check the tenant prerequisites: Work IQ enabled for the tenant, a Copilot Studio usage-based billing plan for "
    "Work IQ with you assigned to it, and a Work IQ spending policy.",
    "Consent and the Search identity's federated credential can take minutes to take effect; then rerun deploy.py up.",
)


def work_iq_evidence(body, name):
    """Structured proof that Work IQ answered for this source; its retrieved content is never kept."""
    activity = [entry for entry in body.get("activity") or [] if entry.get("type") == "workIQ"
                and entry.get("knowledgeSourceName") in (None, name)]
    if not activity:
        return [f"no workIQ activity for {name}"], {}
    errors = [entry["error"] for entry in activity if entry.get("error")]
    if errors:
        details = [str(error.get("message") or error.get("code") or error) if isinstance(error, dict) else str(error)
                   for error in errors]
        return ["the workIQ activity reported an error: " + "; ".join(detail[:300] for detail in details)], {}
    references = [entry for entry in body.get("references") or [] if entry.get("type") == "workIQ"]
    return [], {"activity": len(activity), "references": len(references)}


def verify_work_iq(ctx, endpoint, version):
    work_iq = outputs(ctx).get("work_iq")
    if not work_iq:
        return None
    base, source, scope = (work_iq.get(key) for key in ("knowledge_base_name", "knowledge_source_name", "user_scope"))
    if not (base and source and isinstance(scope, str) and scope.startswith("api://")):
        raise Blocked("The Work IQ knowledge outputs are incomplete; rerun the knowledge phase.")
    token = access_token(ctx, scope=scope)
    if not token:
        raise Waiting(f"Azure CLI could not get your token for {scope}.",
                      "The Work IQ app pre-authorizes Azure CLI: sign in again with az login, then rerun.")
    url = (f"{endpoint}/knowledgebases/{urllib.parse.quote(base, safe='')}/retrieve"
           f"?api-version={urllib.parse.quote(version, safe='')}")
    # Work IQ can take a minute or more to answer.
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": ctx.args.work_iq_question}]}],
            "knowledgeSourceParams": [{"knowledgeSourceName": source, "kind": "workIQ", "includeReferences": True,
                                       "includeReferenceSourceData": True}],
            "includeActivity": True, "maxRuntimeInSeconds": 120}
    problems = []
    for attempt in range(1, 4):
        response = search_call(ctx, "POST", url, body, work_iq_token=token)
        if response.status in (200, 206, 502):
            problems, evidence = work_iq_evidence(response.json(default={}), source)
            if not problems:
                ctx.state.section("work_iq").update(verified_at=utc_now(), **evidence)
                ctx.state.save()
                return (f"Work IQ retrieval verified with your identity ({evidence['references']} reference(s)); "
                        "the Studio agent and app cannot query it yet")
        else:
            error = ServiceError(f"Retrieval from {source}", response)
            if response.status not in (403, 408, 429, 500, 503, 504, 599):
                raise Blocked(f"Work IQ retrieval failed: {error.message}", *WORK_IQ_CHECKS)
            problems = [error.message]
        ctx.log(f"Work IQ retrieval attempt {attempt} was not proven: {'; '.join(problems)}.")
        if attempt == 3 or ctx.remaining() < 180:
            break
        ctx.sleep(30)
    raise Blocked(f"Work IQ retrieval is not verified: {'; '.join(problems)}", *WORK_IQ_CHECKS)


def m365_demo_requested(ctx, values):
    # Approving a saved demo plan by its hash also asks for the phase: tfvars only enables it once that plan applies.
    approved = "m365-demo" in ctx.args.approve and "m365-demo" in ctx.state.section("pending_plans")
    return bool(getattr(ctx.args, "with_m365_demo", False)) or values.get("m365_demo_enabled") is True or approved


def enable_m365_demo(ctx):
    """Record m365_demo_enabled = true in tfvars, so the full plans keep the demo app."""
    result = run(ctx, [sys.executable, SCRIPTS / "configure_m365_demo.py"])
    if result.code:
        raise Blocked((result.stderr or result.stdout).strip(), "Review infra/terraform.tfvars, then rerun.")
    ctx.log("Set m365_demo_enabled = true in infra/terraform.tfvars.")
    ctx.forget()


def m365_demo_plan_check(summary):
    other = [change["address"] for change in summary["changes"] if change["address"] not in M365_DEMO_ADDRESSES]
    if other:
        raise Blocked("The Microsoft 365 demo plan would also change: " + ", ".join(other),
                      "Converge the platform first with deploy.py up, then rerun with --with-m365-demo.")


def administrator_consent(ctx, *, what, api_name, api_app_id, registration, flag, granting, scope_note="",
                          unverified_note=""):
    """Verify, or with `flag` grant, a registration's tenant-wide consent for exactly its declared scopes on one API."""
    scopes = sorted(registration.get("delegated_scopes") or [])
    client, link = registration.get("client_id"), registration.get("api_permissions_url")
    principal = registration.get("service_principal_object_id")
    label = f"Reading the {api_name} service principal"
    grant = delegated_grant_state(ctx, principal, api_app_id, scopes, label)
    if grant == "unverified":
        ctx.log(f"This sign-in cannot read the consent grants of {what}{unverified_note}")
        return "unverified"
    if grant == "granted":
        return "granted"
    if not granting:
        lacking = f" (the existing grant lacks {', '.join(grant[1])})" if isinstance(grant, tuple) else ""
        raise Waiting(f"Administrator consent is pending for {what} {client}{lacking}.",
                      f"An administrator opens {link} and selects Grant admin consent for the {len(scopes)} delegated "
                      f"{api_name} scope{'' if len(scopes) == 1 else 's'}.{scope_note}",
                      f"If you are that administrator, rerun with {flag}.")
    ctx.log(f"Granting tenant-wide consent for exactly {len(scopes)} declared delegated scopes of {what} "
            f"as the signed-in administrator ({flag}).")
    result = run(ctx, ["az", "ad", "app", "permission", "grant", "--only-show-errors", "--id", client,
                       "--api", api_app_id, "--scope", " ".join(scopes),
                       "--consent-type", "AllPrincipals", "--output", "none"])
    if result.code:
        raise Blocked(f"Granting the consent of {what} failed; it needs an administrator role such as "
                      "Cloud Application Administrator.\n" + tail(result.stderr))
    if delegated_grant_state(ctx, principal, api_app_id, scopes, label) != "granted":
        raise Waiting(f"The consent of {what} was granted but is not complete or visible yet.",
                      f"Rerun deploy.py up in a minute; if it persists, an administrator completes it at {link}.")
    return "granted"


def m365_demo_consent(ctx, demo):
    """Verify, or with --grant-m365-demo-consent grant, the demo app's tenant-wide delegated consent."""
    return administrator_consent(
        ctx, what="the Microsoft 365 demo app", api_name="Microsoft Graph", api_app_id=MICROSOFT_GRAPH_APP_ID,
        registration=demo, flag="--grant-m365-demo-consent", granting=ctx.args.grant_m365_demo_consent,
        scope_note=" They include Conditional Access and per-user MFA administration, which the skill uses only as "
                   "the signed-in administrator while a seed or cleanup runs.",
        unverified_note="; the administrator's sign-in at the start of each seeding run shows whether consent is "
                        "in place.")


def work_iq_consent(ctx, work_iq):
    """Verify, or with --grant-work-iq-consent grant, the Work IQ app's WorkIQAgent.Ask consent."""
    return administrator_consent(
        ctx, what="the Work IQ app", api_name="Work IQ", api_app_id=WORK_IQ_APP_ID, registration=work_iq,
        flag="--grant-work-iq-consent", granting=ctx.args.grant_work_iq_consent,
        scope_note=" It lets Azure AI Search query Work IQ on behalf of each signed-in user.",
        unverified_note="; the verify phase's Work IQ retrieval shows whether consent is in place.")


def work_iq_knowledge(ctx, work_iq):
    """The optional Work IQ knowledge base's registration and consent; verify proves its retrieval."""
    if not (work_iq.get("knowledge_base_name") and work_iq.get("knowledge_source_name")):
        raise Blocked("The Work IQ knowledge source is not registered yet: it follows the Search identity's grants.",
                      "Rerun deploy.py up.")
    tenant = guid(work_iq.get("tenant_id"), "work_iq tenant_id")
    if not same_guid(tenant, ctx.target("tenant_id")):
        raise Blocked("The Work IQ app registration's tenant differs from the signed-in deployment tenant.")
    consent = work_iq_consent(ctx, work_iq)
    ctx.state.section("work_iq").update(
        client_id=guid(work_iq.get("client_id"), "work_iq client_id"), knowledge_base=work_iq["knowledge_base_name"],
        consent=consent, checked_at=utc_now(),
        agent_app_integration="blocked: the Foundry IQ connector cannot send the user's Work IQ token")
    ctx.state.save()
    return f"Work IQ knowledge base {work_iq['knowledge_base_name']}, consent {consent}"


def phase_m365_demo(ctx):
    """Optional Microsoft 365 demo seeding setup: a delegated-only app and administrator consent.

    Seeding itself is the m365-demo-seed skill's job; it reads these receipts from deploy.py status.
    """
    values = variables(ctx)
    if not m365_demo_requested(ctx, values):
        return "not enabled (optional Microsoft 365 demo seeding: deploy.py up --with-m365-demo)"
    # Until the app exists, only its own plan sets the variable. Enabling it in tfvars first would put the app into
    # the earlier phases' full plans on the next run, ahead of this phase's approval.
    enabling = values.get("m365_demo_enabled") is not True
    applied = converge(ctx, "m365-demo", check=m365_demo_plan_check,
                       variables={"m365_demo_enabled": "true"} if enabling else None,
                       before_apply=(lambda: enable_m365_demo(ctx)) if enabling else None)
    if enabling and applied == "unchanged":
        enable_m365_demo(ctx)
    demo = required_outputs(ctx, "m365_demo")[0]
    tenant = guid(demo.get("tenant_id"), "m365_demo tenant_id")
    if not same_guid(tenant, ctx.target("tenant_id")):
        raise Blocked("The demo app registration's tenant differs from the signed-in deployment tenant.")
    client = guid(demo.get("client_id"), "m365_demo client_id")
    consent = m365_demo_consent(ctx, demo)
    ctx.state.section("m365_demo").update(
        tenant_id=tenant, client_id=client, service_principal_object_id=demo.get("service_principal_object_id"),
        api_permissions_url=demo.get("api_permissions_url"),
        delegated_scopes=sorted(demo.get("delegated_scopes") or []), consent=consent, checked_at=utc_now())
    ctx.state.save()
    return f"{applied}; demo app {client}, consent {consent}; seed demo content with the m365-demo-seed skill"


def power_platform(ctx):
    environment = ctx.args.environment_id and guid(ctx.args.environment_id, "--environment-id")
    url = ctx.args.environment_url and https_origin(ctx.args.environment_url, "--environment-url")
    environment, url = choose(ctx, "environment_id", environment), choose(ctx, "environment_url", url)
    if not (environment and url):
        raise Waiting("Choose the Power Platform environment for the agent and app (the one `ms` uses for your apps).",
                      "Rerun with --environment-id <guid> --environment-url https://<org>.crm.dynamics.com.")
    return environment, url


def studio_command(ctx, *arguments):
    result = run(ctx, [sys.executable, SCRIPTS / "studio_agent.py", *arguments])
    if result.code not in (0, 1):
        raise Blocked((result.stderr or result.stdout).strip() or f"studio_agent.py {arguments[0]} failed.")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise Blocked(f"studio_agent.py {arguments[0]} returned no JSON report.") from None


def publish_only(blocker):
    return "not published" in blocker


def phase_agent(ctx):
    environment, url = power_platform(ctx)
    tenant = ctx.target("tenant_id")
    schema, name, base, workspace, search = required_outputs(
        ctx, "studio_agent_schema_name", "studio_agent_display_name", "knowledge_base_name",
        "dev_workspace_id", "search_endpoint")
    ontology = ((outputs(ctx).get("ontology_knowledge_sources") or {}).get(SAMPLE_REGISTRATION) or {}).get("ontology_id")
    if not ontology:
        raise Blocked("The scenario ontology is not registered; complete the knowledge phase first.")
    receipts = ctx.state.section("agent")
    checked(ctx, ["dotnet", "tool", "restore"], "dotnet tool restore")
    host = urllib.parse.urlparse(url).netloc
    who = run(ctx, ["dotnet", "pac", "env", "who"])
    if who.code or host not in who.stdout.lower():
        sign_in(ctx, ["dotnet", "pac", "auth", "create", "--environment", environment, "--deviceCode"],
                f"Sign in to the Power Platform CLI for environment {environment} (device code).")
        who = run(ctx, ["dotnet", "pac", "env", "who"])
        if who.code or host not in who.stdout.lower():
            raise Blocked(f"PAC is not using {url}; select that profile with `dotnet pac auth list` / `select`.")
    bot = studio_command(ctx, "find", "--tenant-id", tenant, "--dataverse-url", url, "--agent-schema", schema,
                         "--environment-id", environment)
    project = ctx.root / receipts.get("workspace", f".artifacts/studio/{schema}")
    if bot["status"] == "missing":
        if (project / ".mcs").is_dir():
            raise Blocked(f"{project} exists, but {url} has no agent {schema}.",
                          "Inspect that workspace; remove it only after review, then rerun.")
        approve(ctx, "agent-create", f"Create and publish Copilot Studio agent {schema} ({name}) in {url}")
        checked(ctx, ["dotnet", "pac", "copilot", "init", "--name", name, "--publisher-prefix", "iq",
                      "--schema-name", schema, "--project-dir", project, "--environment", url],
                "pac copilot init", stream=True)
    elif not (project / ".mcs").is_dir():
        clone = ctx.root / ".artifacts" / "studio" / f"{schema}-clone"
        checked(ctx, ["dotnet", "pac", "copilot", "clone", "--bot", schema, "--output-dir", clone],
                "pac copilot clone", stream=True)
        created = [path.parent for path in clone.rglob(".mcs") if path.is_dir()]
        if len(created) != 1:
            raise Blocked(f"pac copilot clone did not produce exactly one agent workspace under {clone}.")
        project = created[0]
    receipts["workspace"] = str(project.relative_to(ctx.root))
    ctx.state.save()
    studio_command(ctx, "render", "--workspace", project, "--agent-schema", schema, "--display-name", name,
                   "--knowledge-base", base)
    digest = tree_digest(project)
    if receipts.get("pushed") != digest:
        checked(ctx, ["dotnet", "pac", "copilot", "push", "--project-dir", project], "pac copilot push", stream=True)
        receipts["pushed"] = digest
        ctx.state.save()
    connect = ("connections", "--tenant-id", tenant, "--environment-id", environment, "--fabric-workspace-id",
               workspace, "--ontology-id", ontology, "--search-endpoint", search)
    plan = studio_command(ctx, *connect)
    if any(entry.get("action") == "create" for entry in plan["connections"].values()):
        approve(ctx, "agent-connections", "Create your Fabric IQ and Foundry IQ connections (you then sign in to each)")
        plan = studio_command(ctx, *connect, "--apply")
    if plan.get("status") != "connected":
        raise Waiting("Your two IQ connections need your own sign-in.",
                      f"Open {plan.get('sign_in')} and select Fix connection on the Fabric IQ and Foundry IQ connections.",
                      "Then rerun deploy.py up.")
    names = {connector: entry["connection"] for connector, entry in plan["connections"].items()}
    configure = ("configure", "--tenant-id", tenant, "--dataverse-url", url, "--agent-schema", schema,
                 "--fabric-connection", names[studio.FABRIC_IQ], "--knowledge-connection", names[studio.FOUNDRY_IQ])
    check = ("check", "--tenant-id", tenant, "--dataverse-url", url, "--agent-schema", schema,
             "--environment-id", environment)
    changes = studio_command(ctx, *configure)
    report = studio_command(ctx, *check)
    if changes["changes"] or report["status"] != "ready":
        unexpected = [blocker for blocker in report.get("blockers", []) if not publish_only(blocker)]
        if unexpected and not changes["changes"]:
            raise Blocked("The agent is not ready: " + " ".join(unexpected))
        published = receipts.get("published") or {}
        if published.get("status") == "failed" and not ctx.args.retry_failed:
            raise Blocked(f"The publish started at {published.get('at')} failed.",
                          "Inspect the agent in Copilot Studio, fix the cause, then rerun with --retry-failed.")
        if changes["changes"] or published.get("digest") != receipts.get("pushed") or ctx.args.retry_failed:
            approve(ctx, "agent-publish", f"Apply {', '.join(changes['changes']) or 'no setting changes'} "
                                          f"and publish agent {schema}")
            if changes["changes"]:
                studio_command(ctx, *configure, "--apply")
            # Recorded before invoking: an accepted publish is observed on rerun, never repeated blindly.
            receipts["published"] = {"digest": receipts.get("pushed"), "at": utc_now(), "status": "started"}
            ctx.state.save()
            result = run(ctx, ["dotnet", "pac", "copilot", "publish", "--environment", url, "--bot", changes["bot_id"]],
                         stream=True)
            receipts["published"]["status"] = "failed" if result.code else "done"
            ctx.state.save()
            if result.code:
                raise Blocked(f"pac copilot publish failed (exit {result.code}).\n{tail(result.stdout)}",
                              "Inspect the agent in Copilot Studio, fix the cause, then rerun with --retry-failed.")
        else:
            ctx.log(f"Waiting for the publish from {published.get('at')} to synchronize; it is not repeated.")
        while True:
            report = studio_command(ctx, *check)
            if report["status"] == "ready":
                break
            unexpected = [blocker for blocker in report.get("blockers", []) if not publish_only(blocker)]
            if unexpected:
                raise Blocked("The published agent is not ready: " + " ".join(unexpected))
            if ctx.remaining() < 20:
                raise Waiting("The agent is still synchronizing after publishing.",
                              "Rerun deploy.py up to check again; add --retry-failed only to publish again.")
            ctx.sleep(20)
    receipts.update(bot_id=changes["bot_id"], connections=names, ready_at=utc_now())
    ctx.state.save()
    return f"agent {schema} ready: Agent readers, end-user IQ tools, grounding off, published"


CONFIG_LINE = re.compile(r"^export const STUDIO_AGENT_SCHEMA = '([^'\r\n]*)'", re.M)


def set_agent_schema(path, schema):
    text = path.read_text(encoding="utf-8")
    match = CONFIG_LINE.search(text)
    if not match:
        raise Blocked(f"{path} no longer contains the expected STUDIO_AGENT_SCHEMA line; review it.")
    if match.group(1) == schema:
        return False
    if match.group(1):
        raise Blocked(f"app/src/config.ts targets agent {match.group(1)}, not {schema}; review it.")
    path.write_text(text[:match.start(1)] + schema + text[match.end(1):], encoding="utf-8")
    return True


def ms_signed_in(ctx, tenant):
    result = run(ctx, ["ms", "auth", "status", "--json"])
    if result.code:
        return False
    try:
        return same_guid(runtime.cached_tenant(json.loads(result.stdout)), tenant)
    except (ValueError, TypeError, runtime.PreflightError):
        return False


def phase_app(ctx):
    environment, _ = power_platform(ctx)
    schema, name = required_outputs(ctx, "studio_agent_schema_name", "studio_agent_display_name")
    receipts = ctx.state.section("app")
    if set_agent_schema(ctx.root / "app" / "src" / "config.ts", schema):
        ctx.log(f"Set STUDIO_AGENT_SCHEMA to {schema} in app/src/config.ts.")
    lock = sha256_file(ctx.root / "package-lock.json")
    if receipts.get("installed") != lock or not (ctx.root / "node_modules").is_dir():
        checked(ctx, ["npm", "ci"], "npm ci", stream=True)
        receipts["installed"] = lock
        ctx.state.save()
    tenant = ctx.target("tenant_id")
    if not ms_signed_in(ctx, tenant):
        sign_in(ctx, ["ms", "auth", "login", "--device-code"], "Sign in to the Managed Runtime CLI (device code).")
        if not ms_signed_in(ctx, tenant):
            raise Blocked("ms is not signed in to the deployment tenant.")
    config = ctx.root / "ms.config.json"
    native = {"stream": not ctx.interactive, "interactive": ctx.interactive}
    if not config.exists():
        if receipts.get("init_started_at") and not ctx.args.retry_failed:
            raise Blocked(f"An earlier `ms app init` ({receipts['init_started_at']}) may have registered the app, "
                          "but ms.config.json is missing.",
                          "Inspect it with `ms app list` / `ms app info` and recover that registration; rerun with "
                          "--retry-failed only after confirming no app was registered.")
        approve(ctx, "app-register", f"Register Managed Runtime app '{name}' in environment {environment}, "
                                     "bound to this GitHub repository")
        receipts["init_started_at"] = utc_now()
        ctx.state.save()
        try:
            checked(ctx, ["ms", "app", "init", "--display-name", name, "--environment-id", environment,
                          "--build-path", "app/dist", "--build-command", "npm ci && npm run build"], "ms app init", **native)
        except Blocked as error:
            message = error.message.lower()
            repository_access = "github app" in message and "can't access the bound repository" in message
            # Interactive CLI output goes directly to the terminal, not the captured error.
            uncaptured = ctx.interactive and re.fullmatch(r"ms app init failed \(exit \d+\)\.", message)
            if not repository_access and not uncaptured:
                raise
            prefix = "Ask" if repository_access else "If the terminal reported a GitHub App repository-access failure, ask"
            raise Blocked(
                error.message, *error.next_steps,
                f"{prefix} the repository/organization administrator to install or configure the Microsoft Managed Apps "
                "GitHub App for this repository: https://github.com/marketplace/microsoft-managed-apps . "
                "Keep the existing registration receipt. Inspect `ms app list` / `ms app info` and recover any "
                "existing registration; rerun with --retry-failed only after confirming no app was registered.") from None
        if not config.exists():
            raise Blocked("ms app init finished without creating ms.config.json; inspect it with `ms app info`.")
    if not studio_reference(config, environment):
        if receipts.get("data_source_started_at") and not ctx.args.retry_failed:
            raise Blocked("An earlier `ms app add data-source` did not record the Copilot Studio data source.",
                          "Inspect ms.config.json and the app's connections; rerun with --retry-failed after review.")
        approve(ctx, "app-register", "Add the Copilot Studio connector data source to the app")
        receipts["data_source_started_at"] = utc_now()
        ctx.state.save()
        checked(ctx, ["ms", "app", "add", "data-source", "--connector", "shared_microsoftcopilotstudio", "--as", "action"],
                "ms app add data-source", **native)
        if not studio_reference(config, environment):
            raise Blocked("ms app add data-source finished without a Copilot Studio connection reference in ms.config.json.",
                          "Inspect ms.config.json; rerun with --retry-failed after review.")
    receipts.setdefault("registered_at", utc_now())
    ctx.state.save()
    return ("app registered with the Copilot Studio data source; each user must authorize their own "
            f"Microsoft Copilot Studio connection at https://make.powerapps.com/environments/{environment}/connections "
            "before playback, then the agent's IQ connections when prompted. Verify it in the App Player (next steps).")


def studio_reference(config, environment):
    """Read ms.config.json: refuse another environment, report whether the Studio data source exists."""
    try:
        registration = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Blocked("ms.config.json is not valid JSON; review it.") from None
    registered = registration.get("environmentId")
    if registered and not same_guid(registered, environment):
        raise Blocked(f"ms.config.json is registered to environment {registered}, not {environment}; review it.")
    references = (registration.get("connectionReferences") or {}).values()
    return any(str((reference or {}).get("id", "")).endswith("/apis/shared_microsoftcopilotstudio")
               for reference in references)


PHASE_FUNCTIONS = {
    "config": phase_config, "preflight": phase_preflight, "identity": phase_identity,
    "consent": phase_consent, "platform": phase_platform, "bootstrap": phase_bootstrap,
    "knowledge": phase_knowledge, "verify": phase_verify, "m365-demo": phase_m365_demo, "agent": phase_agent,
    "app": phase_app,
}


def run_phases(ctx, phases):
    ctx.state.lock()
    ctx.state.load()
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
            seconds = round(ctx.clock() - began)
            report["phases"][phase] = {"status": stop.result, "detail": stop.message, "seconds": seconds}
            report.update(result=stop.result, phase=phase, message=stop.message, next=stop.next_steps,
                          seconds=round(ctx.clock() - began_run))
            if stop.approval:
                report["approval"] = stop.approval
            record_phase(ctx, phase, stop.result, stop.message, seconds)
            return stop.code, report
        seconds = round(ctx.clock() - began)
        ctx.log(f"{detail} ({seconds}s)")
        report["phases"][phase] = {"status": "done", "detail": detail, "seconds": seconds}
        record_phase(ctx, phase, "done", detail, seconds)
    report["seconds"] = round(ctx.clock() - began_run)
    if phases[-1] == "app":
        report["next"] = list(APP_NEXT)
    return OK, report


def record_phase(ctx, phase, status, detail, seconds):
    ctx.state.section("phases")[phase] = {"status": status, "detail": detail, "seconds": seconds, "at": utc_now()}
    ctx.state.save()


def status_report(ctx):
    ctx.state.load()
    recorded = ctx.state.section("phases")
    report = {"result": "status", "phases": {}, "choices": ctx.state.section("choices")}
    for phase in PHASES:
        entry = recorded.get(phase)
        report["phases"][phase] = ({"status": entry["status"], "detail": entry["detail"], "at": entry["at"],
                                    "seconds": entry.get("seconds")}
                                   if entry else {"status": "not run", "detail": ""})
    pending = ctx.state.section("pending_plans")
    if pending:
        report["pending_plans"] = {phase: {"sha256": plan["sha256"], "summary": describe(plan["summary"]),
                                           "plan": plan["path"]} for phase, plan in pending.items()}
    for receipt in ("m365_demo", "work_iq"):
        if ctx.state.data.get(receipt):
            report[receipt] = ctx.state.data[receipt]
    report["next"] = ["Receipts only; `up` re-verifies live state before acting."]
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
                       help="approve one saved plan by hash (identity|platform|knowledge|m365-demo=SHA) or one action "
                            "(agent-create, agent-connections, agent-publish, app-register)")
    group.add_argument("--allow-replace", action="store_true", help="permit an approved plan that deletes or replaces")
    group.add_argument("--retry-failed", action="store_true", help="resubmit after a reviewed failed sync or notebook run")
    group.add_argument("--non-interactive", action="store_true", help="never prompt; stop with exit 3 or 4 instead")
    group.add_argument("--timeout-minutes", type=int, default=30, help="observation window per phase (default 30)")
    group = options.add_argument_group("target (remembered in .artifacts/deploy/state.json)")
    group.add_argument("--subscription", help="Azure subscription ID or name (required on first run)")
    group.add_argument("--tenant", help="expected Entra tenant ID")
    group.add_argument("--local-state", action="store_true", help="accept single-user local Terraform state")
    group.add_argument("--backend-config", help="backend config file, relative to the repository root")
    group.add_argument("--environment-id", help="Power Platform environment ID for the agent and app")
    group.add_argument("--environment-url", help="that environment's Dataverse URL, https://<org>.crm.dynamics.com")
    group = options.add_argument_group("first-use configuration (when infra/terraform.tfvars is missing)")
    group.add_argument("--project-name")
    group.add_argument("--location")
    group.add_argument("--capacity-admin", action="append")
    group.add_argument("--github-branch")
    group = options.add_argument_group("explicit gate choices")
    group.add_argument("--grant-admin-consent", action="store_true",
                       help="you are a tenant administrator: grant consent for exactly the declared delegated scopes")
    group.add_argument("--consent-confirmed", action="store_true",
                       help="an administrator confirmed consent and this sign-in cannot read grants to verify it")
    group.add_argument("--register-search-serverless", action="store_true",
                       help="register the subscription's auto-approved Microsoft.Search/Serverless preview feature "
                            "that the default serverless Search tier needs")
    group.add_argument("--bootstrap-fallback", choices=("notebooks",),
                       help="if plan-attached Git sync is FeatureNotAvailable, sync without it and run Seed then Bind")
    group = options.add_argument_group("optional Microsoft 365 demo seeding (m365-demo-seed skill)")
    group.add_argument("--with-m365-demo", action="store_true",
                       help="enable Microsoft 365 demo seeding: a delegated-only app in this tenant and its consent; "
                            "--through m365-demo stops there, before the agent and app")
    group.add_argument("--grant-m365-demo-consent", action="store_true",
                       help="you are a tenant administrator: grant consent for exactly the demo app's declared "
                            "delegated scopes")
    group = options.add_argument_group("optional Work IQ knowledge source (work_iq_enabled in tfvars)")
    group.add_argument("--grant-work-iq-consent", action="store_true",
                       help="you are a tenant administrator: grant consent for exactly the Work IQ app's "
                            "WorkIQAgent.Ask delegated permission")
    group = options.add_argument_group("readiness evidence")
    group.add_argument("--ontology-question", default=ONTOLOGY_QUESTION)
    group.add_argument("--document-question", default=DOCUMENT_QUESTION)
    group.add_argument("--work-iq-question", default=WORK_IQ_QUESTION,
                       help="question asked as you of the optional Work IQ knowledge base")
    group.add_argument("--expect", action="append", help="fact required in live ontology source data (repeatable)")
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("status", parents=[common], help="summarize receipts without cloud calls")
    up = commands.add_parser("up", parents=[common, options],
                             help="run every phase in order, re-verifying earlier ones, until done or a gate")
    up.add_argument("--through", choices=PHASES, default=PHASES[-1], help="stop after this phase")
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
            code, report = run_phases(ctx, PHASES[:PHASES.index(args.through) + 1])
    except Stop as stop:
        code, report = stop.code, {"result": stop.result, "phases": {}, "message": stop.message, "next": stop.next_steps}
    print_report(report, args.json)
    return code


if __name__ == "__main__":
    sys.exit(main())
