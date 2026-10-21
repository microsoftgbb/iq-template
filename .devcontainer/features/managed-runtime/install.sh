#!/usr/bin/env bash
set -euo pipefail

cli_version=0.28.0
gcm_version=3.0.1
plugin_ref=d227ee470a8300801505546d643bacf79c12ab36

: "${_REMOTE_USER:?Dev Container remote user must be set}"
: "${_REMOTE_USER_HOME:?Dev Container remote home must be set}"

case "$(uname -m)" in
    x86_64)
        arch=x64
        gcm_sha256=530d7e2a99ddd048b62e71ed4e18e8692287bdb60df2eaace3cd439010b8f624
        ;;
    aarch64)
        arch=arm64
        gcm_sha256=a915b3bb648e4e978aa9e198e8bbeb50df229bcdba3007193651dec237165291
        ;;
    *)
        printf 'Unsupported Managed Runtime toolchain architecture\n' >&2
        exit 1
        ;;
esac

install_dir="$(mktemp -d)"
trap 'rm -f -- "$install_dir/gcm.deb"; rmdir -- "$install_dir"' EXIT
chmod 0755 "$install_dir"
curl -fsSL --retry 3 \
    "https://github.com/git-ecosystem/git-credential-manager/releases/download/v${gcm_version}/gcm-linux-${arch}-${gcm_version}.deb" \
    -o "$install_dir/gcm.deb"
printf '%s  %s\n' "$gcm_sha256" "$install_dir/gcm.deb" | sha256sum --check -

apt-get update
apt-get install -y --no-install-recommends \
    "$install_dir/gcm.deb" libsecret-1-dev pkg-config xdg-utils
apt-get clean

git-credential-manager configure --system
# Headless containers must not depend on a desktop keyring or store Git tokens in plaintext.
git config --system credential.credentialStore cache

runuser -u "$_REMOTE_USER" -- npm install --global --engine-strict --no-audit --no-fund \
    --allow-scripts=@azure/msal-node-extensions,esbuild,keytar \
    "@microsoft/managed-apps-cli@${cli_version}"

# Copilot's #ref shortcut clones branches/tags, not immutable commit IDs.
marketplace_dir="$_REMOTE_USER_HOME/.local/share/managed-apps-marketplace"
runuser -u "$_REMOTE_USER" -- mkdir -p "$marketplace_dir"
runuser -u "$_REMOTE_USER" -- git init --quiet "$marketplace_dir"
runuser -u "$_REMOTE_USER" -- git -C "$marketplace_dir" fetch --quiet --depth 1 \
    https://github.com/microsoft/Managed-Apps.git "$plugin_ref"
runuser -u "$_REMOTE_USER" -- git -C "$marketplace_dir" checkout --quiet --detach "$plugin_ref"
runuser -u "$_REMOTE_USER" -- copilot plugin marketplace add "$marketplace_dir"
runuser -u "$_REMOTE_USER" -- copilot plugin install microsoft-managed-apps@Managed-Apps
