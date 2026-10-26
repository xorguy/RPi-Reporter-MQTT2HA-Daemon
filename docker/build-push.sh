#!/bin/bash
# Build the linux/arm64 image and push it to Docker Hub.
#   IMAGE    image name          (default: xorguy/rpi-reporter-mqtt2ha)
#   BUILDER  buildx builder      (default: multiarch, needs QEMU binfmt on non-arm64 hosts)
#   OUTPUT   --push | --load     (default: --push)
#   REVISION git revision label  (default: git rev-parse HEAD)
set -Eeuo pipefail

IMAGE=${IMAGE:-xorguy/rpi-reporter-mqtt2ha}
BUILDER=${BUILDER:-multiarch}
OUTPUT=${OUTPUT:---push}

cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^script_version = "\(.*\)"$/\1/p' ISP-RPi-mqtt-daemon.py)
[ -n "$VERSION" ] || { echo "cannot read script_version" >&2; exit 1; }
REVISION=${REVISION:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}

attest=()
if [ "$OUTPUT" = "--push" ]; then
    attest=(--provenance=mode=max --sbom=true)
fi

docker buildx build \
    --builder "$BUILDER" \
    --platform linux/arm64 \
    --pull \
    --build-arg VERSION="$VERSION" \
    --build-arg REVISION="$REVISION" \
    "${attest[@]}" \
    --tag "$IMAGE:$VERSION" \
    --tag "$IMAGE:latest" \
    "$OUTPUT" \
    .
