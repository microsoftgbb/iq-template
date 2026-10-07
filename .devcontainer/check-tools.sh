#!/usr/bin/env bash
set -euo pipefail

for tool in az terraform copilot ms git git-credential-manager gh node npm python3 jq rg curl ssh sshd make g++ pkg-config xdg-open; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Required tool is missing: %s\n' "$tool" >&2
        exit 1
    fi
done

az version
terraform version
copilot --version
git_version="$(git --version)"
printf '%s\n' "$git_version"
if ! printf '2.27.0\n%s\n' "${git_version#git version }" | sort --version-sort --check=quiet; then
    printf 'Managed Runtime requires Git >= 2.27.0.\n' >&2
    exit 1
fi
git credential-manager --version
gh --version
node --version
node -e '
const [major, minor] = process.versions.node.split(".").map(Number);
if (major < 24 || (major === 24 && minor < 11)) {
    console.error("Managed Runtime requires Node.js >= 24.11.0.");
    process.exit(1);
}
'
npm --version
ms --version
python3 --version
python3 -m venv --help >/dev/null
jq --version
rg --version
pkg-config --modversion libsecret-1

# Load native authentication bindings without creating a login session or token cache.
node -e '
const { createRequire } = require("node:module");
createRequire(process.argv[1])("@azure/msal-node-extensions");
' "$(npm root -g)/@microsoft/managed-apps-cli/package.json"

plugins="$(copilot plugin list --json)"
if ! jq -e 'any(.[]; .name == "microsoft-managed-apps" and .enabled == true)' <<< "$plugins" >/dev/null; then
    printf 'Required Copilot plugin is missing or disabled: microsoft-managed-apps. Rebuild the devcontainer.\n' >&2
    exit 1
fi

printf '\nToolchain ready. No sign-in or deployment was performed. Start with docs/getting-started.md.\n'
