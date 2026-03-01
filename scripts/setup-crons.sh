#!/usr/bin/env bash
# setup-crons.sh — Register triage cron jobs via OpenClaw
#
# Triage crons run in isolated sessions to analyze content collected by daemons.
# They fill in summaries, highlights, and topic tags in Feishu Bitable.
#
# Usage:
#   bash scripts/setup-crons.sh
#
# Prerequisites:
#   - OpenClaw gateway must be running
#   - openclaw CLI must be installed and accessible

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if openclaw CLI is available
if ! command -v openclaw &>/dev/null && ! command -v moltbot &>/dev/null; then
    error "Neither 'openclaw' nor 'moltbot' CLI found in PATH"
    echo "Install OpenClaw first, then run this script."
    exit 1
fi

# Use whichever CLI is available
CLI="openclaw"
if ! command -v openclaw &>/dev/null; then
    CLI="moltbot"
fi

echo ""
echo "Registering triage cron jobs..."
echo ""

# ─────────────────────────────────────────────
# Podcast Triage — every 3 hours
# ─────────────────────────────────────────────
info "Registering podcast-triage (every 3h)..."
$CLI cron add \
    --name "podcast-triage" \
    --every "3h" \
    --session isolated \
    --message '请运行播客内容分析：python3 ~/.openclaw/skills/triage/scripts/triage-helper.py podcast --batch-size 5。分析完成后安静结束，不要发消息给用户。' \
    2>/dev/null && info "podcast-triage registered" || warn "podcast-triage may already exist (use '$CLI cron list' to check)"

# ─────────────────────────────────────────────
# YouTube Triage — every 3 hours
# ─────────────────────────────────────────────
info "Registering youtube-triage (every 3h)..."
$CLI cron add \
    --name "youtube-triage" \
    --every "3h" \
    --session isolated \
    --message '请运行YouTube内容分析：python3 ~/.openclaw/skills/triage/scripts/triage-helper.py youtube --batch-size 5。分析完成后安静结束，不要发消息给用户。' \
    2>/dev/null && info "youtube-triage registered" || warn "youtube-triage may already exist (use '$CLI cron list' to check)"

# ─────────────────────────────────────────────
# Twitter Triage — every 2 hours
# ─────────────────────────────────────────────
info "Registering twitter-triage (every 2h)..."
$CLI cron add \
    --name "twitter-triage" \
    --every "2h" \
    --session isolated \
    --message '请运行Twitter内容分析：python3 ~/.openclaw/skills/triage/scripts/triage-helper.py twitter --batch-size 10。分析完成后安静结束，不要发消息给用户。' \
    2>/dev/null && info "twitter-triage registered" || warn "twitter-triage may already exist (use '$CLI cron list' to check)"

echo ""
info "Cron registration complete!"
echo ""
echo "Verify with: $CLI cron list"
echo ""
echo "Notes:"
echo "  - Triage crons use isolated sessions (no conversation history accumulation)"
echo "  - They run silently — no messages sent to user"
echo "  - Results are written to Feishu Bitable (summaries, highlights, topic tags)"
echo "  - To remove a cron: $CLI cron remove <name>"
echo "  - To test manually: $CLI agent --session-id main --message 'run podcast triage'"
