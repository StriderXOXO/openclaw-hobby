#!/usr/bin/env bash
# install.sh — Install OpenClaw Hobby systemd services
#
# Usage:
#   sudo bash install.sh          # Install all services
#   sudo bash install.sh podcast  # Install only podcast service
#
# This script copies systemd unit files to the appropriate locations,
# reloads the systemd daemon, and enables the services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEM_DIR="/etc/systemd/system"
USER_DIR="$HOME/.config/systemd/user"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# System services (run as root)
SYSTEM_SERVICES=(
    "hobby-podcast.service"
    "hobby-youtube.service"
    "hobby-twitter.service"
    "hobby-watchdog.service"
)

# User services (run as current user)
USER_SERVICES=(
    "openclaw-gateway.service"
)

install_system_service() {
    local service="$1"
    local src="$SCRIPT_DIR/$service"

    if [[ ! -f "$src" ]]; then
        error "Service file not found: $src"
        return 1
    fi

    info "Installing $service to $SYSTEM_DIR/"
    cp "$src" "$SYSTEM_DIR/$service"
    systemctl daemon-reload
    systemctl enable "$service"
    info "$service installed and enabled"
}

install_user_service() {
    local service="$1"
    local src="$SCRIPT_DIR/$service"

    if [[ ! -f "$src" ]]; then
        error "Service file not found: $src"
        return 1
    fi

    mkdir -p "$USER_DIR"
    info "Installing $service to $USER_DIR/"
    cp "$src" "$USER_DIR/$service"
    systemctl --user daemon-reload
    systemctl --user enable "$service"
    info "$service installed and enabled (user service)"
}

# Determine what to install
TARGET="${1:-all}"

if [[ "$TARGET" == "all" ]]; then
    # Install all system services
    for svc in "${SYSTEM_SERVICES[@]}"; do
        install_system_service "$svc"
    done

    # Install user services
    for svc in "${USER_SERVICES[@]}"; do
        install_user_service "$svc"
    done

    # Enable lingering so user services persist after logout
    CURRENT_USER="$(whoami)"
    if ! loginctl show-user "$CURRENT_USER" 2>/dev/null | grep -q "Linger=yes"; then
        info "Enabling linger for $CURRENT_USER (required for gateway to persist after SSH logout)"
        loginctl enable-linger "$CURRENT_USER"
    fi
else
    # Install specific service
    case "$TARGET" in
        gateway)
            install_user_service "openclaw-gateway.service"
            ;;
        podcast|youtube|twitter|watchdog)
            install_system_service "hobby-${TARGET}.service"
            ;;
        *)
            error "Unknown service: $TARGET"
            echo "Available: all, gateway, podcast, youtube, twitter, watchdog"
            exit 1
            ;;
    esac
fi

echo ""
info "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Edit service files if paths need customization"
echo "  2. Start services:"
echo "     systemctl --user start openclaw-gateway"
echo "     systemctl start hobby-podcast"
echo "     systemctl start hobby-youtube"
echo "     systemctl start hobby-twitter"
echo "     systemctl start hobby-watchdog"
echo ""
echo "  3. Check status:"
echo "     systemctl --user status openclaw-gateway"
echo "     systemctl status hobby-podcast hobby-youtube hobby-twitter hobby-watchdog"
