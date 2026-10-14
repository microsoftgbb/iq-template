#!/usr/bin/env bash
set -euo pipefail

azure_ref=9bb168c34349d7a53d24e9033ba8de6df69d9097
work_iq_ref=1060873b3a63a2eadbda04e5be82cc2e0319d1f9
fabric_ref=bc42aaf734771bf752c1d7082dd91e5a6ca72b9f
azure_mcp_version=3.0.0

: "${_REMOTE_USER:?Dev Container remote user must be set}"
: "${_REMOTE_USER_HOME:?Dev Container remote home must be set}"

python3 -c 'import sys; assert sys.version_info >= (3, 12), "Foundry IQ requires Python >= 3.12"'
runuser -u "$_REMOTE_USER" -- npm install --global --engine-strict --no-audit --no-fund \
    "@azure/mcp@${azure_mcp_version}"

install_marketplace() {
    local name="$1" repository="$2" revision="$3"
    shift 3
    local directory="$_REMOTE_USER_HOME/.local/share/iq-marketplaces/$name"
    runuser -u "$_REMOTE_USER" -- mkdir -p "$directory"
    runuser -u "$_REMOTE_USER" -- git init --quiet "$directory"
    runuser -u "$_REMOTE_USER" -- git -C "$directory" fetch --quiet --depth 1 \
        "$repository" "$revision"
    runuser -u "$_REMOTE_USER" -- git -C "$directory" checkout --quiet --detach "$revision"

    if [[ "$name" == azure-skills ]]; then
        # Use the image's reviewed MCP runtime instead of downloading @latest on first use.
        runuser -u "$_REMOTE_USER" -- python3 - "$directory" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]) / ".github/plugins"
config = root / "azure-skills/.mcp.json"
value = json.loads(config.read_text(encoding="utf-8"))
server = value["mcpServers"]["azure"]
if server != {"command": "npx", "args": ["-y", "@azure/mcp@latest", "server", "start"]}:
    raise SystemExit("Upstream Azure MCP declaration changed; review the pinned overlay.")
value["mcpServers"]["azure"] = {"command": "azmcp", "args": ["server", "start"]}
config.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
for plugin in ("azure-skills", "foundry-iq-skills"):
    hook = root / plugin / "hooks/scripts/track-telemetry.sh"
    content = hook.read_text(encoding="utf-8")
    source = "npx -y @azure/mcp@latest"
    if content.count(source) != 2:
        raise SystemExit("Upstream Azure telemetry launcher changed; review the pinned overlay.")
    hook.write_text(content.replace(source, "azmcp"), encoding="utf-8")
PY
    fi

    runuser -u "$_REMOTE_USER" -- copilot plugin marketplace add "$directory"
    for plugin in "$@"; do
        runuser -u "$_REMOTE_USER" -- copilot plugin install "$plugin@$name"
    done
}

install_marketplace azure-skills https://github.com/microsoft/azure-skills.git "$azure_ref" \
    azure foundry-iq-skills
install_marketplace work-iq https://github.com/microsoft/work-iq.git "$work_iq_ref" \
    workiq microsoft-365-agents-toolkit
install_marketplace fabric-collection https://github.com/microsoft/skills-for-fabric.git "$fabric_ref" \
    fabric-skills
