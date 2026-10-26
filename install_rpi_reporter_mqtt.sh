#!/bin/bash

################################################################################
# Script: install_rpi_reporter_mqtt.sh
# Description: Install and configure RPi-Reporter-MQTT2HA-Daemon
#              (Raspberry Pi OS / Debian Trixie)
################################################################################

set -Eeuo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script configuration
REPO_URL="https://github.com/xorguy/RPi-Reporter-MQTT2HA-Daemon.git"
INSTALL_DIR="/opt/RPi-Reporter-MQTT2HA-Daemon"
SERVICE_NAME="isp-rpi-reporter.service"
SERVICE_USER="daemon"
SERVICE_GROUP="daemon"

# Function to print status messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Abort the installation
die() {
    print_error "$1"
    print_error "Installation aborted"
    exit 1
}

trap 'print_error "Unexpected failure at line $LINENO: $BASH_COMMAND"' ERR

# Ask a yes/no question, default No
confirm() {
    local reply
    read -r -p "$1 (y/N): " reply
    [[ $reply =~ ^[Yy]$ ]]
}

# Function to check if running as root
check_root() {
    print_status "Checking if script is running with sudo privileges..."
    if [ "$EUID" -ne 0 ]; then
        die "This script must be run with sudo privileges"
    fi
    print_success "Running with sudo privileges"
}

# Function to check the OS release
check_os() {
    local codename=""
    if [ -r /etc/os-release ]; then
        # shellcheck source=/dev/null
        codename=$(. /etc/os-release && echo "${VERSION_CODENAME:-}")
    fi
    if [ "$codename" != "trixie" ]; then
        print_warning "This version targets Debian/Raspberry Pi OS 'trixie', found '${codename:-unknown}'"
        confirm "Continue anyway?" || die "Unsupported OS release"
    else
        print_success "OS release: $codename"
    fi
}

# Function to install system packages (all dependencies come from apt)
install_system_packages() {
    print_status "Installing required system packages..."

    local packages=(
        "git"
        "python3"
        "python3-sdnotify"
        "python3-colorama"
        "python3-unidecode"
        "python3-apt"
        "python3-paho-mqtt"
    )

    print_status "Updating package list..."
    apt-get update || die "Failed to update package list"

    print_status "Installing: ${packages[*]}"
    apt-get install -y "${packages[@]}" || die "Failed to install system packages"
    print_success "All system packages installed successfully"
}

# Function to clone repository
clone_repository() {
    local config_backup=""

    if [ -d "$INSTALL_DIR" ]; then
        print_warning "Directory $INSTALL_DIR already exists"
        if ! confirm "Do you want to remove it and clone fresh? (config.ini is kept)"; then
            print_warning "Skipping repository clone"
            return 0
        fi
        if [ -f "$INSTALL_DIR/config.ini" ]; then
            config_backup=$(mktemp)
            cp -p "$INSTALL_DIR/config.ini" "$config_backup"
        fi
        rm -rf "$INSTALL_DIR"
        print_success "Removed existing directory"
    fi

    print_status "Cloning repository from $REPO_URL..."
    if ! git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"; then
        [ -n "$config_backup" ] && print_warning "Previous config.ini saved at $config_backup"
        die "Failed to clone repository"
    fi
    print_success "Repository cloned successfully to $INSTALL_DIR"

    if [ -n "$config_backup" ]; then
        mv "$config_backup" "$INSTALL_DIR/config.ini"
        print_success "Restored previous config.ini"
    fi
}

# Function to add daemon user to video group (needed by vcgencmd)
add_user_to_video_group() {
    id -u "$SERVICE_USER" >/dev/null 2>&1 || die "User '$SERVICE_USER' does not exist"

    if id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx video; then
        print_success "User '$SERVICE_USER' is already in video group"
        return 0
    fi

    usermod -a -G video "$SERVICE_USER" || die "Failed to add $SERVICE_USER user to video group"
    print_success "User '$SERVICE_USER' added to video group"
}

# Function to create config.ini file
create_config_file() {
    local config_file="$INSTALL_DIR/config.ini"

    if [ -f "$config_file" ]; then
        print_warning "config.ini already exists"
        if ! confirm "Do you want to overwrite it?"; then
            print_warning "Keeping existing config.ini"
            secure_config_file "$config_file"
            return 0
        fi
        mv "$config_file" "${config_file}.backup.$(date +%Y%m%d_%H%M%S)"
        print_status "Backed up existing config.ini"
    fi

    local current_hostname
    current_hostname=$(hostname)
    [ -n "$current_hostname" ] || die "Failed to get hostname"
    print_status "Current hostname: $current_hostname"

    echo ""
    print_status "Please provide MQTT broker configuration:"
    local mqtt_hostname mqtt_port mqtt_username mqtt_password discovery_prefix base_topic
    local use_tls="false" tls_ca_cert="" default_port="1883"

    read -r -p "MQTT Hostname [mqtt.example.com]: " mqtt_hostname
    mqtt_hostname=${mqtt_hostname:-mqtt.example.com}

    if confirm "Use TLS to connect to the broker (recommended)?"; then
        use_tls="true"
        default_port="8883"
        read -r -p "CA certificate file (empty = system CA store): " tls_ca_cert
        if [ -n "$tls_ca_cert" ] && [ ! -r "$tls_ca_cert" ]; then
            print_warning "CA certificate file '$tls_ca_cert' not readable"
        fi
    fi

    read -r -p "MQTT Port [$default_port]: " mqtt_port
    mqtt_port=${mqtt_port:-$default_port}
    [[ $mqtt_port =~ ^[0-9]+$ ]] || die "Invalid MQTT port '$mqtt_port'"

    read -r -p "MQTT Username [mqttuser]: " mqtt_username
    mqtt_username=${mqtt_username:-mqttuser}

    read -r -s -p "MQTT Password: " mqtt_password
    echo
    [ -n "$mqtt_password" ] || die "MQTT password cannot be empty"
    if [[ $mqtt_password =~ ^[[:space:]]|[[:space:]]$ ]]; then
        print_warning "Leading/trailing spaces in the password are ignored by the daemon"
    fi

    read -r -p "Discovery Prefix [homeassistant]: " discovery_prefix
    discovery_prefix=${discovery_prefix:-homeassistant}

    read -r -p "Base Topic [home/nodes]: " base_topic
    base_topic=${base_topic:-home/nodes}

    print_status "Writing configuration to $config_file..."
    (
        umask 027
        cat > "$config_file" << EOF
[Daemon]
interval_in_minutes = 2

[Commands]
# NOTE: sudo based commands need the systemd unit hardening relaxed (see RMTECTRL.md)
#shutdown = /usr/bin/sudo /sbin/shutdown -h now 'shutdown rqst via MQTT'
#reboot = /usr/bin/sudo /sbin/shutdown -r now 'reboot rqst via MQTT'
#restart_service = /usr/bin/sudo systemctl restart isp-rpi-reporter.service

[MQTT]
hostname = ${mqtt_hostname}
port = ${mqtt_port}
discovery_prefix = ${discovery_prefix}
base_topic = ${base_topic}
sensor_name = rpi-${current_hostname}
username = ${mqtt_username}
password = ${mqtt_password}
tls = ${use_tls}
EOF
        if [ -n "$tls_ca_cert" ]; then
            echo "tls_ca_cert = ${tls_ca_cert}" >> "$config_file"
        fi
    ) || die "Failed to create config.ini"

    secure_config_file "$config_file"
    print_success "config.ini created successfully"
    print_status "Sensor name set to: rpi-${current_hostname}"
}

# config.ini holds credentials: readable by root and the service group only
secure_config_file() {
    chown root:"$SERVICE_GROUP" "$1" || die "Failed to set owner of $1"
    chmod 640 "$1" || die "Failed to set permissions on $1"
    print_success "Set secure permissions on config.ini (root:$SERVICE_GROUP 0640)"
}

# Function to setup systemd service
setup_systemd_service() {
    local service_file="$INSTALL_DIR/$SERVICE_NAME"
    local systemd_service="/etc/systemd/system/$SERVICE_NAME"

    [ -f "$service_file" ] || die "Service file not found: $service_file"

    if [ -L "$systemd_service" ]; then
        rm -f "$systemd_service"
    elif [ -f "$systemd_service" ] && ! cmp -s "$service_file" "$systemd_service"; then
        mv "$systemd_service" "${systemd_service}.backup.$(date +%Y%m%d_%H%M%S)"
        print_warning "Backed up existing (different) service file"
    fi

    install -m 0644 -o root -g root "$service_file" "$systemd_service" || die "Failed to install service file"
    systemctl daemon-reload || die "Failed to reload systemd daemon"
    print_success "Service file installed"
}

# Function to enable and (re)start service
start_service() {
    systemctl enable "$SERVICE_NAME" || die "Failed to enable service"
    systemctl restart "$SERVICE_NAME" || die "Failed to start service"
    print_success "Service enabled and started"
}

# Function to check service status
check_service_status() {
    sleep 3
    echo "----------------------------------------"
    systemctl status "$SERVICE_NAME" --no-pager || true
    echo "----------------------------------------"

    systemctl is-active --quiet "$SERVICE_NAME" || die "Service is not running, see: journalctl -u $SERVICE_NAME"
    print_success "Service is running"
}

# Main installation function
main() {
    echo "========================================================================"
    echo "  RPi-Reporter-MQTT2HA-Daemon Installation Script"
    echo "========================================================================"
    echo ""

    check_root
    check_os
    install_system_packages
    clone_repository
    add_user_to_video_group
    create_config_file
    setup_systemd_service
    start_service
    check_service_status

    echo "========================================================================"
    print_success "Installation completed successfully!"
    echo ""
    echo "Service logs can be viewed with:"
    echo "  sudo journalctl -u $SERVICE_NAME -f"
    echo "========================================================================"
}

main "$@"
