# Reproducible build of the browser extension (Chrome MV3 + Firefox MV2) with no
# Node on the host. Node is pinned here, aligned with .node-version (22); fnm is
# only for local development.
#
#   bash deploy/extension-build.sh
#
# builds it and copies /ext/dist out of a created container to extension/dist
# (chrome-mv3 and firefox-mv2); no container mounts the repository.

FROM docker.io/library/node:22.23.4-alpine AS build
WORKDIR /ext
# Config and sources first: `npm ci` runs `wxt prepare` (postinstall), which reads them.
COPY . .
RUN npm ci --no-audit --no-fund \
    && npm run compile \
    && npm run build

