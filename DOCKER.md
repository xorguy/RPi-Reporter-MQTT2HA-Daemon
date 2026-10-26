# Running RPi Reporter MQTT2HA Daemon in Docker

Image: [`xorguy/rpi-reporter-mqtt2ha`](https://hub.docker.com/r/xorguy/rpi-reporter-mqtt2ha) (`linux/arm64` only, tags `latest` and the daemon version, e.g. `2.1.0`).

Based on the approach of [hostcc/RPi-Reporter-MQTT2HA-Daemon-docker](https://github.com/hostcc/RPi-Reporter-MQTT2HA-Daemon-docker), with tighter privileges.

## Requirements

- Raspberry Pi with a **64-bit** OS (Raspberry Pi OS / Debian Trixie arm64)
- Docker with the compose plugin
- An MQTT broker (Home Assistant with MQTT discovery enabled)

## Quick start

```shell
mkdir -p ~/rpi-reporter && cd ~/rpi-reporter
curl -fsSLO https://raw.githubusercontent.com/xorguy/RPi-Reporter-MQTT2HA-Daemon/master/docker-compose.yml
curl -fsSL -o config.ini https://raw.githubusercontent.com/xorguy/RPi-Reporter-MQTT2HA-Daemon/master/config.ini.dist
vi config.ini                        # set hostname, username, password, tls...

# config.ini holds credentials: readable by root and the container group (nogroup, 65534) only
sudo chown root:65534 config.ini
sudo chmod 0640 config.ini

# the compose file assumes the host video group is gid 44
getent group video

docker compose up -d
docker compose logs -f
```

Equivalent `docker run`:

```shell
docker run -d --name rpi-reporter --restart unless-stopped \
  --network host \
  --read-only --tmpfs /tmp:size=16m,mode=1777,noexec,nosuid,nodev \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --group-add 44 --device /dev/vcio_gencmd:/dev/vcio_gencmd \
  --pids-limit 64 --memory 192m \
  -v "$PWD/config.ini:/etc/rpi-reporter/config.ini:ro" \
  -v /etc/localtime:/etc/localtime:ro \
  -v /etc/os-release:/host/etc/os-release:ro \
  -v /etc/apt:/host/etc/apt:ro \
  -v /var/lib/apt:/host/var/lib/apt:ro \
  -v /var/lib/dpkg:/host/var/lib/dpkg:ro \
  -v /var/cache/apt:/host/var/cache/apt:ro \
  xorguy/rpi-reporter-mqtt2ha:latest
```

Add `-v` or `-d` after the image name for verbose / debug output.

## Why each option

| Option | Purpose |
|---|---|
| user `65534:65534` (image default) | runs as `nobody:nogroup`, never root |
| `--cap-drop ALL`, `no-new-privileges` | no Linux capabilities, no privilege escalation |
| `--read-only` + small `noexec` `/tmp` tmpfs | immutable container filesystem; apt needs `/tmp` to verify the host package lists |
| `--device /dev/vcio_gencmd` + `--group-add 44` | the only device used: VideoCore gencmd mailbox for `vcgencmd` (GPU temperature, throttling / under-voltage). Use your host `video` gid |
| `--network host` | **required** for correct data: the Home Assistant device id is derived from the host MAC address (a container network gives a new random MAC on every re-create = duplicate devices), plus real IP addresses, traffic and host name |
| `--pids-limit`, `--memory` | resource limits: the daemon needs ~20 MB; the apt update check runs in a short-lived child process that needs ~30 MB more with the host apt cache mounted, up to ~200 MB without it (multiarch hosts) |
| `config.ini` `:ro` | configuration and credentials, read-only |
| `/etc/os-release` → `/host/etc/os-release` | host OS release, and root filesystem usage (statistics of the filesystem holding that file) |
| `/etc/apt`, `/var/lib/apt`, `/var/lib/dpkg` → `/host/...` | pending updates count and last update date (read from the host package state, never modified) |
| `/var/cache/apt` → `/host/var/cache/apt` (optional) | the update check maps the host's prebuilt `pkgcache.bin` instead of building the package cache in memory; if missing or stale it falls back to the in-memory build |
| `/etc/localtime` | timestamps in the host time zone (or set `TZ=`) |

Docker's default seccomp and AppArmor profiles stay active; the container is **not** privileged and does **not** need `systempaths=unconfined` (the Pi model is read from `/proc/cpuinfo` when `/proc/device-tree` is masked).

`RPI_HOST_ROOT=/host` (set in the image) tells the daemon where the host paths are mounted.

## Limitations

- Only the root filesystem is reported. To report more drives, mount them read-only below `/host`, e.g. `-v /media/usb:/host/media/usb:ro` (reported as `/media/usb`).
- `[Commands]` (reboot, shutdown...) cannot work: the container has no sudo, no capabilities and no privilege escalation.
- Pending updates are counted from the host's last `apt update`; the container never refreshes package lists.

## Update

```shell
docker compose pull && docker compose up -d
```

## Verify the image

```shell
docker buildx imagetools inspect xorguy/rpi-reporter-mqtt2ha:latest
docker buildx imagetools inspect xorguy/rpi-reporter-mqtt2ha:latest --format '{{ json .Provenance }}'
docker buildx imagetools inspect xorguy/rpi-reporter-mqtt2ha:latest --format '{{ json .SBOM }}'
```

The build installs only Debian packages plus `raspi-utils-core` (and its `libgpiolib0` dependency) from the Raspberry Pi archive, verified with the pinned keyring `docker/raspberrypi-archive-keyring.pgp`; that repository is removed from the image afterwards.

## Build it yourself

```shell
# on any host with docker buildx (QEMU binfmt needed when not on arm64)
OUTPUT=--load BUILDER=default ./docker/build-push.sh   # local image
./docker/build-push.sh                                  # build + push (needs docker login)
```

## Troubleshooting

- `Configuration file ... is not readable by this user`: fix `config.ini` ownership/mode (see quick start).
- Temperature `-1` / throttle `bad response`: check `ls -l /dev/vcio_gencmd` exists and its group gid matches `--group-add`.
- `APT update check failed`: check the three apt/dpkg mounts.
