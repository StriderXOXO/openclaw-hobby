#!/usr/bin/env bash
# setup.sh — Initialize OpenClaw Hobby workspace
#
# This script creates the workspace directory structure, copies example configs,
# and installs Python dependencies.
#
# Usage:
#   bash scripts/setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE="$OPENCLAW_DIR/workspace"
SKILLS="$OPENCLAW_DIR/skills"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

echo ""
echo "========================================="
echo "  OpenClaw Hobby System Setup"
echo "========================================="
echo ""

# ─────────────────────────────────────────────
# Step 1: Create directory structure
# ─────────────────────────────────────────────
step "Creating workspace directory structure..."

DIRS=(
    "$WORKSPACE/hobby/logs"
    "$WORKSPACE/twitter-hobby"
    "$WORKSPACE/youtube-hobby/subtitles"
    "$WORKSPACE/podcast-hobby/transcripts"
    "$SKILLS/twitter-hobby/scripts"
    "$SKILLS/youtube-hobby/scripts"
    "$SKILLS/podcast-hobby/scripts"
    "$SKILLS/watchdog/scripts"
    "$SKILLS/triage/scripts"
)

for dir in "${DIRS[@]}"; do
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        info "Created $dir"
    else
        info "Already exists: $dir"
    fi
done

# ─────────────────────────────────────────────
# Step 2: Copy example configs (don't overwrite)
# ─────────────────────────────────────────────
step "Copying example configs to workspace..."

copy_if_not_exists() {
    local src="$1"
    local dst="$2"
    if [[ ! -f "$dst" ]]; then
        cp "$src" "$dst"
        info "Copied $(basename "$src") -> $dst"
    else
        warn "Skipped (already exists): $dst"
    fi
}

# Mind state and user signals
copy_if_not_exists "$PROJECT_DIR/config/mind-state.example.json" "$WORKSPACE/hobby/mind-state.json"
copy_if_not_exists "$PROJECT_DIR/config/user-signals.example.json" "$WORKSPACE/hobby/user-signals.json"

# Hobby configs
copy_if_not_exists "$PROJECT_DIR/config/podcast.example.json" "$WORKSPACE/podcast-hobby/config.json"
copy_if_not_exists "$PROJECT_DIR/config/youtube.example.json" "$WORKSPACE/youtube-hobby/config.json"
copy_if_not_exists "$PROJECT_DIR/config/twitter.example.json" "$WORKSPACE/twitter-hobby/config.json"

# Initialize empty pending-shares files
for hobby in twitter-hobby youtube-hobby podcast-hobby; do
    pending="$WORKSPACE/$hobby/pending-shares.json"
    if [[ ! -f "$pending" ]]; then
        echo "[]" > "$pending"
        info "Created empty $pending"
    fi
done

# ─────────────────────────────────────────────
# Step 3: Copy daemon scripts
# ─────────────────────────────────────────────
step "Copying daemon scripts to skills directory..."

for hobby in twitter-hobby youtube-hobby podcast-hobby; do
    src_dir="$PROJECT_DIR/daemons/$hobby"
    dst_dir="$SKILLS/$hobby/scripts"
    if [[ -d "$src_dir" ]]; then
        cp "$src_dir"/*.py "$dst_dir/" 2>/dev/null || true
        info "Copied $hobby daemon scripts"
    else
        warn "No daemon scripts found for $hobby"
    fi
done

# Watchdog
if [[ -d "$PROJECT_DIR/watchdog" ]]; then
    cp "$PROJECT_DIR/watchdog"/*.py "$SKILLS/watchdog/scripts/" 2>/dev/null || true
    info "Copied watchdog scripts"
fi

# Triage
if [[ -d "$PROJECT_DIR/triage" ]]; then
    cp "$PROJECT_DIR/triage"/*.py "$SKILLS/triage/scripts/" 2>/dev/null || true
    info "Copied triage scripts"
fi

# ─────────────────────────────────────────────
# Step 4: Generate agent instruction files
# ─────────────────────────────────────────────
step "Generating agent instruction files (replacing placeholders)..."

if [[ -f "$PROJECT_DIR/.env" ]]; then
    bash "$PROJECT_DIR/scripts/generate-agent-files.sh" "$PROJECT_DIR/.env"
else
    warn "No .env file found — copying agent files with raw placeholders."
    warn "Run 'hobee setup' or create .env first, then re-run this script."
    CLAWD_DIR="$HOME/clawd"
    mkdir -p "$CLAWD_DIR"
    for md in HEARTBEAT.md SOUL.md TOOLS.md; do
        copy_if_not_exists "$PROJECT_DIR/agent/$md" "$CLAWD_DIR/$md"
    done
fi

# ─────────────────────────────────────────────
# Step 5: Install Python dependencies
# ─────────────────────────────────────────────
step "Installing Python package..."

# Install the hobee package in editable mode (includes requests + feedparser)
if pip3 install --quiet -e "$PROJECT_DIR" 2>/dev/null; then
    info "hobee package installed (hobee CLI now available)"
else
    warn "pip install -e failed — trying manual dependency install"
    pip3 install --quiet requests feedparser 2>/dev/null || warn "Some dependencies failed to install"
fi

# Optional: YouTube dependencies
pip3 install --quiet google-auth-oauthlib google-api-python-client 2>/dev/null && info "YouTube dependencies installed" || warn "YouTube dependencies not installed (optional)"

# ─────────────────────────────────────────────
# Step 6: OpenClaw gateway config
# ─────────────────────────────────────────────
step "Checking OpenClaw gateway config..."

MOLTBOT_CONFIG="$HOME/.moltbot/moltbot.json"
if [[ ! -f "$MOLTBOT_CONFIG" ]]; then
    mkdir -p "$HOME/.moltbot"
    cp "$PROJECT_DIR/config/moltbot.example.json" "$MOLTBOT_CONFIG"
    info "Created $MOLTBOT_CONFIG from template"
    warn "You MUST edit $MOLTBOT_CONFIG with your real credentials before starting the gateway"
else
    info "Gateway config already exists: $MOLTBOT_CONFIG"
fi

# ─────────────────────────────────────────────
# Done!
# ─────────────────────────────────────────────
echo ""
echo "========================================="
echo "  Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo ""
echo "  快速体验:"
echo "    hobee demo                         # 零配置，30 秒看到采集结果"
echo ""
echo "  持续采集:"
echo "    hobee podcast daemon               # 或用 systemd 管理："
echo "    sudo bash $PROJECT_DIR/systemd/install.sh"
echo "    systemctl start hobby-podcast"
echo ""
echo "  LLM 分析 (可选):"
echo "    # 在 .env 中设置 LLM_API_KEY"
echo "    hobee triage podcast"
echo ""
echo "  完整部署:"
echo "    bash $PROJECT_DIR/scripts/setup-crons.sh   # 注册 triage crons"
echo "    systemctl --user start openclaw-gateway     # 启动 agent gateway"
echo "    systemctl start hobby-watchdog              # 启动健康监控"
echo ""
