#!/usr/bin/env python3
"""Render and compare the reviewed Foundry agent definitions in agents/.

Each variant directory holds agent.json (byte-identical across variants: the model settings and description),
instructions.md (byte-identical: the shared instructions) and, for a variant with IQ, tools/*.json: one MCP tool per
IQ. `render` fills the {{placeholders}} from Terraform outputs into the exact agent version body the deployment
runner sends to Foundry Agent Service, with the body's sha256 in its metadata. It leaves out the optional tools it is
told to omit, such as Work IQ mail when Work IQ is disabled; `status` compares the digest with an agent's latest
version. A tool holds exactly the fixed MCP keys, never headers, so no definition can carry a credential: tools
authenticate through their project connection. Nothing here calls Azure: deploy.py's agent phase does, with the
deployer's token.

  python3 foundry_agent.py render --source agents/scenario-with-iq --variant with_iq --values values.json
  python3 foundry_agent.py render --source agents/scenario-with-iq --variant with_iq --values values.json \\
      --omit WorkIQMail
  python3 foundry_agent.py validate
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[4]
API_VERSION = "v1"
VARIANTS = {"with_iq": "agents/scenario-with-iq", "without_iq": "agents/scenario-without-iq"}
# Tools a render may leave out, by file name without .json: Work IQ is opt-in (Terraform's work_iq_enabled).
OPTIONAL_TOOLS = frozenset({"WorkIQMail"})
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
# Every placeholder a definition may use; load and render fail on any other.
PLACEHOLDERS = frozenset({
    "agent_display_name", "agent_model_deployment", "agent_reasoning_effort",
    "knowledge_base_mcp_endpoint", "knowledge_base_connection", "public_context_mcp_endpoint",
    "public_context_connection", "fabric_iq_mcp_endpoint", "fabric_iq_connection",
    "work_iq_mail_mcp_endpoint", "work_iq_mail_connection",
})
SERVER_LABEL = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
AGENT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
TOOL_KEYS = {"type", "server_label", "server_description", "server_url", "project_connection_id", "allowed_tools",
             "require_approval"}
# Foundry stores at most 16 metadata pairs; these are the runner's.
DIGEST_KEY = "definition_sha256"


class DefinitionError(ValueError):
    pass


def _json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DefinitionError(f"{path} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise DefinitionError(f"{path} must hold a JSON object.")
    return value


def load(source):
    """The reviewed definition in `source`: agent.json, instructions.md and tools/*.json by file name."""
    source = Path(source)
    agent = _json(source / "agent.json")
    if set(agent) != {"description", "definition"} or not isinstance(agent["definition"], dict):
        raise DefinitionError(f"{source}/agent.json must hold exactly description and definition.")
    definition = agent["definition"]
    if definition.get("kind") != "prompt" or not isinstance(definition.get("model"), str):
        raise DefinitionError(f"{source}/agent.json must define a prompt agent with a model.")
    if "instructions" in definition or "tools" in definition:
        raise DefinitionError(f"{source}/agent.json must not inline instructions or tools; they have their own files.")
    instructions = (source / "instructions.md").read_text(encoding="utf-8")
    if not instructions.strip():
        raise DefinitionError(f"{source}/instructions.md is empty.")
    parts = [(source / "agent.json", agent), (source / "instructions.md", instructions)]
    tools = []
    directory = source / "tools"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        tool = _json(path)
        problems = []
        # Headers or any other key could carry a credential; tools authenticate through their connection.
        if set(tool) != TOOL_KEYS:
            problems.append(f"keys must be exactly {', '.join(sorted(TOOL_KEYS))}")
        if tool.get("type") != "mcp":
            problems.append("type must be mcp")
        if not SERVER_LABEL.fullmatch(str(tool.get("server_label", ""))):
            problems.append("server_label must be lowercase letters, digits and underscores")
        allowed = tool.get("allowed_tools")
        # An empty list would allow every tool the server offers.
        if not (isinstance(allowed, list) and allowed and all(isinstance(name, str) and name for name in allowed)):
            problems.append("allowed_tools must name at least one tool")
        if tool.get("require_approval") != "never":
            problems.append("require_approval must be never: the app has no approval step")
        description = tool.get("server_description")
        if not (isinstance(description, str) and 0 < len(description) <= 1024):
            problems.append("server_description must be 1 to 1,024 characters")
        if problems:
            raise DefinitionError(f"{path}: " + "; ".join(problems) + ".")
        tools.append((path.stem, tool))
        parts.append((path, tool))
    labels = [tool["server_label"] for _, tool in tools]
    if len(set(labels)) != len(labels):
        raise DefinitionError(f"{source}: every tool needs its own server_label.")
    # Every file, so a tool that a render omits is still checked.
    for path, value in parts:
        for name in PLACEHOLDER.findall(json.dumps(value, ensure_ascii=False)):
            if name not in PLACEHOLDERS:
                raise DefinitionError(f"Unknown placeholder {{{{{name}}}}} in {path}.")
    return {"agent": agent, "instructions": instructions, "tools": tools}


def _fill(value, values, used):
    if isinstance(value, str):
        def replace(match):
            name = match.group(1)
            if name not in PLACEHOLDERS:
                raise DefinitionError(f"Unknown placeholder {{{{{name}}}}}.")
            if values.get(name) in (None, ""):
                raise DefinitionError(f"No value for {{{{{name}}}}}.")
            used.add(name)
            return str(values[name])
        return PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [_fill(item, values, used) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, values, used) for key, item in value.items()}
    return value


def digest(body):
    """sha256 of the body without its metadata, in canonical JSON."""
    content = {key: value for key, value in body.items() if key != "metadata"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def render(source, values, variant, omit=frozenset()):
    """The agent version body for `source`, filled from `values`, with its digest in metadata.

    `omit` names optional tools in `source`, by file name without .json, to leave out of the body; their placeholders
    need no values. The digest covers the body, so omitting a tool, or no longer omitting it, changes it.
    """
    loaded = load(source)
    if isinstance(omit, str):
        raise DefinitionError("omit takes a set of tool names, not a string.")
    omit = frozenset(omit)
    if not omit <= OPTIONAL_TOOLS:
        raise DefinitionError(f"Only an optional tool ({', '.join(sorted(OPTIONAL_TOOLS))}) can be omitted, not "
                              f"{', '.join(sorted(omit - OPTIONAL_TOOLS))}.")
    absent = omit - {name for name, _ in loaded["tools"]}
    if absent:
        raise DefinitionError(f"{source} has no {', '.join(sorted(absent))} tool to omit.")
    values = dict(values)
    agent = json.loads(json.dumps(loaded["agent"]))
    definition = agent["definition"]
    # A model without reasoning takes no reasoning setting.
    if values.get("agent_reasoning_effort") is None and isinstance(definition.get("reasoning"), dict):
        if definition["reasoning"] != {"effort": "{{agent_reasoning_effort}}"}:
            raise DefinitionError(f"{source}/agent.json reasoning must hold only the effort placeholder.")
        definition.pop("reasoning")
    used = set()
    definition["instructions"] = loaded["instructions"]
    definition["tools"] = [tool for name, tool in loaded["tools"] if name not in omit]
    body = _fill({"description": agent["description"], "definition": definition}, values, used)
    if PLACEHOLDER.search(json.dumps(body)):
        raise DefinitionError(f"{source} still has a placeholder after rendering.")
    body["metadata"] = {DIGEST_KEY: digest(body), "source": str(Path(source).as_posix()).rsplit("agents/", 1)[-1],
                        "variant": variant}
    return body


def status(agent, body):
    """missing, current or changed: the live agent's latest version against a rendered body."""
    if agent is None:
        return "missing"
    latest = ((agent.get("versions") or {}).get("latest") or {}) if isinstance(agent, dict) else {}
    recorded = (latest.get("metadata") or {}).get(DIGEST_KEY)
    return "current" if recorded and recorded == body["metadata"][DIGEST_KEY] else "changed"


def agent_url(endpoint, name=None, versions=False):
    if name is not None and not AGENT_NAME.fullmatch(name):
        raise DefinitionError(f"{name!r} is not a valid Foundry agent name.")
    path = "/agents" + (f"/{name}" if name else "") + ("/versions" if versions else "")
    return f"{endpoint.rstrip('/')}{path}?api-version={API_VERSION}"


def validate(root=ROOT):
    """Every variant loads, including its optional tools, and the shared files are byte-identical."""
    loaded = {variant: load(Path(root) / source) for variant, source in VARIANTS.items()}
    for name in ("agent.json", "instructions.md"):
        contents = {(Path(root) / source / name).read_bytes() for source in VARIANTS.values()}
        if len(contents) != 1:
            raise DefinitionError(f"{name} must be byte-identical in all variants.")
    return {variant: [name for name, _ in entry["tools"]] for variant, entry in loaded.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    rendering = commands.add_parser("render", help="print the agent version body the runner would send")
    rendering.add_argument("--source", required=True, help="the variant directory, such as agents/scenario-with-iq")
    rendering.add_argument("--values", required=True, type=Path, help="a JSON object of placeholder values")
    rendering.add_argument("--variant", choices=tuple(VARIANTS), required=True)
    rendering.add_argument("--omit", action="append", default=[], choices=sorted(OPTIONAL_TOOLS),
                           help="an optional tool to leave out, as the runner does when Work IQ is disabled "
                                "(repeatable)")
    commands.add_parser("validate", help="check every variant's reviewed definition offline")
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            values = json.loads(args.values.read_text(encoding="utf-8"))
            body = render(args.source, values, args.variant, omit=frozenset(args.omit))
            print(json.dumps(body, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(validate(), indent=2))
    except (OSError, ValueError) as error:
        print(f"Agent definition error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
