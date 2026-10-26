#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import fcntl
import importlib.util
import json
import math
import os
import re
import shlex
import signal
import socket
import ssl
import struct
import subprocess
import sys
import traceback
from collections import OrderedDict
from configparser import ConfigParser
from datetime import datetime
from time import time, sleep, localtime, strftime

from colorama import Fore, Style
from unidecode import unidecode
import paho.mqtt.client as mqtt
import sdnotify

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

# python3-apt is only used by the short-lived update check child process, see
# getNumberOfAvailableUpdates(): the daemon itself never loads the package cache
apt_available = importlib.util.find_spec('apt') is not None

script_version = "2.2.0"
script_name = 'ISP-RPi-mqtt-daemon.py'
script_info = '{} v{}'.format(script_name, script_version)
project_name = 'RPi Reporter MQTT2HA Daemon'
project_url = 'https://github.com/xorguy/RPi-Reporter-MQTT2HA-Daemon'

# Argparse
opt_debug = False
opt_verbose = False

# Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()

# Logging function


def print_line(text, error=False, warning=False, info=False, verbose=False, debug=False, console=True, sd_notify=False):
    timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
    if (sd_notify):
        text = '* NOTIFY: {}'.format(text)
    if console:
        if error:
            print(Fore.RED + Style.BRIGHT + '[{}] '.format(
                timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
        elif warning:
            print(Fore.YELLOW + '[{}] '.format(timestamp) +
                  Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
        elif info or verbose:
            if opt_verbose:
                print(Fore.GREEN + '[{}] '.format(timestamp) +
                      Fore.YELLOW + '- ' + '{}'.format(text) + Style.RESET_ALL)
        elif debug:
            if opt_debug:
                print(Fore.CYAN + '[{}] '.format(timestamp) +
                      '- (DBG): ' + '{}'.format(text) + Style.RESET_ALL)
        else:
            print(Fore.GREEN + '[{}] '.format(timestamp) +
                  Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)

    timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
    if sd_notify:
        sd_notifier.notify(
            'STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))

# Identifier cleanup


def clean_identifier(name):
    clean = name.strip()
    for this, that in [
        [' ', '-'],
        ['ä', 'ae'],
        ['Ä', 'Ae'],
        ['ö', 'oe'],
        ['Ö', 'Oe'],
        ['ü', 'ue'],
        ['Ü', 'Ue'],
            ['ß', 'ss']]:
        clean = clean.replace(this, that)
    clean = unidecode(clean)
    return clean


def clean_topic_level(name):
    # MQTT topic level: no wildcards, separators or spaces
    return re.sub(r'[^a-z0-9_-]', '-', clean_identifier(name).lower())


# Argparse
parser = argparse.ArgumentParser(
    description=project_name, epilog='For further details see: ' + project_url)
parser.add_argument("-v", "--verbose",
                    help="increase output verbosity", action="store_true")
parser.add_argument(
    "-d", "--debug", help="show debug output", action="store_true")
parser.add_argument(
    "-s", "--stall", help="TEST: report only the first time", action="store_true")
parser.add_argument("-c", '--config_dir',
                    help='set directory where config.ini is located', default=sys.path[0])
parse_args = parser.parse_args()

config_dir = parse_args.config_dir
opt_debug = parse_args.debug
opt_verbose = parse_args.verbose
opt_stall = parse_args.stall

print_line('--------------------------------------------------------------------', debug=True)
print_line(script_info, info=True)
if opt_verbose:
    print_line('Verbose enabled', info=True)
if opt_debug:
    print_line('Debug enabled', debug=True)
if opt_stall:
    print_line('TEST: Stall (no-re-reporting) enabled', debug=True)

# -----------------------------------------------------------------------------
#  MQTT handlers
# -----------------------------------------------------------------------------

# Eclipse Paho callbacks (CallbackAPIVersion.VERSION2)
#  REF: https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html

mqtt_client_connected = False


def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_client_connected
    if reason_code.is_failure:
        print_line('MQTT connection error - {}'.format(reason_code), error=True, sd_notify=True)
        mqtt_client_connected = False
        # refused by broker (credentials, ACL...): exit and let systemd retry
        os._exit(1)

    print_line('* MQTT connection established', console=True, sd_notify=True)
    mqtt_client_connected = True
    publishOnlineStatus()
    # older versions left a retained status on the command topic, clear it
    client.publish(legacy_command_status_topic, payload=None, retain=True)

    # -------------------------------------------------------------------------
    # Commands Subscription
    if len(commands) > 0:
        print_line('MQTT subscription to {}/+ enabled'.format(command_base_topic), console=True, sd_notify=True)
        client.subscribe('{}/+'.format(command_base_topic))
    else:
        print_line('MQTT subscription to {}/+ disabled'.format(command_base_topic), console=True, sd_notify=True)


def on_disconnect(client, userdata, flags, reason_code, properties):
    global mqtt_client_connected
    mqtt_client_connected = False
    print_line('* MQTT connection lost ({})'.format(reason_code), console=True, sd_notify=True)

# -----------------------------------------------------------------------------
# Commands - MQTT Subscription Callback
# -----------------------------------------------------------------------------


def on_message(client, userdata, message):
    command = message.topic.split('/')[-1]
    if command == 'status':
        return
    if message.retain:
        # never act on a stale (retained) request, e.g. on reconnect
        print_line('* Ignoring retained command "{}"'.format(command), warning=True)
        return
    # NOTE: the payload is intentionally ignored, it is never passed to the command
    if command not in commands:
        print_line('* Invalid Command "{}" received.'.format(command), error=True)
        return
    run_command(command, commands[command])


def run_command(command, argv):
    print_line('- Command "{}" Received - Run {} -'.format(command, argv), console=True, sd_notify=True)
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print_line('- Command "{}" failed: {}'.format(command, exc), error=True)
        return
    if result.returncode:
        print_line('- Command "{}" exit({}) says: [{}]'.format(command, result.returncode,
                   result.stdout.decode('utf-8', errors='replace').strip()), error=True)


# -----------------------------------------------------------------------------
# Load configuration file
# NOTE: only full-line comments are supported so values (e.g. passwords) may contain '#'
config = ConfigParser(delimiters=('=', ), comment_prefixes=('#', ';'),
                      inline_comment_prefixes=None, interpolation=None)
config.optionxform = str
config_filespec = os.path.join(config_dir, 'config.ini')
try:
    with open(config_filespec) as config_file:
        config.read_file(config_file)
except FileNotFoundError:
    print_line('No configuration file "{}"'.format(config_filespec),
               error=True, sd_notify=True)
    sys.exit(1)
except PermissionError:
    print_line('Configuration file "{}" is not readable by this user (expected owner root:daemon, mode 0640)'.format(
        config_filespec), error=True, sd_notify=True)
    sys.exit(1)

# Ensure required values within sections of our config are present
if not config.has_section('MQTT'):
    print_line('ERROR: No MQTT settings found in configuration file "config.ini"! Fix and try again... Aborting',
               error=True, sd_notify=True)
    sys.exit(1)
if not config.has_section('Daemon'):
    config.add_section('Daemon')

default_base_topic = 'home/nodes'
base_topic = config['MQTT'].get('base_topic', default_base_topic).lower()

# Sensor name could be set either via configuration file or `MQTT_SENSOR_NAME`
# environment variable, the latter takes precedence. Default: rpi-{hostname}
configured_sensor_name = os.environ.get(
    "MQTT_SENSOR_NAME", config['MQTT'].get('sensor_name', '')).strip()

# by default Home Assistant listens to the /homeassistant but it can be changed for a given installation
default_discovery_prefix = 'homeassistant'
discovery_prefix = config['MQTT'].get(
    'discovery_prefix', default_discovery_prefix).lower()

# report our RPi values every 5min
min_interval_in_minutes = 1
max_interval_in_minutes = 30
default_interval_in_minutes = 5
interval_in_minutes = config['Daemon'].getint(
    'interval_in_minutes', default_interval_in_minutes)

# check our RPi pending-updates every 4 hours
min_check_interval_in_hours = 2
max_check_interval_in_hours = 24
default_check_interval_in_hours = 4
check_interval_in_hours = config['Daemon'].getint(
    'check_updates_in_hours', default_check_interval_in_hours)

# default domain when hostname -f doesn't return it
default_domain = ''
fallback_domain = config['Daemon'].get(
    'fallback_domain', default_domain).lower()

# -----------------------------------------------------------------------------
#  Commands: name -> argv (run without a shell)
# -----------------------------------------------------------------------------
commands = OrderedDict([])
if config.has_section('Commands'):
    for command_name, command_line in config['Commands'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', command_name):
            print_line('Ignoring command with invalid name [{}]'.format(command_name), warning=True)
            continue
        try:
            command_argv = shlex.split(command_line)
        except ValueError as exc:
            print_line('Ignoring command [{}]: {}'.format(command_name, exc), warning=True)
            continue
        if len(command_argv) > 0:
            commands[command_name] = command_argv

# Check configuration
#
if (interval_in_minutes < min_interval_in_minutes) or (interval_in_minutes > max_interval_in_minutes):
    print_line('ERROR: Invalid "interval_in_minutes" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(
        min_interval_in_minutes, max_interval_in_minutes), error=True, sd_notify=True)
    sys.exit(1)

if (check_interval_in_hours < min_check_interval_in_hours) or (check_interval_in_hours > max_check_interval_in_hours):
    print_line('ERROR: Invalid "check_updates_in_hours" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(
        min_check_interval_in_hours, max_check_interval_in_hours), error=True, sd_notify=True)
    sys.exit(1)

print_line('Configuration accepted', console=False, sd_notify=True)

# -----------------------------------------------------------------------------
#  Helpers: read files / run commands without a shell
# -----------------------------------------------------------------------------


# Container support: host paths are mounted read-only below this directory
# (e.g. RPI_HOST_ROOT=/host), see DOCKER.md
host_root = os.environ.get('RPI_HOST_ROOT', '').rstrip('/')


def host_path(fspec):
    return host_root + fspec


def read_text(fspec, default=''):
    try:
        with open(fspec, 'r', encoding='utf-8', errors='replace') as text_file:
            return text_file.read()
    except OSError as exc:
        print_line('read_text({}) failed: {}'.format(fspec, exc), debug=True)
        return default


def run_cmd(argv, timeout=10):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print_line('run_cmd({}) failed: {}'.format(argv, exc), debug=True)
        return '', -1
    return result.stdout.decode('utf-8', errors='replace'), result.returncode


# -----------------------------------------------------------------------------
#  RPi variables monitored
# -----------------------------------------------------------------------------

rpi_mac = ''
rpi_model_raw = ''
rpi_model = ''
rpi_connections = ''
rpi_hostname = ''
rpi_fqdn = ''
rpi_linux_release = ''
rpi_linux_version = ''
rpi_uptime = ''
rpi_uptime_sec = 0
rpi_last_update_date = None
rpi_filesystem_space = '0'
rpi_filesystem_percent = '0'
rpi_system_temp = -1.0
rpi_gpu_temp = -1.0
rpi_cpu_temp = -1.0
rpi_mqtt_script = script_info
rpi_interfaces = []
rpi_filesystem = []
# Tuple (Total, Free, Avail., Swap Total, Swap Free)
rpi_memory_tuple = ''
# Tuple (Hardware, Model Name, NbrCores, BogoMIPS, Serial, Load1, Load5, Load15)
rpi_cpu_tuple = ''
# for thermal status reporting
rpi_throttle_status = []
rpi_update_count = 0

if apt_available == False:
    rpi_update_count = -1   # if packaging system not avail. report -1

VCGENCMD = '/usr/bin/vcgencmd'

# -----------------------------------------------------------------------------
#  monitor variable fetch routines
#


def getDeviceCpuInfo():
    global rpi_cpu_tuple
    # /proc/cpuinfo (32-bit example):
    #  processor	: 0
    #  model name	: ARMv7 Processor rev 4 (v7l)
    #  BogoMIPS	: 38.40
    #  ...
    #  Hardware	: BCM2835
    #  Serial		: 00000000a8d11642
    cpu_hardware = ''   # 'hardware'
    cpu_cores = 0       # count of 'processor' lines
    cpu_model = ''      # 'model name'
    cpu_bogoMIPS = 0.0  # sum of 'BogoMIPS' lines
    cpu_serial = ''     # 'serial'
    for currLine in read_text('/proc/cpuinfo').splitlines():
        key, separator, value = currLine.partition(':')
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key == 'Hardware':
            cpu_hardware = value
        elif key == 'model name':
            cpu_model = value
        elif key == 'BogoMIPS':
            try:
                cpu_bogoMIPS += float(value)
            except ValueError:
                pass
        elif key == 'processor':
            cpu_cores += 1
        elif key == 'Serial':
            cpu_serial = value
    if cpu_cores == 0:
        cpu_cores = os.cpu_count() or 1

    cpu_load1, cpu_load5, cpu_load15 = [round(load / cpu_cores * 100, 1) for load in os.getloadavg()]

    rpi_cpu_tuple = (cpu_hardware, cpu_model, cpu_cores,
                     cpu_bogoMIPS, cpu_serial, cpu_load1, cpu_load5, cpu_load15)
    print_line('rpi_cpu_tuple=[{}]'.format(rpi_cpu_tuple), debug=True)


def getDeviceMemory():
    global rpi_memory_tuple
    #  MemTotal:         948304 kB
    #  MemFree:           40632 kB
    #  MemAvailable:     513332 kB
    meminfo = {}
    for currLine in read_text('/proc/meminfo').splitlines():
        lineParts = currLine.replace(':', ' ').split()
        if len(lineParts) >= 2:
            try:
                meminfo[lineParts[0]] = float(lineParts[1]) / 1024
            except ValueError:
                pass

    # Tuple (Total, Free, Avail., Swap Total, Swap Free)
    rpi_memory_tuple = (meminfo.get('MemTotal', 0.0), meminfo.get('MemFree', 0.0), meminfo.get('MemAvailable', 0.0),
                        meminfo.get('SwapTotal', 0.0), meminfo.get('SwapFree', 0.0))
    print_line('rpi_memory_tuple=[{}]'.format(rpi_memory_tuple), debug=True)


def getDeviceModel():
    global rpi_model
    global rpi_model_raw
    global rpi_connections
    rpi_model_raw = read_text('/proc/device-tree/model').replace('\x00', '').strip()
    if not rpi_model_raw:
        # /proc/device-tree is masked in containers, the RPi kernel also has it here
        for currLine in read_text('/proc/cpuinfo').splitlines():
            key, _, value = currLine.partition(':')
            if key.strip() == 'Model':
                rpi_model_raw = value.strip()
    rpi_model_raw = rpi_model_raw or 'N/A'
    # now reduce string length (just more compact, same info)
    rpi_model = rpi_model_raw.replace('Raspberry ', 'R').replace(
        'i Model ', 'i 1 Model ').replace('Rev ', 'r').replace(' Plus ', '+')

    # now decode interfaces (e=ethernet, w=wifi, b=bluetooth)
    if 'Zero' in rpi_model_raw:
        rpi_connections = 'w,b' if ('Zero W' in rpi_model_raw or 'Zero 2' in rpi_model_raw) else ''
    elif 'Pi 3 Model A' in rpi_model_raw:
        rpi_connections = 'w,b'
    elif 'Pi 2 ' in rpi_model_raw:
        rpi_connections = 'e'
    elif 'Pi Model A' in rpi_model_raw:
        rpi_connections = ''
    elif 'Pi Model B' in rpi_model_raw:
        rpi_connections = 'e'
    else:
        rpi_connections = 'e,w,b'  # Pi 3B/3B+/4/400/5, CM...

    print_line('rpi_model_raw=[{}]'.format(rpi_model_raw), debug=True)
    print_line('rpi_model=[{}]'.format(rpi_model), debug=True)
    print_line('rpi_connections=[{}]'.format(rpi_connections), debug=True)


def getLinuxRelease():
    global rpi_linux_release
    rpi_linux_release = 'N/A'
    for currLine in read_text(host_path('/etc/os-release')).splitlines():
        key, _, value = currLine.partition('=')
        if key.strip() == 'VERSION_CODENAME' and value.strip():
            rpi_linux_release = value.strip().strip('"\'')
    print_line('rpi_linux_release=[{}]'.format(rpi_linux_release), debug=True)


def getLinuxVersion():
    global rpi_linux_version
    rpi_linux_version = os.uname().release
    print_line('rpi_linux_version=[{}]'.format(rpi_linux_version), debug=True)


def getHostnames():
    global rpi_hostname
    global rpi_fqdn
    hostname = socket.gethostname()
    # same lookup `hostname -f` does
    try:
        fqdn_from_hostname = socket.getaddrinfo(hostname, None, flags=socket.AI_CANONNAME)[0][3] or hostname
    except (OSError, IndexError):
        fqdn_from_hostname = hostname
    # Allow overriding the sensor host name via `MQTT_SENSOR_HOSTNAME`
    # environment variable
    fqdn_raw = os.environ.get('MQTT_SENSOR_HOSTNAME', fqdn_from_hostname)
    print_line('fqdn_raw=[{}]'.format(fqdn_raw), debug=True)
    rpi_hostname = fqdn_raw
    if '.' in fqdn_raw:
        # have good fqdn
        rpi_fqdn = fqdn_raw
        rpi_hostname = fqdn_raw.split('.')[0]
    else:
        # missing domain, if we have a fallback apply it
        if len(fallback_domain) > 0:
            rpi_fqdn = '{}.{}'.format(fqdn_raw, fallback_domain)
        else:
            rpi_fqdn = rpi_hostname

    print_line('rpi_fqdn=[{}]'.format(rpi_fqdn), debug=True)
    print_line('rpi_hostname=[{}]'.format(rpi_hostname), debug=True)


def getUptime():
    global rpi_uptime
    global rpi_uptime_sec
    try:
        rpi_uptime_sec = int(float(read_text('/proc/uptime').split()[0]))
    except (ValueError, IndexError):
        rpi_uptime_sec = 0
    daysVal, remainder = divmod(rpi_uptime_sec, 24 * 60 * 60)
    hoursVal, remainder = divmod(remainder, 60 * 60)
    minsVal = remainder // 60
    # Ex: 10 days, 23:57
    # Ex: 1 day, 27 min
    # Ex: 0 min
    if hoursVal > 0:
        timeStr = '{}:{:02d}'.format(hoursVal, minsVal)
    else:
        timeStr = '{} min'.format(minsVal)
    if daysVal > 0:
        rpi_uptime = '{} day{}, {}'.format(daysVal, '' if daysVal == 1 else 's', timeStr)
    else:
        rpi_uptime = timeStr
    print_line('rpi_uptime=[{}], rpi_uptime_sec=({})'.format(rpi_uptime, rpi_uptime_sec), debug=True)


# Network: physical ethernet (eth*, en*) and wifi (wl*) interfaces
K_NET_IF_PREFIXES = ('eth', 'en', 'wl')
SIOCGIFADDR = 0x8915

previous_net_counters = {}  # { interface: (rx_bytes, tx_bytes) }
previous_net_time = 0.0


def getInterfaceNames():
    interfaces = []
    try:
        entries = os.listdir('/sys/class/net')
    except OSError:
        return interfaces
    for interfaceName in entries:
        if interfaceName.startswith(K_NET_IF_PREFIXES):
            try:
                ifindex = int(read_text('/sys/class/net/{}/ifindex'.format(interfaceName), '0'))
            except ValueError:
                ifindex = 0
            interfaces.append((ifindex, interfaceName))
    return [interfaceName for _, interfaceName in sorted(interfaces)]


def getInterfaceIPv4(interfaceName):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = fcntl.ioctl(sock.fileno(), SIOCGIFADDR,
                                 struct.pack('256s', interfaceName[:15].encode('utf-8')))
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return ''  # no IPv4 address assigned


def readCounter(fspec):
    try:
        return int(read_text(fspec, '0'))
    except ValueError:
        return 0


def getNetworkIFs():
    global rpi_interfaces
    global rpi_mac
    global previous_net_time
    # TYPICAL result:
    #   ('eth0', 'mac', 'b8:27:eb:1a:f3:bc'), ('eth0', 'rx_data', 0), ('eth0', 'tx_data', 0),
    #   ('wlan0', 'IP', '192.168.100.189'), ('wlan0', 'mac', 'b8:27:eb:4f:a6:e9'), ...
    current_time = time()
    elapsed = (current_time - previous_net_time) if previous_net_time else 0
    tmpInterfaces = []
    macs = OrderedDict()
    for interfaceName in getInterfaceNames():
        sysfs_base = '/sys/class/net/{}'.format(interfaceName)
        ip_address = getInterfaceIPv4(interfaceName)
        if ip_address:
            tmpInterfaces.append((interfaceName, 'IP', ip_address))
        mac = read_text(sysfs_base + '/address').strip()
        if mac:
            tmpInterfaces.append((interfaceName, 'mac', mac))
            macs[interfaceName] = mac
        rx_bytes = readCounter(sysfs_base + '/statistics/rx_bytes')
        tx_bytes = readCounter(sysfs_base + '/statistics/tx_bytes')
        rx_data = 0
        tx_data = 0
        previous_counters = previous_net_counters.get(interfaceName)
        if previous_counters and elapsed > 0:
            # Kbit/s since previous report
            rx_data = max(0, round((rx_bytes - previous_counters[0]) / elapsed * 8 / 1024))
            tx_data = max(0, round((tx_bytes - previous_counters[1]) / elapsed * 8 / 1024))
        previous_net_counters[interfaceName] = (rx_bytes, tx_bytes)
        tmpInterfaces.append((interfaceName, 'rx_data', rx_data))
        tmpInterfaces.append((interfaceName, 'tx_data', tx_data))
    previous_net_time = current_time

    # device identity: prefer eth*/wlan* MAC (as older versions did) so the
    # Home Assistant unique ids stay the same
    rpi_mac = ''
    for interfaceName, mac in macs.items():
        if interfaceName.startswith(('eth', 'wlan')):
            rpi_mac = mac
            break
    if rpi_mac == '' and len(macs) > 0:
        rpi_mac = next(iter(macs.values()))

    rpi_interfaces = tmpInterfaces
    print_line('rpi_interfaces=[{}]'.format(rpi_interfaces), debug=True)
    print_line('rpi_mac=[{}]'.format(rpi_mac), debug=True)


# pseudo/virtual filesystems we never report
K_FS_SKIP_TYPES = {'autofs', 'binfmt_misc', 'bpf', 'cgroup', 'cgroup2', 'configfs', 'debugfs', 'devpts',
                   'devtmpfs', 'efivarfs', 'fusectl', 'hugetlbfs', 'mqueue', 'nsfs', 'overlay', 'proc',
                   'pstore', 'ramfs', 'rpc_pipefs', 'securityfs', 'squashfs', 'sysfs', 'tmpfs', 'tracefs'}


def decodeMountField(field):
    # /proc/mounts escapes space, tab, newline and backslash as octal (e.g. \040)
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), field)


def getFileSystemDrives():
    global rpi_filesystem_space
    global rpi_filesystem_percent
    global rpi_filesystem
    candidates = []  # (device, mount point as seen by the host, path to statvfs)
    for currLine in read_text('/proc/mounts').splitlines():
        lineParts = currLine.split()
        if len(lineParts) < 3:
            continue
        device, mount_point, fs_type = [decodeMountField(part) for part in lineParts[:3]]
        stat_path = mount_point
        if host_root:
            # container: only report host filesystems mounted below host_root
            if mount_point == host_path('/etc/os-release'):
                mount_point = '/'  # bind mounted file living on the host root fs
            elif mount_point.startswith(host_root + '/') and os.path.isdir(mount_point):
                mount_point = mount_point[len(host_root):]
            else:
                continue
        if fs_type in K_FS_SKIP_TYPES or fs_type.startswith('fuse.') or 'boot' in mount_point:
            continue
        # real block devices and network shares only (//srv/share, srv:/path)
        if not (device.startswith('/') or ':' in device):
            continue
        candidates.append((device, mount_point, stat_path))

    tmpDrives = []
    seenDevices = set()
    # root first so it wins over other mounts of the same device
    for device, mount_point, stat_path in sorted(candidates, key=lambda candidate: candidate[1] != '/'):
        if device in seenDevices:
            continue  # bind mount
        try:
            stat = os.statvfs(stat_path)
        except OSError as exc:
            print_line('statvfs({}) failed: {}'.format(stat_path, exc), debug=True)
            continue
        if stat.f_blocks == 0:
            continue
        seenDevices.add(device)

        # same values df(1) reports
        total_mb = stat.f_blocks * stat.f_frsize // (1024 * 1024)
        used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
        avail = stat.f_bavail * stat.f_frsize
        used_prcnt = math.ceil(used * 100 / (used + avail)) if (used + avail) > 0 else 0

        # tuple { total GB, used%, mountPoint, device }
        total_size_in_gb = '{:.0f}'.format(next_power_of_2(total_mb))
        newTuple = (total_size_in_gb, str(used_prcnt), mount_point, device)
        tmpDrives.append(newTuple)
        print_line('newTuple=[{}]'.format(newTuple), debug=True)
        if mount_point == '/':
            rpi_filesystem_space = newTuple[0]
            rpi_filesystem_percent = newTuple[1]

    rpi_filesystem = tmpDrives
    print_line('rpi_filesystem=[{}]'.format(rpi_filesystem), debug=True)


def next_power_of_2(size_in_mb):
    size_as_nbr = int(size_in_mb) - 1
    return 1 if size_as_nbr <= 0 else (1 << size_as_nbr.bit_length()) / 1024


def haveVcGenCmd():
    return os.access(VCGENCMD, os.X_OK)


def getSystemTemperature():
    global rpi_system_temp
    global rpi_gpu_temp
    global rpi_cpu_temp
    rpi_cpu_temp = getSystemCPUTemperature()
    rpi_gpu_temp = -1.0
    if haveVcGenCmd():
        for _ in range(3):
            stdout, returncode = run_cmd([VCGENCMD, 'measure_temp'])
            # temp=48.3'C
            match = re.search(r'temp=(-?[\d.]+)', stdout)
            if returncode == 0 and match:
                rpi_gpu_temp = float(match.group(1))
                break
            sleep(1)
    print_line('rpi_gpu_temp=[{}]'.format(rpi_gpu_temp), debug=True)

    # fallback to CPU temp if GPU not available
    rpi_system_temp = rpi_gpu_temp if rpi_gpu_temp != -1.0 else rpi_cpu_temp


def getSystemCPUTemperature():
    rpi_cpu_temp = -1.0
    try:
        rpi_cpu_temp = float(read_text('/sys/class/thermal/thermal_zone0/temp')) / 1000.0
    except ValueError:
        pass
    print_line('rpi_cpu_temp=[{}]'.format(rpi_cpu_temp), debug=True)
    return rpi_cpu_temp


def getSystemThermalStatus():
    global rpi_throttle_status
    # vcgencmd get_throttled
    #   throttled=0x0
    #
    #  REF: https://harlemsquirrel.github.io/shell/2019/01/05/monitoring-raspberry-pi-power-and-thermal-issues.html
    #
    rpi_throttle_status = []
    if not haveVcGenCmd():
        rpi_throttle_status.append('Not Available')
    else:
        stdout, returncode = run_cmd([VCGENCMD, 'get_throttled'])
        rpi_throttle_status_raw = stdout.strip() if returncode == 0 else ''
        print_line('rpi_throttle_status_raw=[{}]'.format(
            rpi_throttle_status_raw), debug=True)

        _, _, rpi_throttle_value_raw = rpi_throttle_status_raw.partition('=')
        try:
            rpi_throttle_value = int(rpi_throttle_value_raw, 0)
        except ValueError:
            rpi_throttle_value = None
        if 'throttled' not in rpi_throttle_status_raw or rpi_throttle_value is None:
            rpi_throttle_status.append(
                'bad response [{}] from vcgencmd'.format(rpi_throttle_status_raw))
        elif rpi_throttle_value > 0:
            rpi_throttle_status = interpretThrottleValue(rpi_throttle_value)
        else:
            rpi_throttle_status = ['throttled = {}'.format(rpi_throttle_value_raw), 'Not throttled']

    print_line('rpi_throttle_status=[{}]'.format(
        rpi_throttle_status), debug=True)


def interpretThrottleValue(throttleValue):
    """
    01110000000000000010
    ||||            ||||_ Under-voltage detected
    ||||            |||_ Arm frequency capped
    ||||            ||_ Currently throttled
    ||||            |_ Soft temperature limit active
    ||||_ Under-voltage has occurred since last reboot
    |||_ Arm frequency capped has occurred
    ||_ Throttling has occurred
    |_ Soft temperature limit has occurred
    """
    print_line('throttleValue=[{}]'.format(bin(throttleValue)), debug=True)
    interpResult = []
    meanings = [
        (2**0, 'Under-voltage detected'),
        (2**1, 'Arm frequency capped'),
        (2**2, 'Currently throttled'),
        (2**3, 'Soft temperature limit active'),
        (2**16, 'Under-voltage has occurred'),
        (2**17, 'Arm frequency capped has occurred'),
        (2**18, 'Throttling has occurred'),
        (2**19, 'Soft temperature limit has occurred'),
    ]

    for bitValue, meaning in meanings:
        if throttleValue & bitValue > 0:
            interpResult.append(meaning)

    print_line('interpResult=[{}]'.format(interpResult), debug=True)
    return interpResult


def getLastUpdateDate():
    global rpi_last_update_date
    # apt-get update writes to following dir (so date changes on update)
    # apt-get dist-upgrade | autoremove update the dpkg lock file when actions are taken
    modTimes = []
    for fspec in ['/var/lib/apt/lists/partial', '/var/lib/dpkg/lock']:
        try:
            modTimes.append(os.path.getmtime(host_path(fspec)))
        except OSError:
            pass
    rpi_last_update_date = datetime.fromtimestamp(max(modTimes)).astimezone() if modTimes else None
    print_line('rpi_last_update_date=[{}]'.format(
        rpi_last_update_date), debug=True)


# Runs in a child process (python3 -I -c ...): the apt package cache needs tens of
# MB (hundreds when it has to be built from the package lists) and glibc does not
# give that heap back, so it is released by exiting. argv[1]: host_root ('' = /).
# apt mmaps the prebuilt (host) pkgcache.bin when it is valid, otherwise it builds
# the cache in memory: the cache directory is never written (read-only / not ours)
APT_CHECK_CODE = '''
import json, sys
import apt, apt_pkg
host_root = sys.argv[1]
if host_root:
    # read the host package state (read-only mounts); unlike apt.Cache(rootdir=)
    # this never tries to create missing files below host_root
    apt_pkg.config.set('Dir', host_root)
    apt_pkg.config.set('Dir::State::status', host_root + '/var/lib/dpkg/status')
    apt_pkg.init_system()
cache = apt.Cache()
cache.upgrade()
print(json.dumps([package.name for package in cache.get_changes()]))
'''


def getNumberOfAvailableUpdates():
    global rpi_update_count
    if apt_available:
        try:
            result = subprocess.run([sys.executable, '-I', '-c', APT_CHECK_CODE, host_root],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=300, check=False)
            if result.returncode:
                # last stderr line holds the exception
                stderr_lines = result.stderr.decode('utf-8', errors='replace').strip().splitlines() or ['']
                raise RuntimeError('exit({}) {}'.format(result.returncode, stderr_lines[-1]))
            changes = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            print_line('APT update check failed: {}'.format(exc), error=True)
            return
        print_line('APT changes=[{}]'.format(changes), debug=True)
        print_line('APT Avail Updates: ({})'.format(len(changes)), info=True)
        rpi_update_count = len(changes)


# get our hostnames so we can setup MQTT
getHostnames()
sensor_name = clean_topic_level(configured_sensor_name or 'rpi-{}'.format(rpi_hostname))

# get model so we can use it too in MQTT
getDeviceModel()
getDeviceCpuInfo()
getLinuxRelease()
getLinuxVersion()
getFileSystemDrives()
if apt_available:
    getNumberOfAvailableUpdates()

# -----------------------------------------------------------------------------
#  MQTT Topic def's
# -----------------------------------------------------------------------------

sensor_base_topic = '{}/sensor/{}'.format(base_topic, sensor_name)
command_base_topic = '{}/command/{}'.format(base_topic, sensor_name)
# single availability topic (LWT) shared by all entities
availability_topic = '{}/status'.format(sensor_base_topic)
legacy_command_status_topic = '{}/status'.format(command_base_topic)
lwt_online_val = 'online'
lwt_offline_val = 'offline'


def publishOnlineStatus():
    print_line('- SEND: online -', debug=True)
    mqtt_client.publish(availability_topic, payload=lwt_online_val, qos=1, retain=True)


def publishShuttingDownStatus():
    print_line('- SEND: shutting down -', debug=True)
    try:
        mqtt_client.publish(availability_topic, payload=lwt_offline_val, qos=1, retain=True).wait_for_publish(timeout=3)
    except (RuntimeError, ValueError) as exc:
        print_line('- offline status not sent: {}'.format(exc), debug=True)


# -----------------------------------------------------------------------------
#  MQTT setup and startup
# -----------------------------------------------------------------------------

print_line('Connecting to MQTT broker ...', verbose=True)
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

# hook up MQTT callbacks
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

mqtt_client.will_set(availability_topic, payload=lwt_offline_val, qos=1, retain=True)

if config['MQTT'].getboolean('tls', False):
    tls_context = ssl.create_default_context(cafile=config['MQTT'].get('tls_ca_cert') or None)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_certfile = config['MQTT'].get('tls_certfile') or None
    if tls_certfile:
        tls_context.load_cert_chain(tls_certfile, config['MQTT'].get('tls_keyfile') or None)
    # Allow skipping server hostname verification if `tls_insecure` is set
    # (the certificate chain is still verified), same as paho tls_insecure_set()
    if config['MQTT'].getboolean('tls_insecure', False):
        print_line('WARNING: tls_insecure enabled, broker hostname is NOT verified', warning=True, sd_notify=True)
        tls_context.check_hostname = False
    mqtt_client.tls_set_context(tls_context)
else:
    print_line('MQTT connection is NOT encrypted (tls = false)', info=True)

mqtt_username = os.environ.get("MQTT_USERNAME", config['MQTT'].get('username'))
mqtt_password = os.environ.get(
    "MQTT_PASSWORD", config['MQTT'].get('password', None))

if mqtt_username:
    mqtt_client.username_pw_set(mqtt_username, mqtt_password)
try:
    mqtt_client.connect(os.environ.get('MQTT_HOSTNAME', config['MQTT'].get('hostname', 'localhost')),
                        port=int(os.environ.get(
                            'MQTT_PORT', config['MQTT'].get('port', '1883'))),
                        keepalive=config['MQTT'].getint('keepalive', 60))
except Exception as exc:
    print_line('MQTT connection error ({}). Please check your settings in the configuration file "config.ini"'.format(exc),
               error=True, sd_notify=True)
    sys.exit(1)

mqtt_client.loop_start()

connect_deadline = time() + 60
while mqtt_client_connected == False:  # wait in loop
    if time() > connect_deadline:
        print_line('MQTT connection not established after 60 seconds, aborting', error=True, sd_notify=True)
        sys.exit(1)
    print_line('* Wait on mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
    sleep(1.0)  # some slack to establish the connection

sd_notifier.notify('READY=1')

# -----------------------------------------------------------------------------
#  Perform our MQTT Discovery Announcement...
# -----------------------------------------------------------------------------

# what RPi device are we on?
getNetworkIFs()  # this will fill-in rpi_mac

mac_basic = rpi_mac.lower().replace(":", "")
mac_left = mac_basic[:6]
mac_right = mac_basic[6:]
print_line('mac lt=[{}], rt=[{}], mac=[{}]'.format(
    mac_left, mac_right, mac_basic), debug=True)
uniqID = "RPi-{}Mon{}".format(mac_left, mac_right)

# our RPi Reporter device
# KeyError: 'home310/sensor/rpi-pi3plus/values' let's not use this 'values' as topic
K_LD_MONITOR = "monitor"
K_LD_SYS_TEMP = "temperature"
K_LD_FS_USED = "disk_used"
K_LD_PAYLOAD_NAME = "info"
K_LD_CPU_USE = "cpu_load"
K_LD_MEM_USED = "mem_used"

if interval_in_minutes < 5:
    K_LD_CPU_USE_JSON = "cpu.load_1min_prcnt"
elif interval_in_minutes < 15:
    K_LD_CPU_USE_JSON = "cpu.load_5min_prcnt"
else:
    K_LD_CPU_USE_JSON = "cpu.load_15min_prcnt"

# determine CPU model
if len(rpi_cpu_tuple) > 0:
    cpu_model = rpi_cpu_tuple[1]
else:
    cpu_model = ''

if cpu_model.find("ARMv7") >= 0 or cpu_model.find("ARMv6") >= 0:
    cpu_use_icon = "mdi:cpu-32-bit"
else:
    cpu_use_icon = "mdi:cpu-64-bit"

print_line('Announcing RPi Monitoring device to MQTT broker for auto-discovery ...')

# Publish our MQTT auto discovery
#  table of key items to publish:
detectorValues = OrderedDict([
    (K_LD_MONITOR, dict(
        title="Monitor",
        topic_category="sensor",
        device_class="timestamp",
        device_ident="RPi-{}".format(rpi_fqdn),
        no_title_prefix="yes",
        icon='mdi:raspberry-pi',
        json_attr="yes",
        json_value="timestamp",
    )),
    (K_LD_SYS_TEMP, dict(
        title="Temperature",
        topic_category="sensor",
        device_class="temperature",
        state_class="measurement",
        no_title_prefix="yes",
        unit="°C",
        icon='mdi:thermometer',
        json_value="temperature_c",
    )),
    (K_LD_FS_USED, dict(
        title="Disk Used",
        topic_category="sensor",
        state_class="measurement",
        no_title_prefix="yes",
        unit="%",
        icon='mdi:sd',
        json_value="fs_used_prcnt",
    )),
    (K_LD_CPU_USE, dict(
        title="CPU Use",
        topic_category="sensor",
        state_class="measurement",
        no_title_prefix="yes",
        unit="%",
        icon=cpu_use_icon,
        json_value=K_LD_CPU_USE_JSON,
    )),
    (K_LD_MEM_USED, dict(
        title="Memory Used",
        topic_category="sensor",
        state_class="measurement",
        no_title_prefix="yes",
        json_value="mem_used_prcnt",
        unit="%",
        icon='mdi:memory'
    ))
])

for command in commands:
    iconName = 'mdi:gesture-tap'
    if 'reboot' in command:
        iconName = 'mdi:restart'
    elif 'shutdown' in command:
        iconName = 'mdi:power-sleep'
    elif 'service' in command:
        iconName = 'mdi:cog-counterclockwise'
    detectorValues.update({
        command: dict(
            title=command,
            topic_category='button',
            no_title_prefix='yes',
            icon=iconName,
            command=command,
        )
    })

values_topic_rel = '{}/{}'.format('~', K_LD_MONITOR)
values_topic = '{}/{}'.format(sensor_base_topic, K_LD_MONITOR)

for [sensor, params] in detectorValues.items():
    discovery_topic = '{}/{}/{}/{}/config'.format(discovery_prefix,
                                                  params['topic_category'], sensor_name, sensor)
    payload = OrderedDict()
    if 'no_title_prefix' in params:
        payload['name'] = "{}".format(params['title'].title())
    else:
        payload['name'] = "{} {}".format(
            sensor_name.title(), params['title'].title())
    payload['uniq_id'] = "{}_{}".format(uniqID, sensor.lower())
    if 'device_class' in params:
        payload['dev_cla'] = params['device_class']
    if 'state_class' in params:
        payload['stat_cla'] = params['state_class']
    if 'unit' in params:
        payload['unit_of_measurement'] = params['unit']
    if 'json_value' in params:
        payload['stat_t'] = values_topic_rel
        payload['val_tpl'] = "{{{{ value_json.{}.{} }}}}".format(K_LD_PAYLOAD_NAME, params['json_value'])
    if 'command' in params:
        payload['~'] = command_base_topic
        payload['cmd_t'] = '~/{}'.format(params['command'])
    else:
        payload['~'] = sensor_base_topic
    payload['avty_t'] = availability_topic
    payload['pl_avail'] = lwt_online_val
    payload['pl_not_avail'] = lwt_offline_val
    if 'icon' in params:
        payload['ic'] = params['icon']
    if 'json_attr' in params:
        payload['json_attr_t'] = values_topic_rel
        payload['json_attr_tpl'] = '{{{{ value_json.{} | tojson }}}}'.format(K_LD_PAYLOAD_NAME)
    if 'device_ident' in params:
        payload['dev'] = {
            'identifiers': ["{}".format(uniqID)],
            'manufacturer': 'Raspberry Pi (Trading) Ltd.',
            'name': params['device_ident'],
            'model': '{}'.format(rpi_model),
            'sw_version': "{} {}".format(rpi_linux_release, rpi_linux_version)
        }
    else:
        payload['dev'] = {
            'identifiers': ["{}".format(uniqID)],
        }
    payload['o'] = {
        'name': project_name,
        'sw': script_version,
        'url': project_url,
    }
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)

# -----------------------------------------------------------------------------
#  MQTT Transmit Helper Routines
# -----------------------------------------------------------------------------
SCRIPT_TIMESTAMP = "timestamp"
K_RPI_MODEL = "rpi_model"
K_RPI_CONNECTIONS = "ifaces"
K_RPI_HOSTNAME = "host_name"
K_RPI_FQDN = "fqdn"
K_RPI_LINUX_RELEASE = "ux_release"
K_RPI_LINUX_VERSION = "ux_version"
K_RPI_LINUX_AVAIL_UPD = "ux_updates"
K_RPI_UPTIME = "up_time"
K_RPI_UPTIME_SECONDS = "up_time_secs"
K_RPI_DATE_LAST_UPDATE = "last_update"
K_RPI_FS_SPACE = 'fs_total_gb'  # "fs_space_gbytes"
K_RPI_FS_AVAIL = 'fs_free_prcnt'  # "fs_available_prcnt"
K_RPI_FS_USED = 'fs_used_prcnt'  # "fs_used_prcnt"
K_RPI_RAM_USED = 'mem_used_prcnt'  # "mem_used_prcnt"
K_RPI_SYSTEM_TEMP = "temperature_c"
K_RPI_GPU_TEMP = "temp_gpu_c"
K_RPI_CPU_TEMP = "temp_cpu_c"
K_RPI_SCRIPT = "reporter"
K_RPI_SCRIPT_VERSIONS = "reporter_releases"
K_RPI_NETWORK = "networking"
K_RPI_INTERFACE = "interface"
SCRIPT_REPORT_INTERVAL = "report_interval"
# new drives dictionary
K_RPI_DRIVES = "drives"
K_RPI_DRV_BLOCKS = "size_gb"
K_RPI_DRV_USED = "used_prcnt"
K_RPI_DRV_MOUNT = "mount_pt"
K_RPI_DRV_DEVICE = "device"
K_RPI_DRV_NFS = "device-nfs"
K_RPI_DVC_IP = "ip"
K_RPI_DVC_PATH = "dvc"
# new memory dictionary
K_RPI_MEMORY = "memory"
K_RPI_MEM_TOTAL = "size_mb"
K_RPI_MEM_FREE = "free_mb"
K_RPI_SWAP_TOTAL = "size_swap"
K_RPI_SWAP_FREE = "free_swap"
# Tuple (Hardware, Model Name, NbrCores, BogoMIPS, Serial)
K_RPI_CPU = "cpu"
K_RPI_CPU_HARDWARE = "hardware"
K_RPI_CPU_MODEL = "model"
K_RPI_CPU_CORES = "number_cores"
K_RPI_CPU_BOGOMIPS = "bogo_mips"
K_RPI_CPU_SERIAL = "serial"
#  add new CPU Load
K_RPI_CPU_LOAD1 = "load_1min_prcnt"
K_RPI_CPU_LOAD5 = "load_5min_prcnt"
K_RPI_CPU_LOAD15 = "load_15min_prcnt"
# list of throttle status
K_RPI_THROTTLE = "throttle"


def send_status(timestamp):
    rpiData = OrderedDict()
    rpiData[SCRIPT_TIMESTAMP] = timestamp.replace(microsecond=0).isoformat()
    rpiData[K_RPI_MODEL] = rpi_model
    rpiData[K_RPI_CONNECTIONS] = rpi_connections
    rpiData[K_RPI_HOSTNAME] = rpi_hostname
    rpiData[K_RPI_FQDN] = rpi_fqdn
    rpiData[K_RPI_LINUX_RELEASE] = rpi_linux_release
    rpiData[K_RPI_LINUX_VERSION] = rpi_linux_version
    rpiData[K_RPI_LINUX_AVAIL_UPD] = rpi_update_count
    rpiData[K_RPI_UPTIME] = rpi_uptime
    rpiData[K_RPI_UPTIME_SECONDS] = rpi_uptime_sec

    if rpi_last_update_date:
        rpiData[K_RPI_DATE_LAST_UPDATE] = rpi_last_update_date.replace(
            microsecond=0).isoformat()
    else:
        rpiData[K_RPI_DATE_LAST_UPDATE] = ''
    rpiData[K_RPI_FS_SPACE] = int(rpi_filesystem_space, 10)
    # TODO: consider eliminating K_RPI_FS_AVAIL/fs_free_prcnt as used is needed but free is not... (can be calculated)
    rpiData[K_RPI_FS_AVAIL] = 100 - int(rpi_filesystem_percent, 10)
    rpiData[K_RPI_FS_USED] = int(rpi_filesystem_percent, 10)

    rpiData[K_RPI_NETWORK] = getNetworkDictionary()

    rpiDrives = getDrivesDictionary()
    if len(rpiDrives) > 0:
        rpiData[K_RPI_DRIVES] = rpiDrives

    rpiRam = getMemoryDictionary()
    if len(rpiRam) > 0:
        rpiData[K_RPI_MEMORY] = rpiRam
        ramSizeMB = rpi_memory_tuple[0]
        if ramSizeMB > 0:
            # used is total - available
            ramUsedMB = rpi_memory_tuple[0] - rpi_memory_tuple[2]
            rpiData[K_RPI_RAM_USED] = int((ramUsedMB / ramSizeMB) * 100)  # "mem_used_prcnt"

    rpiCpu = getCPUDictionary()
    if len(rpiCpu) > 0:
        rpiData[K_RPI_CPU] = rpiCpu

    if len(rpi_throttle_status) > 0:
        rpiData[K_RPI_THROTTLE] = rpi_throttle_status

    rpiData[K_RPI_SYSTEM_TEMP] = forceSingleDigit(rpi_system_temp)
    rpiData[K_RPI_GPU_TEMP] = forceSingleDigit(rpi_gpu_temp)
    rpiData[K_RPI_CPU_TEMP] = forceSingleDigit(rpi_cpu_temp)

    rpiData[K_RPI_SCRIPT] = rpi_mqtt_script.replace('.py', '')
    # kept (empty) for card compatibility: no remote release lookup is done
    rpiData[K_RPI_SCRIPT_VERSIONS] = ''
    rpiData[SCRIPT_REPORT_INTERVAL] = interval_in_minutes

    rpiTopDict = OrderedDict()
    rpiTopDict[K_LD_PAYLOAD_NAME] = rpiData

    publishMonitorData(rpiTopDict, values_topic)


def forceSingleDigit(temperature):
    return round(float(temperature), 1)


def getDrivesDictionary():
    rpiDrives = OrderedDict()

    # tuple { total GB, used%, mountPoint, device }
    for driveTuple in rpi_filesystem:
        rpiSingleDrive = OrderedDict()
        rpiSingleDrive[K_RPI_DRV_BLOCKS] = int(driveTuple[0])
        rpiSingleDrive[K_RPI_DRV_USED] = int(driveTuple[1])
        device = driveTuple[3]
        if ':' in device:
            rpiDevice = OrderedDict()
            lineParts = device.split(':', 1)
            rpiDevice[K_RPI_DVC_IP] = lineParts[0]
            rpiDevice[K_RPI_DVC_PATH] = lineParts[1]
            rpiSingleDrive[K_RPI_DRV_NFS] = rpiDevice
        else:
            rpiSingleDrive[K_RPI_DRV_DEVICE] = device
        rpiSingleDrive[K_RPI_DRV_MOUNT] = driveTuple[2]
        driveKey = driveTuple[2].replace('/', '-').replace('-', '', 1)
        if len(driveKey) == 0:
            driveKey = "root"
        rpiDrives[driveKey] = rpiSingleDrive

    return rpiDrives


def getNetworkDictionary():
    # TYPICAL:
    # rpi_interfaces=[[
    #   ('eth0', 'mac', 'b8:27:eb:1a:f3:bc'),
    #   ('wlan0', 'IP', '192.168.100.189'),
    #   ('wlan0', 'mac', 'b8:27:eb:4f:a6:e9')
    # ]]
    networkData = OrderedDict()
    for interfaceName, subKey, subValue in rpi_interfaces:
        networkData.setdefault(interfaceName, OrderedDict())[subKey] = subValue
    print_line('networkData:{}"'.format(networkData), debug=True)
    return networkData


def getMemoryDictionary():
    # TYPICAL:
    #   Tuple (Total, Free, Avail., Swap Total, Swap Free)
    memoryData = OrderedDict()
    if rpi_memory_tuple != '':
        memoryData[K_RPI_MEM_TOTAL] = round(rpi_memory_tuple[0])
        memoryData[K_RPI_MEM_FREE] = round(rpi_memory_tuple[2])
        memoryData[K_RPI_SWAP_TOTAL] = round(rpi_memory_tuple[3])
        memoryData[K_RPI_SWAP_FREE] = round(rpi_memory_tuple[4])
    return memoryData


def getCPUDictionary():
    # TYPICAL:
    #   Tuple (Hardware, Model Name, NbrCores, BogoMIPS, Serial, Load1, Load5, Load15)
    cpuDict = OrderedDict()
    if rpi_cpu_tuple != '':
        cpuDict[K_RPI_CPU_HARDWARE] = rpi_cpu_tuple[0]
        cpuDict[K_RPI_CPU_MODEL] = rpi_cpu_tuple[1]
        cpuDict[K_RPI_CPU_CORES] = rpi_cpu_tuple[2]
        cpuDict[K_RPI_CPU_BOGOMIPS] = '{:.2f}'.format(rpi_cpu_tuple[3])
        cpuDict[K_RPI_CPU_SERIAL] = rpi_cpu_tuple[4]
        cpuDict[K_RPI_CPU_LOAD1] = rpi_cpu_tuple[5]
        cpuDict[K_RPI_CPU_LOAD5] = rpi_cpu_tuple[6]
        cpuDict[K_RPI_CPU_LOAD15] = rpi_cpu_tuple[7]
    print_line('cpuDict:{}"'.format(cpuDict), debug=True)
    return cpuDict


def publishMonitorData(latestData, topic):
    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(
        topic, json.dumps(latestData)))
    mqtt_client.publish('{}'.format(topic), json.dumps(
        latestData), 1, retain=False)


def update_values():
    # run get latest values for all
    getDeviceCpuInfo()
    getUptime()
    getFileSystemDrives()
    getSystemTemperature()
    getSystemThermalStatus()
    getLastUpdateDate()
    getDeviceMemory()
    getNetworkIFs()

# -----------------------------------------------------------------------------


reported_first_time = False


def report_status():
    global reported_first_time
    current_timestamp = datetime.now().astimezone()
    print_line("<< Time to report! (%s)" %
               current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'), verbose=True)
    update_values()

    if opt_stall == False or reported_first_time == False:
        # ok, report our new detection to MQTT
        send_status(current_timestamp)
        reported_first_time = True
    else:
        print_line("<< Time to report! (%s) but SKIPPED (TEST: stall)" %
                   current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'), verbose=True)


def handle_sigterm(signum, frame):
    # systemctl stop: unwind through the finally below so we report offline
    raise SystemExit(0)


signal.signal(signal.SIGTERM, handle_sigterm)

# check every 4 hours (6 times a day) = 4 hours * 60 minutes * 60 seconds
kUpdateCheckIntervalInSeconds = (check_interval_in_hours * 60 * 60)

next_report_time = 0.0
next_update_check_time = time() + kUpdateCheckIntervalInSeconds  # first count done at startup

# now just hang in forever loop until script is stopped externally
try:
    while True:
        timeNow = time()
        if timeNow >= next_report_time:
            next_report_time = timeNow + interval_in_minutes * 60
            try:
                report_status()
            except Exception as exc:
                # never let one failed sample stop the reporting
                print_line('! Reporting failed: {}'.format(exc), error=True)
                print_line(traceback.format_exc(), debug=True)

        if apt_available and timeNow >= next_update_check_time:
            next_update_check_time = timeNow + kUpdateCheckIntervalInSeconds
            getNumberOfAvailableUpdates()  # and count them!

        next_wakeup_time = min(next_report_time, next_update_check_time) if apt_available else next_report_time
        sleep(max(1.0, next_wakeup_time - time()))

except KeyboardInterrupt:
    pass

finally:
    publishShuttingDownStatus()
    mqtt_client.disconnect()
    mqtt_client.loop_stop()
    print_line('* MQTT Disconnect()', verbose=True)
