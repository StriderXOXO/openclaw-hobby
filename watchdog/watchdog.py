#!/usr/bin/env python3
"""
Watchdog — Health monitoring, auto-remediation, and daily reporting for hobby system services.

Continuously monitors all hobby daemon services, the agent gateway, and external
dependencies (Whisper API, etc.).  Automatically restarts crashed services, fixes
corrupt JSON state files, and sends Feishu alerts for critical issues.

Architecture:
    3-tier health checks:
      - Tier 1 (every 2 min):  Service status, JSON validity
      - Tier 2 (every 10 min): Heartbeat liveness, daemon liveness, triage backlog
      - Tier 3 (every 30 min): External APIs, disk/memory, error rates

    Auto-remediation:
      - Restart crashed services (with daily limits and cooldowns)
      - Fix corrupt JSON files (backup + reset)
      - Restart stalled gateway when heartbeat stops
      - Optionally restart remote Whisper API via SSH
      - Reset stale daily share counter

    Daily report (configurable hour, default CST 8:00):
      - Aggregates all hobby activity stats from daemon logs
      - Sends summary to Feishu chat

Configuration (via environment variables):
    FEISHU_APP_ID            Feishu app ID (for notifications)
    FEISHU_APP_SECRET        Feishu app secret
    FEISHU_CHAT_ID           Feishu chat ID for alerts and daily reports

    WHISPER_ENDPOINT         Whisper API health check URL (e.g. http://host:9876/health)
    WHISPER_TOKEN            Whisper API auth token

    REMOTE_HOST              Remote host for Whisper API restart via SSH (optional)
    REMOTE_USER              SSH username for remote host (optional)
    REMOTE_PASS              SSH password for remote host (optional)

    OPENCLAW_WORKSPACE       Override workspace root (default: ~/.openclaw/workspace)

Usage:
  # Run as daemon (systemd service)
  python3 watchdog.py daemon

  # One-shot health check with auto-remediation
  python3 watchdog.py check [--json] [--no-remediate]

  # Show current health report (no remediation)
  python3 watchdog.py report [--json]

  # Send daily activity report
  python3 watchdog.py daily-report [--dry-run] [--date YYYY-MM-DD]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Paths ---
WORKSPACE = Path(os.environ.get(
    "OPENCLAW_WORKSPACE",
    os.path.expanduser("~/.openclaw/workspace"),
))
HOBBY_DIR = WORKSPACE / "hobby"
HOBBY_LOG_DIR = HOBBY_DIR / "logs"
ALERTS_FILE = HOBBY_DIR / "watchdog-alerts.json"
REMEDIATION_FILE = HOBBY_DIR / "watchdog-remediation.json"

PENDING_FILES = {
    "twitter": WORKSPACE / "twitter-hobby" / "pending-shares.json",
    "youtube": WORKSPACE / "youtube-hobby" / "pending-shares.json",
    "podcast": WORKSPACE / "podcast-hobby" / "pending-shares.json",
}

MIND_STATE = HOBBY_DIR / "mind-state.json"

# --- Config (from environment variables) ---

# External API health check
WHISPER_URL = os.environ.get("WHISPER_ENDPOINT", "")
WHISPER_TOKEN = os.environ.get("WHISPER_TOKEN", "")

# Feishu notification
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")
FEISHU_NOTIFY_COOLDOWN = 3600  # 1 hour between same-type notifications

# Remote host access (optional — for restarting services like Whisper API via SSH)
REMOTE_HOST = os.environ.get("REMOTE_HOST", "")
REMOTE_USER = os.environ.get("REMOTE_USER", "")
SSH_KEY_PATH = os.environ.get("SSH_KEY_PATH", "")     # Recommended: SSH key auth
REMOTE_PASS = os.environ.get("REMOTE_PASS", "")       # Fallback: password auth (sshpass -e)
WHISPER_REMOTE_RESTART_COOLDOWN = 21600  # 6 hours

ACTIVE_HOURS_START = 7   # CST
ACTIVE_HOURS_END = 23    # CST

# Check intervals (seconds)
TIER1_INTERVAL = 120     # 2 min
TIER2_INTERVAL = 600     # 10 min
TIER3_INTERVAL = 1800    # 30 min

# Daily report
DAILY_REPORT_HOUR = 8    # CST hour to send daily report

# Thresholds
HEARTBEAT_MAX_AGE_MIN = 25
TWITTER_MAX_AGE_HOURS = 4
YOUTUBE_MAX_AGE_HOURS = 8
PODCAST_MAX_AGE_HOURS = 6
DISK_WARN_PCT = 85
DISK_CRIT_PCT = 92
MEM_WARN_MB = 200
ERROR_RATE_THRESHOLD = 10

# Remediation limits
MAX_RESTARTS_PER_DAY = 3
RESTART_COOLDOWN_SEC = 1800  # 30 min
MAX_CORRUPT_FIX_PER_DAY = 1

# --- Utilities ---

def load_json(path, default=None):
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default if default is not None else {}
    return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def log_event(event, **kwargs):
    HOBBY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = HOBBY_LOG_DIR / f"daemon-watchdog-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "watchdog",
        "event": event,
        **kwargs,
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cst_now():
    return datetime.now(timezone(timedelta(hours=8)))


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def is_active_hours():
    h = cst_now().hour
    return ACTIVE_HOURS_START <= h < ACTIVE_HOURS_END


# --- Feishu Notification ---

def get_feishu_token():
    """Get tenant access token from Feishu API."""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        log_event("feishu_token_error", error="FEISHU_APP_ID or FEISHU_APP_SECRET not set")
        return None
    try:
        payload = json.dumps({
            "app_id": FEISHU_APP_ID,
            "app_secret": FEISHU_APP_SECRET,
        }).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("code") == 0:
                return data["tenant_access_token"]
    except Exception as e:
        log_event("feishu_token_error", error=str(e))
    return None


def send_feishu_message(text):
    """Send a text message to the configured Feishu chat."""
    if not FEISHU_CHAT_ID:
        log_event("feishu_send_skip", reason="FEISHU_CHAT_ID not set")
        return False
    token = get_feishu_token()
    if not token:
        log_event("feishu_send_skip", reason="no token")
        return False
    try:
        payload = json.dumps({
            "receive_id": FEISHU_CHAT_ID,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("code") == 0:
                log_event("feishu_sent", text=text[:100])
                return True
            else:
                log_event("feishu_send_error", code=data.get("code"), msg=data.get("msg"))
    except Exception as e:
        log_event("feishu_send_error", error=str(e))
    return False


def send_feishu_alert(alert_name, message):
    """Rate-limited Feishu alert. Skips if same alert_name sent within cooldown."""
    state = load_remediation_state()
    notify_state = state.setdefault("feishu_notifications", {})
    last_sent = notify_state.get(alert_name, 0)
    if time.time() - last_sent < FEISHU_NOTIFY_COOLDOWN:
        return False
    if send_feishu_message(message):
        notify_state[alert_name] = time.time()
        save_remediation_state(state)
        return True
    return False


def notify_critical_alerts(all_results):
    """Send Feishu notifications for important alerts."""
    failed = [r for r in all_results if not r.ok]
    if not failed:
        return

    # Collect notable issues
    issues = []
    for r in failed:
        if r.severity == "CRITICAL":
            issues.append(r)
        elif r.name == "whisper_api":
            # Whisper API is WARNING but important for podcast transcription
            issues.append(r)

    if not issues:
        return

    # Build natural-language message
    lines = ["Warning: System Issues\n"]
    for r in issues:
        if r.name == "gateway_active":
            lines.append("Agent gateway is down")
        elif r.name == "heartbeat_liveness":
            lines.append(f"Heartbeat stopped — {r.detail}")
        elif r.name.startswith("triage_"):
            source = r.name.replace("triage_", "")
            lines.append(f"{source} triage backlog — {r.detail}")
        elif r.name == "whisper_api":
            lines.append("Whisper API unreachable")
        elif r.name == "mind_state_valid":
            lines.append("mind-state.json corrupted")
        elif r.name == "disk_usage":
            lines.append(f"Disk space alert — {r.detail}")
        else:
            lines.append(f"{r.name}: {r.detail}")

    lines.append("\nAuto-remediation attempted.")
    message = "\n".join(lines)

    # Use combined alert name for rate limiting
    alert_key = "critical_batch_" + "_".join(sorted(r.name for r in issues))
    send_feishu_alert(alert_key, message)


# --- Daily Report ---

def parse_daemon_log(source, date_str):
    """Read all JSONL entries from a daemon log file for a given date."""
    log_file = HOBBY_LOG_DIR / f"daemon-{source}-{date_str}.jsonl"
    entries = []
    if not log_file.exists():
        return entries
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def parse_decision_log(date_str):
    """Read all JSONL entries from the heartbeat decision log for a given date."""
    log_file = HOBBY_LOG_DIR / f"decisions-{date_str}.jsonl"
    entries = []
    if not log_file.exists():
        return entries
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def collect_daily_stats(date_str):
    """Aggregate daily activity stats from all daemon logs."""
    stats = {}

    # --- Twitter ---
    tw_entries = parse_daemon_log("twitter", date_str)
    tw_cycles = [e for e in tw_entries if e.get("event") == "cycle_start"]
    tw_queued = [e for e in tw_entries if e.get("event") == "item_queued"]
    tw_strategies = {}
    for e in tw_cycles:
        s = e.get("strategy", "unknown")
        tw_strategies[s] = tw_strategies.get(s, 0) + 1
    stats["twitter"] = {
        "cycles": len(tw_cycles),
        "items_queued": len(tw_queued),
        "strategies": tw_strategies,
    }

    # --- YouTube ---
    yt_entries = parse_daemon_log("youtube", date_str)
    yt_cycles = [e for e in yt_entries if e.get("event") == "cycle_start"]
    yt_queued = [e for e in yt_entries if e.get("event") == "item_queued"]
    yt_strategies = {}
    for e in yt_cycles:
        s = e.get("strategy", "unknown")
        yt_strategies[s] = yt_strategies.get(s, 0) + 1
    stats["youtube"] = {
        "cycles": len(yt_cycles),
        "items_queued": len(yt_queued),
        "strategies": yt_strategies,
    }

    # --- Podcast ---
    pc_entries = parse_daemon_log("podcast", date_str)
    pc_cycles = [e for e in pc_entries if e.get("event") == "cycle_start"]
    pc_feeds = [e for e in pc_entries if e.get("event") == "feed_processed"]
    pc_queued = [e for e in pc_entries if e.get("event") == "item_queued"]
    total_episodes = sum(e.get("episodes_count", 0) for e in pc_feeds)
    stats["podcast"] = {
        "cycles": len(pc_cycles),
        "feeds_processed": len(pc_feeds),
        "new_episodes": total_episodes,
        "items_queued": len(pc_queued),
    }

    # --- Heartbeat ---
    hb_entries = parse_decision_log(date_str)
    hb_share = [e for e in hb_entries if e.get("action") == "share"]
    hb_idle = [e for e in hb_entries if e.get("action") in ("idle", "wait")]
    total_items_pushed = 0
    for e in hb_share:
        items = e.get("items", [])
        total_items_pushed += len(items) if isinstance(items, list) else 0
    stats["heartbeat"] = {
        "decisions": len(hb_entries),
        "shares": len(hb_share),
        "idles": len(hb_idle),
        "items_pushed": total_items_pushed,
    }

    # --- Watchdog ---
    wd_entries = parse_daemon_log("watchdog", date_str)
    wd_checks = [e for e in wd_entries if e.get("event") == "check_cycle"]
    wd_remediate = [e for e in wd_entries if e.get("event") == "remediate"]
    wd_feishu = [e for e in wd_entries if e.get("event") == "feishu_sent"]
    # Group remediation by action type
    remediation_types = {}
    for e in wd_remediate:
        action = e.get("action", "unknown")
        remediation_types[action] = remediation_types.get(action, 0) + 1
    stats["watchdog"] = {
        "check_cycles": len(wd_checks),
        "remediations": len(wd_remediate),
        "remediation_types": remediation_types,
        "feishu_alerts": len(wd_feishu),
    }

    return stats


def format_daily_report(date_str, stats, current_health):
    """Format daily stats into a text report for Feishu."""
    lines = [f"Daily System Report — {date_str}", ""]

    # Current status
    status = current_health.get("overall_status", "unknown").upper()
    status_label = "OK" if status == "HEALTHY" else ("DEGRADED" if status == "DEGRADED" else "CRITICAL")
    lines.append(f"Current status: {status_label}")
    lines.append("")

    # Twitter
    tw = stats.get("twitter", {})
    lines.append("Twitter")
    lines.append(f"  Cycles: {tw.get('cycles', 0)}")
    strategies = tw.get("strategies", {})
    if strategies:
        parts = [f"{k}({v})" for k, v in strategies.items()]
        lines.append(f"  Strategies: {', '.join(parts)}")
    lines.append(f"  Items collected: {tw.get('items_queued', 0)}")
    lines.append("")

    # YouTube
    yt = stats.get("youtube", {})
    lines.append("YouTube")
    lines.append(f"  Cycles: {yt.get('cycles', 0)}")
    strategies = yt.get("strategies", {})
    if strategies:
        parts = [f"{k}({v})" for k, v in strategies.items()]
        lines.append(f"  Strategies: {', '.join(parts)}")
    lines.append(f"  Videos collected (with subtitles): {yt.get('items_queued', 0)}")
    lines.append("")

    # Podcast
    pc = stats.get("podcast", {})
    lines.append("Podcast")
    lines.append(f"  Cycles: {pc.get('cycles', 0)}")
    lines.append(f"  Feeds processed: {pc.get('feeds_processed', 0)}")
    lines.append(f"  New episodes: {pc.get('new_episodes', 0)}")
    lines.append(f"  Transcribed & stored: {pc.get('items_queued', 0)}")
    lines.append("")

    # Heartbeat
    hb = stats.get("heartbeat", {})
    lines.append("Heartbeat & Sharing")
    decisions = hb.get("decisions", 0)
    shares = hb.get("shares", 0)
    idles = hb.get("idles", 0)
    if decisions > 0:
        lines.append(f"  Decisions: {decisions} (shares: {shares}, idles: {idles})")
        lines.append(f"  Items pushed: {hb.get('items_pushed', 0)}")
    else:
        lines.append("  No heartbeat decisions recorded")
    lines.append("")

    # Watchdog
    wd = stats.get("watchdog", {})
    lines.append("Ops & Auto-remediation")
    lines.append(f"  Check cycles: {wd.get('check_cycles', 0)}")
    rem_count = wd.get("remediations", 0)
    if rem_count > 0:
        lines.append(f"  Auto-fixes: {rem_count}")
        for action, count in wd.get("remediation_types", {}).items():
            label = action.replace("_", " ")
            lines.append(f"    {label}: {count}")
    else:
        lines.append("  Auto-fixes: none (all clear)")
    if wd.get("feishu_alerts", 0):
        lines.append(f"  Feishu alerts sent: {wd.get('feishu_alerts', 0)}")
    lines.append("")

    # Active alerts
    active_alerts = current_health.get("active_alerts", [])
    if active_alerts:
        lines.append("Active alerts:")
        for a in active_alerts:
            lines.append(f"  [{a['severity']}] {a['name']}: {a['detail']}")
    else:
        lines.append("No active alerts.")

    # Uptime
    uptime = current_health.get("stats", {}).get("uptime_hours", 0)
    if uptime:
        lines.append("")
        lines.append(f"Monitoring uptime: {uptime:.0f} hours")

    return "\n".join(lines)


def send_daily_report(date_str=None, dry_run=False):
    """Collect stats, run health check, format and send daily report."""
    if date_str is None:
        # Default: report on yesterday
        yesterday = cst_now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")

    log_event("daily_report_start", date=date_str)

    # Collect stats
    stats = collect_daily_stats(date_str)

    # Run a fresh health check for current status
    all_results = tier1_checks() + tier2_checks() + tier3_checks()
    actions = remediate(all_results)
    current_health = build_alert_state(all_results, actions)

    # Format report
    report = format_daily_report(date_str, stats, current_health)

    if dry_run:
        print(report)
        return True

    # Send via Feishu (bypass rate limiting — daily report always sends)
    ok = send_feishu_message(report)
    log_event("daily_report_sent", date=date_str, success=ok)
    if ok:
        print(f"[watchdog] Daily report for {date_str} sent to Feishu")
    else:
        print(f"[watchdog] Failed to send daily report for {date_str}")
    return ok


# --- Remote Service Restart ---

def restart_whisper_api_remote():
    """Attempt to restart whisper-api on a remote host via SSH. Rate-limited.

    Auth modes (in priority order):
    1. SSH key: set SSH_KEY_PATH environment variable (recommended)
    2. Password: set REMOTE_PASS environment variable (uses sshpass -e, not -p)

    Also requires REMOTE_HOST and REMOTE_USER environment variables.
    """
    if not REMOTE_HOST or not REMOTE_USER:
        return False, "remote SSH not configured (set REMOTE_HOST, REMOTE_USER)"
    if not SSH_KEY_PATH and not REMOTE_PASS:
        return False, "remote SSH auth not configured (set SSH_KEY_PATH or REMOTE_PASS)"

    state = load_remediation_state()
    whisper_state = state.setdefault("whisper_remote", {})
    last_attempt = whisper_state.get("last_restart", 0)
    if time.time() - last_attempt < WHISPER_REMOTE_RESTART_COOLDOWN:
        return False, "cooldown (max 1 per 6h)"

    # Build SSH command based on auth mode
    ssh_opts = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"
    remote_cmd = "sudo systemctl restart whisper-api"

    if SSH_KEY_PATH:
        ssh_cmd = f"ssh -i {SSH_KEY_PATH} {ssh_opts} {REMOTE_USER}@{REMOTE_HOST} '{remote_cmd}'"
    else:
        # Use sshpass -e (reads password from SSHPASS env var, never on command line)
        os.environ["SSHPASS"] = REMOTE_PASS
        ssh_cmd = f"sshpass -e ssh {ssh_opts} {REMOTE_USER}@{REMOTE_HOST} '{remote_cmd}'"

    log_event("remediate", action="whisper_remote_restart", host=REMOTE_HOST)
    rc, out, err = run_cmd(ssh_cmd, timeout=30)

    whisper_state["last_restart"] = time.time()
    whisper_state["last_result"] = "ok" if rc == 0 else f"rc={rc} err={err}"
    save_remediation_state(state)

    if rc == 0:
        log_event("remediate", action="whisper_remote_restart_ok")
        return True, "restarted whisper-api on remote host"
    else:
        log_event("remediate", action="whisper_remote_restart_fail", rc=rc, err=err)
        return False, f"SSH restart failed (rc={rc}): {err}"


# --- Health Checks ---

class CheckResult:
    def __init__(self, name, ok, severity="WARNING", detail=""):
        self.name = name
        self.ok = ok
        self.severity = severity  # WARNING or CRITICAL
        self.detail = detail

    def to_dict(self):
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
        }


# -- Tier 1: Fast checks (every 2 min) --

def check_service_active(unit, is_user=False):
    flag = "--user " if is_user else ""
    rc, out, _ = run_cmd(f"systemctl {flag}is-active {unit}")
    return out.strip() == "active", out.strip()


def tier1_checks():
    results = []

    # Gateway (user service)
    ok, detail = check_service_active("moltbot-gateway", is_user=True)
    results.append(CheckResult("gateway_active", ok, "CRITICAL", detail))

    # Hobby daemons (system services)
    for svc in ["twitter-hobby", "youtube-hobby", "podcast-hobby"]:
        ok, detail = check_service_active(svc)
        svc_key = svc.replace("-hobby", "")
        results.append(CheckResult(f"{svc_key}_active", ok, "WARNING", detail))

    # mind-state.json validity
    try:
        with open(MIND_STATE) as f:
            json.load(f)
        results.append(CheckResult("mind_state_valid", True, "CRITICAL", "parses OK"))
    except (json.JSONDecodeError, OSError) as e:
        results.append(CheckResult("mind_state_valid", False, "CRITICAL", str(e)))

    # pending-shares.json validity
    for name, path in PENDING_FILES.items():
        try:
            with open(path) as f:
                json.load(f)
            results.append(CheckResult(f"pending_{name}_valid", True, "WARNING", "parses OK"))
        except (json.JSONDecodeError, OSError) as e:
            results.append(CheckResult(f"pending_{name}_valid", False, "WARNING", str(e)))

    return results


# -- Tier 2: Medium checks (every 10 min) --

def get_last_decision_age_min():
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = HOBBY_LOG_DIR / f"decisions-{today}.jsonl"
    if not log_file.exists():
        return None
    try:
        last_line = ""
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return None
        entry = json.loads(last_line)
        ts_str = entry.get("time", "")
        # Format: "2026-02-05 12:08 CST"
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M CST")
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        now = cst_now()
        return (now - dt).total_seconds() / 60
    except Exception:
        return None


def get_daemon_last_cycle_age_hours(source):
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = HOBBY_LOG_DIR / f"daemon-{source}-{today}.jsonl"
    if not log_file.exists():
        # Try yesterday
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        log_file = HOBBY_LOG_DIR / f"daemon-{source}-{yesterday}.jsonl"
        if not log_file.exists():
            return None
    try:
        last_cycle = None
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("event") in ("cycle_end", "cycle_complete", "sleep"):
                    last_cycle = entry
        if not last_cycle:
            return None
        ts = datetime.fromisoformat(last_cycle["ts"])
        now = datetime.now(timezone.utc)
        return (now - ts).total_seconds() / 3600
    except Exception:
        return None


def check_stale_daily_counter():
    """Check if items_shared_today is stale (non-zero but last_share is from a previous day)."""
    ms = load_json(MIND_STATE, {})
    cooldown = ms.get("sharing_cooldown", {})
    count = cooldown.get("items_shared_today", 0)
    last_share = cooldown.get("last_share")
    if count == 0 or not last_share:
        return CheckResult("daily_counter_stale", True, "WARNING", f"counter={count}, ok")
    try:
        share_date = datetime.fromisoformat(last_share).strftime("%Y-%m-%d")
        today = cst_now().strftime("%Y-%m-%d")
        if share_date != today and count > 0:
            return CheckResult("daily_counter_stale", False, "WARNING",
                               f"counter={count} but last_share={share_date} (today={today})")
        return CheckResult("daily_counter_stale", True, "WARNING",
                           f"counter={count}, last_share={share_date}")
    except Exception as e:
        return CheckResult("daily_counter_stale", True, "WARNING", f"parse error: {e}")


def tier2_checks():
    results = []

    # Stale daily counter
    results.append(check_stale_daily_counter())

    # Heartbeat liveness (only during active hours)
    if is_active_hours():
        age = get_last_decision_age_min()
        if age is None:
            results.append(CheckResult("heartbeat_liveness", False, "CRITICAL",
                                       "no decision log today"))
        elif age > HEARTBEAT_MAX_AGE_MIN:
            results.append(CheckResult("heartbeat_liveness", False, "CRITICAL",
                                       f"last decision {age:.0f} min ago (> {HEARTBEAT_MAX_AGE_MIN})"))
        else:
            results.append(CheckResult("heartbeat_liveness", True, "CRITICAL",
                                       f"last decision {age:.0f} min ago"))
    else:
        results.append(CheckResult("heartbeat_liveness", True, "CRITICAL", "outside active hours"))

    # Daemon liveness
    daemon_thresholds = {
        "twitter": TWITTER_MAX_AGE_HOURS,
        "youtube": YOUTUBE_MAX_AGE_HOURS,
        "podcast": PODCAST_MAX_AGE_HOURS,
    }
    for source, max_hours in daemon_thresholds.items():
        age = get_daemon_last_cycle_age_hours(source)
        if age is None:
            results.append(CheckResult(f"{source}_liveness", False, "WARNING",
                                       "no recent cycle found"))
        elif age > max_hours:
            results.append(CheckResult(f"{source}_liveness", False, "WARNING",
                                       f"last cycle {age:.1f}h ago (> {max_hours}h)"))
        else:
            results.append(CheckResult(f"{source}_liveness", True, "WARNING",
                                       f"last cycle {age:.1f}h ago"))

    # Triage backlog
    triage_results = check_triage_health()
    results.extend(triage_results)

    return results


def check_triage_health():
    """Check triage backlog across all hobbies."""
    results = []
    for name, path in PENDING_FILES.items():
        shares = load_json(path, [])
        untriaged = 0
        for item in shares:
            if not isinstance(item, dict):
                continue
            if item.get("triaged") is True:
                continue
            # Must have content to be triageable
            if name == "podcast" and not item.get("transcript_path"):
                continue
            if name == "youtube" and not item.get("subtitles_path"):
                continue
            if name == "twitter" and not item.get("text"):
                continue
            if not item.get("record_id"):
                continue
            untriaged += 1

        if untriaged > 50:
            results.append(CheckResult(f"triage_{name}", False, "CRITICAL",
                                       f"{untriaged} items untriaged, triage may be broken"))
        elif untriaged > 20:
            results.append(CheckResult(f"triage_{name}", False, "WARNING",
                                       f"{untriaged} items untriaged"))
        else:
            results.append(CheckResult(f"triage_{name}", True, "WARNING",
                                       f"{untriaged} items untriaged"))
    return results


# -- Tier 3: Slow checks (every 30 min) --

def check_whisper_api():
    if not WHISPER_URL:
        return True, "skipped (WHISPER_ENDPOINT not configured)"
    try:
        req = urllib.request.Request(WHISPER_URL)
        if WHISPER_TOKEN:
            req.add_header("Authorization", f"Bearer {WHISPER_TOKEN}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                return True, f"ok (model={data.get('model','?')}, device={data.get('device','?')})"
    except Exception as e:
        return False, str(e)
    return False, "non-200 response"


def check_disk_usage():
    usage = shutil.disk_usage("/")
    pct = usage.used / usage.total * 100
    total_gb = usage.total / (1024**3)
    used_gb = usage.used / (1024**3)
    free_gb = usage.free / (1024**3)
    detail = f"{pct:.0f}% ({used_gb:.1f}G/{total_gb:.1f}G, {free_gb:.1f}G free)"
    if pct >= DISK_CRIT_PCT:
        return False, "CRITICAL", detail
    elif pct >= DISK_WARN_PCT:
        return False, "WARNING", detail
    return True, "WARNING", detail


def check_memory():
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    mem[key] = int(parts[1])  # kB
        avail_mb = mem.get("MemAvailable", 0) / 1024
        total_mb = mem.get("MemTotal", 0) / 1024
        used_mb = total_mb - avail_mb
        detail = f"{used_mb:.0f}M/{total_mb:.0f}M ({avail_mb:.0f}M available)"
        if avail_mb < MEM_WARN_MB:
            return False, detail
        return True, detail
    except Exception as e:
        return True, f"cannot read /proc/meminfo: {e}"


def check_daemon_error_rate():
    errors = {}
    for source in ["twitter", "youtube", "podcast"]:
        rc, out, _ = run_cmd(
            f"journalctl -u {source}-hobby --since '30 minutes ago' --no-pager -p err 2>/dev/null | wc -l",
            timeout=15,
        )
        count = 0
        try:
            count = int(out.strip())
        except ValueError:
            pass
        errors[source] = count

    total = sum(errors.values())
    detail = ", ".join(f"{k}={v}" for k, v in errors.items())
    return total < ERROR_RATE_THRESHOLD, detail


def get_content_disk_usage():
    """Get disk usage of transcript and subtitle directories."""
    sizes = {}
    for name, subdir in [
        ("transcripts", WORKSPACE / "podcast-hobby" / "transcripts"),
        ("subtitles", WORKSPACE / "youtube-hobby" / "subtitles"),
    ]:
        if subdir.exists():
            total = sum(f.stat().st_size for f in subdir.rglob("*") if f.is_file())
            sizes[name] = total
        else:
            sizes[name] = 0
    return sizes


def tier3_checks():
    results = []

    # Whisper API
    ok, detail = check_whisper_api()
    results.append(CheckResult("whisper_api", ok, "WARNING", detail))

    # Disk
    ok, sev, detail = check_disk_usage()
    results.append(CheckResult("disk_usage", ok, sev, detail))

    # Memory
    ok, detail = check_memory()
    results.append(CheckResult("memory_usage", ok, "WARNING", detail))

    # Error rate
    ok, detail = check_daemon_error_rate()
    results.append(CheckResult("daemon_error_rate", ok, "WARNING", detail))

    return results


# --- Auto-Remediation ---

def load_remediation_state():
    return load_json(REMEDIATION_FILE, {
        "restarts": {},
        "corrupt_fixes": {},
        "date": datetime.now().strftime("%Y-%m-%d"),
    })


def save_remediation_state(state):
    save_json(REMEDIATION_FILE, state)


def reset_daily_counters(state):
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state["restarts"] = {}
        state["corrupt_fixes"] = {}
        state["date"] = today
    return state


def can_restart(state, service):
    info = state["restarts"].get(service, {"count": 0, "last": 0})
    if info["count"] >= MAX_RESTARTS_PER_DAY:
        return False
    if time.time() - info.get("last", 0) < RESTART_COOLDOWN_SEC:
        return False
    return True


def record_restart(state, service):
    if service not in state["restarts"]:
        state["restarts"][service] = {"count": 0, "last": 0}
    state["restarts"][service]["count"] += 1
    state["restarts"][service]["last"] = time.time()


def can_fix_corrupt(state, filepath):
    key = str(filepath)
    info = state["corrupt_fixes"].get(key, {"count": 0})
    return info["count"] < MAX_CORRUPT_FIX_PER_DAY


def record_corrupt_fix(state, filepath):
    key = str(filepath)
    if key not in state["corrupt_fixes"]:
        state["corrupt_fixes"][key] = {"count": 0}
    state["corrupt_fixes"][key]["count"] += 1


def remediate(results):
    state = load_remediation_state()
    state = reset_daily_counters(state)
    actions = []

    for r in results:
        if r.ok:
            continue

        # Service restart
        if r.name == "gateway_active" and not r.ok:
            svc = "moltbot-gateway"
            if can_restart(state, svc):
                log_event("remediate", action="restart", service=svc)
                rc, out, err = run_cmd(f"systemctl --user restart {svc}", timeout=30)
                record_restart(state, svc)
                actions.append(f"Restarted {svc} (rc={rc})")
            else:
                actions.append(f"Skip restart {svc} (limit/cooldown)")

        for hobby in ["twitter", "youtube", "podcast"]:
            if r.name == f"{hobby}_active" and not r.ok:
                svc = f"{hobby}-hobby"
                if can_restart(state, svc):
                    log_event("remediate", action="restart", service=svc)
                    rc, out, err = run_cmd(f"systemctl restart {svc}", timeout=30)
                    record_restart(state, svc)
                    actions.append(f"Restarted {svc} (rc={rc})")
                else:
                    actions.append(f"Skip restart {svc} (limit/cooldown)")

        # Corrupt JSON fix
        if r.name == "mind_state_valid" and not r.ok:
            if can_fix_corrupt(state, MIND_STATE):
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                backup = MIND_STATE.with_suffix(f".corrupt.{ts}")
                try:
                    MIND_STATE.rename(backup)
                except OSError:
                    pass
                save_json(MIND_STATE, {
                    "last_tick": datetime.now(timezone.utc).isoformat(),
                    "heartbeat_count": 0,
                    "sharing_cooldown": {"last_share": None, "items_shared_today": 0, "max_daily": 15},
                    "user_schedule": {
                        "timezone": "Asia/Shanghai",
                        "busy_hours_cst": [9, 10, 11, 14, 15],
                        "preferred_windows_cst": [7, 12, 16, 19],
                    },
                    "long_term_interests": {},
                    "recent_observations": [],
                    "content_preferences": {"topics": {}, "formats": {}},
                    "active_threads": [],
                })
                record_corrupt_fix(state, MIND_STATE)
                log_event("remediate", action="fix_corrupt", file=str(MIND_STATE))
                actions.append(f"Fixed corrupt {MIND_STATE.name} (backup: {backup.name})")

        for name, path in PENDING_FILES.items():
            if r.name == f"pending_{name}_valid" and not r.ok:
                if can_fix_corrupt(state, path):
                    ts = datetime.now().strftime("%Y%m%d%H%M%S")
                    backup = path.with_suffix(f".corrupt.{ts}")
                    try:
                        path.rename(backup)
                    except OSError:
                        pass
                    save_json(path, [])
                    record_corrupt_fix(state, path)
                    log_event("remediate", action="fix_corrupt", file=str(path))
                    actions.append(f"Fixed corrupt {path.name} (backup: {backup.name})")

        # Gateway stall (heartbeat not firing)
        if r.name == "heartbeat_liveness" and not r.ok:
            svc = "moltbot-gateway"
            if can_restart(state, svc):
                log_event("remediate", action="restart_stalled", service=svc)
                rc, out, err = run_cmd(f"systemctl --user restart {svc}", timeout=30)
                record_restart(state, svc)
                actions.append(f"Restarted stalled {svc} (rc={rc})")

        # Whisper API remote restart
        if r.name == "whisper_api" and not r.ok:
            ok, detail = restart_whisper_api_remote()
            if ok:
                actions.append(f"Remote restart whisper-api: {detail}")
            else:
                actions.append(f"Whisper API remediation skipped: {detail}")

        # Stale daily counter (items_shared_today > 0 but last_share is from a previous day)
        if r.name == "daily_counter_stale" and not r.ok:
            try:
                ms = load_json(MIND_STATE, {})
                old_count = ms.get("sharing_cooldown", {}).get("items_shared_today", 0)
                ms.setdefault("sharing_cooldown", {})["items_shared_today"] = 0
                save_json(MIND_STATE, ms)
                log_event("remediate", action="reset_daily_counter", old_count=old_count)
                actions.append(f"Reset stale daily counter ({old_count} -> 0)")
            except Exception as e:
                actions.append(f"Failed to reset daily counter: {e}")

    save_remediation_state(state)
    return actions


# --- Alert File ---

def build_alert_state(all_results, remediation_actions):
    now = cst_now()
    active = []
    resolved = []

    for r in all_results:
        entry = r.to_dict()
        entry["checked_at"] = now.isoformat()
        if not r.ok:
            active.append(entry)
        else:
            resolved.append(entry)

    has_critical = any(not r.ok and r.severity == "CRITICAL" for r in all_results)
    has_warning = any(not r.ok and r.severity == "WARNING" for r in all_results)

    if has_critical:
        status = "critical"
    elif has_warning:
        status = "degraded"
    else:
        status = "healthy"

    # Load existing for uptime tracking
    existing = load_json(ALERTS_FILE, {})
    start_time = existing.get("monitoring_since", now.isoformat())

    try:
        start_dt = datetime.fromisoformat(start_time)
        uptime_hours = (now - start_dt).total_seconds() / 3600
    except Exception:
        uptime_hours = 0

    checks_today = existing.get("stats", {}).get("checks_run_today", 0) + 1
    last_date = existing.get("stats", {}).get("date", "")
    if last_date != now.strftime("%Y-%m-%d"):
        checks_today = 1

    alert_data = {
        "last_check": now.isoformat(),
        "overall_status": status,
        "active_alerts": active,
        "resolved_alerts": resolved[-10:],  # keep last 10
        "remediation_actions": remediation_actions,
        "monitoring_since": start_time,
        "stats": {
            "date": now.strftime("%Y-%m-%d"),
            "uptime_hours": round(uptime_hours, 1),
            "checks_run_today": checks_today,
            "total_alerts_today": len(active),
        },
    }

    save_json(ALERTS_FILE, alert_data)
    return alert_data


# --- Report ---

def format_human_report(all_results, alert_data):
    now = cst_now()
    status = alert_data["overall_status"].upper()
    lines = [
        f"=== Hobby System Health Report ===",
        f"Time: {now.strftime('%Y-%m-%d %H:%M CST')} | Overall: {status}",
        "",
    ]

    # Services
    lines.append("--- Services ---")
    svc_map = {
        "gateway_active": "gateway",
        "twitter_active": "twitter",
        "youtube_active": "youtube",
        "podcast_active": "podcast",
    }
    svc_parts = []
    for check_name, label in svc_map.items():
        r = next((x for x in all_results if x.name == check_name), None)
        state = "ACTIVE" if (r and r.ok) else "DOWN"
        svc_parts.append(f"{label}: {state}")
    lines.append("  " + " | ".join(svc_parts))
    lines.append("")

    # Heartbeat
    lines.append("--- Heartbeat ---")
    hb = next((x for x in all_results if x.name == "heartbeat_liveness"), None)
    if hb:
        lines.append(f"  {hb.detail}")
    dc = next((x for x in all_results if x.name == "daily_counter_stale"), None)
    if dc and not dc.ok:
        lines.append(f"  Daily counter: STALE — {dc.detail}")

    # Count shares today from decision log
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = HOBBY_LOG_DIR / f"decisions-{today}.jsonl"
    shares_today = 0
    if log_file.exists():
        try:
            with open(log_file) as f:
                for line in f:
                    if '"share"' in line:
                        shares_today += 1
        except OSError:
            pass
    lines.append(f"  Shares today: {shares_today}")
    lines.append("")

    # Daemons
    lines.append("--- Daemons ---")
    for source in ["twitter", "youtube", "podcast"]:
        r = next((x for x in all_results if x.name == f"{source}_liveness"), None)
        if r:
            lines.append(f"  {source.capitalize()}: {r.detail}")
        else:
            lines.append(f"  {source.capitalize()}: not checked")
    lines.append("")

    # Triage
    lines.append("--- Triage ---")
    for source in ["twitter", "youtube", "podcast"]:
        r = next((x for x in all_results if x.name == f"triage_{source}"), None)
        if r:
            status_icon = "OK" if r.ok else r.severity
            lines.append(f"  {source.capitalize()}: {r.detail} [{status_icon}]")
        else:
            lines.append(f"  {source.capitalize()}: not checked")
    lines.append("")

    # External
    lines.append("--- External ---")
    whisper = next((x for x in all_results if x.name == "whisper_api"), None)
    if whisper:
        state = "REACHABLE" if whisper.ok else "UNREACHABLE"
        lines.append(f"  Whisper API: {state} — {whisper.detail}")
    else:
        lines.append("  Whisper API: not checked")
    lines.append("")

    # Resources
    lines.append("--- Resources ---")
    disk = next((x for x in all_results if x.name == "disk_usage"), None)
    mem = next((x for x in all_results if x.name == "memory_usage"), None)
    if disk:
        lines.append(f"  Disk: {disk.detail}")
    if mem:
        lines.append(f"  Memory: {mem.detail}")

    content_sizes = get_content_disk_usage()
    total_content_mb = sum(content_sizes.values()) / (1024 * 1024)
    lines.append(f"  Content storage: {total_content_mb:.0f} MB (transcripts + subtitles)")
    lines.append("")

    # Alerts
    lines.append("--- Alerts ---")
    active = alert_data.get("active_alerts", [])
    if active:
        for a in active:
            lines.append(f"  [{a['severity']}] {a['name']}: {a['detail']}")
    else:
        lines.append("  No active alerts.")

    # Remediation
    actions = alert_data.get("remediation_actions", [])
    if actions:
        lines.append("")
        lines.append("--- Remediation ---")
        for a in actions:
            lines.append(f"  {a}")

    lines.append("")
    stats = alert_data.get("stats", {})
    lines.append(f"Monitoring uptime: {stats.get('uptime_hours', 0):.1f}h | "
                 f"Checks today: {stats.get('checks_run_today', 0)}")

    return "\n".join(lines)


# --- Main Commands ---

def cmd_check(args):
    """Run all health checks once and print results."""
    all_results = tier1_checks() + tier2_checks() + tier3_checks()
    actions = remediate(all_results) if not args.no_remediate else []
    alert_data = build_alert_state(all_results, actions)

    if args.json:
        print(json.dumps(alert_data, indent=2, ensure_ascii=False))
    else:
        print(format_human_report(all_results, alert_data))

    failed = [r for r in all_results if not r.ok]
    sys.exit(1 if failed else 0)


def cmd_report(args):
    """Show current health status from last check data."""
    # Run a fresh check
    all_results = tier1_checks() + tier2_checks() + tier3_checks()
    actions = []  # report doesn't remediate
    alert_data = build_alert_state(all_results, actions)

    if args.json:
        print(json.dumps(alert_data, indent=2, ensure_ascii=False))
    else:
        print(format_human_report(all_results, alert_data))


def cmd_daemon(args):
    """Run watchdog in continuous daemon mode."""
    log_event("daemon_start")
    print(f"[watchdog] Starting daemon (tier1={TIER1_INTERVAL}s, tier2={TIER2_INTERVAL}s, tier3={TIER3_INTERVAL}s)")

    last_tier1 = 0
    last_tier2 = 0
    last_tier3 = 0
    last_daily_report_date = ""

    while True:
        now = time.time()
        all_results = []

        # Daily report check (CST 8:00)
        now_cst = cst_now()
        today_str = now_cst.strftime("%Y-%m-%d")
        if now_cst.hour >= DAILY_REPORT_HOUR and last_daily_report_date != today_str:
            print(f"[watchdog] Sending daily report for yesterday...")
            try:
                send_daily_report()
                last_daily_report_date = today_str
            except Exception as e:
                log_event("daily_report_error", error=str(e))
                print(f"[watchdog] Daily report error: {e}")

        # Tier 1: every 2 min
        if now - last_tier1 >= TIER1_INTERVAL:
            all_results.extend(tier1_checks())
            last_tier1 = now

        # Tier 2: every 10 min
        if now - last_tier2 >= TIER2_INTERVAL:
            all_results.extend(tier2_checks())
            last_tier2 = now

        # Tier 3: every 30 min
        if now - last_tier3 >= TIER3_INTERVAL:
            all_results.extend(tier3_checks())
            last_tier3 = now

        if all_results:
            # Remediate
            actions = remediate(all_results)

            # Update alert file
            alert_data = build_alert_state(all_results, actions)

            # Feishu notifications for important issues
            notify_critical_alerts(all_results)

            # Log summary
            failed = [r for r in all_results if not r.ok]
            status = alert_data["overall_status"]
            log_event("check_cycle", status=status, checks=len(all_results),
                      failed=len(failed), actions=len(actions))

            if failed:
                print(f"[watchdog] {cst_now().strftime('%H:%M')} — {status.upper()}: "
                      f"{len(failed)} issue(s) — "
                      + ", ".join(f"{r.name}" for r in failed))
            else:
                print(f"[watchdog] {cst_now().strftime('%H:%M')} — HEALTHY ({len(all_results)} checks)")

            if actions:
                for a in actions:
                    print(f"  [remediate] {a}")

        # Sleep until next check needed
        next_tier1 = last_tier1 + TIER1_INTERVAL
        next_tier2 = last_tier2 + TIER2_INTERVAL
        next_tier3 = last_tier3 + TIER3_INTERVAL
        sleep_until = min(next_tier1, next_tier2, next_tier3)
        sleep_sec = max(1, sleep_until - time.time())
        time.sleep(sleep_sec)


def main():
    HOBBY_DIR.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(description="Hobby System Watchdog")
    sub = parser.add_subparsers(dest="command")

    p_daemon = sub.add_parser("daemon", help="Run watchdog as a continuous service")

    p_report = sub.add_parser("report", help="Show current health report")
    p_report.add_argument("--json", action="store_true", help="Output as JSON")

    p_check = sub.add_parser("check", help="Run all checks once and exit")
    p_check.add_argument("--json", action="store_true", help="Output as JSON")
    p_check.add_argument("--no-remediate", action="store_true", help="Skip auto-remediation")

    p_daily = sub.add_parser("daily-report", help="Send daily activity report")
    p_daily.add_argument("--dry-run", action="store_true", help="Print report without sending")
    p_daily.add_argument("--date", type=str, default=None, help="Report date (YYYY-MM-DD, default: yesterday)")

    args = parser.parse_args()

    if args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "daily-report":
        send_daily_report(date_str=args.date, dry_run=args.dry_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
