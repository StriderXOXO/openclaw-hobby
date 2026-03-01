#!/usr/bin/env bash
# generate-agent-files.sh — 从 .env 自动填充 agent 指令文件中的占位符
#
# 读取 .env 中的配置值，替换 agent/HEARTBEAT.md 和 agent/TOOLS.md 中的
# {{PLACEHOLDER}}，输出到 ~/clawd/。
#
# 未配置的可选服务保留注释标注 "未配置"。
#
# Usage:
#   bash scripts/generate-agent-files.sh [.env路径]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${1:-$PROJECT_DIR/.env}"
CLAWD_DIR="$HOME/clawd"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${RED}[!!]${NC} $1"; }

# Load .env
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport
    source "$ENV_FILE"
    set +o allexport
    info "Loaded $ENV_FILE"
else
    warn "No .env file found at $ENV_FILE — using defaults (unconfigured placeholders)"
fi

mkdir -p "$CLAWD_DIR"

# Replace placeholders in agent files
for md in HEARTBEAT.md TOOLS.md SOUL.md; do
    src="$PROJECT_DIR/agent/$md"
    dst="$CLAWD_DIR/$md"
    if [[ ! -f "$src" ]]; then
        continue
    fi

    sed \
        -e "s|{{WHISPER_ENDPOINT}}|${WHISPER_ENDPOINT:-（未配置 — 跳过音频转录功能）}|g" \
        -e "s|{{WHISPER_TOKEN}}|${WHISPER_TOKEN:-}|g" \
        -e "s|{{FEISHU_APP_TOKEN}}|${FEISHU_APP_TOKEN:-（未配置 — 飞书功能不可用）}|g" \
        -e "s|{{PODCAST_TABLE_ID}}|${PODCAST_TABLE_ID:-（未配置）}|g" \
        -e "s|{{YOUTUBE_TABLE_ID}}|${YOUTUBE_TABLE_ID:-（未配置）}|g" \
        -e "s|{{TWITTER_TABLE_ID}}|${TWITTER_TABLE_ID:-（未配置）}|g" \
        -e "s|{{CHAT_ID}}|${FEISHU_CHAT_ID:-（未配置 — 飞书推送不可用）}|g" \
        "$src" > "$dst"

    info "Generated $dst"
done

echo ""
echo "Agent 指令文件已生成到 $CLAWD_DIR/"
echo "如需修改配置，编辑 $ENV_FILE 后重新运行此脚本。"
