# syntax=docker/dockerfile:1
# RPi Reporter MQTT2HA Daemon - container image (linux/arm64, Raspberry Pi OS / Debian Trixie hosts)
# Build: docker/build-push.sh  -  Run: see DOCKER.md
FROM debian:trixie-slim

ARG VERSION=dev
ARG REVISION=unknown
# Raspberry Pi archive keyring (raspberrypi-archive-keyring 2025.1+rpt1, key CF8A1AF502A2AA2D763BAE7E82B129927FA3303E)
# vendored because the key served at archive.raspberrypi.com still has SHA-1 bindings rejected by sqv
ARG RPI_KEYRING_SHA256=3a29901549ae65a910de13a32551e6eea7f45ba1fd70f0cb67e5c70d98488071

LABEL org.opencontainers.image.title="rpi-reporter-mqtt2ha" \
      org.opencontainers.image.description="Reports Raspberry Pi status and sensors to Home Assistant via MQTT" \
      org.opencontainers.image.source="https://github.com/xorguy/RPi-Reporter-MQTT2HA-Daemon" \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN --mount=type=bind,source=docker/raspberrypi-archive-keyring.pgp,target=/tmp/raspberrypi.pgp \
    set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 python3-paho-mqtt python3-sdnotify python3-colorama python3-unidecode python3-apt \
        tzdata ca-certificates; \
    # vcgencmd (GPU temperature, throttling) only ships in the Raspberry Pi archive:
    # pinned keyring, only raspi-utils-core (+ libgpiolib0 dep) may come from it, removed afterwards
    echo "${RPI_KEYRING_SHA256}  /tmp/raspberrypi.pgp" | sha256sum -c -; \
    printf '%s\n' 'Types: deb' 'URIs: https://archive.raspberrypi.com/debian/' 'Suites: trixie' \
        'Components: main' 'Signed-By: /tmp/raspberrypi.pgp' \
        > /etc/apt/sources.list.d/raspberrypi-build.sources; \
    printf '%s\n' 'Package: *' 'Pin: origin archive.raspberrypi.com' 'Pin-Priority: -1' '' \
        'Package: raspi-utils-core libgpiolib0' 'Pin: origin archive.raspberrypi.com' 'Pin-Priority: 500' \
        > /etc/apt/preferences.d/raspberrypi-build; \
    apt-get update; \
    apt-get install -y --no-install-recommends raspi-utils-core; \
    test -x /usr/bin/vcgencmd; \
    rm -rf /etc/apt/sources.list.d/raspberrypi-build.sources /etc/apt/preferences.d/raspberrypi-build \
        /var/lib/apt/lists/*; \
    mkdir -p /etc/rpi-reporter

COPY --chmod=0755 ISP-RPi-mqtt-daemon.py /opt/rpi-reporter/ISP-RPi-mqtt-daemon.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RPI_HOST_ROOT=/host

# nobody:nogroup - config.ini must be readable by gid 65534 (root:65534 0640)
USER 65534:65534

ENTRYPOINT ["/usr/bin/python3", "-u", "/opt/rpi-reporter/ISP-RPi-mqtt-daemon.py", "-c", "/etc/rpi-reporter"]
