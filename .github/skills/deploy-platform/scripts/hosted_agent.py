#!/usr/bin/env python3
"""Render, compare and check the hosted agent and toolbox releases that deploy.py publishes.

Pure functions over agents/<name>/hosted-agent.json and Foundry REST payloads. deploy.py owns
every cloud call, approval and receipt; nothing here signs in, reads state or calls a service.
"""

import json
from pathlib import Path
import re

PROOF_PREFIX = "AGENT_PROOF|"
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
# Documented hosted-agent sandbox sizes (CPU, memory).
SIZES = {("0.5", "1Gi"), ("1", "2Gi"), ("2", "4Gi")}
AGENT_FIELDS = ("cpu", "memory", "protocol_versions", "environment_variables")
TOOL_LABEL = re.compile(r"[a-z][a-z0-9_]{0,63}")


def size(value):
    """A CPU or memory setting in one canonical string form, so 0.5, 1.0 and "0.5" or "1" compare as documented."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(value, "g")
    return str(value)


class DefinitionError(ValueError):
    pass


def _fill(node, values):
    if isinstance(node, str):
        def substitute(match):
            if match.group(1) not in values:
                raise DefinitionError(f"Unknown placeholder {match.group(0)} in the hosted agent definition.")
            value = values[match.group(1)]
            if not isinstance(value, str) or not value:
                raise DefinitionError(f"Placeholder {match.group(0)} has no value.")
            return value
        return PLACEHOLDER.sub(substitute, node)
    if isinstance(node, list):
        return [_fill(item, values) for item in node]
    if isinstance(node, dict):
        return {key: _fill(value, values) for key, value in node.items()}
    return node


def render(path, values):
    """Load the agent's deployment definition and fill its {{placeholders}} from deployment outputs."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DefinitionError(f"{path} is not readable JSON: {error}") from None
    data = _fill(data, values)
    validate(data)
    return data


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("agent"), dict) or not isinstance(data.get("toolbox"), dict):
        raise DefinitionError("The hosted agent definition needs agent and toolbox objects.")
    agent, toolbox = data["agent"], data["toolbox"]
    if (size(agent.get("cpu")), size(agent.get("memory"))) not in SIZES:
        raise DefinitionError("Choose a documented hosted-agent size: 0.5/1Gi, 1/2Gi or 2/4Gi.")
    protocols = agent.get("protocol_versions")
    if not isinstance(protocols, list) or not protocols or not all(
            isinstance(item, dict) and item.get("protocol") and item.get("version") for item in protocols):
        raise DefinitionError("The hosted agent needs protocol_versions entries with protocol and version.")
    if not any(item["protocol"] == "responses" for item in protocols):
        raise DefinitionError("The app and readiness checks use the responses protocol; keep it enabled.")
    environment = agent.get("environment_variables")
    if not isinstance(environment, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                                    for key, value in environment.items()):
        raise DefinitionError("environment_variables must map names to strings.")
    reserved = sorted(key for key in environment if key.startswith(("FOUNDRY_", "AGENT_")))
    if reserved:
        raise DefinitionError("FOUNDRY_ and AGENT_ variables are reserved by the platform: " + ", ".join(reserved))
    tools = toolbox.get("tools")
    if not isinstance(tools, list) or not tools:
        raise DefinitionError("The toolbox needs at least one tool.")
    labels = [tool.get("server_label") if isinstance(tool, dict) else None for tool in tools]
    if not all(isinstance(label, str) and TOOL_LABEL.fullmatch(label) for label in labels) or len(set(labels)) != len(labels):
        raise DefinitionError("Every toolbox tool needs a unique lowercase server_label.")
    for tool in tools:
        if not tool.get("type") or not tool.get("project_connection_id"):
            raise DefinitionError(f"Toolbox tool {tool['server_label']} needs a type and project_connection_id.")
        if tool.get("require_approval") != "never":
            raise DefinitionError(f"Toolbox tool {tool['server_label']} must set require_approval to never: "
                                  "the app has no approval flow.")
    if PLACEHOLDER.search(json.dumps(data)):
        raise DefinitionError("The hosted agent definition still contains placeholders.")


def agent_definition(agent, image):
    """The hosted agent version definition for one immutable image."""
    return {
        "kind": "hosted",
        "container_configuration": {"image": image},
        **{field: agent[field] for field in AGENT_FIELDS},
        "cpu": size(agent["cpu"]),
        "memory": size(agent["memory"]),
    }


def definition_matches(current, desired):
    """True when an existing version already runs this image with these settings."""
    if not isinstance(current, dict):
        return False
    image = (current.get("container_configuration") or {}).get("image")
    if current.get("kind") != "hosted" or image != desired["container_configuration"]["image"]:
        return False
    if size(current.get("cpu")) != size(desired["cpu"]) or size(current.get("memory")) != size(desired["memory"]):
        return False
    protocols = sorted((item.get("protocol"), item.get("version")) for item in current.get("protocol_versions") or []
                       if isinstance(item, dict))
    if protocols != sorted((item["protocol"], item["version"]) for item in desired["protocol_versions"]):
        return False
    return (current.get("environment_variables") or {}) == desired["environment_variables"]


def tools_match(current, desired):
    """True when the published tools carry every desired setting, matched by server label."""
    if not isinstance(current, list) or len(current) != len(desired):
        return False
    published = {tool.get("server_label"): tool for tool in current if isinstance(tool, dict)}
    for tool in desired:
        existing = published.get(tool["server_label"])
        if not isinstance(existing, dict) or any(existing.get(key) != value for key, value in tool.items()):
            return False
    return True


def latest_version(agent):
    """The latest version object from a GET /agents/{name} response, or None."""
    versions = (agent or {}).get("versions")
    latest = versions.get("latest") if isinstance(versions, dict) else None
    return latest if isinstance(latest, dict) and latest.get("version") is not None else None


def output_items(response):
    output = response.get("output") if isinstance(response, dict) else None
    return [item for item in output if isinstance(item, dict)] if isinstance(output, list) else []


def output_text(response):
    texts = []
    for item in output_items(response):
        if item.get("type") == "message" and item.get("role") == "assistant":
            texts += [part.get("text") for part in item.get("content") or []
                      if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    return "\n\n".join(texts).strip()


def failure(response):
    """A non-completed response's status and error message, or None for a completed response."""
    if not isinstance(response, dict):
        return "no response object"
    if response.get("status") == "completed":
        return None
    error = response.get("error") if isinstance(response.get("error"), dict) else {}
    detail = str(error.get("message") or error.get("code") or "").strip()[:300]
    return f"status {response.get('status') or 'missing'}" + (f": {detail}" if detail else "")


def tool_calls(response):
    """Tool calls in a response, each with its name, arguments and output text when present."""
    items = output_items(response)
    outputs = {}
    for item in items:
        if item.get("type", "").endswith("_call_output") and item.get("call_id"):
            outputs[item["call_id"]] = item.get("output")
    calls = []
    for item in items:
        kind = item.get("type", "")
        if not kind.endswith("_call") or not isinstance(item.get("name"), str):
            continue
        output = item.get("output") if "output" in item else outputs.get(item.get("call_id"))
        if not isinstance(output, str):
            output = json.dumps(output) if output is not None else ""
        calls.append({"type": kind, "name": item["name"], "status": item.get("status"),
                      "server_label": item.get("server_label"), "output": output, "error": item.get("error")})
    return calls


def proof_matches(response, challenge):
    return failure(response) is None and output_text(response) == PROOF_PREFIX + challenge


def _failed(call):
    """A failed call, including Agent Framework's "Error: ..." result for a tool exception or an MCP isError."""
    return (bool(call.get("error")) or call.get("status") in ("failed", "incomplete")
            or call["output"].lstrip().startswith("Error:"))


def _calls_for(response, label):
    """Calls to one toolbox tool: its exact server label, or the toolbox's "<label>___<tool>" function name."""
    label = label.lower()
    return [call for call in tool_calls(response)
            if (call.get("server_label") or "").lower() == label or call["name"].lower().startswith(label + "___")]


def _answer_problems(response):
    """The app rejects a completed response without an assistant answer, so readiness must too."""
    if failure(response):
        return [f"the response failed ({failure(response)})"]
    return [] if output_text(response) else ["the response has no assistant answer"]


def ontology_evidence(response, label, facts):
    """Problems with a live-ontology answer: the tool must run and its own output must carry every fact."""
    problems = _answer_problems(response)
    calls = _calls_for(response, label)
    if not calls:
        problems.append(f"no {label} tool call")
    elif any(_failed(call) for call in calls):
        problems.append(f"the {label} tool call failed")
    # Only successful calls are evidence: a failure's text must never satisfy the readiness gate.
    data = "\n".join(call["output"] for call in calls if not _failed(call))
    missing = [fact for fact in facts if fact.casefold() not in data.casefold()]
    if calls and missing:
        problems.append(f"the {label} tool output lacks " + ", ".join(missing))
    return problems, {"tool_calls": [call["name"] for call in calls], "facts": list(facts),
                      "answer_chars": len(output_text(response))}


def document_evidence(response, label, document):
    """Problems with a document answer: the knowledge tool must return the named source document."""
    problems = _answer_problems(response)
    calls = _calls_for(response, label)
    if not calls:
        problems.append(f"no {label} tool call")
    elif any(_failed(call) for call in calls):
        problems.append(f"the {label} tool call failed")
    if calls and document.casefold() not in "\n".join(
            call["output"] for call in calls if not _failed(call)).casefold():
        problems.append(f"the {label} tool output does not reference {document}")
    return problems, {"tool_calls": [call["name"] for call in calls], "document": document,
                      "answer_chars": len(output_text(response))}
