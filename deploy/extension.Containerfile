# Reproducible build of the browser extension (Chrome MV3 + Firefox MV2) with no
# Node on the host. Node is pinned here, aligned with .node-version (22); fnm is
# only for local development.
#
#   podman build -f deploy/extension.Containerfile --ignorefile deploy/extension.containerignore \
#     -o extension/dist extension
#
# leaves extension/dist/chrome-mv3 and extension/dist/firefox-mv2 on the host (the
# last stage is exported with --output; no container mounts the repository).

FROM docker.io/library/node:22.23.4-alpine AS build
WORKDIR /ext
# Config and sources first: `npm ci` runs `wxt prepare` (postinstall), which reads them.
COPY . .
RUN npm ci --no-audit --no-fund \
    && npm run compile \
    && npm run build

FROM scratch AS dist
COPY --from=build /ext/dist/ /
