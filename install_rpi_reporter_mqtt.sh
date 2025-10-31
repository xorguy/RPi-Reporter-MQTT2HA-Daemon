#!/bin/bash

################################################################################
# Script: install_rpi_reporter_mqtt.sh
# Description: Install and configure RPi-Reporter-MQTT2HA-Daemon
# Author: Auto-generated
# Date: 2025-10-31
################################################################################

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

# Function to print status messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to check if running as root
check_root() {
    print_status "Checking if script is running with sudo privileges..."
    if [ "$EUID" -ne 0 ]; then
        print_error "This script must be run with sudo privileges"
        exit 1
    fi
    print_success "Running with sudo privileges"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check internet connectivity
check_internet() {
    print_status "Checking internet connectivity..."
    if ping -c 1 google.com >/dev/null 2>&1; then
        print_success "Internet connection is available"
        return 0
    else
        print_error "No internet connection detected"
        return 1
    fi
}

# Function to install system packages
install_system_packages() {
    print_status "Installing required system packages..."
    
    local packages=(
        "git"
        "python3"
        "python3-pip"
        "python3-tzlocal"
        "python3-sdnotify"
        "python3-colorama"
        "python3-unidecode"
        "python3-apt"
        "python3-paho-mqtt"
        "python3-requests"
    )
    
    # Update package list
    print_status "Updating package list..."
    if apt-get update; then
        print_success "Package list updated"
    else
        print_error "Failed to update package list"
        return 1
    fi
    
    # Install packages
    local failed_packages=()
    for package in "${packages[@]}"; do
        print_status "Installing $package..."
        if apt-get install -y "$package" 2>&1 | tee /tmp/apt_install.log | grep -q "E:"; then
            print_error "Failed to install $package"
            failed_packages+=("$package")
        else
            print_success "Installed $package"
        fi
    done
    
    if [ ${#failed_packages[@]} -eq 0 ]; then
        print_success "All system packages installed successfully"
        return 0
    else
        print_error "Failed to install packages: ${failed_packages[*]}"
        return 1
    fi
}

# Function to clone repository
clone_repository() {
    print_status "Checking if repository already exists..."
    
    if [ -d "$INSTALL_DIR" ]; then
        print_warning "Directory $INSTALL_DIR already exists"
        read -p "Do you want to remove it and clone fresh? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_status "Removing existing directory..."
            if rm -rf "$INSTALL_DIR"; then
                print_success "Removed existing directory"
            else
                print_error "Failed to remove existing directory"
                return 1
            fi
        else
            print_warning "Skipping repository clone"
            return 0
        fi
    fi
    
    print_status "Cloning repository from $REPO_URL..."
    if git clone "$REPO_URL" "$INSTALL_DIR"; then
        print_success "Repository cloned successfully to $INSTALL_DIR"
        return 0
    else
        print_error "Failed to clone repository"
        return 1
    fi
}

# Function to install Python requirements
install_python_requirements() {
    print_status "Installing Python requirements..."
    
    if [ ! -f "$INSTALL_DIR/requirements.txt" ]; then
        print_warning "requirements.txt not found, skipping pip install"
        return 0
    fi
    
    print_status "Installing pip requirements from requirements.txt..."
    cd "$INSTALL_DIR" || return 1
    
    if pip3 install -r requirements.txt --break-system-packages 2>&1 | tee /tmp/pip_install.log; then
        print_success "Python requirements installed successfully"
        return 0
    else
        print_error "Failed to install Python requirements"
        return 1
    fi
}

# Function to add daemon user to video group
add_user_to_video_group() {
    print_status "Checking if daemon user exists..."
    
    if ! id -u daemon >/dev/null 2>&1; then
        print_error "User 'daemon' does not exist"
        return 1
    fi
    
    print_status "Checking if daemon user is in video group..."
    if groups daemon | grep -q "\bvideo\b"; then
        print_warning "User 'daemon' is already in video group"
        return 0
    fi
    
    print_status "Adding daemon user to video group..."
    if usermod daemon -a -G video; then
        print_success "User 'daemon' added to video group"
        return 0
    else
        print_error "Failed to add daemon user to video group"
        return 1
    fi
}

# Function to setup systemd service
setup_systemd_service() {
    print_status "Setting up systemd service..."
    
    local service_file="$INSTALL_DIR/$SERVICE_NAME"
    local systemd_service="/etc/systemd/system/$SERVICE_NAME"
    
    # Check if service file exists in repository
    if [ ! -f "$service_file" ]; then
        print_error "Service file not found: $service_file"
        return 1
    fi
    
    # Check if symlink already exists
    if [ -L "$systemd_service" ]; then
        print_warning "Service symlink already exists"
        print_status "Removing existing symlink..."
        rm -f "$systemd_service"
    elif [ -f "$systemd_service" ]; then
        print_warning "Service file (not symlink) already exists"
        print_status "Backing up existing service file..."
        mv "$systemd_service" "${systemd_service}.backup.$(date +%Y%m%d_%H%M%S)"
    fi
    
    # Create symlink
    print_status "Creating service symlink..."
    if ln -s "$service_file" "$systemd_service"; then
        print_success "Service symlink created"
    else
        print_error "Failed to create service symlink"
        return 1
    fi
    
    # Reload systemd daemon
    print_status "Reloading systemd daemon..."
    if systemctl daemon-reload; then
        print_success "Systemd daemon reloaded"
    else
        print_error "Failed to reload systemd daemon"
        return 1
    fi
    
    return 0
}

# Function to enable service
enable_service() {
    print_status "Enabling $SERVICE_NAME to start on boot..."
    
    if systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
        print_warning "Service is already enabled"
        return 0
    fi
    
    if systemctl enable "$SERVICE_NAME"; then
        print_success "Service enabled successfully"
        return 0
    else
        print_error "Failed to enable service"
        return 1
    fi
}

# Function to start service
start_service() {
    print_status "Starting $SERVICE_NAME..."
    
    if systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1; then
        print_warning "Service is already running"
        print_status "Restarting service..."
        if systemctl restart "$SERVICE_NAME"; then
            print_success "Service restarted successfully"
            return 0
        else
            print_error "Failed to restart service"
            return 1
        fi
    fi
    
    if systemctl start "$SERVICE_NAME"; then
        print_success "Service started successfully"
        return 0
    else
        print_error "Failed to start service"
        return 1
    fi
}

# Function to check service status
check_service_status() {
    print_status "Checking service status..."
    echo "----------------------------------------"
    systemctl status "$SERVICE_NAME" --no-pager
    echo "----------------------------------------"
    
    if systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1; then
        print_success "Service is running"
        return 0
    else
        print_error "Service is not running"
        return 1
    fi
}

# Main installation function
main() {
    echo "========================================================================"
    echo "  RPi-Reporter-MQTT2HA-Daemon Installation Script"
    echo "========================================================================"
    echo ""
    
    local steps_failed=0
    
    # Step 1: Check root privileges
    check_root || ((steps_failed++))
    echo ""
    
    # Step 2: Check internet connectivity
    check_internet || ((steps_failed++))
    echo ""
    
    # Step 3: Install system packages
    install_system_packages || ((steps_failed++))
    echo ""
    
    # Step 4: Clone repository
    clone_repository || ((steps_failed++))
    echo ""
    
    # Step 5: Install Python requirements
    install_python_requirements || ((steps_failed++))
    echo ""
    
    # Step 6: Add daemon user to video group
    add_user_to_video_group || ((steps_failed++))
    echo ""
    
    # Step 7: Setup systemd service
    setup_systemd_service || ((steps_failed++))
    echo ""
    
    # Step 8: Enable service
    enable_service || ((steps_failed++))
    echo ""
    
    # Step 9: Start service
    start_service || ((steps_failed++))
    echo ""
    
    # Step 10: Check service status
    check_service_status || ((steps_failed++))
    echo ""
    
    # Final summary
    echo "========================================================================"
    if [ $steps_failed -eq 0 ]; then
        print_success "Installation completed successfully!"
        echo ""
        echo "Service logs can be viewed with:"
        echo "  sudo journalctl -u $SERVICE_NAME -f"
    else
        print_error "Installation completed with $steps_failed error(s)"
        echo ""
        echo "Please review the errors above and try again"
    fi
    echo "========================================================================"
}

# Run main function
main

exit 0
