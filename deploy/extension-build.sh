#!/usr/bin/env bash
# Builds the browser extension (Chrome MV3 + Firefox MV2) in a pinned Node image and copies the result to
# extension/dist. No container mounts the repository. Works with a local or a remote Podman (`podman build -o`
# does not work in remote mode).
#   bash deploy/extension-build.sh
set -euo pipefail
cd "$(dirname "$0")/.."
podman build -q -f deploy/extension.Containerfile --ignorefile deploy/extension.containerignore -t xport-extension \
  extension >/dev/null
c=$(podman create xport-extension)
trap 'podman rm -f "$c" >/dev/null' EXIT
rm -rf extension/dist
podman cp "$c:/ext/dist" extension/dist
echo "extension/dist: $(ls extension/dist | tr '\n' ' ')"
