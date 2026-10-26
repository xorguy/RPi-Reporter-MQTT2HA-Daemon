# RPi Reporter MQTT2HA Daemon

This is a modified version of the original script. It removes the original's periodic release check against a third-party server (`http://kz0q.com/daemon-releases`, contacted at start-up and every 12 hours) and is hardened for current Raspberry Pi OS (see [Security notes](#security-notes)).

This is forked from here <https://github.com/ironsheep/RPi-Reporter-MQTT2HA-Daemon>

A simple Linux python script to query the Raspberry Pi on which it is running for various configuration and status values which it then reports via via [MQTT](https://projects.eclipse.org/projects/iot.mosquitto) to your [Home Assistant](https://www.home-assistant.io/) installation. This allows you to install and run this on each of your RPi's so you can track them all via your own Home Assistant Dashboard.

![Discovery image](./Docs/images/DiscoveryV4.png)

This script should be run continuously in the background as a systemd service. Instructions are provided below.

## Table of Contents

On this Page:

- [Features](#features)- key features of this reporter
- [Prerequisites](#prerequisites)
- [Installation](#installation) - install prerequisites and the daemon project
- [Configuration](#configuration) - configuring the script to talk with your MQTT broker
- [Execution](#execution) - initial run by hand, then setup to run from boot
- [Integration](#integration) - a quick look at what's reported to MQTT about this RPi
- [Security notes](#security-notes) - what is published and how to protect it
- [Troubleshooting](#troubleshooting) - having start up issues? Check here for common problems

Additional pages:

- [Controlling your RPi from Home Assistant](./RMTECTRL.md) - (Optional) Set up to allow remote control from HA
- [In practice: Advertisements to HA](./HA-ADVERT.md) - Details of the actual advertisements this Daemon publishes to MQTT
- [The Associated Lovelace RPi Monitor Card](https://github.com/ironsheep/lovelace-rpi-monitor-card) - This is our companion Lovelace Card that makes displaying this RPi Monitor data very easy
- [ChangeLog](./ChangeLog) - We've been repairing or adding features to this script as users report issues or wishes. This is our list of changes

## Features

- Targets Raspberry Pi OS / Debian Trixie (Python 3.13, paho-mqtt 2.x) on all Raspberry Pi models
- Talks only to your MQTT broker: no telemetry, no third-party connections
- Tested with Mosquitto broker
- Data is published via MQTT
- MQTT discovery messages are sent so RPi's are automatically registered with Home Assistant (if MQTT discovery is enabled in your HA installation)
- MQTT authentication and TLS support
- Runs as the unprivileged `daemon` user inside a hardened systemd sandbox
- Linux daemon / systemd service, sd_notify messages generated

### RPi Device

Each RPi device is reported as:

| Name           | Description                                  |
| -------------- | -------------------------------------------- |
| `Manufacturer` | Raspberry Pi (Trading) Ltd.                  |
| `Model`        | RPi 4 Model B v1.1                           |
| `Name`         | (fqdn) pimon1.home                           |
| `sofware ver`  | OS Name, Version (e.g., Buster v4.19.75v7l+) |

### RPi MQTT Topics

Each RPi device is reported as five topics:

| Name            | Device Class  | Units       | Description                                                                                                                                                                    |
| --------------- | ------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `~/monitor`     | 'timestamp'   | n/a         | Is a timestamp which shows when the RPi last sent information, carries a template payload conveying all monitored values (**attach the lovelace custom card to this sensor!**) |
| `~/temperature` | 'temperature' | degrees C   | Shows the latest system temperature                                                                                                                                            |
| `~/disk_used`   | none          | percent (%) | Shows the percent of root file system used                                                                                                                                     |
| `~/cpu_load`    | none          | percent (%) | Shows CPU load % over the last 5 minutes                                                                                                                                       |
| `~/mem_used`    | none          | percent (%) | Shows the percent of RAM used                                                                                                                                                  |

### RPi Monitor Topic

The monitored topic reports the following information:

| Name                | Sub-name           | Description                                                                                                             |
| ------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `rpi_model`         |                    | tinyfied hardware version string                                                                                        |
| `ifaces`            |                    | comma sep list of interfaces on board [w,e,b]                                                                           |
| `temperature_c`     |                    | System temperature, in [°C] (0.1°C resolution) Note: this is GPU temp. if available, else CPU temp. (used by HA sensor) |
| `temp_gpu_c`        |                    | GPU temperature, in [°C] (0.1°C resolution)                                                                             |
| `temp_cpu_c`        |                    | CPU temperature, in [°C] (0.1°C resolution)                                                                             |
| `up_time`           |                    | duration since last booted, as [days]                                                                                   |
| `last_update`       |                    | updates last applied, as [date]                                                                                         |
| `fs_total_gb`       |                    | / (root) total space in [GBytes]                                                                                        |
| `fs_free_prcnt`     |                    | / (root) available space [%]                                                                                            |
| `fs_used_prcnt`     |                    | / (root) used space [%] (used by HA sensor)                                                                             |
| `host_name`         |                    | hostname                                                                                                                |
| `fqdn`              |                    | hostname.domain                                                                                                         |
| `ux_release`        |                    | os release name (e.g., buster)                                                                                          |
| `ux_version`        |                    | os version (e.g., 4.19.66-v7+)                                                                                          |
| `reporter`          |                    | script name, version running on RPi                                                                                     |
| `networking`        |                    | lists for each interface: interface name, mac address (and IP if the interface is connected)                            |
| `drives`            |                    | lists for each drive mounted: size in GB, % used, device and mount point                                                |
| `cpu`               |                    | lists the model of cpu, number of cores, etc.                                                                           |
|                     | `hardware`         | - typically the Broadcom chip ID (e.g. BCM2835)                                                                         |
|                     | `model`            | - model description string (e.g., ARMv7 Processor rev 4 (v7l))                                                          |
|                     | `number_cores`     | - number of cpu cores [1,4]                                                                                             |
|                     | `bogo_mips`        | - reported performance of this RPi                                                                                      |
|                     | `serial`           | - serial number of this RPi                                                                                             |
|                     | `load_1min_prcnt`  | - average % cpu load during prior minute (avg per core)                                                                 |
|                     | `load_5min_prcnt`  | - average % cpu load during prior 5 minutes (avg per core)                                                              |
|                     | `load_15min_prcnt` | - average % cpu load during prior 15 minutes (avg per core)                                                             |
| `memory`            |                    | shows the RAM configuration in MB for this RPi                                                                          |
|                     | `size_mb`          | - total memory Size in MBytes                                                                                           |
|                     | `free_mb`          | - available memory in MBytes                                                                                            |
|                     | `size_swap`        | - total swap size in MBytes                                                                                             |
|                     | `free_swap`        | - available swap size in MBytes                                                                                         |
| `mem_used_prcnt`    |                    | shows the amount of RAM currently in use (used by HA sensor)                                                            |
| `reporter`          |                    | name and version of the script reporting these values                                                                   |
| `reporter_releases` |                    | always empty (kept for compatibility, no remote lookup)                                                                       |
| `report_interval`   |                    | interval in minutes between reports from this script                                                                    |
| `throttle`          |                    | reports the throttle status value plus interpretation thereof                                                           |
| `timestamp`         |                    | date, time when this report was generated                                                                               |

NOTE: _cpu load averages are divided by the number of cores_

## Prerequisites

An MQTT broker is needed as the counterpart for this daemon.

MQTT is huge help in connecting different parts of your smart home and setting up of a broker is quick and easy. In many cases you've already set one up when you installed Home Assistant.

## Installation

This version targets **Raspberry Pi OS / Debian Trixie** (Python 3.13, paho-mqtt 2.x). All dependencies come from apt; no `pip` is needed.

### Automated install

```shell
curl -fsSLO https://raw.githubusercontent.com/xorguy/RPi-Reporter-MQTT2HA-Daemon/master/install_rpi_reporter_mqtt.sh
less install_rpi_reporter_mqtt.sh   # review before running as root
sudo bash install_rpi_reporter_mqtt.sh
```

The installer installs the apt packages, clones the repo to `/opt/RPi-Reporter-MQTT2HA-Daemon`, adds the `daemon` user to the `video` group, writes `config.ini` (owner `root:daemon`, mode `0640`), installs the hardened systemd unit and starts it.

### Manual install

```shell
sudo apt-get install git python3 python3-sdnotify python3-colorama python3-unidecode python3-apt python3-paho-mqtt

sudo git clone https://github.com/xorguy/RPi-Reporter-MQTT2HA-Daemon.git /opt/RPi-Reporter-MQTT2HA-Daemon
```

**WARNING:** If you choose to install these files in a location other than `/opt/RPi-Reporter-MQTT2HA-Daemon`, you will need to change the paths in **isp-rpi-reporter.service** before you can run this script as a service.

**NOTE**: _on systems without apt (e.g. Arch Linux) the number of updates available will NOT be reported (will always show as '-1')._

## Configuration

To match personal needs, all operational details can be configured by modifying entries within the file [`config.ini`](config.ini.dist).
The file needs to be created first: (_in the following: if you don't have vim installed you might try nano_)

```shell
sudo cp /opt/RPi-Reporter-MQTT2HA-Daemon/config.{ini.dist,ini}
# config.ini holds your MQTT credentials: readable by root and the daemon group only
sudo chown root:daemon /opt/RPi-Reporter-MQTT2HA-Daemon/config.ini
sudo chmod 640 /opt/RPi-Reporter-MQTT2HA-Daemon/config.ini
sudo vim /opt/RPi-Reporter-MQTT2HA-Daemon/config.ini
```

You will likely want to locate and configure the following (at a minimum) in your config.ini:

```shell
fallback_domain = {if you have older RPis that dont report their fqdn correctly}
# ...
hostname = {your-mqtt-broker}
# ...
discovery_prefix = {if you use something other than 'homeassistant'}
# ...
base_topic = {your home-assistant base topic}

# ...
username = {your mqtt username if your setup requires one}
password = {your mqtt password if your setup requires one}

```

Now that your config.ini is setup let's test!

**NOTE:** _If you wish to support remote commanding of your RPi then you can find additional configuration steps in [Setting up RPi Control from Home Assistant](./RMTECTRL.md)  However, to simplifly your effort, please complete the following steps to ensure all is running as desired before you attempt to set up remote control._

## Execution

### Initial Test

A first test run is as easy as:

```shell
python3 /opt/RPi-Reporter-MQTT2HA-Daemon/ISP-RPi-mqtt-daemon.py
```

**NOTE:** _it is a good idea to execute this script by hand this way each time you modify the config.ini. By running after each modification the script can tell you through error messages if it had any problems with any values in the config.ini file, or any missing values. etc._``

Using the command line argument `--config`, a directory where to read the config.ini file from can be specified, e.g.

```shell
python3 /opt/RPi-Reporter-MQTT2HA-Daemon/ISP-RPi-mqtt-daemon.py --config /opt/RPi-Reporter-MQTT2HA-Daemon
```

### Preparing to run full time

In order to have your HA system know if your RPi is online/offline and when it last reported-in then you must set up this script to run as a system service.

But first, we need to grant access to some hardware for the user account under which the sevice will run.

### Set up daemon account to allow access to temperature values

By default this script is run as user:group **daemon:daemon**. As this script requires access to the GPU you'll want to add access to it for the daemon user as follows:

```shell
# list current groups
groups daemon
$ daemon : daemon

# add video if not present
sudo usermod daemon -a -G video

# list current groups
groups daemon
$ daemon : daemon video
#                 ^^^^^ now it is present
```

_NOTE: Yes, `video` is correct. This appears to be due to our accessing the GPU temperatures._

### Run as Systemd Daemon / Service

Set up the script to be run as a system service as follows:

```shell
sudo install -m 0644 /opt/RPi-Reporter-MQTT2HA-Daemon/isp-rpi-reporter.service /etc/systemd/system/isp-rpi-reporter.service

sudo systemctl daemon-reload

# start now and at every boot
sudo systemctl enable --now isp-rpi-reporter.service

# check to make sure all is ok with the start up
sudo systemctl status isp-rpi-reporter.service
```

The unit is sandboxed (read-only filesystem, no capabilities, no privilege escalation, only IP/unix sockets, only the VideoCore devices). Check it with `systemd-analyze security isp-rpi-reporter.service`.

### Update to latest

```shell
cd /opt/RPi-Reporter-MQTT2HA-Daemon
sudo git pull --ff-only

# the unit is copied (not linked): re-install it in case it changed
sudo install -m 0644 isp-rpi-reporter.service /etc/systemd/system/isp-rpi-reporter.service
sudo systemctl daemon-reload
sudo systemctl restart isp-rpi-reporter.service
systemctl status isp-rpi-reporter.service
```

## Security notes

- The daemon only connects to your MQTT broker. The original upstream project fetched a release list from a third-party server (`raw.githubusercontent.com`, later `http://kz0q.com/daemon-releases` over plain HTTP) at start-up and every 12 hours; this fork has removed it completely.
- Enable `tls = true` when your broker supports it, otherwise credentials and data travel in clear text.
- The published data includes hostname, FQDN, IP and MAC addresses and the CPU serial; restrict who can read `{base_topic}/#` with broker ACLs.
- Optional `[Commands]` are executed without a shell and the MQTT payload is ignored, but anyone who can publish to `{base_topic}/command/{sensor_name}/+` can trigger them: protect that topic with ACLs.

## Integration

When this script is running data will be published to the (configured) MQTT broker topic "`raspberrypi/{hostname}/...`" (e.g. `raspberrypi/picam01/...`).

An example:

```json
{
  "info": {
    "timestamp": "2023-03-01T15:37:06-07:00",
    "rpi_model": "RPi 4 Model B r1.5",
    "ifaces": "e,w,b",
    "host_name": "pip2iotgw",
    "fqdn": "pip2iotgw.home",
    "ux_release": "bullseye",
    "ux_version": "5.15.84-v8+",
    "ux_updates": 3,
    "up_time": "21:08",
    "up_time_secs": 76080,
    "last_update": "2023-02-28T18:11:41-07:00",
    "fs_total_gb": 32,
    "fs_free_prcnt": 81,
    "fs_used_prcnt": 19,
    "networking": {
      "eth0": {
        "mac": "e4:5f:01:f8:18:01",
        "rx_data": 0,
        "tx_data": 0
      },
      "wlan0": {
        "IP": "192.168.100.196",
        "mac": "e4:5f:01:f8:18:02",
        "rx_data": 31954,
        "tx_data": 8883
      }
    },
    "drives": {
      "root": {
        "size_gb": 32,
        "used_prcnt": 19,
        "device": "/dev/root",
        "mount_pt": "/"
      }
    },
    "memory": {
      "size_mb": 1849,
      "free_mb": 1484,
      "size_swap": 100,
      "free_swap": 100
    },
    "mem_used_prcnt": 19,
    "cpu": {
      "hardware": "BCM2835",
      "model": "",
      "number_cores": 4,
      "bogo_mips": "432.00",
      "serial": "1000000081ae88c7",
      "load_1min_prcnt": 2.5,
      "load_5min_prcnt": 1,
      "load_15min_prcnt": 0.2
    },
    "throttle": [
      "throttled = 0x0",
      "Not throttled"
    ],
    "temperature_c": 28.7,
    "temp_gpu_c": 28.7,
    "temp_cpu_c": 28.2,
    "reporter": "ISP-RPi-mqtt-daemon v1.8.3",
    "reporter_releases": "v1.8.2,v1.7.2,v1.7.3,v1.7.4",
    "report_interval": 5
  }
}
```

**NOTE:** Where there's an IP address that interface is connected. The `tx_data` and `rx_data` values show the average traffic in Kbit/s since the previous report for each network interface (0 on the first report).

This data can be subscribed to and processed by your home assistant installation. How you build your RPi dashboard from here is up to you!

## Troubleshooting

### Issue: I've updated my RPi OS and now I'm getting reporter script startup errors

Most often fix: _Re-add the video perms to the daemon group_

See Closed Issues: [#94](https://github.com/ironsheep/RPi-Reporter-MQTT2HA-Daemon/issues/94), [#98](https://github.com/ironsheep/RPi-Reporter-MQTT2HA-Daemon/issues/98)

We occasionaly have reports of users who updated their RPi afterwhich the RPI reporter Daemon script fails to start.  The issue is that one of the packages updated appears to have reset the `daemon` group perminsions.  For instructions on resetting the permissions to what is needed see: [Set up daemon account to allow access to temperature values](#set-up-daemon-account-to-allow-access-to-temperature-values)

### Issue: Some of my RPi's don't show up in HA

Most often fix: _check the network interface detection._

We occasionaly have reports of users with more than one RPi on their network but only one shows up in Home Assistant. This is most often caused when this script generates a non-unique id for the RPi's, which is derived from the MAC address of the first `eth*`/`wlan*` interface found in `/sys/class/net`. Run the daemon by hand with `-d` and check the `rpi_mac=` line.

### Issue: I removed the RPi sensor from HA now the RPi won't come back

Most often fix: _reboot the missing RPi._

When you remove a sensor from Home Assistant it tells the MQTT broker to 'forget' everything it knows about the RPi. Some of the information is actually `stored by the MQTT broker` so it is available while the RPi is offline. Our Daemon script only broadcasts this `stored` information when it is first started. As a result the RPi will not re-appear after delete from Home Assistant until you reboot the RPi in question. (or, alternatively, stop then restart the script.). You may find reboot easier to do.

To reboot:

```bash
sudo shutdown -r now
```

To, instead, restart the Daemon:

```bash
sudo systemctl stop isp-rpi-reporter.service
sudo systemctl start isp-rpi-reporter.service

```

### General debug

The deamon script can be run my hand while enabling debug and verbose messaging:

```shell
# first stop the running daemon
sudo systemctl stop isp-rpi-reporter.service

# now run the daemon with Debug and Verbose options enabled
python3 /opt/RPi-Reporter-MQTT2HA-Daemon/ISP-RPi-mqtt-daemon.py -d -v
```

This let's you inspect many of the values the script is going to use and to see the data being sent to the MQTT broker.

Then remember to restart the daemon when you are done:

```shell
# now restart the daemon
sudo systemctl start isp-rpi-reporter.service
```

#### Exploring MQTT state

I find [MQTT Explorer](http://mqtt-explorer.com/) to be an excellent tool to use when trying to see what's going on the MQTT messaging any MQTT enabled device.

Alternatively I also use **MQTTBox** when I want to send messages by hand to interact via MQTT. it is affered as a web extension or a native application.

#### Viewing the Daemon logs

When your script is being run as a Daemon it is logging. You can view the log output since last reboot with:

```bash
journalctl -b --no-pager -u isp-rpi-reporter.service
```

Alternatively you can create a simple script which you can run any time you want to see the log. Here's my show Daemon log script `showRpiLog`:

```bash
#!/bin/bash

(set -x;journalctl -b --no-pager -u isp-rpi-reporter.service)
```

**NOTE**: _the -b says 'since last boot' the --no-pager says just show it all without breaking it up into pages and requiring the enter key press for each page._

## Disclaimer and Legal

> _Raspberry Pi_ is registered trademark of _Raspberry Pi (Trading) Ltd._
>
> This project is a community project not for commercial use.
> The authors will not be held responsible in the event of device failure or simply errant reporting of your RPi status.
>
> This project is in no way affiliated with, authorized, maintained, sponsored or endorsed by _Raspberry Pi (Trading) Ltd._ or any of its affiliates or subsidiaries.

---

### [Copyright](copyright) | [License](LICENSE)
